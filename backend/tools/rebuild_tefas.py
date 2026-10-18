"""TEFAS fiyat dosyasını sıfırdan ve izlenebilir şekilde kurar (15.10.2026'daki veri kaybından sonra).

Sıra:
  1. Mevcut tefas_fiyatlar.csv ve tefas_meta.json -> snapshots/_yedek_tefas/*_onarim_oncesi_* (silinmez).
  2. Taban: Gamze Akkurt'un 46 TEFAS 'Fon Verileri' elle dışa aktarımı (import_gamze.tefas_prices; 288 fon).
  3. tefascrawl/out tarihsel dosyası (2021-10-15 -> 2026-10-15, 2068 fon) kilitli import_export ile;
     aynı fon/gün için farklı fiyat 'conflicts' olarak meta'ya yazılır, yeni dosyanın değeri kullanılır.
  4. tefascrawl/out getiri dosyası (Fon Getirileri, 'Fonun Risk Değeri' dahil) -> tefas_karsilastirma.csv.

Kullanım: backend/.venv/Scripts/python backend/tools/rebuild_tefas.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tools"))
from app.config import now_iso  # noqa: E402
from app.sources import tefas_import as ti  # noqa: E402
import import_gamze  # noqa: E402

CRAWL = ROOT / "tefascrawl" / "out"
HIST = CRAWL / "tefas_tarihsel_2021-10-15_2026-10-15.csv"
RET = CRAWL / "tefas_getiriler_2026-10-15.csv"
CRAWL_METHOD = ("tefascrawl (Playwright tarayıcı otomasyonu; kullanıcının açık talimatıyla yazıldı, bkz. tefascrawl/README.md). "
                "TEFAS Tarihsel Veriler, fon türü YAT.")


def main():
    ti.BACKUP_DIR.mkdir(exist_ok=True)
    stamp = now_iso().replace(":", "").replace("+", "_")
    for f in (ti.PRICES, ti.META):
        if f.exists():
            shutil.copy2(f, ti.BACKUP_DIR / f"{f.stem}_onarim_oncesi_{stamp}{f.suffix}")
    ti.PRICES.unlink(missing_ok=True)
    old_meta = json.loads(ti.META.read_text(encoding="utf-8")) if ti.META.exists() else {}
    ti.META.write_text(json.dumps({"imports": [], "conflicts": [], "onarim": {
        "zaman": now_iso(), "neden": "15.10.2026 eşzamanlı içe aktarımlarda veri kaybı; dosya kaynaklardan yeniden kuruldu.",
        "onceki_meta_yedegi": f"_yedek_tefas/tefas_meta_onarim_oncesi_{stamp}.json",
        "onceki_kayit_sayisi": len(old_meta.get("imports", []))}}, ensure_ascii=False, indent=2), encoding="utf-8")

    log: list[str] = []
    import_gamze.tefas_prices(import_gamze.DEFAULT_SRC, log)
    print(log[-1], flush=True)

    r = ti.import_export(HIST.read_bytes(), HIST.name, yontem=CRAWL_METHOD)
    print(f"{HIST.name}: {r['satir']} satır, {r['fon_sayisi']} fon, {r['tarih_araligi']}; "
          f"çelişki {len(r['celiskiler'])}; toplam {r['toplam_sonrasi']}", flush=True)

    c = ti.import_comparison(RET.read_bytes(), RET.name, "2026-10-15")
    meta = json.loads(ti.META.read_text(encoding="utf-8"))
    meta["karsilastirma_imports"][-1]["yontem"] = CRAWL_METHOD
    ti.META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{RET.name}: {c['fon_sayisi']} fon (getiri + resmi RD)", flush=True)


if __name__ == "__main__":
    main()
