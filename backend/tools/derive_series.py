"""gecmis/ altındaki kaynak serilerden türetilmiş seriler (yeni istek atmaz). Her türetilmiş serinin formülü ve girdileri
gecmis/meta.json'a yazılır; bunlar ölçüm değil HESAPLAMADIR.

  eurobond_hazine_fiyat        Hazine eurobond stokunun ortalama fiyatı = piyasa değeri / yazılı değer × 100 (haftalık).
                               Kompozisyon değişiklikleri (yeni ihraç, itfa) oranı etkiler; getiri değil fiyat vekilidir.
  hedge_prim_usd_1a / _eur_1a  Faiz paritesiyle yıllık vadeli kur primi ≈ (1 + r_TL) / (1 + r_DÖVİZ) − 1;
                               r: TCMB 1 aya kadar tasarruf mevduatı faizi (haftalık). Gerçek forward kotasyonu değildir.
  forward_usdtry_1a            1 aylık vadeli USD/TRY tahmini = spot × (1 + r_TL·30/365) / (1 + r_USD·30/365).

Kullanım: backend/.venv/Scripts/python backend/tools/derive_series.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import SNAPSHOT_DIR, now_iso  # noqa: E402

G = SNAPSHOT_DIR / "gecmis"


def load(sid: str) -> pd.Series:
    d = pd.read_csv(G / f"{sid}.csv", parse_dates=["tarih"])
    return d.set_index("tarih")["deger"].sort_index()


def main():
    meta = json.loads((G / "meta.json").read_text(encoding="utf-8"))
    out = {}

    p, y = load("eurobond_hazine_piyasa_degeri"), load("eurobond_hazine_yazili_deger")
    out["eurobond_hazine_fiyat"] = ((p / y * 100).dropna(), {
        "ad": "Hazine eurobond stokunun ortalama fiyatı (piyasa değeri / yazılı değer × 100)", "birim": "fiyat (nominal=100)",
        "siklik": "haftalık", "girdiler": ["eurobond_hazine_piyasa_degeri", "eurobond_hazine_yazili_deger"],
        "not": "Hesaplama; getiri değil fiyat vekili. Yeni ihraç/itfa kompozisyonu oranı etkileyebilir; kuponlar dahil değil."})

    tl = load("mevduat_tl_1a")
    for cur in ("usd", "eur"):
        fx = load(f"mevduat_{cur}_1a")
        j = pd.concat([tl, fx], axis=1, keys=["tl", "fx"], sort=True).dropna()
        out[f"hedge_prim_{cur}_1a"] = (((1 + j["tl"]) / (1 + j["fx"]) - 1), {
            "ad": f"Faiz paritesiyle yıllık vadeli kur primi, TL/{cur.upper()} (1 aya kadar mevduat faizlerinden)", "birim": "oran",
            "siklik": "haftalık", "girdiler": ["mevduat_tl_1a", f"mevduat_{cur}_1a"],
            "not": "Hesaplama (kapalı faiz paritesi); bankanın gerçek forward kotasyonu değildir. Döviz geliri olan müşteri için "
                   "kur riskini sabitlemenin yaklaşık maliyeti."})

    usd = load("mevduat_usd_1a")
    spot = load("usdtry")
    j = pd.concat([tl, usd], axis=1, keys=["tl", "usd"], sort=True).dropna()
    s = spot.reindex(spot.index.union(j.index)).ffill().reindex(j.index)
    fwd = (s * (1 + j["tl"] * 30 / 365) / (1 + j["usd"] * 30 / 365)).dropna()
    out["forward_usdtry_1a"] = (fwd, {
        "ad": "1 aylık vadeli USD/TRY tahmini (faiz paritesi)", "birim": "TRY", "siklik": "haftalık",
        "girdiler": ["usdtry", "mevduat_tl_1a", "mevduat_usd_1a"],
        "not": "Hesaplama; spot = o tarihteki (veya öncesindeki son) TCMB döviz satış kuru."})

    for sid, (s, info) in out.items():
        df = pd.DataFrame({"tarih": s.index.date, "deger": s.values})
        df.to_csv(G / f"{sid}.csv", index=False)
        meta[sid] = {**info, "kaynak": "Hesaplama (tools/derive_series.py)", "alinma": now_iso(), "satir": int(len(df)),
                     "ilk": str(df["tarih"].min()), "son": str(df["tarih"].max()), "status": "ok", "turetilmis": True}
        print(f"{sid:24} {len(df):>4} satır  {df['tarih'].min()} → {df['tarih'].max()}  son={df['deger'].iloc[-1]:.4f}")
    (G / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
