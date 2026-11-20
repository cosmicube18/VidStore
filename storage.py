"""
storage.py — Cloud & Local Storage Abstraction for VidStore.

Supports:
- Supabase Storage (when SUPABASE_URL & SUPABASE_SERVICE_ROLE_KEY are configured)
- Local Filesystem (fallback: uploads/{user_id}/)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

# Load Keys.env or .env if present locally
BASE_DIR = Path(__file__).resolve().parent

if os.environ.get("VERCEL"):
    LOCAL_UPLOAD_DIR = Path("/tmp/uploads")
else:
    LOCAL_UPLOAD_DIR = BASE_DIR / "uploads"

try:
    LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

# Check environment or local files
def _load_env_fallback():
    for env_name in ("Keys.env", ".env"):
        env_path = BASE_DIR / env_name
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k not in os.environ:
                            os.environ[k] = v

_load_env_fallback()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "gallery")

_storage_backend = "supabase" if (SUPABASE_URL and SUPABASE_KEY) else "local"
print(f"[VidStore] Storage backend: {_storage_backend}")


def is_cloud_storage() -> bool:
    """Return True if Supabase storage credentials are configured."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


def get_headers(content_type: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def save_media(user_id: int, filename: str, data: bytes, content_type: str) -> None:
    """Save media to Supabase Storage or local disk."""
    if is_cloud_storage():
        object_path = f"{user_id}/{filename}"
        url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{object_path}"
        headers = get_headers(content_type)
        headers["x-upsert"] = "true"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Supabase upload failed with status {resp.status}")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Supabase storage error ({e.code}): {err_body}")
    else:
        user_dir = LOCAL_UPLOAD_DIR / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        file_path = user_dir / filename
        with open(file_path, "wb") as f:
            f.write(data)


def remove_media(user_id: int, filename: str) -> None:
    """Remove media from Supabase Storage or local disk."""
    if is_cloud_storage():
        object_path = f"{user_id}/{filename}"
        url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{object_path}"
        headers = get_headers()
        req = urllib.request.Request(url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(req) as resp:
                pass
        except Exception:
            pass
    else:
        file_path = LOCAL_UPLOAD_DIR / str(user_id) / filename
        file_path.unlink(missing_ok=True)


def get_signed_upload_url(user_id: int, filename: str) -> Optional[str]:
    """Generate a single-use signed upload URL for the browser to PUT the file directly into Supabase."""
    if not is_cloud_storage():
        return None

    object_path = f"{user_id}/{filename}"
    url = f"{SUPABASE_URL}/storage/v1/object/upload/sign/{SUPABASE_BUCKET}/{object_path}"
    headers = get_headers("application/json")
    req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            upload_path = data.get("url")
            if upload_path:
                return f"{SUPABASE_URL}/storage/v1{upload_path}"
    except Exception as e:
        print(f"Error creating signed upload URL: {e}")
        return None
    return None


def get_media_url(user_id: int, filename: str, expires_in: int = 7200) -> Optional[str]:
    """Generate a signed download/stream URL for cloud media."""
    if not is_cloud_storage():
        return None

    object_path = f"{user_id}/{filename}"
    url = f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_BUCKET}/{object_path}"
    payload = json.dumps({"expiresIn": expires_in}).encode("utf-8")
    headers = get_headers("application/json")

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            signed_path = data.get("signedURL")
            if signed_path:
                return f"{SUPABASE_URL}/storage/v1{signed_path}"
    except Exception as e:
        print(f"Error generating signed URL: {e}")
        return None
    return None
