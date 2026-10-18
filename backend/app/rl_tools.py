"""Kategori DQN'i için açıklama araçları (LLM tool'ları ve RM özet kartı).

İlke: açıklama bir vekil modelden değil, GERÇEK ağın kendisini sorgulayarak üretilir. Ağ küçük bir MLP olduğundan
(bir ileri geçiş ~1 ms) karşı-olgusal sorular doğrudan cevaplanabilir; böylece açıklamanın sadakati tanım gereği tamdır.

Araçlar (hepsi salt okunur, deterministik):
  rl_karar            bugünkü dağılım, en iyi alternatifler, karar marjı, modelin getiri beklentisi
  rl_neden            gösterge TEMALARI üzerinde kesin Shapley: "ortalama piyasa koşullarındaki karar" → "bugünkü karar"
                      farkını temalara paylaştırır (2^G ileri geçiş; G ≈ 5)
  rl_ne_olursa        gösterge ve/veya persona değişikliğinde modelin gerçek yeni kararı
  rl_esik             bir göstergeyi tarar; kararın değiştiği eşikleri ham birimde verir
  rl_benzer_donemler  bugüne en çok benzeyen geçmiş karar anları, o zamanki karar ve sonrasında gerçekleşen reel getiri
  rl_guvenilirlik     dağılım dışı göstergeler, karar marjı, test dönemi kıyasları, inanç kalibrasyonu, veri notları

Sayılar araçlardan gelir; LLM yalnızca anlatır. ozet() deterministik bir özet metni ve metinde kullanılabilecek
sayıların listesini (guard için) üretir.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from math import factorial
from typing import Any

import numpy as np

from . import rl_category as rc


def _whatif_module():
    """Senaryo tanımları eğitimle ortak tek kaynaktan (tools/gbrl/whatif.py, saf numpy) yüklenir."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "tools" / "gbrl" / "whatif.py"
    spec = importlib.util.spec_from_file_location("gbrl_whatif", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_WHATIF = None


def whatif():
    global _WHATIF
    if _WHATIF is None:
        _WHATIF = _whatif_module()
    return _WHATIF
from .config import PERSONA_DIR, decision_config
from .models import Persona
from .profile import apply_answers, interpret

MODELS = {"canli": None, "v1": "rl_kategori", "v2": "rl_kategori_v2", "v3": "rl_kategori_v3", "v4": "rl_kategori_v4",
          "ppo_v8": "rl_ppo_v8", "ppo_v9": "rl_ppo_v9", "ppo_v10": "rl_ppo_v10", "ppo_v11": "rl_ppo_v11", "ppo_v11_100m": "rl_ppo_v11_100m",
          "ppo_v11_tum25m": "rl_ppo_v11_tum25m", "ppo_v11_tum100m": "rl_ppo_v11_tum100m",
          "ppo_v14_tum6m": "rl_ppo_v14_tum6m"}
DEFAULT_MODEL = "canli"  # rl_category.MODEL_DIR neyi gösteriyorsa (RL_MODEL ortam değişkeni); şu an NN-PPO

# Gösterge temaları: ad içinde ilk eşleşen kalıp temayı belirler (sıra önemli: 'yabanci_hisse' döviz temasına girer).
THEMES = [
    ("Faiz ortamı", ["mevduat faizi", "politika faizi", "tlref", "reel mevduat"]),
    ("Enflasyon", ["tüfe"]),
    ("Kur ve döviz/altın varlıkları", ["usd/try", "kiymetli_maden", "yabanci_hisse", "eurobond_doviz"]),
    ("Hisse piyasası", ["hisse", "bist"]),
    ("TL fon getirileri ve oynaklığı", ["para_piyasasi", "borclanma", "katilim", "karma_degisken", "fon_sepeti"]),
]
CAT_NAMES = {
    "mevduat_tl": "TL mevduat", "para_piyasasi": "para piyasası", "borclanma": "borçlanma araçları", "katilim": "katılım",
    "kiymetli_maden": "kıymetli maden", "karma_degisken": "karma/değişken", "fon_sepeti": "fon sepeti", "hisse": "hisse fonu",
    "yabanci_hisse": "yabancı hisse", "eurobond_doviz": "eurobond/döviz", "bist30_hisse": "BIST 30 hisseleri",
}


class ToolError(ValueError):
    pass


def pct(x: float | None, nd: int = 1) -> str | None:
    if x is None:
        return None
    s = f"%{abs(x) * 100:.{nd}f}".replace(".", ",")
    return ("−" + s) if x < 0 and round(abs(x) * 100, nd) > 0 else s


def num(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def gval(name: str, v: float) -> str:
    """Gösterge değeri: getiri, oynaklık, faiz ve değişim göstergeleri oran (0-1) birimindedir → yüzde yazılır."""
    return pct(v, 1) if abs(v) < 5 else num(v, 2)


# --- Model ve bağlam ------------------------------------------------------------------------------

_MODELS: dict = {}


def load(version: str = DEFAULT_MODEL):
    from .rl import MLP
    if version not in MODELS:
        raise ToolError(f"Bilinmeyen model '{version}'. Seçenekler: {', '.join(MODELS)}")
    d = rc.MODEL_DIR if MODELS[version] is None else rc.MODEL_DIR.parent / MODELS[version]
    mp, mt = d / "model.npz", d / "meta.json"
    if not (mp.exists() and mt.exists()):
        raise ToolError(f"{version} modeli bulunamadı ya da eğitimi bitmedi ({d}).")
    key = (version, mp.stat().st_mtime, mt.stat().st_mtime)
    if key not in _MODELS:
        meta = json.loads(mt.read_text(encoding="utf-8"))
        w = np.load(mp)
        if meta.get("model_turu") == "ppo":
            # forward = aksiyon logitleri; maskeli argmax karar. Aktivasyon modele göre (v8-v10 tanh, v11+ ReLU)
            net = rc.PPOPolicy(w, meta.get("aktivasyon", "tanh"))
        else:
            n_layers = sum(1 for k in w.files if k[0] == "W" and k[1:].isdigit())
            net = MLP(w["W0"].shape[0], w["W0"].shape[1], w[f"W{n_layers - 1}"].shape[1], np.random.default_rng(0), 0.0)
            net.load_state_dict(w)
            net.belief_W = (w["Wb"], w["bb"]) if "Wb" in w.files else None
        for k in [k for k in _MODELS if k[0] == version or k[1:] == key[1:]]:
            del _MODELS[k]
        _MODELS[key] = (net, meta, d)
    return _MODELS[key]


@dataclass
class Ctx:
    version: str
    net: Any
    meta: dict
    model_dir: Any
    ds: dict
    cats: list[str]
    A: np.ndarray
    mu: np.ndarray
    sd: np.ndarray
    names: list[str]
    today: int
    raw: np.ndarray
    persona: Persona
    prof: dict
    mask: np.ndarray
    excluded: list[str]
    prev: np.ndarray
    notes: list[str]

    @property
    def z(self) -> np.ndarray:
        return (self.raw - self.mu) / self.sd

    def x(self, z: np.ndarray, prof: dict | None = None, prev: np.ndarray | None = None) -> np.ndarray:
        zin = rc.model_input(z, self.meta)
        return np.concatenate([zin, rc.profile_vector(prof or self.prof, self.meta.get("profil_surumu", 1)),
                               np.zeros(rc.state_dims(self.meta)),
                               self.prev if prev is None else prev])

    def q(self, Z: np.ndarray, prof: dict | None = None, mask: np.ndarray | None = None) -> np.ndarray:
        Z = np.atleast_2d(Z)
        X = np.stack([self.x(z, prof) for z in Z])
        return np.where(self.mask if mask is None else mask, self.net.forward(X), -np.inf)

    def decide(self, Z: np.ndarray, prof: dict | None = None, mask: np.ndarray | None = None) -> np.ndarray:
        return np.argmax(self.q(Z, prof, mask), axis=1)

    def gate(self, q_row: np.ndarray, a_model: int, mask: np.ndarray | None = None) -> dict | None:
        """Güvenli iyileştirme kapısı (rc.apply_gate ile aynı kural; kapı dosyası bu modelin dizininden okunur):
        model önerisi kendi Q tahminine göre taban kuraldan eşik kadar iyi değilse taban kural uygulanır."""
        path = self.model_dir / "kapi.json"
        if not path.exists():
            return None
        g = json.loads(path.read_text(encoding="utf-8"))
        if g.get("model_olusturma") != self.meta.get("olusturma"):
            return None
        m = self.mask if mask is None else mask
        a_base = rc.baseline_action(self.A, m, self.cats)
        diff = float(q_row[a_model] - q_row[a_base])
        use_model = a_model == a_base or diff > g["delta"]
        return {"aksiyon": a_model if use_model else a_base, "kaynak": "model" if use_model else "taban_kural",
                "model_tercihi": {c: round(w * 100) for c, w in self.weights(a_model).items()},
                "taban": {c: round(w * 100) for c, w in self.weights(a_base).items()},
                "q_farki": round(diff, 3), "esik": g["delta"]}

    def final(self, z: np.ndarray, prof: dict | None = None, mask: np.ndarray | None = None) -> tuple[int, int, dict | None]:
        """(uygulanan aksiyon, modelin tercihi, kapı bilgisi)"""
        q = self.q(z, prof, mask)[0]
        a = int(np.argmax(q))
        g = self.gate(q, a, mask)
        return (g["aksiyon"] if g else a), a, g

    @property
    def soft_clip(self) -> bool:
        """tanh kırpma (c·tanh(z/c)): ±c ötesindeki değerler kesilmez, sıkıştırılır; hâlâ birbirinden ayırt edilir."""
        return self.meta.get("kirpma_turu") == "tanh"

    def clip_text(self, clip: float) -> str:
        return (f"model girdilerini ±{clip:g}σ ötesinde yumuşak biçimde sıkıştırdığı (tanh) için bu bölgedeki farkları zayıf görür"
                if self.soft_clip else f"model girdilerini ±{clip:g}σ'da kırptığı için sınırın ötesindeki farkları görmez")

    @property
    def is_ppo(self) -> bool:
        return self.meta.get("model_turu") == "ppo"

    @property
    def label(self) -> str:
        # Not: 'PPO' bir TEFAS fon koduyla çakıştığı için metinde 'NN-politika' denir (guard dışlanan kod taraması).
        return f"{self.version} ({self.model_dir.name}, {'NN-politika' if self.is_ppo else 'DQN'})"

    def weights(self, a: int) -> dict[str, float]:
        return {self.cats[j]: float(self.A[a][j]) for j in range(len(self.cats)) if self.A[a][j] > 0}

    def feature(self, name: str) -> int:
        n = name.lower().strip()
        exact = [i for i, f in enumerate(self.names) if f.lower() == n]
        hits = exact or [i for i, f in enumerate(self.names) if n in f.lower()]
        if len(hits) != 1:
            raise ToolError(f"Gösterge '{name}' {'bulunamadı' if not hits else 'belirsiz'}. Göstergeler: {'; '.join(self.names)}")
        return hits[0]


def _dataset(feature_set: str):
    from .rl_api import _dataset as shared  # aynı önbellek (fiyat dosyası + config değişince yenilenir)
    return shared(feature_set)


def _persona(args: dict) -> Persona:
    if args.get("persona"):
        return Persona(**args["persona"])
    pid = args.get("persona_id")
    path = PERSONA_DIR / f"{str(pid).lower()}.json"
    if not pid or not path.exists():
        raise ToolError("persona_id (P01-P05) ya da persona nesnesi gerekli.")
    return Persona(**json.loads(path.read_text(encoding="utf-8")))


def context(args: dict, persona_override: dict | None = None) -> Ctx:
    version = args.get("model") or DEFAULT_MODEL
    net, meta, d = load(version)
    ds = _dataset(meta.get("gosterge_seti", "v1"))
    if ds["feature_names"] != meta["gosterge_adlari"]:
        raise ToolError("Modelin gösterge listesi güncel veriyle uyuşmuyor; model yeniden eğitilmeli.")
    full = np.where(np.isfinite(ds["features_raw"]).all(axis=1))[0]
    if not len(full):
        raise ToolError("Göstergeleri tam hesaplanabilen tarih yok.")
    today = int(full[-1])
    notes = ([f"Son veri tarihi {ds['today']}; bazı göstergeler eksik olduğundan karar {ds['dates'][today].date()} "
              f"tarihli göstergelerle verildi."] if today != ds["today_index"] else [])
    persona = _persona(args)
    rm_answers = args.get("rm_answers") or {}
    persona = apply_answers(persona, {**rm_answers, **(persona_override or {})})
    view, questions = interpret(persona, rm_answers)
    if questions:
        raise ToolError("Persona RM teyidi bekliyor: " + " | ".join(q.question for q in questions))
    cats = meta.get("kategoriler") or rc.CATS_V1
    A = np.array(meta["aksiyonlar"])
    prof = rc.profile_from_persona(persona, view)
    mask, why = rc.profile_mask(prof, A, meta["kategori_rd"], cats)
    prev, cur_notes = rc.current_vector(args.get("mevcut_dagilim"), cats)
    return Ctx(version, net, meta, d, ds, cats, A, np.array(meta["gosterge_ort"]), np.array(meta["gosterge_std"]),
               list(meta["gosterge_adlari"]), today, ds["features_raw"][today].astype(float), persona, prof, mask, why,
               prev, notes + cur_notes)


def _theme_of(name: str) -> str:
    n = name.lower()
    for theme, pats in THEMES:
        if any(p in n for p in pats):
            return theme
    return "Diğer"


def themes(ctx: Ctx) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i, n in enumerate(ctx.names):
        out.setdefault(_theme_of(n), []).append(i)
    return out


def _wvec(ctx: Ctx, a: int) -> np.ndarray:
    return ctx.A[a].astype(float)


def _diff_text(ctx: Ctx, before: dict, after: dict) -> list[dict]:
    keys = sorted(set(before) | set(after), key=lambda c: -(abs(after.get(c, 0) - before.get(c, 0))))
    return [{"kategori": c, "ad": CAT_NAMES.get(c, c), "once": round(before.get(c, 0) * 100), "sonra": round(after.get(c, 0) * 100)}
            for c in keys if abs(after.get(c, 0) - before.get(c, 0)) > 1e-9]


# --- Araçlar --------------------------------------------------------------------------------------

def rl_karar(args: dict) -> dict:
    ctx = context(args)
    q = ctx.q(ctx.z)[0]
    order = np.argsort(-q)
    order = [int(a) for a in order if np.isfinite(q[a])][:4]
    if not order:
        raise ToolError("Profil maskesiyle izinli hiçbir dağılım yok.")
    best = order[0]
    gate = ctx.gate(q, best)
    applied = gate["aksiyon"] if gate else best
    alts = [{"sira": i + 1, "dagilim": {c: round(w * 100) for c, w in ctx.weights(a).items()},
             "deger": round(float(q[a]), 4), "fark": round(float(q[best] - q[a]), 4)} for i, a in enumerate(order)]
    spread = float(np.nanstd(q[np.isfinite(q)]))
    margin = float(q[best] - q[order[1]]) if len(order) > 1 else None
    probs = None
    if ctx.is_ppo:
        probs = ctx.net.probs(ctx.x(ctx.z)[None, :], ctx.mask)[0]
        for a in alts:
            a["olasilik"] = round(float(probs[order[a["sira"] - 1]]) * 100, 1)
    belief = rc.belief_table(ctx.net, ctx.x(ctx.z)[None, :], ctx.cats, ctx.mask, ctx.A)
    return {
        "model": ctx.label, "model_olusturma": ctx.meta.get("olusturma"), "persona_id": ctx.persona.persona_id,
        "karar_tarihi": str(ctx.ds["dates"][ctx.today].date()),
        "kategori_dagilimi": {c: round(w * 100) for c, w in ctx.weights(applied).items()},
        "model_tercihi": {c: round(w * 100) for c, w in ctx.weights(best).items()},
        "guvenli_iyilestirme": gate and {k: v for k, v in gate.items() if k != "aksiyon"},
        "alternatifler": alts,
        "karar_marji": None if margin is None else round(margin, 4),
        "marj_gorece": None if margin is None or spread == 0 else round(margin / spread, 2),
        "marj_yorumu": _margin_text(margin, spread, probs, order),
        "secilen_olasilik": None if probs is None else round(float(probs[best]) * 100, 1),
        "beklenen_dagilim": None if probs is None else {c: round(float(w) * 100, 1) for c, w in zip(ctx.cats, probs @ ctx.A) if w >= 0.005},
        "beklenti": belief,
        "dislanan_kategoriler": ctx.excluded,
        "notlar": ctx.notes,
        "model_turu": "NN-politika (politika olasılıkları)" if ctx.is_ppo else "DQN (Q değerleri)",
        "birim": ("dağılım yüzde puan; 'olasilik' politikanın o dağılımı seçme olasılığı (%), 'deger' logit (göreli)"
                  if ctx.is_ppo else "dağılım yüzde puan; 'deger' modelin bölüm boyu reel ödül tahmini (göreli karşılaştırma içindir)"),
    }


def _margin_text(margin, spread, probs, order) -> str:
    if margin is None:
        return "tek seçenek"
    if probs is not None:   # PPO: politikanın seçilen dağılıma verdiği olasılık ve ikinciyle farkı
        p1, p2 = float(probs[order[0]]), float(probs[order[1]])
        return ("dar (alternatiflerle neredeyse eşit)" if p1 - p2 < 0.1 else "orta" if p1 < 0.5 else "geniş")
    return ("dar (alternatiflerle neredeyse eşit)" if margin / (spread or 1) < 0.1
            else "orta" if margin / (spread or 1) < 0.5 else "geniş")


def rl_neden(args: dict) -> dict:
    """Kesin tema Shapley'i: v(S) = S'deki temaların bugünkü değerde, diğerlerinin eğitim ortalamasında (z=0)
    olduğu durumda modelin seçtiği dağılım. φ_g = temanın 'ortalama piyasa → bugün' dağılım farkına katkısı (puan)."""
    ctx = context(args)
    th = themes(ctx)
    groups = list(th)
    G = len(groups)
    z_today, base = ctx.z, np.zeros_like(ctx.z)
    subsets = [s for k in range(G + 1) for s in combinations(range(G), k)]
    Z = []
    for s in subsets:
        z = base.copy()
        for g in s:
            z[th[groups[g]]] = z_today[th[groups[g]]]
        Z.append(z)
    acts = ctx.decide(np.stack(Z))
    val = {s: _wvec(ctx, int(a)) for s, a in zip(subsets, acts)}
    phi = {g: np.zeros(len(ctx.cats)) for g in groups}
    for gi, g in enumerate(groups):
        for s in subsets:
            if gi in s:
                continue
            w = factorial(len(s)) * factorial(G - len(s) - 1) / factorial(G)
            phi[g] += w * (val[tuple(sorted(s + (gi,)))] - val[s])
    base_w, today_w = val[()], val[tuple(range(G))]
    rows = []
    for g in groups:
        v = phi[g]
        etki = 0.5 * float(np.abs(v).sum())
        rows.append({"tema": g, "etki_puan": round(etki * 100, 1),
                     "gostergeler": [{"ad": ctx.names[i], "bugun": round(float(ctx.raw[i]), 4),
                                      "egitim_ort": round(float(ctx.mu[i]), 4), "z": round(float(z_today[i]), 2),
                                      "gorunur_aralik": _visible(ctx, i), "gecmis_aralik": _history(ctx, i),
                                      "senaryo_araligi": _scenario_range(ctx, i), "gecmis_getiri": _is_trailing(ctx.names[i]),
                                      "kirpma_turu": "tanh" if ctx.soft_clip else "sert"}
                                     for i in th[g]],
                     "kategori_katkilari": [{"kategori": ctx.cats[j], "ad": CAT_NAMES.get(ctx.cats[j], ctx.cats[j]),
                                             "puan": round(float(v[j]) * 100, 1)}
                                            for j in np.argsort(-np.abs(v)) if abs(v[j]) > 1e-9]})
    rows.sort(key=lambda r: -r["etki_puan"])
    return {
        "model": ctx.version, "persona_id": ctx.persona.persona_id, "yontem": "Kesin Shapley (tema düzeyinde, gerçek ağ üzerinde)",
        "taban": "Göstergeler eğitim dönemi ortalamasında (z=0); profil ve mevcut dağılım aynı",
        "kapsam": "Modelin kendi tercihini açıklar (güvenli iyileştirme kapısı öncesi).",
        "ortalama_piyasada_karar": {ctx.cats[j]: round(float(base_w[j]) * 100) for j in range(len(ctx.cats)) if base_w[j] > 0},
        "bugunku_karar": {ctx.cats[j]: round(float(today_w[j]) * 100) for j in range(len(ctx.cats)) if today_w[j] > 0},
        "toplam_kayma_puan": round(0.5 * float(np.abs(today_w - base_w).sum()) * 100, 1),
        "temalar": rows,
        "profil_etkisi": {"dislanan_kategoriler": ctx.excluded,
                          "not": "Profilin etkisi iki yoldan gelir: maske (izinsiz kategoriler hiç seçilemez) ve ağın profil girdisi. "
                                 "'ortalama_piyasada_karar' profilin piyasa koşullarından bağımsız tercihini gösterir."},
        "ileri_gecis_sayisi": len(subsets),
        "okuma_notu": "Katkıların toplamı 'bugunku_karar − ortalama_piyasada_karar' farkına eşittir. İki temanın katkısı aynıysa "
                      "karar ancak ikisi birlikte değiştiğinde değişiyor demektir (etkileşim); Shapley bu etkiyi aralarında eşit böler.",
    }


def _visible(ctx: Ctx, k: int) -> list[float] | None:
    """±kırpma·σ aralığı. Sert kırpmada dışındaki değerler sınırla aynı görünür; tanh kırpmada sıkıştırılır (ayırt edilir ama zayıf)."""
    clip = ctx.meta.get("ozellik_kirp")
    if not clip:
        return None
    return [round(float(ctx.mu[k] - clip * ctx.sd[k]), 4), round(float(ctx.mu[k] + clip * ctx.sd[k]), 4)]


def _history(ctx: Ctx, k: int) -> list[float] | None:
    """Göstergenin veri başlangıcından bugüne gerçekte aldığı en düşük ve en yüksek değer."""
    dates = ctx.ds["dates"]
    start = ctx.meta.get("veri_baslangic")
    col = ctx.ds["features_raw"][: ctx.today + 1, k].astype(float)
    if start:
        col = col[np.asarray(dates[: ctx.today + 1] >= np.datetime64(start))]
    col = col[np.isfinite(col)]
    return [round(float(col.min()), 4), round(float(col.max()), 4)] if len(col) else None


def _scenario_range(ctx: Ctx, k: int) -> list[float]:
    """Senaryo için anlamlı aralık: geçmişte görülmüş değerler ile modelin ayırt edebildiği aralığın kesişimi.
    Bunun dışındaki değerler ya hiç yaşanmamıştır ya da model onları sınırdaki değerle aynı görür. Bugünkü değer hep dahildir."""
    lo, hi = -np.inf, np.inf
    for r in (_history(ctx, k), None if ctx.soft_clip else _visible(ctx, k)):
        if r:
            lo, hi = max(lo, r[0]), min(hi, r[1])
    today = float(ctx.raw[k])
    return [round(float(min(lo, today)), 4), round(float(max(hi, today)), 4)]


def _is_trailing(name: str) -> bool:
    """Geriye dönük gerçekleşmiş getiri / değişim göstergesi mi (beklenti değil)."""
    n = name.lower()
    return "getiri" in n or "değişim" in n


def market_sensitivity(ctx: Ctx, points: int = 21) -> dict:
    """Her göstergeyi tek başına görünür aralığı boyunca tarar (diğerleri bugünkü değerde) ve kararı değiştirenleri sayar.
    Ayrıca rastgele piyasa durumlarında modelin kaç farklı karar ürettiğine bakar (0 duyarlılık = profil sabit dağılımı)."""
    a0 = int(ctx.decide(ctx.z)[0])
    clip = float(ctx.meta.get("ozellik_kirp") or 3.0)
    movers = []
    for k in range(len(ctx.names)):
        Z = np.repeat(ctx.z[None, :], points, axis=0)
        Z[:, k] = np.linspace(-clip, clip, points)
        if (ctx.decide(Z) != a0).any():
            movers.append(ctx.names[k])
    rnd = np.random.default_rng(0).normal(0, 1, (300, len(ctx.names)))
    distinct = len(set(ctx.decide(rnd).tolist()))
    return {"kararı_degistiren_gosterge": len(movers), "toplam_gosterge": len(ctx.names), "gostergeler": movers,
            "rastgele_300_piyasada_farkli_karar": distinct,
            "okuma": f"300 rastgele piyasa durumunda model toplam {distinct} farklı dağılım önerdi (bu bir senaryo sayısı değil, "
                     f"farklı dağılım sayısıdır). {len(movers)}/{len(ctx.names)} gösterge tek başına kararı değiştirebiliyor."}


def _apply_scenario(ctx: Ctx, spec: dict) -> tuple[np.ndarray, list[str], list[int]]:
    """Tutarlı senaryo: aynı ekonomik olay ilgili TÜM göstergelere birlikte uygulanır (model eğitimindeki what-if takımıyla aynı).
    spec: {"tur": "faiz_indirimi" | ... } (hazır senaryo) ya da {"tur": "rate"|"infl"|"usd"|"equity"|"cat:<kategori>", "buyukluk": d}."""
    W = whatif()
    tur = spec.get("tur")
    if tur in W.SCENARIOS:
        kind, d = W.SCENARIOS[tur][0], float(spec.get("buyukluk", W.SCENARIOS[tur][1]))
    elif tur in ("rate", "infl", "usd", "equity") or str(tur).startswith("cat:"):
        if "buyukluk" not in spec:
            raise ToolError("Senaryo türü verildiyse 'buyukluk' de verilmeli (oran birimi: 5 puan = 0.05).")
        kind, d = tur, float(spec["buyukluk"])
    else:
        raise ToolError(f"Bilinmeyen senaryo '{tur}'. Hazır senaryolar: {', '.join(W.SCENARIOS)}; "
                        "ya da tur rate/infl/usd/equity/cat:<kategori> + buyukluk.")
    new = W.apply(ctx.names, ctx.raw[None, :], kind, d)[0]
    changed = [k for k in range(len(new)) if abs(new[k] - ctx.raw[k]) > 1e-12]
    applied = [f"Senaryo {tur} ({d:+.2f}): " + ", ".join(f"{ctx.names[k]} {gval(ctx.names[k], ctx.raw[k])} → {gval(ctx.names[k], new[k])}"
                                                        for k in changed)]
    return (new - ctx.mu) / ctx.sd, applied, changed


def _expected(ctx: Ctx, z: np.ndarray) -> tuple[dict, float] | None:
    """NN-politika: olasılık ağırlıklı beklenen dağılım ve en olası dağılımın olasılığı."""
    if not ctx.is_ppo:
        return None
    pr = ctx.net.probs(ctx.x(z)[None, :], ctx.mask)[0]
    w = pr @ ctx.A
    return ({c: round(float(w[j]) * 100, 1) for j, c in enumerate(ctx.cats) if w[j] >= 0.005}, round(float(pr.max()) * 100, 1))


def _apply_indicator_changes(ctx: Ctx, changes: list[dict], z0: np.ndarray | None = None) -> tuple[np.ndarray, list[str]]:
    z, applied = (ctx.z if z0 is None else z0).copy(), []
    for ch in changes or []:
        k = ctx.feature(ch["ad"])
        old = float(ctx.mu[k] + z[k] * ctx.sd[k])
        ratio = abs(ctx.mu[k]) < 5 and abs(ctx.sd[k]) < 5      # gösterge oran biriminde (0,37 = %37)
        if "deger" in ch:
            new = float(ch["deger"])
            if ratio and abs(new) >= 2:                          # '%40' kastedilip 40 yazılmış
                new /= 100
                applied.append(f"{ctx.names[k]}: değer {ch['deger']} yüzde olarak yorumlandı ({gval(ctx.names[k], new)})")
        elif "degisim" in ch:
            d = float(ch["degisim"])
            if ratio and abs(d) >= 1:                            # '5 puan' kastedilip 5 yazılmış
                d /= 100
                applied.append(f"{ctx.names[k]}: değişim {ch['degisim']} yüzde puan olarak yorumlandı ({d:+.2f} oran)")
            new = old + d
        elif "sigma" in ch:
            new = old + float(ch["sigma"]) * float(ctx.sd[k])
        else:
            raise ToolError(f"'{ch['ad']}' için 'deger', 'degisim' veya 'sigma' verilmeli.")
        z[k] = (new - ctx.mu[k]) / ctx.sd[k]
        applied.append(f"{ctx.names[k]}: {gval(ctx.names[k], old)} → {gval(ctx.names[k], new)}")
    return z, applied


def rl_ne_olursa(args: dict) -> dict:
    base = context(args)
    a0, _, g0 = base.final(base.z)
    ctx = context(args, persona_override=args.get("persona_degisiklikleri")) if args.get("persona_degisiklikleri") else base
    z0, applied, changed = ctx.z, [], []
    if args.get("senaryo"):
        z0, applied, changed = _apply_scenario(ctx, args["senaryo"])
    z, more = _apply_indicator_changes(ctx, args.get("gostergeler") or [], z0)
    applied += more
    a1, m1, g1 = ctx.final(z)
    e0, e1 = _expected(base, base.z), _expected(ctx, z)
    ood = [ctx.names[k] for k in range(len(z)) if abs(z[k]) > decision_config()["rl"]["ood_esik_sigma"]]
    clip = ctx.meta.get("ozellik_kirp")
    clipped = [ctx.names[k] for k in range(len(z)) if clip and abs(z[k]) > clip]
    w0, w1 = base.weights(a0), ctx.weights(a1)
    changed = list(dict.fromkeys(changed + [ctx.feature(ch["ad"]) for ch in args.get("gostergeler") or []]))
    unseen, seen_as = [], []
    for k in changed:
        new = float(ctx.mu[k] + z[k] * ctx.sd[k])
        h = _history(ctx, k)
        if h and not (h[0] - 1e-9 <= new <= h[1] + 1e-9):
            unseen.append(f"{ctx.names[k]} {gval(ctx.names[k], new)} (geçmiş aralık {gval(ctx.names[k], h[0])}–{gval(ctx.names[k], h[1])})")
        if clip and abs(z[k]) > clip:
            seen = float(ctx.mu[k] + rc.model_input(np.array([z[k]]), ctx.meta)[0] * ctx.sd[k])
            seen_as.append({"ad": ctx.names[k], "girilen": round(new, 4), "modelin_gordugu": round(seen, 4),
                            "kirpma_turu": "tanh" if ctx.soft_clip else "sert"})
    return {
        "model": ctx.version, "persona_id": ctx.persona.persona_id,
        "uygulanan_degisiklikler": applied + [f"persona.{k} = {v}" for k, v in (args.get("persona_degisiklikleri") or {}).items()],
        "onceki_dagilim": {c: round(w * 100) for c, w in w0.items()},
        "yeni_dagilim": {c: round(w * 100) for c, w in w1.items()},
        "karar_degisti": a0 != a1, "degisen_kategoriler": _diff_text(ctx, w0, w1),
        "yeni_kararin_kaynagi": g1["kaynak"] if g1 else "model",
        "modelin_yeni_tercihi": {c: round(w * 100) for c, w in ctx.weights(m1).items()},
        "yeni_dislanan_kategoriler": [e for e in ctx.excluded if e not in base.excluded],
        "modelin_gordugu_degerler": seen_as,
        "beklenen_dagilim": None if e1 is None else {
            "once": e0[0], "sonra": e1[0], "en_olasi_olasilik_once": e0[1], "en_olasi_olasilik_sonra": e1[1],
            "not": "Politikanın olasılık ağırlıklı dağılımı. En olası dağılımın olasılığı düşükse (politika düz) karar "
                   "(argmax) gürültüye duyarlıdır; yönü beklenen dağılımdan okumak daha güvenilirdir."},
        "uyarilar": ([f"Model bu senaryoda kararsız: en olası dağılımın olasılığı %{str(e1[1]).replace('.', ',')}. Tek dağılım yerine olasılık ağırlıklı "
                      f"beklenen dağılıma bakın."] if e1 and e1[1] < 20 else [])
                    + ([f"Geçmiş veride hiç görülmemiş değer: {'; '.join(unseen)}. Bu senaryo gerçekçi olmayabilir."] if unseen else [])
                    + ([f"Eğitim dağılımı dışı (±{decision_config()['rl']['ood_esik_sigma']:.0f}σ): {', '.join(ood)}"] if ood else [])
                    + ([f"Şu göstergeler (bugün ya da değişiklik sonrası) ±{clip:g}σ dışında; {ctx.clip_text(clip)}: "
                        f"{', '.join(clipped)}"] if clipped else []),
    }


def rl_esik(args: dict) -> dict:
    ctx = context(args)
    k = ctx.feature(args["gosterge"])
    clip = float(ctx.meta.get("ozellik_kirp") or 3.0)
    lo, hi = -clip - 0.25, clip + 0.25
    n = int(args.get("nokta", 121))
    grid = np.linspace(lo, hi, n)
    Z = np.repeat(ctx.z[None, :], n, axis=0)
    Z[:, k] = grid
    acts = ctx.decide(Z)
    raw = ctx.mu[k] + grid * ctx.sd[k]
    segs, start = [], 0
    for i in range(1, n + 1):
        if i == n or acts[i] != acts[start]:
            segs.append({"aralik": [round(float(raw[start]), 4), round(float(raw[i - 1]), 4)],
                         "dagilim": {c: round(w * 100) for c, w in ctx.weights(int(acts[start])).items()}})
            start = i
    today_v = float(ctx.raw[k])
    a_today = int(ctx.decide(ctx.z)[0])
    flips_down = [s for s in segs if s["aralik"][1] < today_v and s["dagilim"] != {c: round(w * 100) for c, w in ctx.weights(a_today).items()}]
    flips_up = [s for s in segs if s["aralik"][0] > today_v and s["dagilim"] != {c: round(w * 100) for c, w in ctx.weights(a_today).items()}]
    return {
        "model": ctx.version, "persona_id": ctx.persona.persona_id, "gosterge": ctx.names[k],
        "bugun": round(today_v, 4), "egitim_ort": round(float(ctx.mu[k]), 4), "egitim_std": round(float(ctx.sd[k]), 4),
        "bolgeler": segs,
        "asagi_yonde_ilk_degisim": flips_down[-1] if flips_down else None,
        "yukari_yonde_ilk_degisim": flips_up[0] if flips_up else None,
        "not": f"Diğer tüm göstergeler bugünkü değerinde sabit; tarama ±{clip:g}σ "
               + ("(model girdileri bu sınırın ötesinde tanh ile sıkıştırılır)." if ctx.soft_clip else "(model girdileri bu sınırda kırpılır)."),
    }


def rl_benzer_donemler(args: dict) -> dict:
    ctx = context(args)
    k = int(args.get("adet", 5))
    clip = ctx.meta.get("ozellik_kirp") or 3.0
    real = ctx.ds["real"]
    n_real = len(real)
    F = ctx.ds["features_raw"][:n_real]
    ok = np.where(np.isfinite(F).all(axis=1) & np.isfinite(real).all(axis=1))[0]
    Zc = rc.model_input((F[ok] - ctx.mu) / ctx.sd, {**ctx.meta, "ozellik_kirp": clip})
    d = np.sqrt(((Zc - rc.model_input(ctx.z, {**ctx.meta, "ozellik_kirp": clip})) ** 2).mean(axis=1))
    # Aynı dönemden art arda günleri tekrarlamamak için seçilenler arasında en az bir adım (≈3 ay) aralık bırakılır
    step_days = int(ctx.meta["rl"]["adim_takvim_gunu"])
    chosen: list[int] = []
    for i in np.argsort(d):
        t = ctx.ds["dates"][ok[i]]
        if all(abs((t - ctx.ds["dates"][ok[j]]).days) >= step_days for j in chosen):
            chosen.append(int(i))
        if len(chosen) == k:
            break
    period = ctx.meta.get("donem", {})

    def which(t):
        s = str(t.date())
        for name in ("test", "dogrulama", "egitim"):
            span = period.get(name)
            if span and span[0] <= s <= span[1]:
                return name
        first = (period.get("egitim") or [None])[0]
        return "egitim_oncesi" if first and s < first else "kapsam_disi"  # modelin eğitimde görmediği dönem

    acts = ctx.decide(np.stack([(F[ok[i]] - ctx.mu) / ctx.sd for i in chosen])) if chosen else []
    rows = []
    for a, i in zip(acts, chosen):
        idx = ok[i]
        w = _wvec(ctx, int(a))
        r = real[idx]
        rows.append({"tarih": str(ctx.ds["dates"][idx].date()), "donem": which(ctx.ds["dates"][idx]), "uzaklik": round(float(d[i]), 3),
                     "o_zamanki_karar": {ctx.cats[j]: round(float(w[j]) * 100) for j in range(len(ctx.cats)) if w[j] > 0},
                     "kararin_gerceklesen_reel_getirisi": round(float(w @ r) * 100, 2),
                     "kategori_reel_getirileri": {ctx.cats[j]: round(float(r[j]) * 100, 2) for j in range(len(ctx.cats))}})
    return {"model": ctx.version, "persona_id": ctx.persona.persona_id, "karar_tarihi": str(ctx.ds["dates"][ctx.today].date()),
            "benzer_donemler": rows,
            "birim": "getiriler yüzde, sonraki karar adımı (≈3 ay), vergi sonrası reel",
            "not": "Uzaklık: kırpılmış standart göstergeler arasındaki ortalama karesel farkın kökü (0 = birebir aynı koşullar). "
                   "'egitim_oncesi' dönemler modelin eğitimde hiç görmediği tarihlerdir. "
                   "Geçmiş dönemlerdeki gerçekleşme gelecekteki sonucun göstergesi değildir."}


def rl_guvenilirlik(args: dict) -> dict:
    ctx = context(args)
    karar = rl_karar(args)
    thr = decision_config()["rl"]["ood_esik_sigma"]
    z = ctx.z
    ood = [{"ad": ctx.names[k], "z": round(float(z[k]), 1)} for k in range(len(z)) if abs(z[k]) > thr]
    test = (ctx.meta.get("test") or {})
    pt = (test.get("personalar") or {}).get(ctx.persona.persona_id)
    dqn_key = next((k for k in (pt or {}) if k.startswith(("DQN", "Model"))), None)
    kiyas, uyarilar = [], []
    if pt and dqn_key:
        d = pt[dqn_key]
        better = []
        for name, m in pt.items():
            name = name.replace("PPO", "NN-politika")   # fon koduyla çakışmasın
            r1 = lambda k: None if m.get(k) is None else round(m[k] * 100, 1)
            kiyas.append({"politika": name, "ortalama_reel_kazanc": r1("ortalama_reel_kazanc"), "kotu_yuzde5": r1("kotu_yuzde5"),
                          "anapara_alti_bitis_olasiligi": r1("anapara_alti_bitis_olasiligi")})
            if name != dqn_key and m["ortalama_reel_kazanc"] > d["ortalama_reel_kazanc"] + 1e-9:
                better.append(f"'{name}' {pct(m['ortalama_reel_kazanc'])}")
        if better:
            uyarilar.append(f"Test döneminde (eğitimde görülmemiş) bu personada şu basit politikalar modelden daha yüksek ortalama reel "
                            f"kazanç sağladı: {', '.join(better)}; model {pct(d['ortalama_reel_kazanc'])}. Modelin kararı bu kıyasla "
                            f"birlikte değerlendirilmeli.")
    cal = (ctx.meta.get("inanc_kalibrasyon") or {}).get("dogrulama", {}).get("kategoriler", {})
    cov = [v["kapsama_1sigma"] for v in cal.values() if v.get("kapsama_1sigma") is not None]
    if cov and np.mean(cov) < 0.5:
        uyarilar.append(f"Modelin getiri beklentisi belirsizliği olduğundan dar gösteriyor: ±1σ aralığının doğrulamadaki kapsama oranı "
                        f"ortalama {pct(float(np.mean(cov)), 0)} (iyi kalibre edilmiş bir modelde ≈%68).")
    if ood:
        uyarilar.append("Bazı göstergeler eğitim döneminin dışında; model bu bölgede ekstrapolasyon yapıyor.")
    clip = ctx.meta.get("ozellik_kirp")
    sat = [{"ad": ctx.names[k], "z": round(float(z[k]), 1)} for k in range(len(z)) if clip and abs(z[k]) > clip]
    if sat:
        uyarilar.append(f"Bugün {len(sat)} gösterge eğitim ortalamasından ±{clip:g}σ'dan fazla uzakta ("
                        + ", ".join(f"{s['ad']} {s['z']:+.1f}σ".replace(".", ",") for s in sat)
                        + f"); {ctx.clip_text(clip)}.")
    sens = market_sensitivity(ctx)
    if sens["kararı_degistiren_gosterge"] == 0 and sens["rastgele_300_piyasada_farkli_karar"] <= 1:
        uyarilar.append("Model bu profil için piyasa göstergelerine duyarsız: hiçbir gösterge tek başına kararı değiştirmiyor ve "
                        "300 farklı rastgele piyasa durumunda aynı dağılımı öneriyor. Öneri pratikte profile bağlı sabit bir "
                        "dağılımdır; piyasa what-if senaryoları bu modelde sonucu değiştirmez.")
    elif sens["kararı_degistiren_gosterge"] == 0:
        uyarilar.append(f"Hiçbir gösterge tek başına kararı değiştirmiyor; karar yalnızca birden çok göstergenin birlikte değişmesine "
                        f"tepki veriyor (300 rastgele piyasada {sens['rastgele_300_piyasada_farkli_karar']} farklı karar).")
    if karar["marj_yorumu"].startswith("dar"):
        uyarilar.append("Karar marjı dar: en iyi alternatiflerle neredeyse eşit değerde; küçük veri değişimleri kararı değiştirebilir.")
    expl = None
    path = ctx.model_dir / "aciklama.json"
    if path.exists():
        e = json.loads(path.read_text(encoding="utf-8"))
        if e.get("model_olusturma") == ctx.meta.get("olusturma"):
            expl = e.get("sadakat")
    unseen = test.get("gorulmemis_profiller")
    return {
        "persona_id": ctx.persona.persona_id, "karar_tarihi": karar["karar_tarihi"],
        "egitim_donemi": ctx.meta.get("donem"),
        "dagilim_disi_gostergeler": ood, "kirpilan_gostergeler": sat, "karar_marji": karar["marj_yorumu"],
        "piyasa_duyarliligi": sens,
        "persona_test_kiyasi": kiyas or None,
        "gorulmemis_profil_testi": ({k: {"ortalama_reel_kazanc": round(v["ortalama_reel_kazanc"] * 100, 1)}
                                     for k, v in unseen.items() if isinstance(v, dict) and v.get("ortalama_reel_kazanc") is not None}
                                    if isinstance(unseen, dict) else None),
        "model": ctx.label, "test_notu": test.get("not"),
        "inanc_kalibrasyonu_ort_kapsama_1sigma": round(float(np.mean(cov)), 2) if cov else None,
        "ebm_vekil_sadakati": expl,
        "veri_notlari": ctx.meta.get("veri_notlari"),
        "uyarilar": uyarilar,
        "birim": "kazançlar test dönemi senaryolarında yüzde (reel, vergi sonrası)",
    }


TOOLS = {"rl_karar": rl_karar, "rl_neden": rl_neden, "rl_ne_olursa": rl_ne_olursa, "rl_esik": rl_esik,
         "rl_benzer_donemler": rl_benzer_donemler, "rl_guvenilirlik": rl_guvenilirlik}


def call(name: str, args: dict) -> dict:
    if name not in TOOLS:
        raise ToolError(f"Bilinmeyen araç '{name}'. Araçlar: {', '.join(TOOLS)}")
    return TOOLS[name](args or {})


_COMMON = {
    "persona_id": {"type": "string", "description": "P01-P05"},
    "model": {"type": "string", "enum": list(MODELS), "description": "Varsayılan v2"},
    "mevcut_dagilim": {"type": "object", "description": "RM'in girdiği mevcut kategori dağılımı (kategori → oran 0-1)"},
}


def schemas() -> list[dict]:
    """LLM tool tanımları (OpenAI / Ollama 'tools' biçimi)."""
    def fn(name, desc, props=None, req=None):
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": {**_COMMON, **(props or {})}, "required": ["persona_id"] + (req or [])}}}
    gost = {"type": "array", "items": {"type": "object", "properties": {
        "ad": {"type": "string"}, "deger": {"type": "number"}, "degisim": {"type": "number"}, "sigma": {"type": "number"}},
        "required": ["ad"]}, "description": "Gösterge değişiklikleri: yeni değer (deger), fark (degisim) veya std cinsinden (sigma). "
                                            "Faiz, getiri, TÜFE, kur değişimi gibi göstergeler ORAN birimindedir: %37 = 0.37; "
                                            "'5 puan düşüş' = degisim -0.05; '%40 olursa' = deger 0.40."}
    return [
        fn("rl_karar", "RL kategori modelinin bugünkü dağılımı, en iyi alternatifler, karar marjı ve kategori başına getiri beklentisi."),
        fn("rl_neden", "Bugünkü kararın ortalama piyasa koşullarındaki karardan farkını gösterge temalarına (faiz, enflasyon, kur, hisse, "
                       "TL fonlar) paylaştıran kesin Shapley açıklaması. Gerçek model üzerinde hesaplanır."),
        fn("rl_ne_olursa", "Karşı-olgusal: göstergeler veya persona alanları değişirse modelin gerçek yeni kararı.",
           {"senaryo": {"type": "object", "properties": {"tur": {"type": "string"}, "buyukluk": {"type": "number"}},
                        "description": "Tutarlı makro senaryo (tercih edilir; ilgili tüm göstergeleri birlikte değiştirir): tur = "
                                       "faiz_indirimi, faiz_artisi, enflasyon_artisi, enflasyon_dususu, kur_soku, kur_gevsemesi, "
                                       "borsa_dususu, borsa_yukselisi; isteğe bağlı buyukluk oran biriminde (5 puan = 0.05)."},
            "gostergeler": gost, "persona_degisiklikleri": {"type": "object", "description": "Persona alanı → yeni değer"}}),
        fn("rl_esik", "Bir göstergeyi tarayıp kararın değiştiği eşikleri (ham birimde) verir.",
           {"gosterge": {"type": "string", "description": "Gösterge adı veya adının bir parçası"}}, ["gosterge"]),
        fn("rl_benzer_donemler", "Bugüne en çok benzeyen geçmiş karar anları, o zamanki karar ve sonrasında gerçekleşen reel getiriler.",
           {"adet": {"type": "integer", "default": 5}}),
        fn("rl_guvenilirlik", "Modelin bu persona için güvenilirliği: dağılım dışı göstergeler, karar marjı, test dönemi kıyasları, "
                              "beklenti kalibrasyonu ve uyarılar."),
    ]


# --- Standart özet (deterministik; RM kartı ve LLM bağlamı) ---------------------------------------

def _fmt_dist(d: dict) -> str:
    return ", ".join(f"{CAT_NAMES.get(c, c)} %{v}" for c, v in sorted(d.items(), key=lambda kv: -kv[1]))


def ozet(args: dict) -> dict:
    karar = rl_karar(args)
    neden = rl_neden(args)
    guven = rl_guvenilirlik(args)
    benzer = rl_benzer_donemler({**args, "adet": 3})
    ctx = context(args)
    # En etkili iki tema için, temanın içinde kararı en çok etkileyen tek göstergenin eşiği
    esikler = []
    a_today = int(ctx.decide(ctx.z)[0])
    w_today = _wvec(ctx, a_today)
    th = themes(ctx)
    for t in [r for r in neden["temalar"] if r["etki_puan"] > 0][:2]:
        best_k, best_eff = None, 0.0
        for k in th[t["tema"]]:
            z = ctx.z.copy()
            z[k] = 0.0
            eff = 0.5 * float(np.abs(_wvec(ctx, int(ctx.decide(z)[0])) - w_today).sum())
            if eff > best_eff:
                best_k, best_eff = k, eff
        if best_k is not None:
            esikler.append(rl_esik({**args, "gosterge": ctx.names[best_k]}))

    lines = {"karar": f"Model ({karar['model']}, {karar['karar_tarihi']} göstergeleriyle) bu profil için şu başlangıç dağılımını "
                      f"seçiyor: {_fmt_dist(karar['model_tercihi'])}. En yakın alternatife göre karar marjı {karar['marj_yorumu']}."}
    gi = karar.get("guvenli_iyilestirme")
    if gi and gi["kaynak"] == "taban_kural":
        lines["karar"] += (f" Ancak güvenli iyileştirme kapısı devrede: modelin önerisi kendi tahminine göre taban kuraldan yalnızca "
                           f"{num(gi['q_farki'], 2)} puan iyi (eşik {num(gi['esik'], 2)}), bu yüzden uygulanan dağılım taban kural: "
                           f"{_fmt_dist(karar['kategori_dagilimi'])}.")
    elif gi:
        lines["karar"] += f" Güvenli iyileştirme kapısı modelin önerisini onayladı (taban kurala göre {num(gi['q_farki'], 2)} puan üstün)."
    top = [t for t in neden["temalar"] if t["etki_puan"] > 0][:3]
    if neden["toplam_kayma_puan"] == 0:
        lines["neden"] = ("Bugünkü piyasa koşulları kararı ortalama koşullardaki karardan farklılaştırmıyor; dağılımı belirleyen "
                          "ağırlıklı olarak profil (risk sınırı, kısıtlar, anapara tercihi).")
    else:
        parts = []
        for t in top:
            kk = [k for k in t["kategori_katkilari"] if abs(k["puan"]) >= 1][:2]
            eff = ", ".join(f"{k['ad']} {'+' if k['puan'] > 0 else '−'}{num(abs(k['puan']), 0)} puan" for k in kk)
            parts.append(f"{t['tema']} ({eff})" if eff else t["tema"])
        lines["neden"] = (f"Ortalama piyasa koşullarında model {_fmt_dist(neden['ortalama_piyasada_karar'])} seçerdi. Bugünkü koşullar "
                          f"dağılımın {num(neden['toplam_kayma_puan'], 0)} puanını kaydırıyor; en etkili temalar: " + "; ".join(parts) + ".")
    esik_txt = []
    for e in esikler:
        for yon, s in (("altına inerse", e["asagi_yonde_ilk_degisim"]), ("üstüne çıkarsa", e["yukari_yonde_ilk_degisim"])):
            if s:
                v = s["aralik"][1] if yon == "altına inerse" else s["aralik"][0]
                esik_txt.append(f"{e['gosterge']} {gval(e['gosterge'], v)} {yon} (bugün {gval(e['gosterge'], e['bugun'])}) "
                                f"dağılım {_fmt_dist(s['dagilim'])} olur")
    lines["esikler"] = ("; ".join(esik_txt) + ".") if esik_txt else         "Taranan aralıkta en etkili göstergelerin tek başına değişimi kararı değiştirmiyor."
    lines["guven"] = " ".join(guven["uyarilar"]) or "Belirgin bir güvenilirlik uyarısı yok."
    if benzer["benzer_donemler"]:
        b = benzer["benzer_donemler"][0]
        donem = {"egitim": "eğitim dönemi", "dogrulama": "doğrulama dönemi", "test": "test dönemi",
                 "egitim_oncesi": "eğitim öncesi, modelin görmediği dönem"}.get(b["donem"], b["donem"])
        lines["benzer"] = (f"Bugüne en çok benzeyen geçmiş an {b['tarih']} ({donem}); model o gün "
                           f"{_fmt_dist(b['o_zamanki_karar'])} seçerdi; bu dağılımın sonraki adımda (≈3 ay) gerçekleşen reel getirisi "
                           f"{pct(b['kararin_gerceklesen_reel_getirisi'] / 100, 2)} oldu.")
    lines["ne_degildir"] = ("Bu bir getiri tahmini değil, geçmiş verilerle yapılan bir simülasyondur. Model yalnızca kural motorunun "
                            "izin verdiği kategoriler arasında seçer; nihai karar RM'indir.")
    return {"model": karar["model"], "persona_id": karar["persona_id"], "metin": lines,
            "araclar": {"rl_karar": karar, "rl_neden": neden, "rl_guvenilirlik": guven, "rl_benzer_donemler": benzer, "rl_esik": esikler}}
