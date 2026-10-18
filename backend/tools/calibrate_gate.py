"""Güvenli iyileştirme kapısının eşiğini doğrulama döneminde seçer ve <model>/kapi.json yazar.

Kural (çıkarımda): modelin seçtiği dağılımın Q değeri, taban kuralın (%50 para piyasası + %50 mevduat/borçlanma)
Q değerini `delta`dan fazla aşmıyorsa taban kural uygulanır. delta = ∞ her zaman taban kural, delta = −∞ her zaman model.

Seçim: görülmemiş (holdout) profiller × doğrulama dönemi senaryoları; adayların her biri aynı senaryolarla değerlendirilir,
ortalama ödülü en yüksek eşik seçilir (eşitlikte daha temkinli, büyük eşik). Test dönemi seçimde kullanılmaz.

Kullanım:  backend/.venv/Scripts/python backend/tools/calibrate_gate.py [--model-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import rl_category as rc  # noqa: E402
from app.config import now_iso  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(rc.MODEL_DIR))
    ap.add_argument("--senaryo", type=int, default=8)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    rc.MODEL_DIR = Path(args.model_dir)
    rc._MODEL_CACHE.clear()
    net, meta = rc._load_model()
    if net is None:
        sys.exit(f"Model yok: {args.model_dir}")
    ds = rc.build_dataset(meta.get("gosterge_seti", "v1"), meta.get("veri_baslangic"), meta.get("test_baslangic"))
    if ds["feature_names"] != meta["gosterge_adlari"]:
        sys.exit("Modelin gösterge listesi veriyle uyuşmuyor.")
    if len(ds["val"]) == 0:
        sys.exit("Doğrulama havuzu boş.")
    cats, A = meta["kategoriler"], np.array(meta["aksiyonlar"])
    pol = rc.LoadedPolicy(net, meta, ds)
    profiles, _ = rc.enumerate_profiles()
    masks = np.array([rc.profile_mask(p, A, meta["kategori_rd"], cats)[0] for p in profiles])
    sim = rc.Sim(ds, A, profiles, masks, nominal=meta.get("anapara_tabani", "reel") == "nominal")   # modelin kendi ödülü
    c = rc.cfg()
    order = np.random.default_rng(c["profil_tohum"] + 1).permutation(len(profiles))
    hold = order[:int(round(len(profiles) * c["profil_holdout_orani"]))]

    # Eşik adayları: doğrulama başlangıç durumlarında (nakit, t=0) modelin taban kurala göre Q üstünlüğünün dağılımı
    rng = np.random.default_rng(0)
    pi, di = rng.choice(hold, 4000), rng.choice(ds["val"], 4000)
    X = pol.x(sim.pvec[pi], np.zeros(len(pi), int), np.ones((len(pi), 2)), di, np.zeros((len(pi), len(cats))))
    q = pol.q(X, masks[pi])
    base = np.array([rc.baseline_action(A, masks[p], cats) for p in range(len(profiles))])
    adv = q[np.arange(len(pi)), q.argmax(1)] - q[np.arange(len(pi)), base[pi]]
    pos = adv[adv > 1e-9]
    grid = [0.0] + (sorted(set(np.round(np.percentile(pos, [10, 25, 40, 50, 60, 75, 90]), 4).tolist())) if len(pos) else [])

    def ev(policy, delta=None):
        return rc.evaluate(pol, sim, hold, ds["val"], args.senaryo, np.random.default_rng(21), policy, delta)

    dqn, taban = ev("dqn"), ev("pp_agirlikli")
    rows = []
    for d in grid:
        e = ev("kapili", d)
        rows.append({"delta": d, "odul": round(e["ortalama_odul"], 4), "reel": round(e["ortalama_reel_kazanc"], 4),
                     "anapara_alti": round(e["anapara_alti_bitis_olasiligi"], 4), "model_karar_orani": round(e["model_karar_orani"], 3)})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    rows.append({"delta": None, "odul": round(taban["ortalama_odul"], 4), "reel": round(taban["ortalama_reel_kazanc"], 4),
                 "anapara_alti": round(taban["anapara_alti_bitis_olasiligi"], 4), "model_karar_orani": 0.0})
    best_val = max(r["odul"] for r in rows)
    best = [r for r in rows if r["odul"] >= best_val - 0.002][-1]       # eşitlikte en temkinli
    delta = float("inf") if best["delta"] is None else best["delta"]
    out = {"olusturma": now_iso(), "model_olusturma": meta.get("olusturma"), "delta": delta if np.isfinite(delta) else 1e9,
           "taban": "%50 para piyasası + %50 mevduat (izinli değilse borçlanma)", "secim": "doğrulama dönemi, görülmemiş profiller",
           "izgara": rows,
           "ozet": {"secilen_delta": best["delta"], "dogrulama_odul": best["odul"], "dqn_odul": round(dqn["ortalama_odul"], 4),
                    "taban_odul": round(taban["ortalama_odul"], 4), "model_karar_orani": best["model_karar_orani"]}}
    (Path(args.model_dir) / "kapi.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"olay": "bitti", **out["ozet"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
