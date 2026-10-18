"""Pekiştirmeli öğrenme (RL) ile dinamik dağılım: her vade sonunda kısa listedeki ürünler arasında yeniden karar.

Ortam
  - Ürün evreni: kural motorunun kısa listesi (sert kısıtlar korunur). Ajan yalnızca izinli ağırlık ızgarasından seçer
    (action masking): ağırlıklar `izgara` adımlı, toplamı 1; her fon ≤ `fon_basina_max_agirlik`; yoğunlaşma kısıtı
    varsa her ürün ≤ `yogunlasma_max_agirlik`.
  - Karar adımı: `adim_is_gunu` (≈3 ay). Vadesi bu adımdan uzun mevduat kilitli sayılır ve RL evrenine girmez.
  - Senaryolar sıralıdır (durağan blok bootstrap): çeyrekler tarihteki sırasıyla ilerler, ortalama `blok_ceyrek`
    çeyrekte bir rastgele başka bir tarihe atlanır. Her kararın durumu o tarihte bilinen piyasa göstergeleridir,
    ödülü o tarihten sonra gerçekleşen getiridir (geleceğe bakış yok).
  - Getiriler: fonlar TEFAS fiyatından (yönetim ücreti fiyata yansımış), mevduat güncel sözleşme oranından; pozitif
    kazanca ürün kategorisinin stopajı; aynı dönemin gerçek enflasyonuyla (EVDS TÜFE endeksi) reel getiriye çevrilir.
  - Ufuk persona ufkuna göre rastgeledir ve ajana gösterilmez; Q hedefinde beklenen devam olasılığıyla yer alır.
  - Ödül: reel servet değişimi − anapara cezası (persona anapara tercihine göre) − işlem maliyeti.

Ajanlar
  - Tablo tabanlı Q-öğrenme: durum (geçen adım, reel servet bandı). Piyasa göstergelerini görmez; karşılaştırma tabanı.
  - DQN (numpy MLP): durum = piyasa göstergeleri + geçen adım + reel servet + mevcut dağılım. Adam, deneyim tamponu,
    hedef ağ. Piyasa ajanın kararından bağımsız (dışsal) olduğu için her geçişte tüm aksiyonların hedefi hesaplanır.
  - Seçim eğitim döneminin son kısmı (doğrulama) üzerinde yapılır; test dönemi seçimde kullanılmaz.

Bu modül bir getiri tahmini üretmez; geçmiş verilerle yapılan bir simülasyonla RM'e karar desteği sunar.
"""
from __future__ import annotations

import itertools
from datetime import date

import numpy as np
import pandas as pd

from .config import decision_config
from .llm import fmt_pct
from .sources import evds, tefas_import

CPI_CODE = "TP.GENENDEKS.T1"
DEPOSIT_CODE = "TP.TRYTAS.MT01"
RL_NAME, DQN_NAME, TAB_NAME = "RL politikası (dinamik)", "DQN (sinir ağı)", "Tablo tabanlı Q"


def cfg() -> dict:
    return decision_config()["rl"]


# --- Piyasa verisi ve göstergeler ---------------------------------------------------------------

def _monthly_to_daily(points: list, dates: pd.DatetimeIndex) -> pd.Series:
    """Aylık endeksi ('YYYY-M') ay ortasına yerleştirip günlüğe enterpole eder; son ayın artışıyla ileri uzatır."""
    s = pd.Series({pd.Timestamp(int(p[0].split("-")[0]), int(p[0].split("-")[1]), 15): p[1] for p in points}).sort_index()
    step = (s.iloc[-1] / s.iloc[-2]) ** (1 / 30.4) if len(s) > 1 else 1.0
    end = dates.max() + pd.Timedelta(days=120)
    ext = pd.date_range(s.index[-1] + pd.Timedelta(days=1), end, freq="D")
    if len(ext):
        s = pd.concat([s, pd.Series(s.iloc[-1] * step ** np.arange(1, len(ext) + 1), index=ext)])
    full = dates.union(dates - pd.Timedelta(days=200))
    return s.reindex(s.index.union(full)).interpolate("time").bfill()


def _weekly_series(points: list) -> pd.Series:
    return pd.Series({pd.Timestamp(pd.to_datetime(p[0], format="%d-%m-%Y")): p[1] for p in points}).sort_index()


