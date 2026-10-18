"""Veri tabanını kurar: tohumdan (data/seed/veri_tohum.db.gz) açar ya da boş şema oluşturur; istenirse toplayıcıları çalıştırır.

Kullanım (backend klasöründe):
  .venv/Scripts/python tools/veri_kur.py            # veri.db yoksa tohumdan kur
  .venv/Scripts/python tools/veri_kur.py --topla    # kur + tüm otomatik toplayıcılarla güncelle
  .venv/Scripts/python tools/veri_kur.py --sifirla  # mevcut veri.db'yi yedekleyip tohumdan yeniden kur

Tohum, veri tabanının sıkıştırılmış bir dökümüdür (tools/veri_tohum.py üretir); elle düzenlenen bir dosya değildir.
TEFAS fiyatları yalnızca dışa aktarım yüklemesiyle geldiği için temiz kurulumda tohumdan gelir.
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "veri.db"
SEED = ROOT / "data" / "seed" / "veri_tohum.db.gz"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topla", action="store_true", help="kurulumdan sonra tüm toplayıcıları çalıştır")
    ap.add_argument("--sifirla", action="store_true", help="mevcut veri tabanını yedekleyip tohumdan yeniden kur")
    a = ap.parse_args()
    if DB.exists() and a.sifirla:
        bak = DB.with_name(f"veri_yedek_{datetime.now():%Y%m%d_%H%M%S}.db")
        DB.rename(bak)
        for ext in ("-wal", "-shm"):
            Path(str(DB) + ext).unlink(missing_ok=True)
        print(f"mevcut veri tabanı yedeklendi: {bak.name}")
    if not DB.exists():
        if SEED.exists() and SEED.stat().st_size > 1000:
            with gzip.open(SEED, "rb") as src, open(DB, "wb") as dst:
                shutil.copyfileobj(src, dst, length=16 << 20)
            print(f"tohumdan kuruldu: {DB} ({DB.stat().st_size / 1e6:.0f} MB)")
        else:
            print("tohum yok (git lfs pull yapıldı mı?); boş şema oluşturuluyor")
    from app.db import store
    store.ready()
    counts = store.table_counts()
    print("tablolar:", ", ".join(f"{k} {v:,}".replace(",", ".") for k, v in counts.items() if v))
    if a.topla:
        sys.exit(subprocess.call([sys.executable, str(ROOT / "tools" / "veri_topla.py")]))


if __name__ == "__main__":
    main()
