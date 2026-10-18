"""Tahmin + optimizasyon karar modeli (RL'e alternatif).

1) Tahmin: her kategori için ayrı CatBoost regresyonu; hedef, karar anından sonraki 3 ayın vergi sonrası REEL getirisi;
   girdi o anda bilinen piyasa göstergeleri (ham). Simetrik ağaç + monoton kısıtlar = what-if öncülü yapıyla:
     faiz göstergeleri ↑ → TL taşıma getirili kategoriler (mevduat, PP, borçlanma, katılım) ↑
     TÜFE göstergeleri ↑ → TL sabit getirili kategorilerin reel getirisi ↓
     USD/TRY göstergeleri ↑ → döviz/altın kategorileri ↑ (momentum öncülü)
     BIST göstergeleri ↑ → hisse kategorileri ↑;  bir kategorinin kendi geçmiş getirisi ↑ → o kategori ↑
   Büzülme: μ̂ = eğitim ortalaması + λ·(tahmin − ortalama).
2) Optimizasyon (belirlenimci, profil ve maske kısıtları altında, aksiyon ızgarasının tamamında):
     skor(a) = wᵀμ̂ − γ_p/2·wᵀΣw − profil cezaları(a) − maliyet·devir(a) − beklenen anapara cezası(a)
   Profil cezaları ve devir maliyeti RL ödülündeki terimlerin aynısı (ortam dizileri). γ_p = γ / risk seviyesi.
   Beklenen anapara cezası: nominal servet ~ Normal(ortalama, wᵀΣw) altında λ_p·E[açık] + sabit_p·P(açık), tavanlı.
3) Değerlendirme: RL ile aynı dışa aktarılmış senaryo izleri (doğrulama: persona doğrulama izleri + görülmemiş profillerin iki
   yarısı; test: son 3 ay), aynı ödül, aynı what-if takımı (tools/gbrl/whatif.py). λ, γ ve ağaç sayısı yalnızca
   doğrulamayla seçilir (HPO ile aynı skor); test yalnızca seçilen ayar için, eğitim∪doğrulamayla yeniden eğitilen
   modelle okunur.

Kullanım:  backend/.venv/Scripts/python backend/tools/catboost_karar.py --env backend/models/rl_ortam_v13_v2.npz
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from math import sqrt
from pathlib import Path

import numpy as np
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "gbrl"))
import whatif  # noqa: E402
from train_gbrl import Core  # noqa: E402

Q_INF = 91 / 365


def monotone_spec(names: list[str], cats: list[str]) -> dict[str, list[int]]:
    """Kategori → gösterge başına monoton kısıt listesi (+1, −1, 0). Göreli çekicilik öncülü: bir senaryo göstergesi
    izlenen grubun getiri tahminini öncül yönünde, DİĞER kategorilerinkini ters yönde (ya da sabit) hareket ettirebilir;
    böylece optimizasyonun seçtiği grubun payı öncülle aynı yönde değişir. Diğer göstergeler serbest (0)."""
    groups = {k: set(v) for k, v in whatif.GROUPS.items()}
    out = {}
    for c in cats:
        m = []
        for n in names:
            if n in whatif.RATE:                                            # faiz ↑ → nakit (mevduat + PP) ↑
                m.append(1 if c in groups["nakit"] else -1)
            elif n in whatif.INFL_UP:                                       # TÜFE ↑ → TL sabit getiri ↓
                m.append(-1 if c in groups["tl_sabit"] else 1)
            elif n.startswith("USD/TRY"):                                   # kur ↑ → döviz + altın ↑
                m.append(1 if c in groups["doviz"] else -1)
            elif n.startswith("BIST 100 toplam getiri") or n.startswith("BIST 30 endeksi"):
                m.append(1 if c in groups["hisse"] else -1)
            elif "getiri" in n and n.split(" ")[0] in cats:                 # kategorinin kendi geçmiş getirisi
                m.append(1 if n.split(" ")[0] == c else -1)
            else:
                m.append(0)
        out[c] = m
    return out


CARRY = {"mevduat_tl": "Mevduat faizi 1 ay", "para_piyasasi": "TLREF", "borclanma": "TLREF", "katilim": "TLREF"}


class Forecaster:
    """Getiri = yapısal temel + CatBoost artığı.
    Yapısal temel (karar anında bilinen, göstergelerle doğrusal → ağaçların ekstrapolasyon yapamaması sorun olmaz):
      TL kategorileri: nominal ≈ k_c · faiz · 3/12 (k_c: eğitimde ortalamalar oranı; vergi ve fon farkını taşır)
      diğerleri: nominal ≈ beklenen enflasyon (reel 0)
      reel = (1 + nominal) / (1 + π̂) − 1,  π̂ = TÜFE 3 ay eğiliminin 3 aylık karşılığı
    CatBoost artığı (gerçekleşen − yapısal) monoton kısıtlarla öğrenir; tahmin = yapısal + artık."""

    def __init__(self, names, cats, iters=400, depth=4, seed=0):
        self.names, self.cats, self.iters, self.depth, self.seed = names, cats, iters, depth, seed
        self.mono = monotone_spec(names, cats)
        self.k_cpi = names.index("TÜFE 3 ay eğilimi (yıllık)")

    def infl_q(self, X):
        return (1 + np.clip(X[:, self.k_cpi], -0.5, 5.0)) ** Q_INF - 1

    def structural(self, X, nominal: bool = False):
        pi = self.infl_q(X)
        nom = np.column_stack([self.k[c] * X[:, self.names.index(CARRY[c])] * Q_INF if c in CARRY else pi for c in self.cats])
        return nom if nominal else (1 + nom) / (1 + pi)[:, None] - 1

    def fit(self, X, Y, Yn=None):
        from catboost import CatBoostRegressor
        self.k = {}
        for c, f in CARRY.items():
            if c in self.cats:      # nominal getiri / (faiz · 3/12) oranı (Yn: vergi sonrası nominal; yoksa reel+enflasyon)
                j = self.cats.index(c)
                yn = Yn[:, j] if Yn is not None else (1 + Y[:, j]) * (1 + self.infl_q(X)) - 1
                self.k[c] = float(np.mean(yn) / np.mean(X[:, self.names.index(f)] * Q_INF))
        R = Y - self.structural(X)
        self.mean = R.mean(0)
        self.models = []
        for j, c in enumerate(self.cats):
            m = CatBoostRegressor(iterations=self.iters, depth=self.depth, learning_rate=0.03, l2_leaf_reg=10, loss_function="RMSE",
                                  monotone_constraints=self.mono[c], random_seed=self.seed, verbose=False, thread_count=-1)
            m.fit(X, R[:, j])
            self.models.append(m)
        return self

    def predict(self, X, shrink: float = 1.0):
        """yapısal + ortalama artık + shrink·(artık tahmini − ortalama artık)."""
        R = np.column_stack([m.predict(X) for m in self.models])
        return self.structural(X) + self.mean + shrink * (R - self.mean)


class Optimizer:
    """Tahminden aksiyona: profil ve maske kısıtları altında skor(a) en büyük aksiyon."""

    def __init__(self, core: Core, d: dict, Sigma: np.ndarray, gamma: float):
        self.core, self.d, self.A = core, d, core.A
        self.var = np.einsum("ai,ij,aj->a", self.A, Sigma, self.A)
        self.sd = np.sqrt(np.maximum(self.var, 1e-12))
        self.gamma_p = gamma / np.maximum(core.pvec[:, 0], 0.05)
        self.pen_profile = None

    def act(self, mu, infl_q, pid, w, prev):
        """mu: (n, kategori) beklenen reel getiri; infl_q: (n,) beklenen 3 aylık enflasyon; w: (n, 2) servet; prev: (n, C)."""
        core, A = self.core, self.A
        out = np.empty(len(pid), int)
        for s in range(0, len(pid), 256):
            sl = slice(s, s + 256)
            p, m = pid[sl], mu[sl] @ A.T                                          # (n, a) beklenen reel getiri
            pen = core.risk_pen(p[:, None], np.arange(len(A))[None, :]) if core.prof else 0.0
            turnover = 0.5 * np.abs(A[None] - prev[sl][:, None]).sum(-1) + 0.5 * (1 - prev[sl].sum(-1))[:, None]
            cost = (core.cost_p[p] if core.cost_p is not None else core.cost)
            cost = np.broadcast_to(np.asarray(cost, float).reshape(-1, 1) if np.ndim(cost) else cost, turnover.shape)
            # anapara cezası (nominal taban): nominal servet sonrası ~ N(w_n·(1+m_n), (w_n·σ)²)
            wn = (w[sl, 1] if core.nominal else w[sl, 0])[:, None]
            m_nom = (1 + m) * (1 + (infl_q[sl][:, None] if core.nominal else 0.0)) - 1
            mean_w, sd_w = wn * (1 + m_nom), wn * self.sd[None]
            z = (1 - mean_w) / sd_w
            p_short = ndtr(z)
            e_short = sd_w * (np.exp(-0.5 * z ** 2) / sqrt(2 * np.pi)) + (1 - mean_w) * p_short
            lam, fix = core.lam[p][:, None], core.fixed[p][:, None]
            principal = np.minimum(lam * e_short + fix * p_short, core.cap)
            score = m - 0.5 * self.gamma_p[p][:, None] * self.var[None] - pen - cost * turnover - principal
            score = np.where(core.M[p], score, -np.inf)
            out[sl] = score.argmax(1)
        return out


def replay_idx(core: Core, d: dict, prefix: str, policy) -> dict:
    """train_gbrl.replay ile aynı; politika gözlem yerine (idx, pid, w, prev) alır."""
    pid, T, path = d[f"{prefix}_pid"], d[f"{prefix}_T"], d[f"{prefix}_idx"]
    E = len(pid)
    t, w, prev = np.zeros(E, int), np.ones((E, 2)), np.zeros((E, core.C))
    total, active = np.zeros(E), np.ones(E, bool)
    for s in range(int(T.max())):
        idx = path[s]
        a = policy(idx, pid, w, prev)
        w2, r, dd = core.outcome(pid, w, prev, idx, a)
        r = r - core.risk_pen(pid, a)
        total = np.where(active, total + r, total)
        w = np.where(active[:, None], w2, w)
        prev = np.where(active[:, None], dd, prev)
        t = t + 1
        active &= t < T
    g, gn = w[:, 0] - 1, w[:, 1] - 1
    return {"ortalama_reel_kazanc": float(g.mean()), "kotu_yuzde5": float(np.percentile(g, 5)),
            "anapara_alti_bitis_olasiligi": float((g < 0).mean()), "nominal_anapara_alti_bitis_olasiligi": float((gn < 0).mean()),
            "ortalama_odul": float(total.mean())}


def seed_score(pdiff, halves):
    p = np.array(pdiff)
    return float(p.mean() + 3 * np.minimum(p, 0).sum() + 0.5 * min(halves))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--out", default=str(ROOT / "models" / "catboost_karar"))
    ap.add_argument("--hizli", action="store_true", help="küçük ızgara (duman testi)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log = open(out / "log.jsonl", "w", encoding="utf-8")

    def emit(row):
        row = {"dk": round((time.time() - t0) / 60, 1), **row}
        log.write(json.dumps(row, ensure_ascii=False) + "\n"); log.flush()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    d = dict(np.load(args.env))
    core = Core(d)
    core.set_features("sert", 1e9)
    names, cats = core.names, core.cats
    X, Y = d["F_raw"], d["real"]
    Yn = d["nominal_net"]
    base = d["base"]
    n = len(d["persona_ids"])
    tr, va, te = d["train"], d["val"], d["test"]

    # --- 1) tahmin kalitesi: doğrulamada tarihsel ortalamaya karşı
    def r2_report(fc, idx_fit, idx_eval, tag):
        P = fc.predict(X[idx_eval])
        P0 = fc.predict(X[idx_eval], 0.0)                                  # yalnızca yapısal temel (+ ortalama artık)
        mse_m = ((Y[idx_eval] - Y[idx_fit].mean(0)) ** 2).mean(0)
        r2 = 1 - ((Y[idx_eval] - P) ** 2).mean(0) / mse_m
        r2s = 1 - ((Y[idx_eval] - P0) ** 2).mean(0) / mse_m
        emit({"olay": "tahmin_kalitesi", "donem": tag, "R2_ortalamaya_gore": {c: round(float(x), 3) for c, x in zip(cats, r2)},
              "R2_yalniz_yapisal": {c: round(float(x), 3) for c, x in zip(cats, r2s)},
              "ort_R2": round(float(np.mean(r2)), 3), "ort_R2_yapisal": round(float(np.mean(r2s)), 3)})
        return P

    Sigma_tr = np.cov(Y[tr].T)
    rule = {f"personaval{i}": replay_idx(core, d, f"personaval{i}", lambda idx, p, w, pr: base[p])["ortalama_odul"] for i in range(n)}
    rule |= {k: replay_idx(core, d, k, lambda idx, p, w, pr: base[p])["ortalama_odul"] for k in ("val_a", "val_b")}

    iters_grid = [200] if args.hizli else [150, 400, 1000]
    lam_grid = [0.0, 1.0] if args.hizli else [0.0, 0.25, 0.5, 1.0]
    gam_grid = [1.0] if args.hizli else [0.0, 1.0, 3.0, 10.0]
    results = []
    for iters in iters_grid:
        fc = Forecaster(names, cats, iters=iters).fit(X[tr], Y[tr], Yn[tr])
        pi_all = fc.infl_q(X)
        r2_report(fc, tr, va, f"doğrulama (ağaç {iters})")
        for lam, gam in itertools.product(lam_grid, gam_grid):
            mu_all = fc.predict(X, lam)
            opt = Optimizer(core, d, Sigma_tr, gam)
            pol = lambda idx, p, w, pr: opt.act(mu_all[idx], pi_all[idx], p, w, pr)
            pd_ = [replay_idx(core, d, f"personaval{i}", pol)["ortalama_odul"] - rule[f"personaval{i}"] for i in range(n)]
            hv = [replay_idx(core, d, k, pol)["ortalama_odul"] - rule[k] for k in ("val_a", "val_b")]
            sc = seed_score(pd_, hv)
            row = {"olay": "ayar", "agac": iters, "buzulme": lam, "gamma": gam, "skor": round(sc, 4),
                   "persona_dogrulama_farki": [round(x * 100, 2) for x in pd_], "dogrulama_yarilar": [round(x * 100, 2) for x in hv]}
            emit(row)
            results.append(row)
    best = max(results, key=lambda r: r["skor"])
    emit({"olay": "secim", **best})

    # --- 2) seçilen ayar: what-if (doğrulama tarihleri) ve TEST (eğitim ∪ doğrulama ile yeniden eğitilmiş tahminci)
    def build(idx_fit):
        fc = Forecaster(names, cats, iters=best["agac"]).fit(X[idx_fit], Y[idx_fit], Yn[idx_fit])
        mu_all = fc.predict(X, best["buzulme"])
        opt = Optimizer(core, d, np.cov(Y[idx_fit].T), best["gamma"])
        return fc, opt, mu_all

    def whatif_eval(fc, opt, pool, every):
        dates = np.asarray(pool)[::every]
        pids = np.concatenate([d["persona_ids"], d["hold_ids"][:40]])
        rows = np.tile(pids, len(dates))
        idx = np.repeat(dates, len(pids))
        raw = X[idx]

        def alloc(zin):                         # sert, c=1e9 → zin = (raw − mu)/sd (kırpma yok)
            rr = zin * core.sd + core.mu
            mu = fc.predict(rr, best["buzulme"])
            a = opt.act(mu, fc.infl_q(rr), rows, np.ones((len(rows), 2)), np.zeros((len(rows), core.C)))
            return core.A[a]
        allowed = whatif.allowed_groups_from_masks(rows, core.M, core.A, cats)
        return whatif.evaluate(alloc, names, raw, core.mu, core.sd, "sert", 1e9, cats, allowed, rows=rows, M=core.M, A=core.A)

    fc, opt, mu_all = build(tr)
    wi = whatif_eval(fc, opt, va, 4)
    emit({"olay": "whatif_dogrulama", "makro": wi["makro"], "kategori": wi["kategori"], "en_kotu": wi["en_kotu_senaryo_yanlis"],
          "makul": wi["makul"], "senaryolar": {k: (v["yanlis_yon"], v["dogru_tepki"]) for k, v in wi["senaryolar"].items()}})
    fc2, opt2, mu2 = build(np.concatenate([tr, va]))
    pol2 = lambda idx, p, w, pr: opt2.act(mu2[idx], fc2.infl_q(X)[idx], p, w, pr)
    te_p = [replay_idx(core, d, f"persona{i}", pol2)["ortalama_reel_kazanc"] for i in range(n)]
    te_r = [replay_idx(core, d, f"persona{i}", lambda idx, p, w, pr: base[p])["ortalama_reel_kazanc"] for i in range(n)]
    th = replay_idx(core, d, "test_hold", pol2)["ortalama_reel_kazanc"]
    thr = replay_idx(core, d, "test_hold", lambda idx, p, w, pr: base[p])["ortalama_reel_kazanc"]
    wi_t = whatif_eval(fc2, opt2, te, 2)
    emit({"olay": "TEST", "persona_reel_farki": [round((a - b) * 100, 2) for a, b in zip(te_p, te_r)],
          "gorulmemis_reel": round(th * 100, 2), "kural_gorulmemis_reel": round(thr * 100, 2),
          "whatif_test": {"makro": wi_t["makro"], "kategori": wi_t["kategori"], "makul": wi_t["makul"]}})
    json.dump({"secim": best, "ayarlar": results}, open(out / "sonuc.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