def build_market(products: list, as_of: date | None = None) -> dict:
    """Kısa listedeki ürünler için adım getirileri, enflasyon, durum göstergeleri ve eğitim/doğrulama/test ayrımı."""
    c = cfg()
    step, cal = c["adim_is_gunu"], c["adim_takvim_gunu"]
    included, excluded, fund_codes = [], [], []
    for p in products:
        if p.product_id.startswith("FON:"):
            fund_codes.append(p.product_id[4:])
            included.append(p)
        elif p.v("vade_gun") is not None:
            if p.v("vade_gun") > cal + 1:
                excluded.append({"product_id": p.product_id, "neden": f"Vade {p.v('vade_gun')} gün, karar adımından ({cal} gün) uzun: kilitli."})
            elif p.v("getiri_1y") is None:
                excluded.append({"product_id": p.product_id, "neden": "Oran verisi yok."})
            else:
                included.append(p)
        else:
            excluded.append({"product_id": p.product_id, "neden": "Fiyat geçmişi veya oran yok."})

    prices = tefas_import.price_matrix(fund_codes) if fund_codes else None
    have = set(prices.columns) if prices is not None else set()
    for code in fund_codes:
        if code not in have:
            excluded.append({"product_id": f"FON:{code}", "neden": "TEFAS fiyat geçmişi yok."})
            included = [p for p in included if p.product_id != f"FON:{code}"]
    if prices is not None:
        prices = prices.replace(0, np.nan).ffill(limit=5)
        need_from = pd.Timestamp(c["test_baslangic"]) - pd.DateOffset(months=c["egitim_min_ay"])
        for code in list(prices.columns):
            first = prices[code].first_valid_index()
            reason = None
            if prices[code].notna().sum() < c["min_gecmis_gun"]:
                reason = f"Fiyat geçmişi {c['min_gecmis_gun']} günden kısa."
            elif first is None or first > need_from:
                reason = (f"Test başlangıcından ({c['test_baslangic']}) önce en az {c['egitim_min_ay']} aylık geçmiş yok "
                          f"(ilk fiyat {first.date() if first is not None else '-'}); eğitim/test ayrımı korunamaz.")
            if reason:
                excluded.append({"product_id": f"FON:{code}", "neden": reason})
                prices = prices.drop(columns=code)
                included = [p for p in included if p.product_id != f"FON:{code}"]
        prices = prices.dropna()
    if prices is None or prices.empty:
        if any(p.product_id.startswith("FON:") for p in included):
            return {"ok": False, "neden": "Ortak fiyat geçmişi yok."}
        prices = pd.DataFrame(index=pd.bdate_range(end=pd.Timestamp(as_of or date.today()), periods=900))
    dates = pd.DatetimeIndex(prices.index)
    n = len(dates) - step
    if n <= step + 20:
        return {"ok": False, "neden": "Fiyat geçmişi karar adımına göre çok kısa."}

    # Adım getirileri (başlangıç indeksi i: dates[i] → dates[i+step])
    level = []
    for p in included:
        if p.product_id.startswith("FON:"):
            level.append(prices[p.product_id[4:]].to_numpy())
        else:
            daily = (1 + p.v("getiri_1y") * cal / 365) ** (1 / step)
            level.append(daily ** np.arange(len(dates)))
    L = np.column_stack(level) if level else np.ones((len(dates), 0))
    nominal = L[step:] / L[:-step] - 1                                    # (n, ürün)

    cpi_hist = evds.fetch_history(CPI_CODE)
    cpi_info = {k: cpi_hist.get(k) for k in ("source", "url", "fetched_at", "status", "note")}
    if cpi_hist["points"]:
        cpi_info["son_ay"] = cpi_hist["points"][-1][0]
        cpi = _monthly_to_daily(cpi_hist["points"], dates)
        cpi_on = cpi.reindex(dates).to_numpy()
        inflation = cpi_on[step:] / cpi_on[:-step] - 1
        lag = pd.Timedelta(days=c["tufe_gecikme_gun"])                      # açıklanma gecikmesi: geleceğe bakış yok
        known = cpi.reindex(dates - lag).to_numpy()
        known_prev = cpi.reindex(dates - lag - pd.Timedelta(days=cal)).to_numpy()
        infl_trend = (known / known_prev) ** (365 / cal) - 1
    else:
        inflation, infl_trend = np.zeros(n), np.zeros(len(dates))

    feat_names, feats, notes = [], [], []
    for j, p in enumerate(included):
        trail = np.full(len(dates), np.nan)
        trail[step:] = L[step:, j] / L[:-step, j] - 1
        feat_names.append(f"{p.product_id} son 3 ay getirisi")
        feats.append(trail)
    feat_names.append("Son 3 ay enflasyonu (yıllık, 1 ay gecikmeli)")
    feats.append(infl_trend)
    dep = evds.fetch_history(DEPOSIT_CODE)
    if dep["points"]:
        ws = _weekly_series(dep["points"])
        lagged = ws.reindex(ws.index.union(dates - pd.Timedelta(days=7))).ffill().reindex(dates - pd.Timedelta(days=7)).to_numpy() / 100
        feat_names.append("TCMB 1 ay tasarruf mevduatı faizi (1 hafta gecikmeli)")
        feats.append(lagged)
    else:
        notes.append(f"Mevduat faizi göstergesi alınamadı ({dep.get('note')}); durumdan çıkarıldı.")
    F = np.column_stack(feats)

    tax = np.array([(p.v("stopaj") or 0.0) for p in included])
    start_dates, end_dates = dates[:-step], dates[step:]
    valid = np.where(~np.isnan(F[:n]).any(axis=1))[0]                      # göstergeleri hesaplanabilen başlangıçlar
    test_start = pd.Timestamp(c["test_baslangic"])
    train_all = valid[end_dates[valid] < test_start]
    test = valid[start_dates[valid] >= test_start]
    split_note = None
    if len(train_all) < 60 or len(test) < 20:
        split_note = "Fiyat geçmişi zaman ayrımı için kısa; eğitim, doğrulama ve test aynı dönemden (iyimser değerlendirme)."
        train_all = test = valid
    cut = int(len(train_all) * (1 - c["dogrulama_orani"]))
    train, val = train_all[:cut], train_all[cut:]
    mu, sd = np.nanmean(F[train], axis=0), np.nanstd(F[train], axis=0)
    sd[sd < 1e-9] = 1.0
    last = len(dates) - 1
    return {
        "ok": True, "products": included, "excluded": excluded, "nominal": nominal, "inflation": inflation, "tax": tax,
        "features": (F - mu) / sd, "features_raw": F, "feature_names": feat_names, "feature_mu": mu, "feature_sd": sd,
        "today_index": last, "today": str(dates[last].date()),
        "fund_mask": np.array([p.product_id.startswith("FON:") for p in included]),
        "train": train, "val": val, "test": test, "train_all": train_all, "split_note": split_note, "step": step,
        "period": {"baslangic": str(dates[0].date()), "bitis": str(dates[-1].date()),
                   "egitim": [str(start_dates[train[0]].date()), str(end_dates[train[-1]].date())],
                   "dogrulama": [str(start_dates[val[0]].date()), str(end_dates[val[-1]].date())] if len(val) else None,
                   "test": [str(start_dates[test[0]].date()), str(end_dates[test[-1]].date())]},
        "cpi": cpi_info, "missing_tax": [p.product_id for p in included if p.v("stopaj") is None], "notes": notes,
    }


