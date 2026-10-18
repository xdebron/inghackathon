"""Kategori DQN'inin PyTorch (CUDA) eğitimi. Mantık rl_category.py'deki numpy sürümüyle aynıdır; veri, deneyim tamponu,
ortam adımları ve hedef hesapları GPU'da tensör olarak yürür. Ağırlıklar numpy çıkarımının okuduğu biçimde
(W0, b0, ... ; W: girdi × çıktı) kaydedilir, yani çevrimiçi kullanım torch gerektirmez."""
from __future__ import annotations

import numpy as np
import torch
from torch import nn

from . import rl_category as rc


BSCALE = 10.0          # inanç başlığı getirileri bu ölçekte öğrenir (3 aylık reel getiri ~ ±0,1 → ±1)
LOG_SD_RANGE = (-5.0, 2.0)


class Net(nn.Module):
    """Q başlığı (aksiyon değerleri) + isteğe bağlı inanç başlığı (beklenen getiri).

    İnanç başlığı ortak gövdenin (iki gizli katman) üzerinde, her kategori için bir sonraki adımın vergi sonrası reel
    getirisinin ortalamasını ve log standart sapmasını tahmin eder (Gauss olabilirlik kaybı). Q kaybına yardımcı görev olarak
    eklenir: gövdeyi getiri yapısını temsil etmeye zorlar ve RM'e modelin "piyasadan ne beklediğini" gösterir."""

    def __init__(self, d_in: int, hidden: int, d_out: int, n_cat: int = 0):
        super().__init__()
        self.l0, self.l1, self.l2 = nn.Linear(d_in, hidden), nn.Linear(hidden, hidden), nn.Linear(hidden, d_out)
        self.lb = nn.Linear(hidden, 2 * n_cat) if n_cat else None
        for l in (self.l0, self.l1):
            nn.init.kaiming_normal_(l.weight, nonlinearity="relu")
            nn.init.zeros_(l.bias)
        nn.init.normal_(self.l2.weight, std=0.01)
        nn.init.zeros_(self.l2.bias)
        if self.lb is not None:
            nn.init.normal_(self.lb.weight, std=0.01)
            nn.init.zeros_(self.lb.bias)

    @property
    def n_cat(self) -> int:
        return self.lb.out_features // 2 if self.lb is not None else 0

    def trunk(self, x):
        return torch.relu(self.l1(torch.relu(self.l0(x))))

    def forward(self, x):
        return self.l2(self.trunk(x))

    def belief_from(self, h):
        """Gövde çıktısından (ortalama, log std), BSCALE ölçeğinde."""
        mu, log_sd = self.lb(h).chunk(2, dim=-1)
        return mu, torch.clamp(log_sd, *LOG_SD_RANGE)

    def belief(self, x):
        """Gerçek ölçekte beklenen getiri ve standart sapma (kategori başına)."""
        mu, log_sd = self.belief_from(self.trunk(x))
        return mu / BSCALE, torch.exp(log_sd) / BSCALE

    def numpy_state(self) -> dict:
        """numpy MLP biçimi: W_i = (girdi × çıktı). İnanç başlığı Wb/bb anahtarlarında."""
        out = {f"W{i}": l.weight.detach().T.cpu().numpy().astype(np.float64) for i, l in enumerate((self.l0, self.l1, self.l2))} | \
              {f"b{i}": l.bias.detach().cpu().numpy().astype(np.float64) for i, l in enumerate((self.l0, self.l1, self.l2))}
        if self.lb is not None:
            out |= {"Wb": self.lb.weight.detach().T.cpu().numpy().astype(np.float64),
                    "bb": self.lb.bias.detach().cpu().numpy().astype(np.float64)}
        return out

    def load_numpy_state(self, w) -> None:
        with torch.no_grad():
            layers = [(f"W{i}", f"b{i}", l) for i, l in enumerate((self.l0, self.l1, self.l2))]
            if self.lb is not None and "Wb" in w:
                layers.append(("Wb", "bb", self.lb))
            for kw, kb, l in layers:
                l.weight.copy_(torch.as_tensor(w[kw].T, dtype=torch.float32))
                l.bias.copy_(torch.as_tensor(w[kb], dtype=torch.float32))


def belief_nll(mu, log_sd, y):
    """Gauss negatif log-olabilirlik (sabit terim hariç); y, mu, log_sd BSCALE ölçeğinde."""
    return (0.5 * (y - mu) ** 2 * torch.exp(-2 * log_sd) + log_sd).mean()


