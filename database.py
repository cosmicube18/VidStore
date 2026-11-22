"""
database.py — SQLAlchemy engine, session factory, and ORM models.

Supports:
- PostgreSQL (e.g. Supabase Postgres via DATABASE_URL)
- SQLite (Local development fallback)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

# ---------------------------------------------------------------------------
# Load environment & database URL
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

# Check for local env files if not set
for env_name in ("Keys.env", ".env"):
    env_file = BASE_DIR / env_name
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k not in os.environ:
                        os.environ[k] = v

IS_VERCEL = bool(os.environ.get("VERCEL"))

raw_db_url = os.environ.get("DATABASE_URL", "").strip().strip('"').strip("'")

_PLACEHOLDER_TOKENS = ("[PASSWORD]", "[YOUR-PASSWORD]", "[YOUR_PASSWORD]")

if raw_db_url and not any(tok in raw_db_url for tok in _PLACEHOLDER_TOKENS):
    # Normalize dialect prefix so SQLAlchemy uses psycopg2 (not psycopg v3)
    if raw_db_url.startswith("postgres://"):
        raw_db_url = raw_db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif raw_db_url.startswith("postgresql://") and not raw_db_url.startswith("postgresql+"):
        raw_db_url = raw_db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    DATABASE_URL = raw_db_url
    DB_BACKEND = "postgresql"
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    print("[VidStore] Database backend: postgresql (Supabase)")
else:
    if IS_VERCEL:
        if not raw_db_url:
            reason = "missing (not set in Vercel Environment Variables)"
        else:
            reason = "contains an unreplaced placeholder — replace [PASSWORD] with your real Supabase password"
        raise RuntimeError(
            f"DATABASE_URL is {reason}. "
            "A PostgreSQL DATABASE_URL is required on Vercel. "
            "Go to Vercel Project Settings → Environment Variables and set it."
        )
    sqlite_path = str(BASE_DIR / "vidstore.db")
    DATABASE_URL = f"sqlite:///{sqlite_path}"
    DB_BACKEND = "sqlite"
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    print("[VidStore] Database backend: sqlite (local dev)")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ---------------------------------------------------------------------------
# Base model & helpers
# ---------------------------------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: int = Column(Integer, primary_key=True, index=True)
    username: str = Column(String(64), unique=True, nullable=False, index=True)
    hashed_password: str = Column(String(256), nullable=False)
    created_at: datetime = Column(DateTime, default=utc_now)

    media_items = relationship("MediaItem", back_populates="owner", cascade="all, delete-orphan")
    folders = relationship("Folder", back_populates="owner", cascade="all, delete-orphan")


class Folder(Base):
    __tablename__ = "folders"

    id: int = Column(Integer, primary_key=True, index=True)
    user_id: int = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: str = Column(String(100), nullable=False)
    created_at: datetime = Column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_folder_user_name"),
    )

    owner = relationship("User", back_populates="folders")
    media_items = relationship("MediaItem", back_populates="folder")


class MediaItem(Base):
    __tablename__ = "media_items"

    id: int = Column(Integer, primary_key=True, index=True)
    filename: str = Column(String(256), nullable=False)          # stored filename on disk / bucket
    original_name: str = Column(String(256), nullable=False)     # original upload name
    media_type: str = Column(String(16), nullable=False)         # "image" | "video"
    mime_type: str = Column(String(64), nullable=False)
    file_size: int = Column(Integer, nullable=False)             # bytes
    uploaded_at: datetime = Column(DateTime, default=utc_now)
    is_favourite: bool = Column(Boolean, default=False)

    user_id: int = Column(Integer, ForeignKey("users.id"), nullable=False)
    folder_id: int = Column(Integer, ForeignKey("folders.id", ondelete="SET NULL"), nullable=True, index=True)

    owner = relationship("User", back_populates="media_items")
    folder = relationship("Folder", back_populates="media_items")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db():
    """FastAPI dependency — yields a DB session and closes it afterwards."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables (safe to call repeatedly) and apply safe column migrations."""
    Base.metadata.create_all(bind=engine)

    # Safe idempotent migration: add folder_id to existing media_items tables
    with engine.connect() as conn:
        if DB_BACKEND == "postgresql":
            conn.execute(text(
                "ALTER TABLE media_items "
                "ADD COLUMN IF NOT EXISTS folder_id INTEGER "
                "REFERENCES folders(id) ON DELETE SET NULL"
            ))
            conn.commit()
        else:
            # SQLite: check via PRAGMA
            cols = conn.execute(text("PRAGMA table_info(media_items)")).fetchall()
            col_names = [row[1] for row in cols]
            if "folder_id" not in col_names:
                conn.execute(text(
                    "ALTER TABLE media_items ADD COLUMN folder_id INTEGER REFERENCES folders(id)"
                ))
                conn.commit()
