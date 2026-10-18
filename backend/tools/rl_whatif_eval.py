"""PPO modellerinin what-if makullük ölçüsü (tools/gbrl/whatif.py senaryoları).

Durumlar: son 60 tam göstergeli günden her 5.si (UI'daki "bugün" bağlamı) × (5 persona + ortamdaki görülmemiş profiller),
nakitten başlangıç. Model girdisi eğitimdeki dönüşümün aynısı (meta: ozellik_kirp, kirpma_turu).

Kullanım:  backend/.venv/Scripts/python backend/tools/rl_whatif_eval.py rl_ppo_v11_tum100m --ortam models/rl_ortam_v10.npz
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "gbrl"))

from app import rl_category as rc  # noqa: E402
import whatif  # noqa: E402


def run(model_dir: str, ortam: str, n_days: int = 60, step: int = 5, n_hold: int = 60) -> dict:
    rc.MODEL_DIR = ROOT / "models" / model_dir
    rc._MODEL_CACHE.clear()
    net, meta = rc._load_model()
    ds = rc.build_dataset(meta.get("gosterge_seti", "v1"), meta.get("veri_baslangic"), meta.get("test_baslangic"))
    assert ds["feature_names"] == meta["gosterge_adlari"]
    names = ds["feature_names"]
    mu, sd = np.array(meta["gosterge_ort"]), np.array(meta["gosterge_std"])
    full = np.where(np.isfinite(ds["features_raw"]).all(1))[0]
    days = full[-n_days:][::step]
    env = np.load(ROOT / ortam)
    A, cats = np.array(meta["aksiyonlar"]), meta["kategoriler"]
    pids = list(env["persona_ids"]) + list(env["hold_ids"][:n_hold])
    raw = np.repeat(ds["features_raw"][days], len(pids), 0)
    prof_rows = np.tile(np.array(pids), len(days))
    P = env["pvec"][prof_rows]
    Mk = env["M"][prof_rows]
    nz = rc.state_dims(meta) + len(cats)
    mode, c = meta.get("kirpma_turu", "sert"), meta.get("ozellik_kirp") or 1e9

    def expected(zin):
        X = np.column_stack([zin, P, np.zeros((len(zin), nz))])
        return net.probs(X, Mk) @ A

    allowed = whatif.allowed_groups_from_masks(prof_rows, env["M"], A, cats)
    r = whatif.evaluate(expected, names, raw, mu, sd, mode, c, cats, allowed, rows=prof_rows, M=env["M"], A=A)
    pr = net.probs(np.column_stack([whatif.squash((raw - mu) / sd, mode, c), P, np.zeros((len(raw), nz))]), Mk)
    r["ust_olasilik"] = round(float(pr.max(1).mean()), 3)
    r["model"] = model_dir
    r["durum"] = int(len(raw))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modeller", nargs="+")
    ap.add_argument("--ortam", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    for m in args.modeller:
        r = run(m, args.ortam)
        print(f"== {m}: {r['durum']} durum, yanlış yön %{r['yanlis_yon']*100:.0f}, doğru tepki %{r['dogru_tepki']*100:.0f}, "
              f"en kötü senaryo yanlış %{r['en_kotu_senaryo_yanlis']*100:.0f}, üst olasılık {r['ust_olasilik']}, makul={r['makul']}")
        if "kategori" in r:
            print(f"   makro: yanlış %{r['makro']['yanlis_yon']*100:.0f} doğru %{r['makro']['dogru_tepki']*100:.0f} | kategori şokları: "
                  f"yanlış %{r['kategori']['yanlis_yon']*100:.0f} doğru %{r['kategori']['dogru_tepki']*100:.0f} en kötü %{r['kategori']['en_kotu']*100:.0f}")
        for k, v in r["senaryolar"].items():
            print(f"   {k:17s} yanlış %{v['yanlis_yon']*100:3.0f}  doğru %{v['dogru_tepki']*100:3.0f}  ort Δ {v['ort_degisim']*100:+.1f} puan  "
                  f"girdi görünür %{v['girdi_gorunur']*100:.0f}  (n={v['n']})")
        json.dump(r, open(ROOT / "models" / f"whatif_{m}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
