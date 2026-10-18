"""v14 eğitim bütçesi seçimi — YALNIZCA doğrulama (HPO ile aynı ölçüt: getiri tohum skoru + 0,1·what-if skoru).
Test alanları burada okunmaz."""
import glob
import json
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
M = "C:/Users/oof/Documents/inghackathon/backend/models"


def seed_score(r):
    d = np.array(r["persona_dogrulama_farki"])
    return float(d.mean() + 3 * np.minimum(d, 0).sum() + 0.5 * min(r.get("dogrulama_yarilar") or [0.0]))


def wi(w):
    return float(w["dogru_tepki"] - 3 * w["yanlis_yon"] - 0.5 * max(0.0, w["en_kotu_senaryo_yanlis"] - 0.2))


groups = {"6M": glob.glob(f"{M}/pilot_v14/tut10_s*/sonuc.json"), "12M": glob.glob(f"{M}/nnppo_v14_butce/b12m_s*/sonuc.json"),
          "25M": glob.glob(f"{M}/nnppo_v14_butce/b25m_s*/sonuc.json"), "100M": [f"{M}/nnppo_v14_100m/sonuc.json"]}
for g, files in groups.items():
    R = [json.load(open(f)) for f in files]
    if not R:
        continue
    tot = [seed_score(r) + 0.1 * wi(r["whatif_dogrulama"]) for r in R]
    pers = np.mean([r["persona_dogrulama_farki"] for r in R], 0)
    print(f"{g:5s} tohum {len(R)}  skor {np.mean(tot) - 0.5 * np.std(tot):+.4f}  persona doğrulama farkı {np.round(pers * 100, 1)}  "
          f"what-if doğru/yanlış {np.mean([r['whatif_dogrulama']['dogru_tepki'] for r in R]):.2f}/"
          f"{np.mean([r['whatif_dogrulama']['yanlis_yon'] for r in R]):.2f}")