class TorchSim:
    def __init__(self, sim: rc.Sim, device: str):
        f = lambda a, dt=torch.float32: torch.as_tensor(np.asarray(a), dtype=dt, device=device)
        self.dev = device
        self.real, self.F = f(sim.ds["real"]), f(sim.ds["features"])
        self.real_nom = f(sim.ds["nominal_net"])
        self.nominal, self.pen_cap = sim.nominal, float(sim.pen_cap)
        self.A, self.M = f(sim.A), torch.as_tensor(sim.M, device=device)
        self.pvec = f(sim.pvec)
        self.hmin, self.hmax = f(sim.hmin, torch.long), f(sim.hmax, torch.long)
        self.lam, self.fixed = f(sim.lam), f(sim.fixed)
        self.cost, self.step, self.L = sim.cost, sim.step, sim.L
        self.C = sim.A.shape[1]
        self.t_cap = rc.decision_config()["rl"]["durum_max_adim"]
        self.noise = 0.0          # eğitimde göstergelere eklenen gürültü (std, normalize ölçekte)
        self.gen = None

    def set_feature_policy(self, clip: float | None = None, zero: bool = False):
        """Göstergelerin kullanımı: kırpma (ekstrapolasyonu sınırlar) veya tamamen kapatma (ablasyon)."""
        if zero:
            self.F = torch.zeros_like(self.F)
        elif clip:
            self.F = torch.clamp(self.F, -clip, clip)

    def x(self, pid, t, w, idx, prev):
        F = self.F[idx]
        if self.noise > 0:
            F = F + self.noise * torch.randn(F.shape, device=self.dev, generator=self.gen)
        wealth = (w - 1) * 5 if self.nominal else ((w[:, 0] - 1) * 5)[:, None]   # w: (B, 2) = [reel, nominal]
        return torch.cat([F, self.pvec[pid], (torch.clamp(t, max=self.t_cap).float() / self.t_cap)[:, None],
                          wealth, prev], dim=1)

    def p_continue(self, pid, t):
        lo, hi = self.hmin[pid], self.hmax[pid]

        def alive(k):
            return torch.where(k < lo, hi - lo + 1, torch.clamp(hi - torch.maximum(k, lo) + 1, min=0))
        a_now, a_next = alive(t + 1), alive(t + 2)
        return torch.where(a_now > 0, a_next.float() / torch.clamp(a_now, min=1).float(), torch.zeros_like(a_now, dtype=torch.float32))

    def next_idx(self, idx, member, pool, gen):
        nxt = idx + self.step
        inside = nxt < member.shape[0]
        ok = inside & member[torch.clamp(nxt, max=member.shape[0] - 1)] & (torch.rand(idx.shape, device=self.dev, generator=gen) >= 1 / self.L)
        rnd = pool[torch.randint(len(pool), idx.shape, device=self.dev, generator=gen)]
        return torch.where(ok, nxt, rnd)

    def outcome(self, pid, w, prev, idx, acts):
        """rl_category.Sim.outcome ile aynı: servet [reel, nominal], ceza tabanı config'e göre, adım cezası tavanlı."""
        r, rn = self.real[idx], self.real_nom[idx]
        Wt = self.A[acts]
        if Wt.dim() == 3:
            r_, rn_, w_, prev_ = r[:, None, :], rn[:, None, :], w[:, None, :], prev[:, None, :]
            lam, fixed = self.lam[pid][:, None], self.fixed[pid][:, None]
        else:
            r_, rn_, w_, prev_, lam, fixed = r, rn, w, prev, self.lam[pid], self.fixed[pid]
        cash = 1 - prev_.sum(-1)
        turnover = 0.5 * (Wt - prev_).abs().sum(-1) + 0.5 * cash
        wr, wn = w_[..., 0], w_[..., 1]
        wr_new = wr * (1 + (Wt * r_).sum(-1)) - wr * self.cost * turnover
        wn_new = wn * (1 + (Wt * rn_).sum(-1)) - wn * self.cost * turnover
        base = wn_new if self.nominal else wr_new
        pen = torch.where(base < 1, torch.clamp(lam * (1 - base) + fixed, max=self.pen_cap), torch.zeros_like(base))
        reward = (wr_new - wr) - pen
        d = Wt * (1 + r_)
        d = d / torch.clamp(d.sum(-1, keepdim=True), min=1e-9)
        return torch.stack([wr_new, wn_new], dim=-1), reward, d

    def random_valid(self, pid, k, gen):
        s = torch.rand((len(pid), self.M.shape[1]), device=self.dev, generator=gen)
        s = torch.where(self.M[pid], s, torch.full_like(s, -1.0))
        return torch.topk(s, k, dim=1).indices


