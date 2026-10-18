"""WSL'de çalışır: NVIDIA GBRL (gradyan artırmalı ağaçlarla PPO, maskeli) ile kategori politikası.

Girdi: tools/export_rl_env.py çıktısı (.npz). Simülatör rl_category.Sim.outcome / next_idx ile birebir aynıdır (numpy kopya).
Değerlendirme: dışa aktarılan senaryo izleri (bölüm uzunluğu + adım adım başlangıç indeksleri) aynen yeniden oynatılır;
böylece sonuçlar Windows'taki adil karşılaştırmayla aynı senaryolardadır. Taban kural aynı izlerde yeniden oynatılıp
Windows sonucuyla eşleştiği doğrulanır.

Seçim: doğrulama (görülmemiş profiller × doğrulama dönemi) ödülü en iyi olan kontrol noktası; test sonucu yalnızca o nokta için
raporlanır (test seçimde kullanılmaz).

Kullanım (WSL):  ~/gbrl-env/bin/python train_gbrl.py --env /mnt/c/.../rl_ortam_v5.npz --out /mnt/c/.../gbrl_v5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.expanduser("~/gbrl_sb3"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import whatif  # noqa: E402

SCALE = 10.0
Q = 91 / 365          # adım = 3 ay (takvim)
# Senaryo artırımı: şok türü → (büyüklük aralığı, kategori başına 3 aylık NOMİNAL getiri etkisi / birim şok, enflasyon etkisi).
# Mekanizmalar: faiz → TL mevduat/PP/borçlanma taşıma getirisi (şok fiyatlandı, ileri taşıma değişir); TÜFE → 3 aylık
# enflasyon (kalıcı), döviz bazlı varlıklar TL'de tam, hisse/karma yarı yansıtır; kur ve borsa → momentum katsayısı κ ile
# devam (zaman serisi momentumu öncülü, --momentum).
SHOCK_KINDS = ["rate", "infl", "usd", "equity"]
SHOCK_RANGE = {"rate": 0.10, "infl": 0.20, "usd": 0.20, "equity": 0.25}


def shock_effects(cats, kappa):
    e = {k: np.zeros(len(cats)) for k in SHOCK_KINDS}

    def put(k, m):
        for c, v in m.items():
            if c in cats:
                e[k][cats.index(c)] = v
    put("rate", {"mevduat_tl": Q, "para_piyasasi": Q, "borclanma": Q, "katilim": 0.8 * Q, "karma_degisken": 0.3 * Q, "fon_sepeti": 0.3 * Q})
    put("infl", {"kiymetli_maden": Q, "yabanci_hisse": Q, "eurobond_doviz": Q, "hisse": 0.5 * Q, "bist30_hisse": 0.5 * Q,
                 "karma_degisken": 0.5 * Q, "fon_sepeti": 0.5 * Q})
    put("usd", {"kiymetli_maden": kappa, "yabanci_hisse": kappa, "eurobond_doviz": kappa})
    put("equity", {"hisse": kappa, "bist30_hisse": kappa, "karma_degisken": 0.3 * kappa, "fon_sepeti": 0.3 * kappa})
    infl = {"rate": 0.0, "infl": Q, "usd": 0.0, "equity": 0.0}
    return e, infl


class Core:
    """rl_category.Sim'in numpy kopyası (tek bir ortak veri, çok sayıda paralel bölüm)."""

    def __init__(self, d):
        self.d = d
        self.real, self.rn, self.F = d["real"], d["nominal_net"], d["F"]
        self.A, self.M, self.pvec = d["A"], d["M"], d["pvec"]
        self.hmin, self.hmax, self.lam, self.fixed = d["hmin"], d["hmax"], d["lam"], d["fixed"]
        self.cost, self.cap, self.L, self.step = float(d["cost"]), float(d["pen_cap"]), int(d["L"]), int(d["step"])
        self.nominal, self.t_cap = bool(d["nominal"]), int(d["t_cap"])
        self.C = self.A.shape[1]
        self.prof = "profil_K" in d   # profil ödülü dizileri varsa (export --profil-tam)
        self.cost_p = d["profil_maliyet_carpani"] * self.cost if self.prof else None
        self.risk_q = None      # aksiyon başına nominal varyans (wᵀΣw); set_risk ile
        self.gamma = None       # profil başına risk kaçınma katsayısı
        self.raw = "F_raw" in d
        self.shock_p, self.squash_mode, self.c = 0.0, "sert", 2.0

    def set_features(self, mode, c, shock_p=0.0, kappa=0.0, scale=1.0):
        """Ağ girdisi dönüşümü (sert kesme / tanh) ve what-if senaryo artırımı (adım başına olasılık shock_p)."""
        d = self.d
        self.squash_mode, self.c, self.shock_p, self.scale = mode, c, shock_p, scale
        if not self.raw:
            assert mode == "sert" and shock_p == 0, "ortamda ham gösterge yok (eski export)"
            return
        self.mu, self.sd, self.names = d["mu"], d["sd"], [str(x) for x in d["feature_names"]]
        self.cats = [str(x) for x in d["cats"]]
        self.F = whatif.squash((d["F_raw"] - self.mu) / self.sd, mode, c).astype(np.float32)
        self.eff, self.eff_inf = shock_effects(self.cats, kappa)

    def sample_shock(self, n, rng):
        if self.shock_p <= 0:
            return None
        kind = np.where(rng.random(n) < self.shock_p, rng.integers(len(SHOCK_KINDS), size=n), -1)
        mag = np.array([SHOCK_RANGE[k] for k in SHOCK_KINDS])[np.maximum(kind, 0)] * self.scale
        return kind, rng.uniform(-1, 1, n) * mag

    def feats(self, idx, shock=None):
        X = self.F[idx]
        if shock is None or not (shock[0] >= 0).any():
            return X
        X = X.copy()
        kind, dd = shock
        for k, name in enumerate(SHOCK_KINDS):
            r = np.where(kind == k)[0]
            if len(r):
                raw = whatif.apply(self.names, self.d["F_raw"][idx[r]], name, dd[r])
                X[r] = whatif.squash((raw - self.mu) / self.sd, self.squash_mode, self.c)
        return X

    def shocked_returns(self, idx, shock):
        r, rn = self.real[idx], self.rn[idx]
        if shock is None or not (shock[0] >= 0).any():
            return r, rn
        kind, dd = shock
        on = kind >= 0
        E = np.stack([self.eff[k] for k in SHOCK_KINDS])[np.maximum(kind, 0)] * (dd * on)[:, None]
        Ei = np.array([self.eff_inf[k] for k in SHOCK_KINDS])[np.maximum(kind, 0)] * dd * on
        inf = (1 + rn[:, 0]) / (1 + r[:, 0]) - 1
        rn2 = rn + E
        return (1 + rn2) / (1 + inf + Ei)[:, None] - 1, rn2

    def set_risk(self, cov, gamma_base):
        """Risk duyarlı ödül: her adımda −γ_profil/2 · wᵀΣw. Σ testten önceki nominal getiri kovaryansı; γ_profil =
        γ_temel / risk seviyesi (Agresif 1,0 → γ_temel; Muhafazakâr 0,1 → 10 × γ_temel)."""
        self.risk_q = np.einsum("ai,ij,aj->a", self.A, cov, self.A)
        self.gamma = gamma_base / np.maximum(self.pvec[:, 0], 0.05)

    def risk_pen(self, pid, acts):
        pen = 0.0 if self.risk_q is None else 0.5 * self.gamma[pid] * self.risk_q[acts]
        if self.prof:   # profil ödülü: kriter ağırlıklı cezalar (profilin tamamı)
            d = self.d
            pen = pen + (d["profil_K"][pid] * d["profil_c"] * d["profil_PA"][acts]).sum(-1) \
                + d["profil_cfx"] * d["profil_Kfx"][pid] * np.maximum(0.0, d["profil_fx_pay"][acts] - d["profil_fx_hedef"][pid])
        return pen

    def obs(self, pid, t, w, idx, prev, shock=None):
        wealth = [(w[:, 0] - 1) * 5] + ([(w[:, 1] - 1) * 5] if self.nominal else [])
        return np.column_stack([self.feats(idx, shock), self.pvec[pid], np.minimum(t, self.t_cap) / self.t_cap, *wealth, prev]).astype(np.float32)

    def outcome(self, pid, w, prev, idx, acts, shock=None):
        (r, rn), Wt = self.shocked_returns(idx, shock), self.A[acts]
        lam, fixed = self.lam[pid], self.fixed[pid]
        turnover = 0.5 * np.abs(Wt - prev).sum(-1) + 0.5 * (1 - prev.sum(-1))
        wr, wn = w[:, 0], w[:, 1]
        cost = self.cost if self.cost_p is None else self.cost_p[pid]       # maliyet ağırlığına göre devir maliyeti
        wr2 = wr * (1 + (Wt * r).sum(-1)) - wr * cost * turnover
        wn2 = wn * (1 + (Wt * rn).sum(-1)) - wn * cost * turnover
        base = wn2 if self.nominal else wr2
        pen = np.where(base < 1, np.minimum(lam * (1 - base) + fixed, self.cap), 0.0)
        d = Wt * (1 + r)
        d = d / np.clip(d.sum(-1, keepdims=True), 1e-9, None)
        return np.stack([wr2, wn2], -1), (wr2 - wr) - pen, d

    def next_idx(self, idx, member, pool, rng):
        nxt = idx + self.step
        ok = (nxt < len(member)) & member[np.minimum(nxt, len(member) - 1)] & (rng.random(len(idx)) >= 1 / self.L)
        return np.where(ok, nxt, rng.choice(pool, size=len(idx)))


