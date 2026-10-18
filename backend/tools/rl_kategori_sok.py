"""Tek kategori şoku: bir kategorinin geçmiş 3 aylık getirisi ±d puan değişince (bugünün göstergeleri, personalar, nakitten)
modelin beklenen dağılımında O kategorinin payı nasıl değişiyor. Öncül (momentum / getirisi çöken varlığa yığılmama):
getiri düşünce pay artmamalı. Eğitimde düzenlenmeyen, bağımsız bir kontrol.

Kullanım: backend/.venv/Scripts/python backend/tools/rl_kategori_sok.py rl_ppo_v11_tum100m rl_ppo_v13_teyit [--d 0.15]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import rl_category as rc  # noqa: E402
from app.pipeline import load_persona  # noqa: E402
from app.profile import interpret  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modeller", nargs="+")
    ap.add_argument("--d", type=float, default=0.15)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    for m in args.modeller:
        rc.MODEL_DIR = ROOT / "models" / m
        rc._MODEL_CACHE.clear()
        net, meta = rc._load_model()
        ds = rc.build_dataset(meta.get("gosterge_seti", "v1"), meta.get("veri_baslangic"), meta.get("test_baslangic"))
        names, mu, sd = ds["feature_names"], np.array(meta["gosterge_ort"]), np.array(meta["gosterge_std"])
        full = np.where(np.isfinite(ds["features_raw"]).all(1))[0]
        raw = ds["features_raw"][full[-1]]
        A, cats = np.array(meta["aksiyonlar"]), meta["kategoriler"]
        nz = rc.state_dims(meta) + len(cats)
        wrong = tot = 0
        print(f"== {m} (kategori payı değişimi, puan: getiri −{args.d*100:.0f} / +{args.d*100:.0f})")
        for pid in ["P01", "P02", "P03", "P04", "P05"]:
            p = load_persona(pid)
            v, _ = interpret(p, {})
            prof = rc.profile_from_persona(p, v)
            mask = rc.profile_mask(prof, A, meta["kategori_rd"], cats)[0]
            pv = rc.profile_vector(prof, meta.get("profil_surumu", 1))

            def exp(r):
                x = np.concatenate([rc.model_input((r - mu) / sd, meta), pv, np.zeros(nz)])[None]
                return (net.probs(x, mask[None]) @ A)[0]
            w0 = exp(raw)
            cells = []
            for j, c in enumerate(cats):
                k = [i for i, n in enumerate(names) if n == f"{c} 3 ay getirisi"]
                if not k or not (mask & (A[:, j] > 0)).any():
                    continue
                out = []
                for s in (-1, 1):
                    r = raw.copy()
                    r[k[0]] += s * args.d
                    out.append((exp(r)[j] - w0[j]) * 100)
                bad = out[0] > 2 or out[1] < -2
                wrong += bad
                tot += 1
                cells.append(f"{c} {out[0]:+.0f}/{out[1]:+.0f}{' ✗' if bad else ''}")
            print(f"  {pid}: " + "; ".join(cells))
        print(f"  ters yönlü (±2 puan ötesi): {wrong}/{tot}")


if __name__ == "__main__":
    main()