# --- Ortam ----------------------------------------------------------------------------------------

def action_grid(n: int, step: float, max_w: float | None, fund_mask: np.ndarray | None = None,
                fund_max: float | None = None) -> np.ndarray:
    k = round(1 / step)
    acts = np.array([c for c in itertools.product(range(k + 1), repeat=n) if sum(c) == k], dtype=float) / k
    if len(acts) == 0:
        return acts
    keep = np.ones(len(acts), bool)
    if max_w is not None:
        keep &= acts.max(axis=1) <= max_w + 1e-9
    if fund_mask is not None and fund_max is not None and fund_mask.any():
        keep &= (acts[:, fund_mask] <= fund_max + 1e-9).all(axis=1)
    return acts[keep]


class Env:
    def __init__(self, market: dict, persona_level: str, horizon_years: float, concentration: bool, rng: np.random.Generator):
        c = cfg()
        self.m = market
        self.rng = rng
        n = len(market["products"])
        fm = market.get("fund_mask", np.zeros(n, bool))
        self.notes: list[str] = []
        self.actions = action_grid(n, c["izgara"], c["yogunlasma_max_agirlik"] if concentration else None, fm, c["fon_basina_max_agirlik"])
        if len(self.actions) == 0:
            self.notes.append("Ürün sayısı ağırlık sınırlarına yetmiyor; fon başına üst sınır bu kısa liste için gevşetildi.")
            self.actions = action_grid(n, c["izgara"], c["yogunlasma_max_agirlik"] if concentration else None)
        if len(self.actions) == 0:
            self.actions = action_grid(n, c["izgara"], None)
        pen = c["ceza"].get(persona_level) or c["ceza"]["Orta"]
        self.lam, self.fixed = pen["lambda"], pen["sabit"]
        key = min((k for k in c["ufuk_ceyrek"] if not k.startswith("_")), key=lambda k: abs(float(k) - horizon_years))
        self.h_min, self.h_max = c["ufuk_ceyrek"][key]
        self.bands = np.array(c["servet_bantlari"])
        self.t_cap = c["durum_max_adim"]
        self.cost = c["degistirme_maliyeti"]
        self.step = market.get("step", c["adim_is_gunu"])
        self.block_q = c["blok_ceyrek"]

    # Ufuk: T ~ U{h_min..h_max}; bölüm t. adımdan sonra devam etme olasılığı
    def sample_horizon(self) -> int:
        return int(self.rng.integers(self.h_min, self.h_max + 1))

    def p_continue(self, t: int) -> float:
        alive = lambda k: max(0, self.h_max - max(k, self.h_min) + 1) if k >= self.h_min else self.h_max - self.h_min + 1
        a_now, a_next = alive(t + 1), alive(t + 2)
        return a_next / a_now if a_now > 0 else 0.0

    def path(self, pool: np.ndarray, T: int, rng: np.random.Generator) -> list[int]:
        """Durağan blok bootstrap: çeyrekler sırayla ilerler, ortalama `blok_ceyrek` çeyrekte bir rastgele atlar."""
        member = np.zeros(int(pool.max()) + self.step + 1, bool)
        member[pool] = True
        s = int(rng.choice(pool))
        out = [s]
        for _ in range(T - 1):
            nxt = s + self.step
            if rng.random() < 1 / self.block_q or nxt >= len(member) or not member[nxt]:
                nxt = int(rng.choice(pool))
            out.append(nxt)
            s = nxt
        return out

    def state(self, t: int, w: float) -> tuple[int, int]:
        return min(t, self.t_cap), int(np.digitize(w, self.bands))

    def returns_batch(self, idxs) -> np.ndarray:
        nom = self.m["nominal"][idxs]
        after_tax = np.where(nom > 0, nom * (1 - self.m["tax"]), nom)
        infl = np.asarray(self.m["inflation"][idxs])
        return (1 + after_tax) / (1 + infl)[..., None] - 1

    def outcome_all(self, w, prev, r) -> tuple[np.ndarray, np.ndarray]:
        """Verilen piyasa getirisinde (r: ... × ürün) tüm aksiyonların yeni serveti ve ödülü. w, prev batch olabilir."""
        A = self.actions
        w = np.asarray(w, float)[..., None]
        prev = np.zeros(A.shape[1]) if prev is None else np.asarray(prev, float)   # sıfır vektör = nakit (ilk alım)
        cash = 1.0 - prev.sum(axis=-1)
        turnover = 0.5 * np.abs(A - prev[..., None, :]).sum(axis=-1) + 0.5 * cash[..., None]
        w_new = w * (1 + r @ A.T) - w * self.cost * turnover
        reward = (w_new - w) - np.where(w_new < 1.0, self.lam * (1.0 - w_new) + self.fixed, 0.0)
        return w_new, reward

    def drift(self, a: int, r: np.ndarray) -> np.ndarray:
        d = self.actions[a] * (1 + r)
        return d / d.sum() if d.sum() > 0 else self.actions[a]


