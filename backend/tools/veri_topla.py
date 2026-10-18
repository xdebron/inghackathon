"""Veri toplama: tüm otomatik kaynakları sırayla çeker ve veri tabanına yazar.

Kullanım (backend klasöründe):
  .venv/Scripts/python tools/veri_topla.py                  # hepsi, sırayla
  .venv/Scripts/python tools/veri_topla.py ing_mevduat bist # yalnızca seçilenler
  .venv/Scripts/python tools/veri_topla.py --liste          # toplayıcılar ve son çalıştırmaları
  .venv/Scripts/python tools/veri_topla.py kap_fon --kap-limit 1000

Bir toplayıcı hata verirse diğerleri devam eder; sonuç ve hata 'cekim' tablosuna yazılır.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import collectors  # noqa: E402
from app.db import store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("adlar", nargs="*", help=f"toplayıcılar (boşsa hepsi): {', '.join(collectors.COLLECTORS)}")
    ap.add_argument("--liste", action="store_true")
    ap.add_argument("--kap-limit", type=int, default=300)
    ap.add_argument("--kap-yas", type=int, default=7, help="KAP kaydı bu kadar günden eskiyse yenilenir")
    a = ap.parse_args()
    if a.liste:
        last = {}
        for r in store.runs(500):
            last.setdefault(r["kaynak"], r)
        for k, v in collectors.COLLECTORS.items():
            r = last.get(k)
            print(f"{k:14} {v}\n{'':14} son: {r['baslangic'] + ' ' + r['durum'] + ' ' + str(r['satir']) + ' satır' if r else 'hiç çalışmadı'}")
        return
    names = a.adlar or list(collectors.COLLECTORS)
    failed = []
    for name in names:
        t = time.time()
        print(f"[{name}] {collectors.COLLECTORS.get(name, '')}", flush=True)
        kw = {"limit": a.kap_limit, "max_age_days": a.kap_yas} if name == "kap_fon" else {}
        try:
            res = collectors.run(name, log=lambda m: print(m, flush=True), **kw)
            print(f"[{name}] tamam ({time.time() - t:.0f} sn): {res}", flush=True)
        except Exception as exc:
            failed.append(name)
            print(f"[{name}] HATA: {type(exc).__name__}: {exc}", flush=True)
    if failed:
        print("Hata veren toplayıcılar:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