SCALE = 10.0


def train(net: Net, ts: TorchSim, train_ids: np.ndarray, pool: np.ndarray, updates: int, c: dict, seed: int,
          on_eval=None, eval_every: int = 5000, log=print) -> list[dict]:
    dev = ts.dev
    gen = torch.Generator(device=dev).manual_seed(seed)
    torch.manual_seed(seed)
    target = Net(net.l0.in_features, net.l0.out_features, net.l2.out_features, net.n_cat).to(dev)
    b_w = c.get("inanc_agirligi", 1.0) if net.n_cat else 0.0
    target.load_state_dict(net.state_dict())
    opt = torch.optim.AdamW(net.parameters(), lr=c["ogrenme_orani"], weight_decay=c.get("agirlik_cokmesi", 0.0))
    ts.gen = gen
    E, B, K, cap, sync = c["paralel_bolum"], c["batch"], c["hedef_aksiyon"], c["tampon"], c["hedef_senkron"]
    tid = torch.as_tensor(train_ids, device=dev)
    pool_t = torch.as_tensor(pool, device=dev)
    member = torch.zeros(ts.real.shape[0] + ts.step + 1, dtype=torch.bool, device=dev)
    member[pool_t] = True
    buf_pid = torch.zeros(cap, dtype=torch.long, device=dev)
    buf_t = torch.zeros(cap, dtype=torch.long, device=dev)
    buf_w = torch.zeros((cap, 2), device=dev)
    buf_idx = torch.zeros(cap, dtype=torch.long, device=dev)
    buf_prev = torch.zeros((cap, ts.C), device=dev)
    buf_a = torch.zeros(cap, dtype=torch.long, device=dev)
    n_buf, head = 0, 0

    def new_episodes(n):
        pid = tid[torch.randint(len(tid), (n,), device=dev, generator=gen)]
        span = (ts.hmax[pid] - ts.hmin[pid] + 1).float()
        T = ts.hmin[pid] + (torch.rand(n, device=dev, generator=gen) * span).long()
        return pid, T

    pid, T = new_episodes(E)
    t = torch.zeros(E, dtype=torch.long, device=dev)
    w = torch.ones((E, 2), device=dev)
    idx = pool_t[torch.randint(len(pool_t), (E,), device=dev, generator=gen)]
    p_cur = c.get("mevcut_portfoy_olasiligi", rc.cfg().get("mevcut_portfoy_olasiligi", 0.0))

    def start_prev(n):
        # Bölümlerin bir kısmı rastgele bir mevcut portföyle başlar (izinsiz kategoriler dahil): model geçiş maliyetini
        # ve mevcut pozisyondan çıkmaya değip değmediğini öğrenir. Diğerleri nakitten başlar.
        rnd = ts.A[torch.randint(ts.A.shape[0], (n,), device=dev, generator=gen)]
        use = (torch.rand(n, device=dev, generator=gen) < p_cur)[:, None]
        return torch.where(use, rnd, torch.zeros_like(rnd))

    prev = start_prev(E)
    ep_rew = torch.zeros(E, device=dev)
    done_sum, done_n, loss_sum, loss_n, curve = 0.0, 0, 0.0, 0, []
    bloss_sum = 0.0
    for u in range(updates):
        eps = max(c["eps_son"], 1.0 - u / (c["eps_azalma_orani"] * updates))
        with torch.no_grad():
            q = net(ts.x(pid, t, w, idx, prev))
            q = torch.where(ts.M[pid], q, torch.full_like(q, -torch.inf))
            greedy = q.argmax(1)
            rnd = ts.random_valid(pid, 1, gen)[:, 0]
            a = torch.where(torch.rand(E, device=dev, generator=gen) < eps, rnd, greedy)
            sl = (torch.arange(E, device=dev) + head) % cap
            buf_pid[sl], buf_t[sl], buf_w[sl], buf_idx[sl], buf_prev[sl], buf_a[sl] = pid, t, w, idx, prev, a
            head, n_buf = (head + E) % cap, min(cap, n_buf + E)
            w2, r, d = ts.outcome(pid, w, prev, idx, a)
            ep_rew += r
            t, w, prev = t + 1, w2, d
            idx = ts.next_idx(idx, member, pool_t, gen)
            done = t >= T
            nd = int(done.sum())
            if nd:
                done_sum += float(ep_rew[done].sum())
                done_n += nd
                npid, nT = new_episodes(nd)
                pid[done], T[done] = npid, nT
                t[done], w[done], ep_rew[done] = 0, 1.0, 0.0
                prev[done] = start_prev(nd)
                idx[done] = pool_t[torch.randint(len(pool_t), (nd,), device=dev, generator=gen)]
        if n_buf >= B:
            j = torch.randint(n_buf, (B,), device=dev, generator=gen)
            bp, bt, bw, bi, bprev, ba = buf_pid[j], buf_t[j], buf_w[j], buf_idx[j], buf_prev[j], buf_a[j]
            with torch.no_grad():
                acts = torch.cat([ba[:, None], ts.random_valid(bp, K - 1, gen)], dim=1)          # (B,K)
                wn, rw, dn = ts.outcome(bp, bw, bprev, bi, acts)
                ni = ts.next_idx(bi, member, pool_t, gen)
                Xn = ts.x(bp.repeat_interleave(K), (bt + 1).repeat_interleave(K), wn.reshape(-1, 2),
                          ni.repeat_interleave(K), dn.reshape(-1, ts.C))
                qn = target(Xn)
                qn = torch.where(ts.M[bp].repeat_interleave(K, dim=0), qn, torch.full_like(qn, -torch.inf)).max(1).values
                y = (rw + ts.p_continue(bp, bt)[:, None] * qn.reshape(B, K) / SCALE) * SCALE
            ts.noise = c.get("ozellik_gurultu", 0.0)      # yalnızca öğrenme girdisinde gürültü (veri artırma)
            h = net.trunk(ts.x(bp, bt, bw, bi, bprev))
            ts.noise = 0.0
            pred = net.l2(h).gather(1, acts)
            loss = nn.functional.smooth_l1_loss(pred, y)
            if b_w:
                # İnanç başlığı: bu durumdan başlayan adımın gerçekleşen reel getirisi (kategori başına) hedeftir.
                # Göstergeler karar anında bilinen gecikmeli değerler olduğundan geleceğe bakma yoktur.
                mu, log_sd = net.belief_from(h)
                bl = belief_nll(mu, log_sd, ts.real[bi] * BSCALE)
                loss = loss + b_w * bl
                bloss_sum += float(bl.detach()) if (u + 1) % 100 == 0 else 0.0
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(net.parameters(), 10.0)
            opt.step()
            loss_sum += float(loss.detach()) if (u + 1) % 100 == 0 else 0.0
            loss_n += 1 if (u + 1) % 100 == 0 else 0
            if (u + 1) % sync == 0:
                target.load_state_dict(net.state_dict())
        if (u + 1) % eval_every == 0:
            row = {"guncelleme": u + 1, "eps": round(eps, 3),
                   "ortalama_bolum_odulu": round(done_sum / max(1, done_n), 4), "kayip": round(loss_sum / max(1, loss_n), 5)}
            if b_w:
                row["inanc_kaybi"] = round(bloss_sum / max(1, loss_n), 4)
            done_sum, done_n, loss_sum, loss_n, bloss_sum = 0.0, 0, 0.0, 0, 0.0
            if on_eval:
                row.update(on_eval(u + 1))
            curve.append(row)
            log(row)
    return curve


