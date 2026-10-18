"""Veri tabanındaki kaynak serilerden türetilmiş seriler ve benchmark (yeni istek atmaz). Bunlar ölçüm değil HESAPLAMADIR;
formül ve girdiler seri metasına yazılır ve arayüzde 'varsayım' etiketiyle görünür.

  eurobond_hazine_fiyat        piyasa değeri / yazılı değer × 100 (haftalık; getiri değil fiyat vekili)
  hedge_prim_usd_1a / _eur_1a  (1 + r_TL) / (1 + r_DÖVİZ) − 1  (1 aya kadar mevduat faizleri; forward kotasyonu değil)
  forward_usdtry_1a            spot × (1 + r_TL·30/365) / (1 + r_USD·30/365)
  benchmark 'BIST 100'         BIST 100 getiri endeksinin (EVDS, temettü dahil) son 1 yıllık değişimi
"""
from __future__ import annotations

import pandas as pd

from ..db import models as M, store
from .base import load_series, write_series


def run(log=print) -> dict:
    out = {}
    with store.Run("turetilmis") as run_:
        p, y = load_series("eurobond_hazine_piyasa_degeri"), load_series("eurobond_hazine_yazili_deger")
        out["eurobond_hazine_fiyat"] = ((p / y * 100).dropna(), {
            "ad": "Hazine eurobond stokunun ortalama fiyatı (piyasa değeri / yazılı değer × 100)", "birim": "fiyat (nominal=100)",
            "siklik": "haftalık", "girdiler": ["eurobond_hazine_piyasa_degeri", "eurobond_hazine_yazili_deger"],
            "not": "Hesaplama; getiri değil fiyat vekili. Yeni ihraç/itfa kompozisyonu oranı etkileyebilir; kuponlar dahil değil."})
        tl = load_series("mevduat_tl_1a")
        for cur in ("usd", "eur"):
            fx = load_series(f"mevduat_{cur}_1a")
            j = pd.concat([tl, fx], axis=1, keys=["tl", "fx"], sort=True).dropna()
            out[f"hedge_prim_{cur}_1a"] = (((1 + j["tl"]) / (1 + j["fx"]) - 1), {
                "ad": f"Faiz paritesiyle yıllık vadeli kur primi, TL/{cur.upper()} (1 aya kadar mevduat faizlerinden)", "birim": "oran",
                "siklik": "haftalık", "girdiler": ["mevduat_tl_1a", f"mevduat_{cur}_1a"],
                "not": "Hesaplama (kapalı faiz paritesi); bankanın gerçek forward kotasyonu değildir."})
        usd, spot = load_series("mevduat_usd_1a"), load_series("usdtry")
        j = pd.concat([tl, usd], axis=1, keys=["tl", "usd"], sort=True).dropna()
        s = spot.reindex(spot.index.union(j.index)).ffill().reindex(j.index)
        out["forward_usdtry_1a"] = ((s * (1 + j["tl"] * 30 / 365) / (1 + j["usd"] * 30 / 365)).dropna(), {
            "ad": "1 aylık vadeli USD/TRY tahmini (faiz paritesi)", "birim": "TRY", "siklik": "haftalık",
            "girdiler": ["usdtry", "mevduat_tl_1a", "mevduat_usd_1a"],
            "not": "Hesaplama; spot = o tarihteki (veya öncesindeki son) TCMB döviz satış kuru."})
        for sid, (ser, info) in out.items():
            df = pd.DataFrame({"tarih": [str(d.date()) for d in ser.index], "deger": ser.values})
            m = write_series(sid, df, {**info, "kaynak": "Hesaplama (app/collectors/turetilmis.py)", "alinma": store.now_iso(),
                                       "turetilmis": True}, run_)
            log(f"  {sid:24} {m['satir']:>4} satır  {m['ilk']} → {m['son']}")

        # Benchmark: BIST 100 getiri endeksi (temettü dahil), son 1 yıl
        b = load_series("bist100_getiri")
        end = b.index[-1]
        start = b[b.index <= end - pd.Timedelta(days=365)]
        meta = store.series_meta("gecmis").get("bist100_getiri", {})
        if len(start):
            store.replace_rows(M.Benchmark, [{
                "ad": "BIST 100", "kategoriler": ["hisse"], "getiri_1y": float(b.iloc[-1] / start.iloc[-1] - 1),
                "kaynak": f"{meta.get('kaynak', 'TCMB EVDS (TP.MK.G.BILESIK)')}: BIST 100 getiri endeksi (XU100-CFNNTLTL, temettü dahil)",
                "url": meta.get("url"), "as_of": str(end.date()), "fetched_at": meta.get("alinma"),
                "notlar": f"Hesaplama: {start.index[-1].date()} → {end.date()} endeks değişimi"}], M.Benchmark.ad == "BIST 100", run_)
            log(f"  benchmark BIST 100 1Y %{(b.iloc[-1] / start.iloc[-1] - 1) * 100:.1f}")
    return {"seri": len(out)}
