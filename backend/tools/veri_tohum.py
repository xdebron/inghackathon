"""Veri tabanının sıkıştırılmış dökümünü (tohum) üretir: data/seed/veri_tohum.db.gz (Git LFS ile repoda).

Tutarlı bir kopya için SQLite yedekleme API'si kullanılır (çalışan uygulamayı durdurmak gerekmez); kopya VACUUM'lanır ve
gzip'lenir. Temiz kurulum tools/veri_kur.py ile bu tohumdan başlar, sonra toplayıcılar günceller.

Kullanım (backend klasöründe):  .venv/Scripts/python tools/veri_tohum.py
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "veri.db"
OUT = ROOT / "data" / "seed" / "veri_tohum.db.gz"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix("")  # .db
    src, dst = sqlite3.connect(DB), sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.execute("VACUUM")
    dst.close()
    with open(tmp, "rb") as f, gzip.open(OUT, "wb", compresslevel=9) as g:
        shutil.copyfileobj(f, g, length=16 << 20)
    size = tmp.stat().st_size
    tmp.unlink()
    print(f"tohum: {OUT} ({OUT.stat().st_size / 1e6:.0f} MB sıkıştırılmış, {size / 1e6:.0f} MB açık)")


if __name__ == "__main__":
    main()