def evaluate(net: Net | None, ts: TorchSim, sim: rc.Sim, ids: np.ndarray, pool: np.ndarray, n_per: int, seed: int,
             policy: str = "dqn") -> dict:
    """rl_category.evaluate ile aynı ölçütler; GPU'da vektörel."""
    dev = ts.dev
    gen = torch.Generator(device=dev).manual_seed(seed)
    pool_t = torch.as_tensor(pool, device=dev)
    member = torch.zeros(ts.real.shape[0] + ts.step + 1, dtype=torch.bool, device=dev)
    member[pool_t] = True
    pid = torch.as_tensor(np.repeat(ids, n_per), device=dev)
    E = len(pid)
    span = (ts.hmax[pid] - ts.hmin[pid] + 1).float()
    T = rc.eval_horizon(ts.hmin[pid] + (torch.rand(E, device=dev, generator=gen) * span).long())
    idx = pool_t[torch.randint(len(pool_t), (E,), device=dev, generator=gen)]
    t, w, prev = torch.zeros(E, dtype=torch.long, device=dev), torch.ones((E, 2), device=dev), torch.zeros((E, ts.C), device=dev)
    below, active = torch.zeros(E, dtype=torch.bool, device=dev), torch.ones(E, dtype=torch.bool, device=dev)
    total = torch.zeros(E, device=dev)
    static = None
    if policy != "dqn":
        static_np = _static_choice(sim, np.repeat(ids, n_per), pool, policy)
        static = torch.as_tensor(static_np, device=dev)
    with torch.no_grad():
        for _ in range(int(T.max())):
            if policy == "dqn":
                q = net(ts.x(pid, t, w, idx, prev))
                a = torch.where(ts.M[pid], q, torch.full_like(q, -torch.inf)).argmax(1)
            else:
                a = static
            w2, r, d = ts.outcome(pid, w, prev, idx, a)
            total = torch.where(active, total + r, total)
            w = torch.where(active[:, None], w2, w)
            prev = torch.where(active[:, None], d, prev)
            below |= active & (w[:, 0] < 1)
            t = t + 1
            active &= t < T
            idx = ts.next_idx(idx, member, pool_t, gen)
    g = (w[:, 0] - 1).cpu().numpy()
    gn = (w[:, 1] - 1).cpu().numpy()
    return {"ortalama_reel_kazanc": float(g.mean()), "medyan_reel_kazanc": float(np.median(g)),
            "kotu_yuzde5": float(np.percentile(g, 5)), "anapara_alti_bitis_olasiligi": float((g < 0).mean()),
            "nominal_anapara_alti_bitis_olasiligi": float((gn < 0).mean()),
            "herhangi_an_anapara_alti_olasiligi": float(below.float().mean()), "ortalama_odul": float(total.mean())}


