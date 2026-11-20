"""
main.py — VidStore FastAPI application entry-point.

Routes:
- GET  /                   → redirect to /gallery or /login
- GET  /login              → login/register page
- POST /login              → authenticate user, set session cookie
- POST /register           → create account, set session cookie
- POST /logout             → clear session cookie
- GET  /gallery            → authenticated gallery page
- POST /upload             → upload one or more files
- GET  /media/{item_id}    → stream or redirect to protected media
- POST /favourite/{id}     → toggle favourite flag
- POST /delete/{id}        → delete a media item
"""

from __future__ import annotations

import mimetypes
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import aiofiles
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import auth
from auth import COOKIE_NAME, SESSION_MAX_AGE
from database import MediaItem, User, get_db, init_db
from pydantic import BaseModel
import storage

# ---------------------------------------------------------------------------
# App setup & Lifespan
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

if os.environ.get("VERCEL"):
    UPLOAD_DIR = Path("/tmp/uploads")
else:
    UPLOAD_DIR = BASE_DIR / "uploads"

try:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables safely
    try:
        init_db()
    except Exception as e:
        print(f"Database init warning: {e}")
    yield

app = FastAPI(title="VidStore", lifespan=lifespan, docs_url=None, redoc_url=None)

# Mount static files safely
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------------------------------------------------------------------
# Security Middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    # Handle Vercel /api prefix if present
    path = request.scope.get("path", "")
    if path == "/api":
        request.scope["path"] = "/"
    elif path.startswith("/api/"):
        request.scope["path"] = path[4:]

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def _is_secure(request: Request) -> bool:
    """Return True if running on HTTPS / Vercel."""
    if os.environ.get("VERCEL"):
        return True
    proto = request.headers.get("x-forwarded-proto")
    if proto == "https":
        return True
    return request.url.scheme == "https"


# ---------------------------------------------------------------------------
# Health check (GET) — no auth required, no secrets exposed
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    from database import DB_BACKEND, IS_VERCEL
    from sqlalchemy import text as sa_text
    db_connected = False
    db_error = None
    try:
        db.execute(sa_text("SELECT 1"))
        db_connected = True
    except Exception as exc:
        db_error = str(exc)[:200]
    return {
        "status": "ok" if db_connected else "degraded",
        "environment": "vercel" if IS_VERCEL else "local",
        "database_backend": DB_BACKEND,
        "database_connected": db_connected,
        "database_error": db_error,
        "storage_backend": storage._storage_backend,
    }


# ---------------------------------------------------------------------------
# Allowed media types & limits
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/avif", "image/bmp", "image/tiff",
}
ALLOWED_VIDEO_TYPES = {
    "video/mp4", "video/webm", "video/ogg", "video/quicktime",
    "video/x-matroska", "video/x-msvideo",
}
ALLOWED_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB per file


# ---------------------------------------------------------------------------
# Auth Dependency Helpers
# ---------------------------------------------------------------------------

def _resolve_user(
    db: Session = Depends(get_db),
    vs_session: Optional[str] = Cookie(default=None),
) -> Optional[User]:
    if not vs_session:
        return None
    uid = auth.decode_session_token(vs_session)
    if uid is None:
        return None
    return db.get(User, uid)


def _require_user(
    db: Session = Depends(get_db),
    vs_session: Optional[str] = Cookie(default=None),
) -> User:
    user = _resolve_user(db=db, vs_session=vs_session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------

@app.get("/", response_class=RedirectResponse)
@app.get("/api", response_class=RedirectResponse)
@app.get("/api/", response_class=RedirectResponse)
def root(user: Optional[User] = Depends(_resolve_user)):
    return RedirectResponse(url="/gallery" if user else "/login", status_code=302)


# ---------------------------------------------------------------------------
# Login / Register page (GET)
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    error: Optional[str] = None,
    tab: str = "login",
    user: Optional[User] = Depends(_resolve_user),
):
    if user:
        return RedirectResponse(url="/gallery", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "tab": tab},
    )


# ---------------------------------------------------------------------------
# Register (POST)
# ---------------------------------------------------------------------------

@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    username = username.strip().lower()
    if len(username) < 3 or len(username) > 32:
        return RedirectResponse(
            url="/login?tab=register&error=Username+must+be+3-32+characters",
            status_code=302,
        )
    if len(password) < 8:
        return RedirectResponse(
            url="/login?tab=register&error=Password+must+be+at+least+8+characters",
            status_code=302,
        )
    if db.query(User).filter(User.username == username).first():
        return RedirectResponse(
            url="/login?tab=register&error=Username+already+taken",
            status_code=302,
        )

    user = User(username=username, hashed_password=auth.hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)

    redirect = RedirectResponse(url="/gallery", status_code=302)
    redirect.set_cookie(
        key=COOKIE_NAME,
        value=auth.create_session_token(user.id),
        httponly=True,
        secure=_is_secure(request),
        max_age=SESSION_MAX_AGE,
        samesite="lax",
    )
    return redirect


