from __future__ import annotations

from sqlalchemy.orm import Session

from .core.config import get_settings
from .db import make_engine, make_session_factory

_settings = None
_engine = None
_SessionLocal = None


def _init():
    global _settings, _engine, _SessionLocal
    if _settings is None:
        _settings = get_settings()
        _engine = make_engine(_settings.database_url)
        _SessionLocal = make_session_factory(_engine)


def get_db():
    _init()
    db: Session = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_engine():
    _init()
    return _engine


def get_settings_cached():
    _init()
    return _settings
