"""Dosya tabanlı eski snapshot'ları veri tabanına bir kez aktarır (kaynak, URL, veri tarihi ve alınma zamanı korunur).

Çalıştırma (backend klasöründe):  .venv/Scripts/python tools/db_aktar.py
Sonrasında uygulama yalnızca veri tabanından okur; veriyi toplayıcılar günceller (tools/veri_topla.py).
Her tablo bir 'aktarim' çekimi altında yazılır; kayıtların kendi kaynak alanları (kim, nereden, ne zaman) değişmez.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import CACHE_DIR, SNAPSHOT_DIR  # noqa: E402
from app.db import models as M, store  # noqa: E402

G = SNAPSHOT_DIR / "gecmis"
GAMZE = ROOT.parent / "gamze_work" / "AGENTIC AI HACKATHON-ING" / "data" / "processed"


def J(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def prov(rec: dict) -> dict:
    notes = [x for x in (rec.get("not"), f"Toplayan: {rec['toplayan']}" if rec.get("toplayan") else None) if x]
    return {"kaynak": rec.get("source") or "bilinmiyor", "url": rec.get("url"), "as_of": rec.get("as_of"),
            "fetched_at": rec.get("fetched_at"), "notlar": " | ".join(notes) or None}


def manual(log):
    raw = J(SNAPSHOT_DIR / "manuel_veriler.json") or {}
    with store.Run("aktarim:gostergeler", "aktarim") as run:
        store.replace_rows(M.Gosterge, [{"kod": k, "deger": v.get("deger"), "birim": v.get("birim"), **prov(v)}
                                        for k, v in raw.get("gostergeler", {}).items()], run=run)
    rows = [{"banka": m["banka"], "tur": m.get("tur", "mevduat"), "para_birimi": "TRY", "vade_gun": int(m["vade_gun"]),
             "oran": m.get("oran"), "min_tutar": float(m.get("min_tutar") or 0), "stopaj_orani": None, **prov(m)}
            for m in raw.get("mevduat", [])]
    fx = J(GAMZE / "ing_deposit_rates.json")
    if fx:
        for r in fx["rows"]:
            if r["currency"] in ("USD", "EUR") and r.get("status") == "ok":
                rows.append({"banka": "ING", "tur": "mevduat", "para_birimi": r["currency"], "vade_gun": int(r["day_count"]),
                             "oran": r["annual_gross_rate_pct"] / 100, "min_tutar": float(r["capital"]),
                             "stopaj_orani": None if r.get("withholding_rate_pct") is None else r["withholding_rate_pct"] / 100,
                             "kaynak": "ING mevduat hesaplayıcısı", "url": r["source"], "as_of": r["fetched_at"][:10],
                             "fetched_at": r["fetched_at"], "notlar": "Toplayan: Gamze Akkurt"})
    with store.Run("aktarim:mevduat", "aktarim") as run:
        store.replace_rows(M.Mevduat, rows, run=run)
    with store.Run("aktarim:stopaj", "aktarim") as run:
        store.replace_rows(M.Stopaj, [{"kategori": s["kategori"], "oran": s.get("oran"), **prov(s)} for s in raw.get("stopaj", [])], run=run)
    with store.Run("aktarim:benchmark", "aktarim") as run:
        store.replace_rows(M.Benchmark, [{"ad": b["ad"], "kategoriler": b.get("kategoriler", []), "getiri_1y": b.get("getiri_1y"), **prov(b)}
                                         for b in raw.get("benchmarklar", [])], run=run)
    log.append(f"göstergeler {len(raw.get('gostergeler', {}))}, mevduat {len(rows)}, stopaj {len(raw.get('stopaj', []))}, "
               f"benchmark {len(raw.get('benchmarklar', []))}")


def stopaj_history(log):
    h = J(SNAPSHOT_DIR / "stopaj_tarihce.json")
    if not h:
        return
    rows = []
    for cat, rates in h["kategoriler"].items():
        for i, (d, oran) in enumerate(zip(h["donemler"], rates)):
            rows.append({"kategori": cat, "donem": d["ad"], "baslangic": d.get("baslangic"), "bitis": d.get("bitis"),
                         "oran": oran, "sira": i, **prov(h)})
    with store.Run("aktarim:stopaj_donem", "aktarim") as run:
        store.replace_rows(M.StopajDonem, rows, run=run)
    log.append(f"stopaj dönemleri {len(rows)}")


def kap(log):
    d = J(SNAPSHOT_DIR / "kap_fon_bilgileri.json")
    if not d:
        return
    cols = {c.name for c in M.FonBilgi.__table__.columns}
    rows = []
    for kod, rec in d["fonlar"].items():
        row = {k: v for k, v in rec.items() if k in cols and k not in ("kaynak", "url", "as_of", "fetched_at", "notlar")}
        rows.append({"kod": kod, **row, **{k: v for k, v in prov(rec).items() if k != "notlar"}})
    with store.Run("aktarim:kap_fon", "aktarim") as run:
        store.replace_rows(M.FonBilgi, rows, run=run)
    log.append(f"KAP fon bilgisi {len(rows)}")


def platform(log):
    d = J(SNAPSHOT_DIR / "ing_fon_listesi.json")
    if not d:
        return
    with store.Run("aktarim:ing_fon", "aktarim") as run:
        store.replace_rows(M.PlatformFon, [{"platform": "ING", "kod": k, **prov(d)} for k in d["kodlar"]], run=run)
    log.append(f"ING fon listesi {len(d['kodlar'])}")


def series(log):
    meta = J(G / "meta.json") or {}
    n = 0
    with store.Run("aktarim:seriler", "aktarim") as run:
        for f in sorted(G.glob("*.csv")):
            df = pd.read_csv(f)
            if "tarih" in df.columns and len(df.columns) >= 2 and df.drop(columns="tarih").apply(pd.api.types.is_numeric_dtype).all():
                store.save_series(f.stem, "gecmis", df, meta.get(f.stem, {}), run)
                n += 1
            else:
                store.save_raw_table(f.stem, df, run)
        for grup in ("bist", "byf"):
            d = G / grup
            if not d.exists():
                continue
            gm = J(d / "meta.json") or {}
            for f in sorted(d.glob("*.csv")):
                store.save_series(f"{grup}/{f.stem}", grup, pd.read_csv(f), (gm.get("seriler") or {}).get(f.stem, {}), run)
                n += 1
            if gm:
                store.save_document(f"{grup}_meta", gm, run)
            for jf in d.glob("*.json"):
                if jf.name != "meta.json":
                    store.save_document(f"{grup}:{jf.stem}", J(jf), run)
        kd = G / "kap"
        if kd.exists():
            for f in sorted(kd.glob("*.csv")):
                store.save_raw_table(f"kap_{f.stem}", pd.read_csv(f), run)
            if (kd / "meta.json").exists():
                store.save_document("kap_gecmis_meta", J(kd / "meta.json"), run)
    log.append(f"seriler {n}")


def tefas(log):
    with store.Run("aktarim:tefas_export", "aktarim") as run:
        n1 = store.save_fund_prices("tefas_export", pd.read_csv(SNAPSHOT_DIR / "tefas_fiyatlar.csv"), run)
        if (SNAPSHOT_DIR / "tefas_meta.json").exists():
            store.save_document("tefas_meta", J(SNAPSHOT_DIR / "tefas_meta.json"), run)
        if (SNAPSHOT_DIR / "tefas_kalite_raporu.json").exists():
            store.save_document("tefas_kalite_raporu", J(SNAPSHOT_DIR / "tefas_kalite_raporu.json"), run)
    n2 = 0
    if (SNAPSHOT_DIR / "tefas_api_fiyatlar.csv").exists():
        with store.Run("aktarim:tefas_api", "aktarim") as run:
            n2 = store.save_fund_prices("tefas_api", pd.read_csv(SNAPSHOT_DIR / "tefas_api_fiyatlar.csv"), run)
            store.save_document("tefas_api_meta", J(SNAPSHOT_DIR / "tefas_api_meta.json"), run)
    if (SNAPSHOT_DIR / "tefas_karsilastirma.csv").exists():
        df = pd.read_csv(SNAPSHOT_DIR / "tefas_karsilastirma.csv")
        df = df.astype(object).where(pd.notna(df), None)
        with store.Run("aktarim:tefas_getiri", "aktarim") as run:
            store.replace_rows(M.FonGetiri, df.to_dict("records"), run=run)
    log.append(f"TEFAS fiyat {n1} (dışa aktarım), {n2} (API)")


def caches(log):
    """Canlı kaynakların (TCMB, EVDS) son başarılı çekimleri: gösterge satırı (ad alanlı) ya da tarihçe belgesi."""
    n = 0
    with store.Run("aktarim:canli_son_cekim", "aktarim") as run:
        for f in sorted(CACHE_DIR.glob("*.json")):
            d = J(f)
            if f.stem == "tcmb_kurlar":
                for p in d:
                    if p.get("status") == "ok":
                        store.put_indicator(f"tcmb:{p['key']}", p["value"], p.get("unit"), p["source"], p.get("url"),
                                            p.get("as_of"), p.get("fetched_at"), p.get("note"), run)
                        n += 1
            elif f.stem.startswith("evds_hist_"):
                store.save_document(f"onbellek:{f.stem}", d, run)
                n += 1
            elif f.stem.startswith("evds_") and d.get("status", "ok") == "ok":
                store.put_indicator(f"evds:{f.stem[5:]}", d.get("value"), d.get("unit"), d["source"], d.get("url"),
                                    d.get("as_of"), d.get("fetched_at"), d.get("note"), run)
                n += 1
    log.append(f"canlı kaynak son çekimleri {n}")


def main():
    log: list[str] = []
    for step in (manual, stopaj_history, kap, platform, series, tefas, caches):
        step(log)
        print(log[-1] if log else step.__name__, flush=True)
    print(json.dumps(store.table_counts(), ensure_ascii=False))


if __name__ == "__main__":
    main()
