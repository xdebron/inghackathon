"""Motor ve oturum. Varsayılan SQLite; DATABASE_URL ile başka bir veritabanına (ör. PostgreSQL) geçilebilir."""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import BASE_DIR

DEFAULT_URL = f"sqlite:///{(BASE_DIR / 'data' / 'veri.db').as_posix()}"
URL = os.getenv("DATABASE_URL", DEFAULT_URL)

engine = create_engine(URL, future=True)

if URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")      # okuma ve yazma birbirini beklemesin (API + toplayıcı aynı anda)
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    from . import models  # noqa: F401  (tabloları kaydeder)
    models.Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Session:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