# ---------------------------------------------------------------------------
# Login (POST)
# ---------------------------------------------------------------------------

@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    username = username.strip().lower()
    user: Optional[User] = db.query(User).filter(User.username == username).first()
    if not user or not auth.verify_password(password, user.hashed_password):
        return RedirectResponse(
            url="/login?tab=login&error=Invalid+username+or+password",
            status_code=302,
        )
    redirect = RedirectResponse(url="/gallery", status_code=302)
    redirect.set_cookie(
        key=COOKIE_NAME,
        value=auth.create_session_token(user.id),
        httponly=True,
        secure=_is_secure(request),
        max_age=SESSION_MAX_AGE,
        samesite="lax",
    )
    return redirect


# ---------------------------------------------------------------------------
# Logout (POST)
# ---------------------------------------------------------------------------

@app.post("/logout")
def logout():
    redirect = RedirectResponse(url="/login", status_code=302)
    redirect.delete_cookie(COOKIE_NAME)
    return redirect


# ---------------------------------------------------------------------------
# Gallery (GET) — authenticated
# ---------------------------------------------------------------------------

@app.get("/gallery", response_class=HTMLResponse)
def gallery(
    request: Request,
    filter: str = "all",   # all | images | videos | favourites
    error: Optional[str] = None,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(_resolve_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    q = db.query(MediaItem).filter(MediaItem.user_id == user.id)
    if filter == "images":
        q = q.filter(MediaItem.media_type == "image")
    elif filter == "videos":
        q = q.filter(MediaItem.media_type == "video")
    elif filter == "favourites":
        q = q.filter(MediaItem.is_favourite == True)  # noqa: E712

    items: List[MediaItem] = q.order_by(MediaItem.uploaded_at.desc()).all()

    total = db.query(MediaItem).filter(MediaItem.user_id == user.id).count()
    img_count = db.query(MediaItem).filter(
        MediaItem.user_id == user.id, MediaItem.media_type == "image"
    ).count()
    vid_count = db.query(MediaItem).filter(
        MediaItem.user_id == user.id, MediaItem.media_type == "video"
    ).count()
    fav_count = db.query(MediaItem).filter(
        MediaItem.user_id == user.id, MediaItem.is_favourite == True  # noqa: E712
    ).count()

    return templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={
            "user": user,
            "items": items,
            "filter": filter,
            "error": error,
            "counts": {
                "all": total,
                "images": img_count,
                "videos": vid_count,
                "favourites": fav_count,
            },
        },
    )


# ---------------------------------------------------------------------------
# Upload (POST) — authenticated
# ---------------------------------------------------------------------------

@app.post("/upload")
async def upload(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(_resolve_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    errors: List[str] = []

    for file in files:
        if not file.filename:
            continue

        guessed_type, _ = mimetypes.guess_type(file.filename)
        content_type = (file.content_type or guessed_type or "").lower()
        content_type = content_type.split(";")[0].strip()

        if content_type not in ALLOWED_TYPES:
            errors.append(f"{file.filename}: unsupported file type")
            continue

        media_type = "image" if content_type in ALLOWED_IMAGE_TYPES else "video"
        ext = Path(file.filename).suffix or mimetypes.guess_extension(content_type) or ""
        stored_name = f"{uuid.uuid4().hex}{ext}"

        # Read content
        content = await file.read()
        file_size = len(content)

        if file_size > MAX_FILE_SIZE:
            errors.append(f"{file.filename}: exceeds size limit")
            continue

        try:
            storage.save_media(user.id, stored_name, content, content_type)
            item = MediaItem(
                filename=stored_name,
                original_name=file.filename or stored_name,
                media_type=media_type,
                mime_type=content_type,
                file_size=file_size,
                user_id=user.id,
            )
            db.add(item)
        except Exception as e:
            errors.append(f"{file.filename}: upload failed ({str(e)})")

    db.commit()

    if errors:
        error_msg = "; ".join(errors)
        return RedirectResponse(url=f"/gallery?error={error_msg}", status_code=302)

    return RedirectResponse(url="/gallery", status_code=302)


class UploadTicketRequest(BaseModel):
    filename: str
    content_type: str
    file_size: int

class UploadCompleteRequest(BaseModel):
    stored_name: str
    original_name: str
    media_type: str
    mime_type: str
    file_size: int


@app.post("/upload-ticket")
@app.post("/api/upload-ticket")
def create_upload_ticket(
    payload: UploadTicketRequest,
    user: Optional[User] = Depends(_resolve_user),
):
    """Generate a direct-to-cloud signed upload URL or specify local mode."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    filename = payload.filename.strip()
    guessed_type, _ = mimetypes.guess_type(filename)
    content_type = (payload.content_type or guessed_type or "").lower().split(";")[0].strip()

    if content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    if payload.file_size > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File exceeds 50 MB limit")

    media_type = "image" if content_type in ALLOWED_IMAGE_TYPES else "video"
    ext = Path(filename).suffix or mimetypes.guess_extension(content_type) or ""
    stored_name = f"{uuid.uuid4().hex}{ext}"

    if storage.is_cloud_storage():
        signed_upload_url = storage.get_signed_upload_url(user.id, stored_name)
        if not signed_upload_url:
            raise HTTPException(status_code=500, detail="Failed to generate upload ticket")
        return {
            "mode": "cloud",
            "upload_url": signed_upload_url,
            "stored_name": stored_name,
            "media_type": media_type,
            "content_type": content_type,
        }
    else:
        return {
            "mode": "local",
            "upload_url": "/upload",
            "stored_name": stored_name,
            "media_type": media_type,
            "content_type": content_type,
        }


@app.post("/upload-complete")
@app.post("/api/upload-complete")
def finalize_upload(
    payload: UploadCompleteRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(_resolve_user),
):
    """Finalize upload by saving file metadata to the database."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    item = MediaItem(
        filename=payload.stored_name,
        original_name=payload.original_name,
        media_type=payload.media_type,
        mime_type=payload.mime_type,
        file_size=payload.file_size,
        user_id=user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"status": "ok", "id": item.id}


# ---------------------------------------------------------------------------
# Protected media streaming or redirect (GET) — authenticated
# ---------------------------------------------------------------------------

@app.get("/media/{item_id}")
async def serve_media(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(_resolve_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    item: Optional[MediaItem] = db.get(MediaItem, item_id)
    if not item or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")

    # If Supabase cloud storage is enabled, redirect directly to signed CDN URL
    if storage.is_cloud_storage():
        signed_url = storage.get_media_url(user.id, item.filename, expires_in=7200)
        if signed_url:
            return RedirectResponse(url=signed_url, status_code=307)

    # Local filesystem streaming fallback
    file_path = UPLOAD_DIR / str(user.id) / item.filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    file_size = file_path.stat().st_size

    # Support HTTP Range requests (needed for video seeking)
    range_header = request.headers.get("range")
    if range_header:
        try:
            range_val = range_header.strip().replace("bytes=", "")
            start_str, end_str = range_val.split("-")
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
        except ValueError:
            raise HTTPException(status_code=416, detail="Invalid range")

        end = min(end, file_size - 1)
        chunk_size = end - start + 1

        async def range_iter():
            async with aiofiles.open(file_path, "rb") as f:
                await f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = await f.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            range_iter(),
            status_code=206,
            media_type=item.mime_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
            },
        )

    # Full file response
    async def full_iter():
        async with aiofiles.open(file_path, "rb") as f:
            while True:
                chunk = await f.read(65536)
                if not chunk:
                    break
                yield chunk

    safe_filename = item.original_name.replace('"', '\\"')
    return StreamingResponse(
        full_iter(),
        media_type=item.mime_type,
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'inline; filename="{safe_filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Toggle favourite (POST)
# ---------------------------------------------------------------------------

@app.post("/favourite/{item_id}")
def toggle_favourite(
    item_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(_resolve_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    item: Optional[MediaItem] = db.get(MediaItem, item_id)
    if not item or item.user_id != user.id:
        raise HTTPException(status_code=404)

    item.is_favourite = not item.is_favourite
    db.commit()
    return RedirectResponse(url="/gallery", status_code=302)


# ---------------------------------------------------------------------------
# Delete item (POST)
# ---------------------------------------------------------------------------

@app.post("/delete/{item_id}")
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(_resolve_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    item: Optional[MediaItem] = db.get(MediaItem, item_id)
    if not item or item.user_id != user.id:
        raise HTTPException(status_code=404)

    # Remove from storage (cloud or local)
    storage.remove_media(user.id, item.filename)

    db.delete(item)
    db.commit()
    return RedirectResponse(url="/gallery", status_code=302)
