"""WSL: en olası dağılım (mod) ile olasılık ağırlıklı beklenen dağılımın (Σ π(a)·A[a]) aynı senaryo izlerinde karşılaştırması.

Modeller holdout ortamında (test dönemi eğitimde yok) eğitilmiş olmalı. Beklenen dağılım için alt eşik τ (τ altındaki
kategori payları sıfırlanıp kalan yeniden ölçeklenir; ürüne çevrilmeyecek küçük paylar) yalnızca doğrulama izlerinde
seçilir; test izleri yalnızca raporlama içindir.

Ödül simülatörün kendisidir (train_gbrl.Core.outcome / risk_pen), yalnızca aksiyon indeksi yerine ağırlık vektörüyle:
profil cezaları ağırlığın fonksiyonu olarak yeniden hesaplanır (wᵀΣw, Σw·likit olmama, 1−Σw·gelir, 1−Σw·sadelik, Σw²,
döviz payı). Doğrusal öznitelikler (likidite, gelir, sadelik, döviz) aksiyon tablosundan en küçük kareler ile geri çözülür.

Kullanım: python beklenen_degerlendir.py --env .../rl_ortam_v13_v2.npz --model .../tut10_s0 [--model ...] --out sonuc.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_gbrl import Core  # noqa: E402

TAUS = [0.0, 0.05, 0.10, 0.15]


class WCore(Core):
    """Ağırlık vektörüyle çalışan simülatör (aksiyon tablosu dışındaki dağılımlar için)."""

    def __init__(self, d):
        super().__init__(d)
        A = self.A
        if self.prof:
            lin = lambda y: np.linalg.lstsq(A, y, rcond=None)[0]  # noqa: E731
            self.illiq, self.gelir = lin(d["profil_PA"][:, 1]), lin(1 - d["profil_PA"][:, 2])
            self.sade, self.fx = lin(1 - d["profil_PA"][:, 3]), lin(d["profil_fx_pay"])
            self.S = d["profil_kov"]
            for k, v in {"likidite": (A @ self.illiq, d["profil_PA"][:, 1]), "fx": (A @ self.fx, d["profil_fx_pay"]),
                         "oynaklik": (np.einsum("ai,ij,aj->a", A, self.S, A), d["profil_PA"][:, 0])}.items():
                assert np.allclose(*v, atol=1e-6), f"profil cezası geri çözülemedi: {k}"

    def pa(self, W):
        return np.column_stack([np.einsum("ei,ij,ej->e", W, self.S, W), W @ self.illiq, 1 - W @ self.gelir, 1 - W @ self.sade,
                                (W ** 2).sum(1)])

    def risk_pen_w(self, pid, W):
        if not self.prof:
            return 0.0
        d = self.d
        return (d["profil_K"][pid] * d["profil_c"] * self.pa(W)).sum(-1) \
            + d["profil_cfx"] * d["profil_Kfx"][pid] * np.maximum(0.0, W @ self.fx - d["profil_fx_hedef"][pid])

    def outcome_w(self, pid, w, prev, idx, Wt):
        r, rn = self.real[idx], self.rn[idx]
        lam, fixed = self.lam[pid], self.fixed[pid]
        turnover = 0.5 * np.abs(Wt - prev).sum(-1) + 0.5 * (1 - prev.sum(-1))
        cost = self.cost if self.cost_p is None else self.cost_p[pid]
        wr, wn = w[:, 0], w[:, 1]
        wr2 = wr * (1 + (Wt * r).sum(-1)) - wr * cost * turnover
        wn2 = wn * (1 + (Wt * rn).sum(-1)) - wn * cost * turnover
        base = wn2 if self.nominal else wr2
        pen = np.where(base < 1, np.minimum(lam * (1 - base) + fixed, self.cap), 0.0)
        dd = Wt * (1 + r)
        dd = dd / np.clip(dd.sum(-1, keepdims=True), 1e-9, None)
        return np.stack([wr2, wn2], -1), (wr2 - wr) - pen, dd, turnover


def replay_w(core: WCore, d, prefix, w_fn) -> dict:
    pid, T, path = d[f"{prefix}_pid"], d[f"{prefix}_T"], d[f"{prefix}_idx"]
    E = len(pid)
    t, w, prev = np.zeros(E, int), np.ones((E, 2)), np.zeros((E, core.C))
    total, active = np.zeros(E), np.ones(E, bool)
    turn, steps = np.zeros(E), np.zeros(E)
    for s in range(int(T.max())):
        idx = path[s]
        W = w_fn(core.obs(pid, t, w, idx, prev), pid)
        w2, r, dd, to = core.outcome_w(pid, w, prev, idx, W)
        r = r - core.risk_pen_w(pid, W)
        total = np.where(active, total + r, total)
        if s > 0:     # ilk adım nakitten kuruluş; devir ölçüsü yeniden dengeleme
            turn = np.where(active, turn + to, turn)
            steps = steps + (active & True)
        w = np.where(active[:, None], w2, w)
        prev = np.where(active[:, None], dd, prev)
        t = t + 1
        active &= t < T
    g = w[:, 0] - 1
    return {"ortalama_odul": float(total.mean()), "ortalama_reel_kazanc": float(g.mean()), "kotu_yuzde5": float(np.percentile(g, 5)),
            "anapara_alti_bitis_olasiligi": float((g < 0).mean()),
            "ortalama_devir": float((turn / np.maximum(steps, 1)).mean())}


def load_probs_fn(path, core):
    import torch
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.policies import MaskableActorCriticPolicy
    from train_gbrl import action_embed_head
    k = json.load(open(os.path.join(path, "argumanlar.json"))).get("aksiyon_gomme", 0)
    orig = MaskableActorCriticPolicy._build
    if k:     # aksiyon gömmeli çıkış başlığı eğitimdeki gibi kurulmadan ağırlıklar yüklenemez
        def build(self, lr_schedule):
            orig(self, lr_schedule)
            self.action_net = action_embed_head(self.mlp_extractor.latent_dim_pi, core.A, k)
            self.optimizer = self.optimizer_class(self.parameters(), lr=lr_schedule(1), **self.optimizer_kwargs)
        MaskableActorCriticPolicy._build = build
    try:
        model = MaskablePPO.load(os.path.join(path, "model_nnppo.zip"), device="cpu")
    finally:
        MaskableActorCriticPolicy._build = orig

    def probs(obs, pid):
        out = []
        with torch.no_grad():
            for s in range(0, len(obs), 4096):
                dist = model.policy.get_distribution(torch.as_tensor(obs[s:s + 4096]), action_masks=core.M[pid[s:s + 4096]])
                out.append(dist.distribution.probs.numpy().astype(np.float64))
        return np.concatenate(out)
    return probs


def policies(core, probs):
    def mode(o, p):
        return core.A[probs(o, p).argmax(1)]

    def expected(tau):
        def f(o, p):
            W = probs(o, p) @ core.A
            if tau > 0:
                keep = W >= tau
                keep[np.arange(len(W)), W.argmax(1)] = True        # en büyük pay her zaman kalır
                W = np.where(keep, W, 0.0)
                W = W / W.sum(1, keepdims=True)
            return W
        return f
    return {"mod": mode, **{f"beklenen_t{int(t * 100):02d}": expected(t) for t in TAUS}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    d = dict(np.load(a.env))
    args0 = json.load(open(os.path.join(a.model[0], "argumanlar.json")))
    core = WCore(d)
    core.set_features(args0["kirpma_turu"], args0["kirp"], 0.0)       # değerlendirmede senaryo şoku yok
    n_pers = len(d["persona_ids"])
    val_keys = ["val_hold"] + [f"personaval{i}" for i in range(n_pers) if f"personaval{i}_pid" in d]
    test_keys = ["test_hold"] + [f"persona{i}" for i in range(n_pers)]
    res = {"ortam": a.env, "modeller": {}, "not": "seçim yalnızca doğrulama izlerinde; test yalnızca raporlama"}
    for mp in a.model:
        probs = load_probs_fn(mp, core)
        pol = policies(core, probs)
        r = {"dogrulama": {}, "test": {}}
        for name, fn in pol.items():
            r["dogrulama"][name] = {k: replay_w(core, d, k, fn) for k in val_keys}
            print(json.dumps({"model": os.path.basename(mp), "politika": name, "dogrulama_odul": round(r["dogrulama"][name]["val_hold"]["ortalama_odul"], 4)}), flush=True)
        res["modeller"][os.path.basename(mp)] = r
    # τ seçimi: doğrulamada (görülmemiş profiller + persona doğrulama izleri) ortalama ödül, modeller üzerinden ortalama
    def vscore(name):
        return float(np.mean([np.mean([v["ortalama_odul"] for v in m["dogrulama"][name].values()]) for m in res["modeller"].values()]))
    cands = [n for n in policies(core, None) if n.startswith("beklenen")]
    res["dogrulama_skoru"] = {n: vscore(n) for n in ["mod", *cands]}
    best = max(cands, key=vscore)
    res["secilen_beklenen"] = best
    for mp in a.model:
        probs = load_probs_fn(mp, core)
        pol = policies(core, probs)
        m = res["modeller"][os.path.basename(mp)]
        for name in ("mod", best):
            m["test"][name] = {k: replay_w(core, d, k, pol[name]) for k in test_keys}
    json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"secilen": best, "dogrulama_skoru": res["dogrulama_skoru"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
