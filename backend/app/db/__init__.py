"""Veri tabanı katmanı (SQLAlchemy). Tüm piyasa ve ürün verisi burada tutulur; her satır kaynağını ve zamanını taşır.

Bağlantı: DATABASE_URL ortam değişkeni (varsayılan: backend/data/veri.db, SQLite).
Şema: app/db/models.py. Okuma yardımcıları: app/db/store.py. Veri toplama: app/collectors/.
"""
from .session import engine, init_db, session_scope  # noqa: F401