# --- Tablo tabanlı Q (karşılaştırma tabanı) -------------------------------------------------------

class TabularAgent:
    name = TAB_NAME

    def __init__(self, env: Env):
        self.env = env
        self.Q = np.zeros((env.t_cap + 1, len(env.bands) + 1, len(env.actions)))
        self.N = np.zeros_like(self.Q)
        self.curve: list = []

    def act(self, t, w, idx, prev) -> int:
        return int(np.argmax(self.Q[self.env.state(t, w)]))

    def train(self, pool: np.ndarray, budget: int, rng: np.random.Generator):
        """Tüm-aksiyon, mini-batch Q-öğrenme: piyasa göstergesi görmediği için hedef, havuzdan rastgele `blok_ornek`
        bloğun ortalamasıdır."""
        env, k = self.env, cfg()["blok_ornek"]
        n_a = len(env.actions)
        episodes = max(500, int(budget / ((env.h_min + env.h_max) / 2)))
        chunk, acc = max(1, episodes // 20), []
        for ep in range(episodes):
            eps = max(0.05, 1.0 - ep / (0.7 * episodes))
            T = env.sample_horizon()
            w, prev, total = 1.0, None, 0.0
            for t, idx in enumerate(env.path(pool, T, rng)):
                s = env.state(t, w)
                r = env.returns_batch(rng.choice(pool, size=k))
                w_all, rew = env.outcome_all(w, prev, r)
                q_next = self.Q[min(t + 1, env.t_cap)][np.digitize(w_all, env.bands)].max(axis=2)
                target = (rew + env.p_continue(t) * q_next).mean(axis=0)
                self.N[s] += 1
                self.Q[s] += (target - self.Q[s]) / self.N[s] ** 0.6
                a = int(rng.integers(n_a)) if rng.random() < eps else int(np.argmax(self.Q[s]))
                r_real = env.returns_batch(idx)
                w_new, rew_real = env.outcome_all(w, prev, r_real)
                total += float(rew_real[a])
                prev, w = env.drift(a, r_real), float(w_new[a])
            acc.append(total)
            if (ep + 1) % chunk == 0:
                self.curve.append({"bolum": ep + 1, "ortalama_odul": round(float(np.mean(acc)), 4)})
                acc = []
        return self

    def table(self, products: list) -> list[dict]:
        env, names = self.env, [p.product_id for p in products]
        labels = ["< %95", "%95-100", "%100-105", "%105-115", "%115-130", "> %130"]
        rows = []
        for t in range(min(env.h_max, env.t_cap + 1)):
            for b in range(len(env.bands) + 1):
                visits = int(self.N[t, b].sum())
                if visits == 0:
                    continue
                a = int(np.argmax(self.Q[t, b]))
                rows.append({"adim": t, "ay": t * 3, "servet_bandi": labels[b], "ziyaret": visits,
                             "agirliklar": {names[i]: float(env.actions[a][i]) for i in range(len(names)) if env.actions[a][i] > 0},
                             "guvenilir": visits >= 200})
        return rows


# --- DQN (numpy MLP) ------------------------------------------------------------------------------

class MLP:
    """Küçük çok katmanlı algılayıcı: d → h → h → çıkış, ReLU, Adam."""

    def __init__(self, d_in: int, hidden: int, d_out: int, rng: np.random.Generator, lr: float):
        dims = [d_in, hidden, hidden, d_out]
        self.W = [rng.normal(0, np.sqrt(2 / a), (a, b)) for a, b in zip(dims[:-1], dims[1:])]
        self.b = [np.zeros(b) for b in dims[1:]]
        self.W[-1] *= 0.1
        self.lr, self.t = lr, 0
        self.m = [np.zeros_like(p) for p in self.W + self.b]
        self.v = [np.zeros_like(p) for p in self.W + self.b]

    def forward(self, x: np.ndarray, keep: bool = False):
        acts = [x]
        h = x
        for i, (W, b) in enumerate(zip(self.W, self.b)):
            h = h @ W + b
            if i < len(self.W) - 1:
                h = np.maximum(h, 0)
            acts.append(h)
        return (h, acts) if keep else h

    def step(self, x: np.ndarray, target: np.ndarray) -> float:
        """Tüm çıktılar için Huber kaybıyla bir Adam adımı."""
        out, acts = self.forward(x, keep=True)
        err = out - target
        g = np.clip(err, -1, 1) / len(x)
        grads_W, grads_b = [], []
        for i in reversed(range(len(self.W))):
            grads_W.insert(0, acts[i].T @ g)
            grads_b.insert(0, g.sum(axis=0))
            if i > 0:
                g = (g @ self.W[i].T) * (acts[i] > 0)
        self.t += 1
        params = self.W + self.b
        for j, (p, gr) in enumerate(zip(params, grads_W + grads_b)):
            self.m[j] = 0.9 * self.m[j] + 0.1 * gr
            self.v[j] = 0.999 * self.v[j] + 0.001 * gr * gr
            mh, vh = self.m[j] / (1 - 0.9 ** self.t), self.v[j] / (1 - 0.999 ** self.t)
            p -= self.lr * mh / (np.sqrt(vh) + 1e-8)
        return float(np.mean(np.minimum(np.abs(err), 1) * np.abs(err)))

    def step_masked(self, x: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
        """Yalnızca mask=True olan çıktılarda (hedefi hesaplanmış aksiyonlar) Huber kaybıyla bir Adam adımı."""
        out, acts = self.forward(x, keep=True)
        err = np.where(mask, out - target, 0.0)
        g = np.clip(err, -1, 1) / max(1, mask.sum())
        grads_W, grads_b = [], []
        for i in reversed(range(len(self.W))):
            grads_W.insert(0, acts[i].T @ g)
            grads_b.insert(0, g.sum(axis=0))
            if i > 0:
                g = (g @ self.W[i].T) * (acts[i] > 0)
        self.t += 1
        for j, (p, gr) in enumerate(zip(self.W + self.b, grads_W + grads_b)):
            self.m[j] = 0.9 * self.m[j] + 0.1 * gr
            self.v[j] = 0.999 * self.v[j] + 0.001 * gr * gr
            mh, vh = self.m[j] / (1 - 0.9 ** self.t), self.v[j] / (1 - 0.999 ** self.t)
            p -= self.lr * mh / (np.sqrt(vh) + 1e-8)
        e = np.abs(err[mask])
        return float(np.mean(np.minimum(e, 1) * e)) if e.size else 0.0

    def state_dict(self) -> dict:
        return {**{f"W{i}": w for i, w in enumerate(self.W)}, **{f"b{i}": b for i, b in enumerate(self.b)}}

    def load_state_dict(self, d) -> None:
        n = len(self.W)
        self.W = [np.array(d[f"W{i}"]) for i in range(n)]
        self.b = [np.array(d[f"b{i}"]) for i in range(n)]

    def copy_from(self, other: "MLP"):
        self.W = [w.copy() for w in other.W]
        self.b = [b.copy() for b in other.b]


class DQNAgent:
    name = DQN_NAME
    SCALE = 10.0  # ödül ölçeği (sayısal kararlılık); Q çıktısı bu ölçekte öğrenilir

    def __init__(self, env: Env, rng: np.random.Generator):
        c = cfg()["dqn"]
        self.env = env
        n_p = len(env.m["products"])
        self.d_in = env.m["features"].shape[1] + 2 + n_p
        self.net = MLP(self.d_in, c["gizli"], len(env.actions), rng, c["ogrenme_orani"])
        self.target = MLP(self.d_in, c["gizli"], len(env.actions), rng, c["ogrenme_orani"])
        self.target.copy_from(self.net)
        self.curve: list = []

    def x(self, t, w, idx, prev) -> np.ndarray:
        """Durum vektörü: piyasa göstergeleri (normalize), geçen adım, reel servet − 1, mevcut dağılım."""
        t, w, idx = np.atleast_1d(t), np.atleast_1d(w), np.atleast_1d(idx)
        n_p = len(self.env.m["products"])
        prev = np.zeros((len(t), n_p)) if prev is None else np.atleast_2d(prev)
        return np.column_stack([self.env.m["features"][idx], np.minimum(t, self.env.t_cap) / self.env.t_cap, (w - 1) * 5, prev])

    def q(self, t, w, idx, prev) -> np.ndarray:
        return self.net.forward(self.x(t, w, idx, prev))

    def act(self, t, w, idx, prev) -> int:
        return int(np.argmax(self.q(t, w, idx, prev)[0]))

    def train(self, pool: np.ndarray, rng: np.random.Generator):
        c, env = cfg()["dqn"], self.env
        U, B, sync = c["guncelleme"], c["batch"], c["hedef_senkron"]
        n_a, n_p = len(env.actions), len(env.m["products"])
        buf_t, buf_w, buf_i, buf_p = [], [], [], []
        member = np.zeros(int(env.m["nominal"].shape[0]) + env.step + 1, bool)
        member[pool] = True
        T, t, w, prev, path = env.sample_horizon(), 0, 1.0, None, None
        path = env.path(pool, T, rng)
        losses, rewards, ep_total = [], [], 0.0
        for u in range(U):
            eps = max(0.05, 1.0 - u / (0.6 * U))
            idx = path[t]
            buf_t.append(t); buf_w.append(w); buf_i.append(idx)
            buf_p.append(np.zeros(n_p) if prev is None else prev)
            a = int(rng.integers(n_a)) if rng.random() < eps else self.act(t, w, idx, prev)
            r_real = env.returns_batch(idx)
            w_new, rew = env.outcome_all(w, prev, r_real)
            ep_total += float(rew[a])
            prev, w, t = env.drift(a, r_real), float(w_new[a]), t + 1
            if t >= T:
                rewards.append(ep_total)
                T, t, w, prev, ep_total = env.sample_horizon(), 0, 1.0, None, 0.0
                path = env.path(pool, T, rng)
            # Mini-batch güncelleme: tüm aksiyonların hedefi (piyasa dışsal)
            if len(buf_t) >= B:
                j = rng.integers(len(buf_t), size=B)
                bt, bw, bi = np.array(buf_t)[j], np.array(buf_w)[j], np.array(buf_i)[j]
                bp = np.array(buf_p)[j]                                         # ilk adımda sıfır vektör (nakit)
                r = env.returns_batch(bi)                                       # (B, ürün)
                w_all, rew_all = env.outcome_all(bw, bp, r)                     # (B, aksiyon)
                nxt = bi + env.step
                ok_next = (nxt < len(member)) & member[np.minimum(nxt, len(member) - 1)]
                nxt = np.where(ok_next, nxt, rng.choice(pool, size=B))
                drifted = env.actions[None, :, :] * (1 + r[:, None, :])
                drifted = drifted / np.clip(drifted.sum(axis=2, keepdims=True), 1e-9, None)
                xs = self.x(np.repeat(bt + 1, n_a), w_all.ravel(), np.repeat(nxt, n_a), drifted.reshape(-1, n_p))
                q_next = self.target.forward(xs).reshape(B, n_a, n_a).max(axis=2) / self.SCALE
                cont = np.array([env.p_continue(int(tt)) for tt in bt])
                y = (rew_all + cont[:, None] * q_next) * self.SCALE
                losses.append(self.net.step(self.x(bt, bw, bi, bp), y))
                if (u + 1) % sync == 0:
                    self.target.copy_from(self.net)
            if (u + 1) % max(1, U // 20) == 0:
                self.curve.append({"guncelleme": u + 1, "ortalama_odul": round(float(np.mean(rewards[-200:])) if rewards else 0.0, 4),
                                   "kayip": round(float(np.mean(losses[-200:])) if losses else 0.0, 5)})
        return self

    def sensitivity(self, today_idx: int, products: list) -> list[dict]:
        """Bugünkü durumda her göstergeyi ±1 standart sapma oynatınca başlangıç dağılımı nasıl değişir."""
        env, m = self.env, self.env.m
        names = [p.product_id for p in products]
        base_x = self.x(0, 1.0, today_idx, None)
        fmt = lambda a: {names[i]: float(env.actions[a][i]) for i in range(len(names)) if env.actions[a][i] > 0}
        base_a = int(np.argmax(self.net.forward(base_x)[0]))
        rows = [{"gosterge": "Bugünkü durum", "deger": None, "dagilim": fmt(base_a)}]
        for k, fname in enumerate(m["feature_names"]):
            for sgn, label in ((-1, "−1σ"), (1, "+1σ")):
                x = base_x.copy()
                x[0, k] += sgn
                a = int(np.argmax(self.net.forward(x)[0]))
                raw = m["features_raw"][today_idx, k] + sgn * m["feature_sd"][k]
                rows.append({"gosterge": f"{fname} {label}", "deger": float(raw), "dagilim": fmt(a), "degisti": a != base_a})
        return rows


# --- Değerlendirme --------------------------------------------------------------------------------

def scenarios(env: Env, pool: np.ndarray, n: int, rng: np.random.Generator) -> list[list[int]]:
    return [env.path(pool, env.sample_horizon(), rng) for _ in range(n)]


def run_policy(env: Env, choose, scen: list[list[int]]) -> dict:
    finals, ever_below, turn = [], [], []
    for idxs in scen:
        w, prev, below, tv = 1.0, None, False, 0.0
        for t, idx in enumerate(idxs):
            a = choose(t, w, idx, prev)
            tv += 1.0 if prev is None else 0.5 * np.abs(env.actions[a] - prev).sum()  # ilk adım: nakitten tam alım
            r = env.returns_batch(idx)
            w_new, _ = env.outcome_all(w, prev, r)
            prev, w = env.drift(a, r), float(w_new[a])
            below |= w < 1.0
        finals.append(w)
        ever_below.append(below)
        turn.append(tv / len(idxs))
    f = np.array(finals) - 1
    return {"ortalama_reel_kazanc": float(f.mean()), "medyan_reel_kazanc": float(np.median(f)),
            "kotu_yuzde5": float(np.percentile(f, 5)), "anapara_alti_bitis_olasiligi": float((f < 0).mean()),
            "herhangi_an_anapara_alti_olasiligi": float(np.mean(ever_below)), "ortalama_devir": float(np.mean(turn))}


def baselines(env: Env, scores: np.ndarray, vols: np.ndarray) -> dict:
    A = env.actions
    nearest = lambda w: int(np.argmin(np.abs(A - w).sum(axis=1)))
    static = lambda w: (lambda t, wealth, idx, prev, a=nearest(w): a)
    n = A.shape[1]
    eq = np.ones(n) / n
    sw = scores / scores.sum() if scores.sum() > 0 else eq
    best = A[int(np.argmax(A @ scores))]  # izinli dağılımlar içinde skoru en yüksek olan (fon sınırına uyar)
    low = A[int(np.argmin(A @ vols))]
    return {"Eşit ağırlık (sabit)": static(eq), "Skor ağırlıklı (sabit)": static(sw),
            "En yüksek skorlu izinli dağılım": static(best), "En düşük oynaklıklı izinli dağılım": static(low)}


def evaluate(env: Env, agents: dict, scores, vols, pool: np.ndarray, n: int, rng: np.random.Generator) -> dict:
    scen = scenarios(env, pool, n, rng)
    pols = {name: ag.act for name, ag in agents.items()}
    pols.update(baselines(env, scores, vols))
    return {name: run_policy(env, fn, scen) for name, fn in pols.items()}


# --- Çalıştırma -----------------------------------------------------------------------------------

_RUN_CACHE: dict = {}


def run(shortlist: list, persona, view, as_of: date | None = None) -> dict:
    """Kısa liste (ScoredProduct listesi) için RL ajanlarını eğitir, seçer ve değerlendirir (önbellekli, sabit tohum)."""
    from .config import config_hash
    key = (tuple(s.product.product_id for s in shortlist), tuple(round(s.score, 4) for s in shortlist),
           persona.anapara_koruma_tercihi, view.horizon_years, tuple(sorted(r["id"] for r in view.rules)),
           config_hash(), tefas_import.data_version(), date.today().isoformat())
    if key not in _RUN_CACHE:
        _RUN_CACHE.clear()
        _RUN_CACHE[key] = _run(shortlist, persona, view, as_of)
    return _RUN_CACHE[key]


def _run(shortlist: list, persona, view, as_of: date | None = None) -> dict:
    c = cfg()
    market = build_market([s.product for s in shortlist], as_of)
    if not market["ok"]:
        return {"durum": "calismadi", "neden": market["neden"]}
    prods = market["products"]
    if len(prods) < 2:
        return {"durum": "calismadi", "neden": "RL için en az 2 uygun ürün gerekli.", "dislanan": market["excluded"]}
    concentration = any(r["id"] == "K_YOGUNLASMA" for r in view.rules)
    env = Env(market, persona.anapara_koruma_tercihi or "Orta", view.horizon_years, concentration, np.random.default_rng(c["tohum"]))
    by_id = {s.product.product_id: s for s in shortlist}
    scores = np.array([by_id[p.product_id].score for p in prods])
    vols = np.array([p.v("volatilite") if p.v("volatilite") is not None else 0.0 for p in prods])

    tab = TabularAgent(env).train(market["train"], c["adim_butcesi"], np.random.default_rng(c["tohum"]))
    dqn = DQNAgent(env, np.random.default_rng(c["tohum"] + 10)).train(market["train"], np.random.default_rng(c["tohum"] + 11))
    agents = {DQN_NAME: dqn, TAB_NAME: tab}
    n_eval = c["degerlendirme_bolum"]
    val_pool = market["val"] if len(market["val"]) >= 10 else market["train"]
    val = evaluate(env, agents, scores, vols, val_pool, n_eval, np.random.default_rng(c["tohum"] + 1))
    chosen = max(agents, key=lambda k: val[k]["ortalama_reel_kazanc"] - val[k]["anapara_alti_bitis_olasiligi"] * (env.lam > 0))
    test = evaluate(env, agents, scores, vols, market["test"], n_eval, np.random.default_rng(c["tohum"] + 2))
    train_eval = evaluate(env, agents, scores, vols, market["train"], n_eval, np.random.default_rng(c["tohum"] + 3))

    today = market["today_index"]
    notes = list(market["notes"]) + env.notes
    # Dağılım dışı (OOD) kontrolü: bugünkü göstergeler eğitim dağılımından çok uzaksa DQN ekstrapolasyon yapar
    z = market["features"][today]
    ood = [{"ad": market["feature_names"][k], "z": round(float(z[k]), 1)} for k in range(len(z))
           if not np.isnan(z[k]) and abs(z[k]) > c["ood_esik_sigma"]]
    start_agent = chosen
    if ood and chosen == DQN_NAME:
        start_agent = TAB_NAME
        notes.append("Bugünkü piyasa göstergeleri eğitim döneminin dışında: "
                     + ", ".join(f"{o['ad']} ({o['z']:+.1f}σ)" for o in ood)
                     + ". DQN bu bölgede ekstrapolasyon yapacağı için başlangıç dağılımı göstergeleri kullanmayan tablo tabanlı ajandan alındı.")
    a0 = (dqn if start_agent == DQN_NAME else tab).act(0, 1.0, today, None)
    start = {prods[i].product_id: float(env.actions[a0][i]) for i in range(len(prods)) if env.actions[a0][i] > 0}

    if market["split_note"]:
        notes.append(market["split_note"])
    statics = {k: v for k, v in test.items() if k not in agents}
    if len({round(v["ortalama_reel_kazanc"], 6) for v in statics.values()}) == 1:
        notes.append(f"RL evreninde yalnızca {len(prods)} ürün kaldı ve sabit stratejilerin hepsi aynı sonucu veriyor; "
                     "karşılaştırma anlamlı değil (diğer ürünler simülasyona alınamadı, bkz. dışlananlar).")
    else:
        ch, best_name = test[chosen], max(statics, key=lambda k: statics[k]["ortalama_reel_kazanc"])
        if statics[best_name]["ortalama_reel_kazanc"] > ch["ortalama_reel_kazanc"] + 0.005:
            notes.append(f"Test döneminde '{best_name}' ortalama reel kazançta seçilen ajandan ({chosen}) yüksek "
                         f"({fmt_pct(statics[best_name]['ortalama_reel_kazanc'])} / {fmt_pct(ch['ortalama_reel_kazanc'])}); "
                         "dinamik yeniden dengelemenin ek faydası sınırlı.")
    tr, te = train_eval[chosen]["ortalama_reel_kazanc"], test[chosen]["ortalama_reel_kazanc"]
    if tr - te > 0.05:
        notes.append(f"Eğitim ve test arasında belirgin fark var ({chosen}: {fmt_pct(tr)} → {fmt_pct(te)}); olası aşırı uyum.")
    if market["missing_tax"]:
        notes.append(f"Stopaj oranı olmayan ürünlerde vergi 0 varsayıldı: {', '.join(market['missing_tax'])}.")
    if market["cpi"].get("status") != "ok":
        notes.append(f"TÜFE serisi: {market['cpi'].get('status')} ({market['cpi'].get('note')}).")

    rename = {chosen: RL_NAME}
    relabel = lambda d: {rename.get(k, k): v for k, v in d.items()}
    return {
        "durum": "tamam",
        "secilen_ajan": chosen,
        "baslangic_ajani": start_agent,
        "dagilim_disi": ood,
        "secim_dogrulama": {k: val[k] for k in agents},
        "urunler": [p.product_id for p in prods],
        "urun_adlari": {p.product_id: p.name for p in prods},
        "dislanan": market["excluded"],
        "baslangic_tarihi": market["today"],
        "baslangic_dagilimi": start,
        "politika": tab.table(prods),
        "duyarlilik": dqn.sensitivity(today, prods),
        "gostergeler": [{"ad": n, "bugun": float(market["features_raw"][today, k]) if not np.isnan(market["features_raw"][today, k]) else None,
                         "egitim_ort": float(market["feature_mu"][k]), "egitim_std": float(market["feature_sd"][k])}
                        for k, n in enumerate(market["feature_names"])],
        "test": relabel(test),
        "egitim": relabel(train_eval),
        "ogrenme_egrisi": tab.curve,
        "dqn_egrisi": dqn.curve,
        "parametreler": {"aksiyon_sayisi": int(len(env.actions)), "ufuk_ceyrek": [env.h_min, env.h_max], "ceza_lambda": env.lam,
                         "ceza_sabit": env.fixed, "degistirme_maliyeti": env.cost, "izgara": c["izgara"], "tohum": c["tohum"],
                         "yogunlasma_siniri": concentration, "fon_basina_max": c["fon_basina_max_agirlik"],
                         "adim_is_gunu": c["adim_is_gunu"], "blok_ceyrek": c["blok_ceyrek"], "dqn": c["dqn"]},
        "donem": market["period"],
        "enflasyon_kaynagi": market["cpi"],
        "notlar": notes,
    }