def _static_choice(sim: rc.Sim, pid: np.ndarray, pool: np.ndarray, policy: str) -> np.ndarray:
    A, C = sim.A, sim.A.shape[1]
    vol = sim.ds["real"][pool].std(axis=0)
    out = np.zeros(len(pid), int)
    for p_ in np.unique(pid):
        ok = np.where(sim.M[p_])[0]
        if policy == "esit":
            allowed_c = (A[ok] > 0).any(axis=0)
            goal = allowed_c / max(1, allowed_c.sum())
        elif policy == "en_guvenli":
            out[pid == p_] = ok[np.argmin(A[ok] @ vol)]
            continue
        else:
            out[pid == p_] = rc.baseline_action(A, sim.M[p_], sim.ds["cats"])
            continue
        out[pid == p_] = ok[np.argmin(np.abs(A[ok] - goal).sum(axis=1))]
    return out


def belief_report(net: Net, ts: TorchSim, pool: np.ndarray, base_pool: np.ndarray, cats: list[str]) -> dict:
    """İnanç başlığının kalibrasyonu (verilen havuzda): kategori başına tahmin–gerçekleşen korelasyonu, hata, saf tahmin
    (eğitim ortalaması) hatası, yön isabeti ve ±1σ aralığının kapsama oranı (iyi kalibrasyonda ~%68).
    İnanç piyasaya bağlıdır; profil/mevcut portföy girdisi sabit (ilk profil, nakit, t=0) tutulur."""
    if not net.n_cat or len(pool) == 0:
        return {}
    dev = ts.dev
    idx = torch.as_tensor(pool, device=dev)
    n = len(idx)
    pid = torch.zeros(n, dtype=torch.long, device=dev)
    with torch.no_grad():
        mu, sd = net.belief(ts.x(pid, torch.zeros(n, dtype=torch.long, device=dev), torch.ones((n, 2), device=dev), idx,
                                 torch.zeros((n, ts.C), device=dev)))
    mu, sd = mu.cpu().numpy(), sd.cpu().numpy()
    y = ts.real[idx].cpu().numpy()
    naive = ts.real[torch.as_tensor(base_pool, device=dev)].mean(0).cpu().numpy()
    out = {}
    for j, cat in enumerate(cats):
        ok = mu[:, j].std() > 1e-9 and y[:, j].std() > 1e-9
        corr = float(np.corrcoef(mu[:, j], y[:, j])[0, 1]) if ok else None
        out[cat] = {"korelasyon": None if corr is None or not np.isfinite(corr) else round(corr, 3),
                    "hata_rmse": round(float(np.sqrt(((mu[:, j] - y[:, j]) ** 2).mean())), 4),
                    "saf_tahmin_rmse": round(float(np.sqrt(((naive[j] - y[:, j]) ** 2).mean())), 4),
                    "yon_isabeti": round(float((np.sign(mu[:, j]) == np.sign(y[:, j])).mean()), 3),
                    "kapsama_1sigma": round(float((np.abs(y[:, j] - mu[:, j]) <= sd[:, j]).mean()), 3),
                    "ortalama_tahmin": round(float(mu[:, j].mean()), 4), "ortalama_gerceklesen": round(float(y[:, j].mean()), 4)}
    return {"ornek": int(n), "kategoriler": out,
            "not": "Örtüşen 3 aylık pencereler; bağımsız gözlem sayısı yaklaşık ornek/63. Saf tahmin: eğitim dönemi ortalaması."}
