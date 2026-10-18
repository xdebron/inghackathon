"""Eğitilmiş kategori DQN'ini EBM vekil modellerine damıtır (çevrimdışı) ve <model>/aciklama.json yazar.

Kullanım:  backend/.venv/Scripts/python backend/tools/explain_rl.py [--model-dir DIR] [--ornek N]

Adımlar
  1. Durum örnekleri: veri setindeki tüm geçerli karar tarihleri × brief profil kombinasyonları ve 5 persona; karar anı
     (nakit, t=0) — RM'in görüşme öncesi gördüğü durum.
  2. DQN'in her durumdaki maskeli kararı (kategori ağırlıkları) ve inanç başlığının beklenen getirileri.
  3. Kategori başına EBM (etkileşimsiz, toplamsal): ağırlık ~ piyasa göstergeleri + profil alanları;
     inanç başlığı varsa beklenen getiri ~ piyasa göstergeleri.
  4. Sadakat: ayrılmış %20 durumda R² ve "en büyük kategori" uyumu. Tablolar bağımlılıksız biçimde kaydedilir ve
     interpret'in kendi tahminiyle birebir doğrulanır.
Eğitimde kullanılmamış test dönemi tarihleri de durum örneklerine girer: amaç genelleme ölçmek değil, politikayı
tarif etmektir (politika bu durumlarda da aynı ağdır).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import rl_category as rc, rl_explain as rx  # noqa: E402
from app.config import now_iso  # noqa: E402
from app.pipeline import load_persona  # noqa: E402
from app.profile import interpret as interpret_persona  # noqa: E402


def r2(y, p):
    v = y.var()
    return float(1 - ((y - p) ** 2).mean() / v) if v > 1e-12 else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(rc.MODEL_DIR))
    ap.add_argument("--ornek", type=int, default=60000)
    ap.add_argument("--torba", type=int, default=4, help="EBM outer_bags")
    ap.add_argument("--etkilesim", type=int, default=10, help="ikili etkileşim terimi sayısı (0: tamamen toplamsal)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    from interpret.glassbox import ExplainableBoostingRegressor

    t0 = time.time()
    rc.MODEL_DIR = Path(args.model_dir)
    rc._MODEL_CACHE.clear()
    net, meta = rc._load_model()
    if net is None:
        sys.exit(f"Model yok: {args.model_dir}")
    ds = rc.build_dataset(meta.get("gosterge_seti", "v1"), meta.get("veri_baslangic"), meta.get("test_baslangic"))
    if ds["feature_names"] != meta["gosterge_adlari"]:
        sys.exit("Modelin gösterge listesi veriyle uyuşmuyor.")
    cats = meta.get("kategoriler") or rc.CATS_V1
    A = np.array(meta["aksiyonlar"])
    profiles = rc.enumerate_profiles()[0]
    for pid in ["P01", "P02", "P03", "P04", "P05"]:
        p = load_persona(pid)
        view, _ = interpret_persona(p, {})
        if view is not None:
            profiles.append(rc.profile_from_persona(p, view))
    masks = np.array([rc.profile_mask(p, A, meta["kategori_rd"], cats)[0] for p in profiles])
    pvec = np.array([rc.profile_vector(p) for p in profiles])

    days = np.concatenate([ds["train"], ds["val"], ds["test"]])
    rng = np.random.default_rng(0)
    n = args.ornek
    di, pi = rng.choice(days, n), rng.integers(len(profiles), size=n)
    raw = ds["features_raw"][di]
    mu, sd, clip = np.array(meta["gosterge_ort"]), np.array(meta["gosterge_std"]), meta.get("ozellik_kirp")
    z = (raw - mu) / sd
    z = rc.model_input(z, meta)
    X_net = np.column_stack([z, pvec[pi], np.zeros((n, rc.state_dims(meta))), np.zeros((n, len(cats)))])
    acts, bel = np.zeros(n, int), np.zeros((n, len(cats)))
    for s in range(0, n, 4096):
        sl = slice(s, s + 4096)
        q = np.where(masks[pi[sl]], net.forward(X_net[sl]), -np.inf)
        acts[sl] = q.argmax(axis=1)
        b = rc.belief(net, X_net[sl])
        if b is not None:
            bel[sl] = b[0]
    W = A[acts]
    has_belief = getattr(net, "belief_W", None) is not None

    names = rx.feature_names(meta["gosterge_adlari"])
    X = np.column_stack([raw, np.array([[f(profiles[i]) for _, f in rx.PROFILE_FEATURES] for i in pi])])
    n_mkt = len(meta["gosterge_adlari"])
    split = rng.random(n) < 0.8
    print(json.dumps({"olay": "ornekler", "durum": n, "tarih": int(len(np.unique(di))), "profil": len(profiles),
                      "degisken": len(names), "inanc": has_belief}, ensure_ascii=False), flush=True)

    def fit(y, cols):
        ebm = ExplainableBoostingRegressor(feature_names=[names[c] for c in cols], feature_types=["continuous"] * len(cols),
                                           interactions=args.etkilesim, outer_bags=args.torba, max_bins=64, random_state=0)
        ebm.fit(X[split][:, cols], y[split])
        table = rx.export_ebm(ebm, cols)
        p_tab = rx.predict(table, X[~split])
        gap = float(np.abs(p_tab - ebm.predict(X[~split][:, cols])).max())
        assert gap < 1e-8, f"tablo ile interpret tahmini farklı: {gap}"
        imp = dict(zip(ebm.term_names_, map(float, ebm.term_importances())))
        return {"tablo": table, "r2": round(r2(y[~split], p_tab), 3), "onem": imp}, p_tab

    all_cols, mkt_cols = list(range(len(names))), list(range(n_mkt))
    weight_models, weight_pred = {}, np.zeros((int((~split).sum()), len(cats)))
    for j, cat in enumerate(cats):
        if W[:, j].std() < 1e-6:
            weight_pred[:, j] = W[0, j]
            continue
        weight_models[cat], weight_pred[:, j] = fit(W[:, j], all_cols)
        print(json.dumps({"olay": "agirlik", "kategori": cat, "r2": weight_models[cat]["r2"],
                          "dk": round((time.time() - t0) / 60, 1)}, ensure_ascii=False), flush=True)
    belief_models = {}
    if has_belief:
        for j, cat in enumerate(cats):
            if bel[:, j].std() < 1e-7:
                continue
            belief_models[cat], _ = fit(bel[:, j], mkt_cols)
            print(json.dumps({"olay": "inanc", "kategori": cat, "r2": belief_models[cat]["r2"]}, ensure_ascii=False), flush=True)

    Wt = W[~split]
    top_agree = float((weight_pred.argmax(axis=1) == Wt.argmax(axis=1)).mean())
    l1 = float(0.5 * np.abs(np.clip(weight_pred, 0, None) - Wt).sum(axis=1).mean())
    out = {
        "olusturma": now_iso(), "model_olusturma": meta.get("olusturma"), "sure_dk": round((time.time() - t0) / 60, 1),
        "yontem": (f"EBM vekil modeli (interpret): kategori ağırlığı ≈ sabit + Σ değişken katkısı + {args.etkilesim} ikili "
                   "etkileşim. DQN kararlarına damıtıldı; DQN'in kendisi değildir."),
        "etkilesim": args.etkilesim,
        "ornek": {"durum": n, "tarih": int(len(np.unique(di))), "profil": len(profiles), "ayrilan_oran": 0.2},
        "degiskenler": names,
        "sadakat": {"en_buyuk_kategori_uyumu": round(top_agree, 3), "ortalama_dagilim_farki": round(l1, 3),
                    "agirlik_r2_ortalama": round(float(np.mean([m["r2"] for m in weight_models.values()])), 3) if weight_models else None,
                    "aciklama": "Ayrılmış durumlarda: vekilin en büyük ağırlık verdiği kategori DQN ile aynı mı; dağılım farkı = ½Σ|vekil−DQN|."},
        "agirlik": weight_models, "inanc": belief_models,
    }
    path = Path(args.model_dir) / "aciklama.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"olay": "bitti", "dosya": str(path), "sadakat": out["sadakat"], "sure_dk": out["sure_dk"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