def action_embed_head(latent: int, A: np.ndarray, k: int, hidden: int = 64):
    """Aksiyon gömmeli çıkış: logit_a = (W·h + b) · g(A_a) + c_a. g: dağılım vektörü (kategori ağırlıkları) → k boyut
    (küçük MLP). Aksiyon başına tam ağırlık matrisi (latent × aksiyon sayısı) yerine; benzer dağılımlar benzer puan alır.
    c_a aksiyon başına sapma (aksiyon sayısı kadar parametre)."""
    import torch
    from torch import nn

    class Head(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("A", torch.as_tensor(A, dtype=torch.float32))
            self.proj = nn.Linear(latent, k)
            self.emb = nn.Sequential(nn.Linear(A.shape[1], hidden), nn.ReLU(), nn.Linear(hidden, k))
            self.bias = nn.Parameter(torch.zeros(A.shape[0]))
            nn.init.orthogonal_(self.proj.weight, gain=0.01)
            nn.init.zeros_(self.proj.bias)

        def forward(self, h):
            return self.proj(h) @ self.emb(self.A).T + self.bias

    return Head()


def make_vec_env(core: Core, ids, pool, n_envs, seed, p_cur, persona_ids=None, persona_frac=0.0):
    import gymnasium as gym
    from stable_baselines3.common.vec_env import VecEnv

    class SimVecEnv(VecEnv):
        """Tüm paralel bölümleri tek numpy çağrısıyla ilerleten vektör ortam; maskeler action_masks ile verilir."""

        def __init__(self):
            d_obs = core.F.shape[1] + core.pvec.shape[1] + (3 if core.nominal else 2) + core.C
            super().__init__(n_envs, gym.spaces.Box(-np.inf, np.inf, (d_obs,), np.float32), gym.spaces.Discrete(len(core.A)))
            self.rng = np.random.default_rng(seed)
            self.member = np.zeros(len(core.real) + core.step + 1, bool)
            self.member[pool] = True
            self.ids, self.pool = np.asarray(ids), np.asarray(pool)
            self._actions = None
            self._reset_all()

        def _new(self, n):
            pid = self.rng.choice(self.ids, n)
            if persona_frac > 0 and persona_ids is not None and len(persona_ids):
                # bölümlerin bir kısmı doğrudan persona profilleriyle (önceden bilinen müşteriler; test dönemi görülmez)
                pid = np.where(self.rng.random(n) < persona_frac, self.rng.choice(persona_ids, n), pid)
            T = self.rng.integers(core.hmin[pid], core.hmax[pid] + 1)
            rnd = core.A[self.rng.integers(len(core.A), size=n)]
            prev = np.where((self.rng.random(n) < p_cur)[:, None], rnd, 0.0)
            return pid, T, np.zeros(n, int), np.ones((n, 2)), self.rng.choice(self.pool, n), prev

        def _reset_all(self):
            self.pid, self.T, self.t, self.w, self.idx, self.prev = self._new(self.num_envs)
            self.shock = core.sample_shock(self.num_envs, self.rng)
            self.ep_ret = np.zeros(self.num_envs)

        def reset(self):
            self._reset_all()
            return core.obs(self.pid, self.t, self.w, self.idx, self.prev, self.shock)

        def step_async(self, actions):
            self._actions = np.asarray(actions).astype(int)

        def step_wait(self):
            a = self._actions
            bad = ~core.M[self.pid, a]                       # maske ile olmamalı; güvenlik için taban kurala çevir
            a = np.where(bad, core.d["base"][self.pid], a)
            self.w, r, self.prev = core.outcome(self.pid, self.w, self.prev, self.idx, a, self.shock)
            r = (r - core.risk_pen(self.pid, a)) * SCALE - bad * 0.1
            self.ep_ret += r
            self.t += 1
            self.idx = core.next_idx(self.idx, self.member, self.pool, self.rng)
            self.shock = core.sample_shock(self.num_envs, self.rng)     # her adımın durumu ve getirisi için yeni şok
            done = self.t >= self.T
            infos = [{} for _ in range(self.num_envs)]
            if done.any():
                term_obs = core.obs(self.pid, self.t, self.w, self.idx, self.prev, self.shock)
                for i in np.where(done)[0]:
                    infos[i] = {"terminal_observation": term_obs[i], "episode": {"r": float(self.ep_ret[i]), "l": int(self.t[i])}}
                n = int(done.sum())
                self.pid[done], self.T[done], self.t[done], self.w[done], self.idx[done], self.prev[done] = self._new(n)
                self.ep_ret[done] = 0.0
            return core.obs(self.pid, self.t, self.w, self.idx, self.prev, self.shock), r.astype(np.float32), done, infos

        def action_masks(self):
            return core.M[self.pid]

        def env_method(self, method_name, *args, indices=None, **kwargs):
            if method_name == "action_masks":
                m = self.action_masks()
                return [m[i] for i in (range(self.num_envs) if indices is None else indices)]
            raise NotImplementedError(method_name)

        def close(self):
            pass

        def get_attr(self, attr_name, indices=None):
            n = self.num_envs if indices is None else len(list(indices))
            return [getattr(self, attr_name, None)] * n

        def set_attr(self, attr_name, value, indices=None):
            setattr(self, attr_name, value)

        def env_is_wrapped(self, wrapper_class, indices=None):
            return [False] * self.num_envs

        def seed(self, seed=None):
            self.rng = np.random.default_rng(seed)
            return [seed] * self.num_envs

    return SimVecEnv()


def replay(core: Core, d, prefix: str, act_fn) -> dict:
    """Dışa aktarılan senaryo izini aynen oynatır. act_fn(obs, pid) → aksiyon (maskeli)."""
    pid, T, path = d[f"{prefix}_pid"], d[f"{prefix}_T"], d[f"{prefix}_idx"]
    E = len(pid)
    t, w, prev = np.zeros(E, int), np.ones((E, 2)), np.zeros((E, core.C))
    total, below, active = np.zeros(E), np.zeros(E, bool), np.ones(E, bool)
    for s in range(int(T.max())):
        idx = path[s]
        a = act_fn(core.obs(pid, t, w, idx, prev), pid)
        w2, r, dd = core.outcome(pid, w, prev, idx, a)
        r = r - core.risk_pen(pid, a)
        total = np.where(active, total + r, total)
        w = np.where(active[:, None], w2, w)
        prev = np.where(active[:, None], dd, prev)
        below |= active & (w[:, 0] < 1)
        t = t + 1
        active &= t < T
    g, gn = w[:, 0] - 1, w[:, 1] - 1
    return {"ortalama_reel_kazanc": float(g.mean()), "kotu_yuzde5": float(np.percentile(g, 5)),
            "anapara_alti_bitis_olasiligi": float((g < 0).mean()), "nominal_anapara_alti_bitis_olasiligi": float((gn < 0).mean()),
            "ortalama_odul": float(total.mean())}


def whatif_report(core, d, probs_fn, pool, n_hold=40, every=4) -> dict:
    """What-if makullüğü (whatif.evaluate): havuzdaki tarihler × (personalar + görülmemiş profiller), nakitten t=0."""
    if not core.raw:
        return {}
    dates = np.asarray(pool)[::every]
    pids = np.concatenate([d["persona_ids"], d["hold_ids"][:n_hold]])
    rows = np.tile(pids, len(dates))
    idx = np.repeat(dates, len(pids))
    raw = d["F_raw"][idx]
    n_state = 3 if core.nominal else 2
    tail = np.column_stack([core.pvec[rows], np.zeros((len(rows), n_state + core.C))])

    def expected(zin):
        return probs_fn(np.column_stack([zin, tail]).astype(np.float32), rows) @ core.A

    allowed = whatif.allowed_groups_from_masks(rows, core.M, core.A, core.cats)
    r = whatif.evaluate(expected, core.names, raw, core.mu, core.sd, core.squash_mode, core.c, core.cats, allowed,
                        rows=rows, M=core.M, A=core.A)
    pr = probs_fn(np.column_stack([whatif.squash((raw - core.mu) / core.sd, core.squash_mode, core.c), tail]).astype(np.float32), rows)
    r["ust_olasilik"] = round(float(pr.max(1).mean()), 3)
    return r


def monotone_triples(core, hi_grp=0.5, hi_small=0.3):
    """GBRL monoton kısıtları (özellik, aksiyon logit'i, yön): what-if öncülü yapıyla garanti edilir.
    Özellik → (izlenen pay, öncül yönü s):
        faiz göstergeleri (politika faizi, TLREF, mevduat faizi, değişimleri, reel faiz) → nakit (mevduat+PP), s=+1
        TÜFE göstergeleri → TL sabit getiri, s=−1;  USD/TRY göstergeleri → döviz+altın, s=+1
        BIST 100 / BIST 30 göstergeleri → hisse, s=+1;  bir kategorinin geçmiş getirileri → o kategorinin payı, s=+1
    Aksiyon yönü: payı yüksek (nakit/TL sabit ≥ %50; döviz/hisse/kategori ≥ %30) olan aksiyonların logit'i s yönünde, payı
    sıfır olanlarınki ters yönde monoton; aradakiler serbest. Softmax'ta bu, yüksek paylı aksiyonların olasılığını öncül
    yönünde artırır. Oynaklık ve diğer göstergeler serbest."""
    names, cats, A = core.names, core.cats, core.A
    share = lambda grp: A[:, [cats.index(c) for c in whatif.GROUPS[grp] if c in cats]].sum(1)
    feats, outs, dirs = [], [], []
    for k, n in enumerate(names):
        if n in whatif.RATE:
            sh, s, hi = share("nakit"), 1, hi_grp
        elif n in whatif.INFL_UP:
            sh, s, hi = share("tl_sabit"), -1, hi_grp
        elif n.startswith("USD/TRY"):
            sh, s, hi = share("doviz"), 1, hi_small
        elif n.startswith("BIST 100 toplam getiri") or n.startswith("BIST 30 endeksi"):
            sh, s, hi = share("hisse"), 1, hi_small
        elif "getiri" in n and n.split(" ")[0] in cats:
            sh, s, hi = A[:, cats.index(n.split(" ")[0])], 1, hi_small
        else:
            continue
        for a in np.where(sh >= hi - 1e-9)[0]:
            feats.append(k); outs.append(int(a)); dirs.append(s)
        for a in np.where(sh <= 1e-9)[0]:
            feats.append(k); outs.append(int(a)); dirs.append(-s)
    return np.array(feats, np.int32), np.array(outs, np.int32), np.array(dirs, np.int32)


def consistency_callback(core, d, model, args, emit):
    """What-if tutarlılık düzenleyicisi. Her rollout sonunda (PPO güncellemesinden önce) K adım: eğitim havuzundan rastgele
    tarih × eğitim profili (nakitten, t=0), rastgele şok türü ve büyüklüğü (artırımla aynı aralık). Kayıp:
        relu(−s·Δg) + β·relu(0,02 − s·Δg)      Δg: beklenen dağılımda izlenen grubun payındaki değişim; s: öncülün yönü
    (faiz ↑ → nakit; TÜFE ↑ → TL sabit getiri ↓; kur ↑ → döviz/altın; borsa ↑ → hisse). Grup profilde izinli değilse kayıp yok."""
    import torch
    from stable_baselines3.common.callbacks import BaseCallback
    dev = model.policy.device
    A_t = torch.as_tensor(core.A, dtype=torch.float32, device=dev)
    grp = {"rate": ("nakit", 1.0), "infl": ("tl_sabit", -1.0), "usd": ("doviz", 1.0), "equity": ("hisse", 1.0)}
    gcols = {g: [core.cats.index(c) for c in v if c in core.cats] for g, v in whatif.GROUPS.items()}
    # tek kategori şokları (geçmiş getirisi olan kategoriler): grup = kategorinin kendisi, yön +
    kcats = [c for c in core.cats if any(n.startswith(c + " ") and "getiri" in n for n in core.names)] if args.tutarlilik_kategori else []
    for c in kcats:
        grp["cat:" + c] = (c, 1.0)
        gcols[c] = [core.cats.index(c)]
    allowed = {g: (core.M & (core.A[:, cols].sum(1) > 0)[None]).any(1) for g, cols in gcols.items()}
    kinds = SHOCK_KINDS + (["cat"] if kcats else [])
    rng = np.random.default_rng(args.seed + 999)
    n_state = 3 if core.nominal else 2
    B = 2048

    class Consistency(BaseCallback):
        def __init__(self):
            super().__init__(); self.n = 0; self.hist = []

        def _on_rollout_end(self):
            self.n += 1
            pol = model.policy
            for _ in range(args.tutarlilik_adim):
                pid = rng.choice(d["train_ids"], B)
                idx = rng.choice(d["train"], B)
                k = int(rng.integers(len(kinds)))
                name = kinds[k]
                tail = np.column_stack([core.pvec[pid], np.zeros((B, n_state + core.C))])
                x0 = np.column_stack([core.F[idx], tail]).astype(np.float32)
                if name == "cat":
                    name = "cat:" + kcats[int(rng.integers(len(kcats)))]
                    mag = rng.uniform(0.25, 1.0, B) * 0.2 * rng.choice([-1.0, 1.0], B)
                    raw1 = whatif.apply(core.names, d["F_raw"][idx], name, mag)
                    f1 = whatif.squash((raw1 - core.mu) / core.sd, core.squash_mode, core.c)
                else:
                    mag = rng.uniform(0.25, 1.0, B) * SHOCK_RANGE[name] * rng.choice([-1.0, 1.0], B)
                    f1 = core.feats(idx, (np.full(B, k), mag))
                x1 = np.column_stack([f1, tail]).astype(np.float32)
                g, sgn = grp[name]
                ok = torch.as_tensor(allowed[g][pid], device=dev, dtype=torch.float32)
                s = torch.as_tensor(np.sign(mag) * sgn, device=dev, dtype=torch.float32)
                m = core.M[pid]
                p0 = pol.get_distribution(torch.as_tensor(x0, device=dev), action_masks=m).distribution.probs
                p1 = pol.get_distribution(torch.as_tensor(x1, device=dev), action_masks=m).distribution.probs
                dg = (p1 @ A_t[:, gcols[g]]).sum(1) - (p0 @ A_t[:, gcols[g]]).sum(1)
                loss = (ok * (torch.relu(-s * dg) + args.tutarlilik_tepki * torch.relu(0.02 - s * dg))).mean() * args.tutarlilik
                pol.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(pol.parameters(), 0.5)
                pol.optimizer.step()
                self.hist.append(float(loss.detach()) / args.tutarlilik)
            if args.ilerleme and self.n % args.ilerleme == 0:
                emit({"olay": "tutarlilik", "rollout": self.n, "kayip": round(float(np.mean(self.hist[-50:])), 5)})

        def _on_step(self):
            return True
    return Consistency()


def final_report(core, d, act_fn, base, rule_val) -> dict:
    """Son ağırlıklarla: doğrulama (persona izleri, görülmemiş profillerin iki yarısı) ve test. HPO yalnızca doğrulama
    alanlarını kullanır; test alanları yalnızca seçilen ayar için okunur."""
    n_pers = len(d["persona_ids"])
    out = {}
    if rule_val:
        pv = [replay(core, d, f"personaval{i}", act_fn) for i in range(n_pers)]
        out["persona_dogrulama_farki"] = [pv[i]["ortalama_odul"] - rule_val[i] for i in range(n_pers)]
        out["persona_dogrulama_reel_farki"] = [pv[i]["ortalama_reel_kazanc"]
                                               - replay(core, d, f"personaval{i}", lambda o, p: base[p])["ortalama_reel_kazanc"]
                                               for i in range(n_pers)]
    if "val_a_pid" in d:
        out["dogrulama_yarilar"] = [replay(core, d, k, act_fn)["ortalama_odul"] - replay(core, d, k, lambda o, p: base[p])["ortalama_odul"]
                                    for k in ("val_a", "val_b")]
    out["test"] = replay(core, d, "test_hold", act_fn)
    pt = [replay(core, d, f"persona{i}", act_fn) for i in range(n_pers)]
    pr = [replay(core, d, f"persona{i}", lambda o, p: base[p]) for i in range(n_pers)]
    out["persona"] = [x["ortalama_reel_kazanc"] for x in pt]
    out["persona_odul"] = [x["ortalama_odul"] for x in pt]            # profil ödülü (profilin tamamına göre fayda)
    out["taban_persona"] = [x["ortalama_reel_kazanc"] for x in pr]
    out["taban_persona_odul"] = [x["ortalama_odul"] for x in pr]
    first = []
    for i, p in enumerate(d["persona_ids"]):
        o = core.obs(np.array([p]), np.zeros(1, int), np.ones((1, 2)), d[f"persona{i}_idx"][0][:1], np.zeros((1, core.C)))
        a = int(act_fn(o, np.array([p]))[0])
        first.append({j: round(float(core.A[a][j]), 2) for j in range(core.C) if core.A[a][j] > 0})
    out["persona_ilk_karar"] = first
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--adim", type=float, default=3e6, help="toplam ortam adımı")
    ap.add_argument("--ortam", type=int, default=512)
    ap.add_argument("--n-steps", type=int, default=16)
    ap.add_argument("--epoch", type=int, default=2)
    ap.add_argument("--derinlik", type=int, default=4)
    ap.add_argument("--policy-lr", type=float, default=0.05)
    ap.add_argument("--value-lr", type=float, default=0.02)
    ap.add_argument("--ent", type=float, default=0.001)
    ap.add_argument("--degerlendirme", type=int, default=20, help="kaç güncellemede bir doğrulama")
    ap.add_argument("--cihaz", default="cuda")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--persona-oran", type=float, default=0.0, help="persona profilleriyle başlayan bölüm oranı")
    ap.add_argument("--indirim", type=float, default=0.99, help="indirim katsayısı (gamma)")
    ap.add_argument("--ilerleme", type=int, default=500,
                    help="son-agirlik modunda her N rollout'ta ilerleme satırı (adım, süre, kalan süre, bölüm ödülü); 0 = kapalı")
    ap.add_argument("--egri", type=int, default=0,
                    help="son-agirlik modunda öğrenme eğrisi: her N rollout'ta eğitim ve doğrulama dönemlerinde ölçüm (seçim yok)")
    ap.add_argument("--son-agirlik", action="store_true",
                    help="erken durdurma yok: ara değerlendirme/seçim yapılmaz, yalnızca son ağırlıklar değerlendirilir (HPO için)")
    ap.add_argument("--risk-kov", default=None, help="nominal getiri kovaryansı (.npy, testten önceki veri)")
    ap.add_argument("--risk-gamma", type=float, default=0.0, help="temel risk kaçınma (Agresif profilin γ'sı)")
    ap.add_argument("--persona-secim", action="store_true",
                    help="kontrol noktasını personaların doğrulama izlerinde kurala göre farkla seç (kayıp ağır cezalı)")
    ap.add_argument("--algo", choices=["gbrl", "nnppo"], default="gbrl",
                    help="gbrl: NVIDIA GBRL (ağaç, PPO); nnppo: sb3-contrib MaskablePPO (sinir ağı) — aynı ortam ve izler")
    ap.add_argument("--lr", type=float, default=3e-4, help="nnppo öğrenme oranı")
    ap.add_argument("--minibatch", type=int, default=2048, help="nnppo mini-parti")
    ap.add_argument("--gizli", type=int, default=256, help="nnppo gizli katman genişliği (2 katman)")
    ap.add_argument("--katman", type=int, default=2, help="nnppo gizli katman sayısı (aktör ve kritik ayrı)")
    ap.add_argument("--aktivasyon", choices=["tanh", "relu"], default="tanh")
    ap.add_argument("--aksiyon-gomme", type=int, default=0, help=">0: aksiyon gömmeli çıkış (boyut); 0: tam doğrusal çıkış")
    ap.add_argument("--kirpma-turu", choices=["sert", "tanh"], default="sert", help="gösterge girdisi: ±c kesme ya da c·tanh(z/c)")
    ap.add_argument("--kirp", type=float, default=2.0, help="kırpma sınırı c (σ)")
    ap.add_argument("--senaryo-oran", type=float, default=0.0, help="what-if senaryo artırımı: adım başına şok olasılığı")
    ap.add_argument("--momentum", type=float, default=0.15, help="kur/borsa şokunun sonraki 3 aya devam katsayısı κ")
    ap.add_argument("--senaryo-olcek", type=float, default=1.0, help="şok büyüklüğü çarpanı")
    ap.add_argument("--tutarlilik", type=float, default=0.0,
                    help="what-if tutarlılık düzenleyicisi ağırlığı: her rollout sonrası, rastgele eğitim durumları × şoklarda beklenen "
                         "dağılımın ekonomik öncüle ters yönde kaymasını cezalandıran ek gradyan adımları (0 = kapalı)")
    ap.add_argument("--tutarlilik-adim", type=int, default=4, help="rollout başına düzenleyici gradyan adımı")
    ap.add_argument("--buyume", choices=["greedy", "oblivious"], default="greedy",
                    help="GBRL ağaç büyütme: greedy (asimetrik) ya da oblivious (simetrik: her seviyede tek bölme kuralı)")
    ap.add_argument("--monoton", type=int, default=0,
                    help="1: GBRL'de what-if öncülü monoton kısıtlarla (oblivious gerekir; bkz. monotone_triples)")
    ap.add_argument("--tutarlilik-kategori", type=int, default=1,
                    help="1: düzenleyiciye tek kategori getiri şokları da girer (getirisi düşen kategorinin payı artmamalı)")
    ap.add_argument("--tutarlilik-tepki", type=float, default=0.1,
                    help="doğru yönde en az %%2'lik tepkiyi teşvik eden terimin göreli ağırlığı (yalnızca grup izinliyse)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    json.dump(vars(args), open(os.path.join(args.out, "argumanlar.json"), "w"), indent=1)
    d = dict(np.load(args.env))
    core = Core(d)
    core.set_features(args.kirpma_turu, args.kirp, args.senaryo_oran, args.momentum, args.senaryo_olcek)
    if args.risk_kov and args.risk_gamma > 0:
        core.set_risk(np.load(args.risk_kov), args.risk_gamma)
    log = open(os.path.join(args.out, "egitim_log.jsonl"), "w")
    t0 = time.time()

    def emit(row):
        row = {"dk": round((time.time() - t0) / 60, 1), **row}
        log.write(json.dumps(row, ensure_ascii=False) + "\n")
        log.flush()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    # 1) Simülatör doğrulaması: taban kuralı test izinde oynat (Windows adil karşılaştırmasıyla eşleşmeli)
    base = d["base"]
    emit({"olay": "taban_test", **replay(core, d, "test_hold", lambda o, p: base[p])})
    emit({"olay": "taban_dogrulama", **replay(core, d, "val_hold", lambda o, p: base[p])})

    env = make_vec_env(core, d["train_ids"], d["train"], args.ortam, args.seed, float(d["p_cur"]),
                       d["persona_ids"], args.persona_oran)
    n_pers = len(d["persona_ids"])
    rule_val = [replay(core, d, f"personaval{i}", lambda o, p: base[p])["ortalama_odul"] for i in range(n_pers)]         if f"personaval0_pid" in d else None
    if rule_val:
        emit({"olay": "taban_persona_dogrulama", "odul": [round(x, 4) for x in rule_val]})
    rollout = args.ortam * args.n_steps
    if args.algo == "nnppo":
        from sb3_contrib import MaskablePPO
        import torch
        arch = [args.gizli] * args.katman
        model = MaskablePPO("MlpPolicy", env, n_steps=args.n_steps, batch_size=args.minibatch, n_epochs=args.epoch,
                            learning_rate=args.lr, gamma=args.indirim, gae_lambda=0.95, ent_coef=args.ent, vf_coef=0.5,
                            policy_kwargs={"net_arch": {"pi": arch, "vf": arch},
                                           "activation_fn": torch.nn.ReLU if args.aktivasyon == "relu" else torch.nn.Tanh},
                            device=args.cihaz, seed=args.seed, verbose=0)
        if args.aksiyon_gomme:
            pol = model.policy
            pol.action_net = action_embed_head(pol.mlp_extractor.latent_dim_pi, core.A, args.aksiyon_gomme).to(pol.device)
            pol.optimizer = pol.optimizer_class(pol.parameters(), lr=model.lr_schedule(1), **pol.optimizer_kwargs)
        n_par = sum(p.numel() for p in model.policy.parameters())
        n_out = sum(p.numel() for p in model.policy.action_net.parameters())
        emit({"olay": "ag", "parametre": int(n_par), "cikis_katmani": int(n_out), "katman": arch, "aktivasyon": args.aktivasyon,
              "aksiyon_gomme": args.aksiyon_gomme})
    else:
        from algos.ppo import PPO_GBRL
        mono = {}
        if args.monoton:
            assert args.buyume == "oblivious" and core.raw, "monoton kısıt oblivious ağaç ve ham göstergeli ortam ister"
            import gbrl.learners.gbt_learner as GL
            trip = monotone_triples(core)
            orig = GL.process_monotonic_constraints
            # Python API özellik başına tek yön alır; aynı özellik farklı aksiyonlarda zıt yönlü olduğundan üçlüleri doğrudan ver
            def process(constraints, policy_dim, input_dim):
                return trip if constraints == mono["monotonic_constraints"] else orig(constraints, policy_dim, input_dim)

            GL.process_monotonic_constraints = process
            mono = {"monotonic_constraints": {int(trip[0][0]): ("increasing", [int(trip[1][0])])}}   # yer tutucu; üçlüler process'ten
            emit({"olay": "monoton", "kisit": int(len(trip[0])), "ozellik": int(len(set(trip[0].tolist()))),
                  "aksiyon": int(len(set(trip[1].tolist())))})
        model = PPO_GBRL(env=env, n_steps=args.n_steps, batch_size=rollout, n_epochs=args.epoch, gamma=args.indirim, gae_lambda=0.95,
                         ent_coef=args.ent, vf_coef=0.5, total_n_steps=int(args.adim), use_masking=True, device=args.cihaz,
                         seed=args.seed, verbose=0, _init_setup_model=True,
                         policy_kwargs={"shared_tree_struct": True,
                                        "tree_struct": {"max_depth": args.derinlik, "n_bins": 256, "min_data_in_leaf": 0, "par_th": 2,
                                                        "grow_policy": args.buyume},
                                        "tree_optimizer": {"params": {"control_variates": False, "split_score_func": "cosine",
                                                                      "generator_type": "Quantile", "feature_weights": None, **mono},
                                                           "policy_optimizer": {"policy_algo": "SGD", "policy_lr": args.policy_lr},
                                                           "value_optimizer": {"value_algo": "SGD", "value_lr": args.value_lr}}})

    def act_fn(obs, pid):
        out = []
        for s in range(0, len(obs), 8192):
            if args.algo == "nnppo":
                a, _ = model.predict(obs[s:s + 8192], deterministic=True, action_masks=core.M[pid[s:s + 8192]])
            else:
                dist = model.policy.get_distribution(obs=obs[s:s + 8192], action_masks=core.M[pid[s:s + 8192]])
                a = dist.get_actions(deterministic=True)                   # maskeli dağılımın en olası aksiyonu
            out.append(np.asarray(a.detach().cpu() if hasattr(a, "detach") else a).astype(int).ravel())
        a = np.concatenate(out)
        return np.where(core.M[pid, a], a, base[pid])

    def probs_fn(obs, pid):
        import torch
        out = []
        with torch.no_grad():
            for s in range(0, len(obs), 4096):
                if args.algo == "nnppo":
                    o = torch.as_tensor(obs[s:s + 4096], device=model.policy.device)
                    dist = model.policy.get_distribution(o, action_masks=core.M[pid[s:s + 4096]])
                else:
                    dist = model.policy.get_distribution(obs=obs[s:s + 4096], action_masks=core.M[pid[s:s + 4096]])
                pr = dist.distribution.probs
                out.append((pr.detach().cpu().numpy() if hasattr(pr, "detach") else np.asarray(pr)).astype(np.float64))
        return np.concatenate(out)

    from stable_baselines3.common.callbacks import BaseCallback
    best = {"odul": -np.inf}

    class Eval(BaseCallback):
        def __init__(self):
            super().__init__()
            self.n = 0

        def _on_rollout_end(self):
            self.n += 1
            if self.n % args.degerlendirme:
                return
            v = replay(core, d, "val_hold", act_fn)
            score, parts = v["ortalama_odul"], None
            pdiff = None
            if args.persona_secim and rule_val:
                # her personanın doğrulama izlerinde kurala göre ödül farkı; kayıplar 3 kat cezalı
                pdiff = [replay(core, d, f"personaval{i}", act_fn)["ortalama_odul"] - rule_val[i] for i in range(n_pers)]
            if pdiff is not None:
                score = float(np.mean(pdiff) + 3 * np.sum(np.minimum(pdiff, 0)))
                parts = None
            elif "val_a_pid" in d:   # doğrulama döneminin iki yarısının en kötüsü (Windows eğitimiyle aynı ölçüt)
                parts = [replay(core, d, k, act_fn)["ortalama_odul"] for k in ("val_a", "val_b")]
                score = min(parts)
            row = {"guncelleme": self.n, "adim": int(self.num_timesteps), "dogrulama_odul": round(v["ortalama_odul"], 4),
                   "dogrulama_reel": round(v["ortalama_reel_kazanc"], 4), "dogrulama_anapara_alti": round(v["anapara_alti_bitis_olasiligi"], 4),
                   "secim_skoru": round(score, 4), **({"dogrulama_yarilar": [round(x, 4) for x in parts]} if parts else {}),
                   **({"persona_dogrulama_farki": [round(x, 4) for x in pdiff]} if pdiff is not None else {})}
            if score > best["odul"]:
                best.update(odul=score, guncelleme=self.n)
                te = replay(core, d, "test_hold", act_fn)
                per = [replay(core, d, f"persona{i}", act_fn)["ortalama_reel_kazanc"] for i in range(len(d["persona_ids"]))]
                # personaların test döneminin ilk günündeki (nakitten) kararı: hangi dağılımı seçtiği
                first = []
                for i, p in enumerate(d["persona_ids"]):
                    idx0 = d[f"persona{i}_idx"][0][:1]
                    o = core.obs(np.array([p]), np.zeros(1, int), np.ones((1, 2)), idx0, np.zeros((1, core.C)))
                    a = int(act_fn(o, np.array([p]))[0])
                    first.append({j: round(float(core.A[a][j]), 2) for j in range(core.C) if core.A[a][j] > 0})
                best.update(test=te, persona=per, persona_ilk_karar=first)
                try:   # en iyi noktayı kaydet: GBRL → kendi biçimi + C başlığı (bağımlılıksız çıkarım), NN-PPO → sb3 zip
                    if args.algo == "gbrl":
                        model.policy.model.save_learner(os.path.join(args.out, "en_iyi_gbrl"))
                        model.policy.model.export_learner(os.path.join(args.out, "en_iyi_gbrl_c"))
                    else:
                        model.save(os.path.join(args.out, "en_iyi_nnppo"))
                except Exception as e:  # noqa: BLE001
                    emit({"olay": "kaydetme_hatasi", "hata": f"{type(e).__name__}: {e}"})
                row["en_iyi"] = True
            emit(row)

        def _on_step(self):
            return True

    if args.son_agirlik:
        cb = None
        if args.egri:
            from stable_baselines3.common.callbacks import BaseCallback
            # eğitim dönemi izleri: personalar + görülmemiş profiller, eğitim havuzunda (doğrulama/test izleriyle aynı biçim)
            mem = np.zeros(len(core.real) + core.step + 1, bool)
            mem[d["train"]] = True
            def paths(ids, n_per, seed):
                g = np.random.default_rng(seed)
                pid = np.repeat(ids, n_per)
                T = np.minimum(g.integers(core.hmin[pid], core.hmax[pid] + 1), 4)
                idx = g.choice(d["train"], len(pid)); path = [idx]
                for _ in range(int(T.max()) - 1):
                    idx = core.next_idx(idx, mem, d["train"], g); path.append(idx)
                return {"pid": pid, "T": T, "idx": np.array(path)}
            for k, v in paths(d["hold_ids"], 8, 101).items():
                d[f"train_hold_{k}"] = v
            for i, pp in enumerate(d["persona_ids"]):
                for k, v in paths(np.array([pp]), 400, 200 + i).items():
                    d[f"trainpersona{i}_{k}"] = v
            rule_tr = [replay(core, d, f"trainpersona{i}", lambda o, p: base[p])["ortalama_odul"] for i in range(n_pers)]
            rule_trh = replay(core, d, "train_hold", lambda o, p: base[p])["ortalama_odul"]
            rule_vh = replay(core, d, "val_hold", lambda o, p: base[p])["ortalama_odul"]

            class Curve(BaseCallback):
                def __init__(self):
                    super().__init__(); self.n = 0

                def _on_rollout_end(self):
                    self.n += 1
                    if self.n % args.egri:
                        return
                    tr = [replay(core, d, f"trainpersona{i}", act_fn)["ortalama_odul"] - rule_tr[i] for i in range(n_pers)]
                    va = [replay(core, d, f"personaval{i}", act_fn)["ortalama_odul"] - rule_val[i] for i in range(n_pers)]
                    emit({"olay": "egri", "rollout": self.n, "adim": int(self.num_timesteps),
                          "egitim_persona_farki": [round(x, 4) for x in tr], "dogrulama_persona_farki": [round(x, 4) for x in va],
                          "egitim_gorulmemis_farki": round(replay(core, d, "train_hold", act_fn)["ortalama_odul"] - rule_trh, 4),
                          "dogrulama_gorulmemis_farki": round(replay(core, d, "val_hold", act_fn)["ortalama_odul"] - rule_vh, 4)})

                def _on_step(self):
                    return True
            cb = Curve()
        from stable_baselines3.common.callbacks import BaseCallback, CallbackList

        class Progress(BaseCallback):
            """Hafif ilerleme logu: değerlendirme yapmaz, yalnızca adım/süre/bölüm ödülü yazar."""
            def __init__(self):
                super().__init__(); self.n = 0

            def _on_rollout_end(self):
                self.n += 1
                if not args.ilerleme or self.n % args.ilerleme:
                    return
                el = time.time() - t0
                done = self.num_timesteps / args.adim
                buf = [e["r"] for e in self.model.ep_info_buffer] if self.model.ep_info_buffer else []
                emit({"olay": "ilerleme", "adim": int(self.num_timesteps), "yuzde": round(100 * done, 1),
                      "kalan_dk": round(el / max(done, 1e-9) * (1 - done) / 60, 1),
                      "ort_bolum_odulu": round(float(np.mean(buf)), 4) if buf else None})

            def _on_step(self):
                return True
        cbs = [Progress()] + ([cb] if cb else [])
        if args.tutarlilik > 0 and args.algo == "nnppo":
            cbs.append(consistency_callback(core, d, model, args, emit))
        model.learn(total_timesteps=int(args.adim), callback=CallbackList(cbs))
        out = final_report(core, d, act_fn, base, rule_val)
        if core.raw:
            out["whatif_dogrulama"] = whatif_report(core, d, probs_fn, d["val"])
            out["whatif_test"] = whatif_report(core, d, probs_fn, d["test"], every=2)
        emit({"olay": "bitti", "mod": "son_agirlik", **{k: v for k, v in out.items() if k != "persona_ilk_karar"}})
        json.dump(out, open(os.path.join(args.out, "sonuc.json"), "w"), ensure_ascii=False, indent=1, default=float)
        try:
            if args.algo == "nnppo":
                model.save(os.path.join(args.out, "model_nnppo"))
            else:
                model.policy.model.save_learner(os.path.join(args.out, "model_gbrl"))
        except Exception as e:  # noqa: BLE001
            emit({"olay": "kaydetme_hatasi", "hata": f"{type(e).__name__}: {e}"})
        return
    model.learn(total_timesteps=int(args.adim), callback=Eval())
    emit({"olay": "bitti", "en_iyi_guncelleme": best.get("guncelleme"), "dogrulama_odul": best["odul"],
          "test": best.get("test"), "persona": best.get("persona")})
    json.dump({k: v for k, v in best.items()}, open(os.path.join(args.out, "sonuc.json"), "w"), ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()
