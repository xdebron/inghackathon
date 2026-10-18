"""RL modellerinin piyasa duyarlılığı: her persona için geçmişteki tüm karar tarihlerinin göstergeleriyle (nakitten, t=0)
modelin kararı. Ölçüler: farklı karar sayısı, en sık kararın payı, kırpılan gösterge oranı ve rejime göre ortalama dağılım
(2022–23: negatif reel faiz; 2025–26: pozitif reel faiz). Duyarlı bir politika rejim değişince kararını değiştirmeli.

Kullanım:  backend/.venv/Scripts/python backend/tools/rl_duyarlilik.py rl_ppo_v9 rl_ppo_v10 [--adim 5]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import rl_category as rc  # noqa: E402
from app.pipeline import load_persona  # noqa: E402
from app.profile import interpret  # noqa: E402

REGIMES = {"2022-23 (negatif reel faiz)": ("2022-01-01", "2024-01-01"), "2025-26 (pozitif reel faiz)": ("2025-01-01", "2027-01-01")}


def analyse(model_dir: str, step: int) -> dict:
    rc.MODEL_DIR = ROOT / "models" / model_dir
    rc._MODEL_CACHE.clear()
    net, meta = rc._load_model()
    ds = rc.build_dataset(meta.get("gosterge_seti", "v1"))
    mu, sd, clip = np.array(meta["gosterge_ort"]), np.array(meta["gosterge_std"]), meta.get("ozellik_kirp")
    raw = ds["features_raw"]
    ok = np.where(np.isfinite(raw).all(1))[0][::step]
    zr = (raw[ok] - mu) / sd
    z = rc.model_input(zr, meta)
    dates = ds["dates"][ok]
    A, cats, pv = np.array(meta["aksiyonlar"]), meta["kategoriler"], meta.get("profil_surumu", 1)
    out = {"model": model_dir, "tarih": int(len(ok)), "kirpilan_oran": float((np.abs(zr) > clip).mean()) if clip else 0.0, "persona": {}}
    for pid in ["P01", "P02", "P03", "P04", "P05"]:
        p = load_persona(pid)
        v, _ = interpret(p, {})
        prof = rc.profile_from_persona(p, v)
        mask = rc.profile_mask(prof, A, meta["kategori_rd"], cats)[0]
        X = np.column_stack([z, np.repeat(rc.profile_vector(prof, pv)[None], len(ok), 0),
                             np.zeros((len(ok), rc.state_dims(meta))), np.zeros((len(ok), len(cats)))])
        a = np.argmax(np.where(mask, net.forward(X), -np.inf), axis=1)
        cnt = collections.Counter(a.tolist())
        top, n_top = cnt.most_common(1)[0]
        reg = {}
        for name, (lo, hi) in REGIMES.items():
            sel = (dates >= np.datetime64(lo)) & (dates < np.datetime64(hi))
            if sel.any():
                w = A[a[sel]].mean(0)
                reg[name] = {cats[j]: round(float(w[j]), 2) for j in range(len(cats)) if w[j] >= 0.05}
        out["persona"][pid] = {"farkli_karar": len(cnt), "en_sik_pay": round(n_top / len(a), 3),
                               "en_sik": {cats[j]: float(A[top][j]) for j in range(len(cats)) if A[top][j] > 0}, "rejim": reg}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modeller", nargs="+")
    ap.add_argument("--adim", type=int, default=5, help="her kaçıncı tarih")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    for m in args.modeller:
        r = analyse(m, args.adim)
        print(f"== {r['model']}: {r['tarih']} tarih, kırpılan gösterge %{r['kirpilan_oran']*100:.0f}")
        for pid, x in r["persona"].items():
            print(f"   {pid}: {x['farkli_karar']} farklı karar, en sık %{x['en_sik_pay']*100:.0f} {x['en_sik']}")
            for name, w in x["rejim"].items():
                print(f"        {name}: {w}")
        json.dump(r, open(ROOT / "models" / f"duyarlilik_{m}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
