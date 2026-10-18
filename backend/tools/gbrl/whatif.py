"""What-if senaryo takımı (saf numpy; hem Windows backend'inde hem WSL eğitim ortamında kullanılır).

1) Tutarlı şok uygulaması: bir senaryo, aynı ekonomik olayın etkilediği TÜM göstergelere birlikte uygulanır (yalnızca adı geçen
   göstergeye değil). Örn. faiz indirimi politika faizini, TLREF'i, mevduat faizini, bunların değişim göstergelerini ve reel
   mevduat faizini aynı miktarda düşürür; kur şoku tüm USD/TRY ufuklarını ve döviz bazlı kategorilerin TL getirilerini artırır.
2) Yön kontrolleri (ekonomik öncül; tolerans ±2 puan, modelin olasılık ağırlıklı BEKLENEN dağılımı üzerinden):
     faiz ↓  → TL nakit benzeri (mevduat + para piyasası) payı artmamalı      faiz ↑  → azalmamalı
     TÜFE ↑ → TL sabit getiri (mevduat, PP, borçlanma, katılım) payı artmamalı TÜFE ↓ → azalmamalı
     kur ↑  → döviz/altın payı azalmamalı                                     kur ↓  → artmamalı
     borsa ↓ → hisse payı artmamalı                                           borsa ↑ → azalmamalı
     kategori getirisi ↓ (ör. PP'nin geçmiş getirisi −15 puan) → o kategorinin payı artmamalı; ↑ → azalmamalı
   Ölçüler: yanlış yön oranı (tolerans ötesinde ters tepki) ve doğru yön tepki oranı (model gerçekten tepki veriyor mu).
"""
from __future__ import annotations

import numpy as np

TOL = 0.02

RATE = ["Politika faizi (karar)", "TLREF", "Mevduat faizi 1 ay", "Mevduat faizi 3 ay değişimi", "Reel mevduat faizi",
        "Politika faizi 3 ay değişimi", "Politika faizi 6 ay değişimi", "Politika faizi 12 ay değişimi", "TLREF 3 ay değişimi",
        "Mevduat faizi 6 ay değişimi", "Mevduat faizi 12 ay değişimi", "Reel mevduat faizi 3 ay değişimi", "Reel TLREF"]
INFL_UP = ["TÜFE yıllık", "TÜFE 3 ay eğilimi (yıllık)", "TÜFE 6 ay eğilimi (yıllık)"]
INFL_REAL = ["Reel mevduat faizi", "Reel mevduat faizi 3 ay değişimi", "Reel TLREF"]
FX_TRAIL = ("kiymetli_maden", "eurobond_doviz", "yabanci_hisse")
EQ_TRAIL = ("hisse", "bist30_hisse")

GROUPS = {"nakit": ["mevduat_tl", "para_piyasasi"], "tl_sabit": ["mevduat_tl", "para_piyasasi", "borclanma", "katilim"],
          "doviz": ["kiymetli_maden", "yabanci_hisse", "eurobond_doviz"], "hisse": ["hisse", "bist30_hisse"]}
# senaryo: (tür, büyüklük, izlenen grup, beklenen yön: -1 artmamalı / +1 azalmamalı)
SCENARIOS = {
    "faiz_indirimi": ("rate", -0.05, "nakit", -1), "faiz_artisi": ("rate", 0.05, "nakit", +1),
    "enflasyon_artisi": ("infl", 0.10, "tl_sabit", -1), "enflasyon_dususu": ("infl", -0.10, "tl_sabit", +1),
    "kur_soku": ("usd", 0.15, "doviz", +1), "kur_gevsemesi": ("usd", -0.10, "doviz", -1),
    "borsa_dususu": ("equity", -0.15, "hisse", -1), "borsa_yukselisi": ("equity", 0.15, "hisse", +1),
}


def _idx(names, pred):
    return [k for k, n in enumerate(names) if pred(n)]


def apply(names: list[str], raw: np.ndarray, kind: str, d: float) -> np.ndarray:
    """raw: (n, g) ham göstergeler. Senaryoyu ilgili tüm göstergelere uygular."""
    x = np.array(raw, dtype=float, copy=True)
    if kind == "rate":
        for k in _idx(names, lambda n: n in RATE):
            x[:, k] += d
    elif kind == "infl":
        for k in _idx(names, lambda n: n in INFL_UP):
            x[:, k] += d
        for k in _idx(names, lambda n: n in INFL_REAL):
            x[:, k] -= d
    elif kind == "usd":
        mult = lambda n: n.startswith("USD/TRY") or (n.split(" ")[0] in FX_TRAIL and "getiri" in n)
        for k in _idx(names, mult):
            x[:, k] = (1 + x[:, k]) * (1 + d) - 1
    elif kind.startswith("cat:"):      # tek kategorinin geçmiş getirileri (tüm ufuklar), toplamsal
        c = kind[4:]
        for k in _idx(names, lambda n: n.startswith(c + " ") and "getiri" in n):
            x[:, k] += d
    elif kind == "equity":
        mult = lambda n: n.startswith("BIST 100 toplam getiri") or n.startswith("BIST 30 endeksi") or \
            (n.split(" ")[0] in EQ_TRAIL and "getiri" in n)
        for k in _idx(names, mult):
            x[:, k] = (1 + x[:, k]) * (1 + d) - 1
    return x


def squash(z: np.ndarray, mode: str, c: float) -> np.ndarray:
    """Normalize gösterge → ağ girdisi. sert: ±c'de kesme (eski); tanh: c·tanh(z/c) (monoton, uç değerler de görünür)."""
    if mode == "tanh":
        return c * np.tanh(z / c)
    return np.clip(z, -c, c)


CAT_SHOCK = 0.15


def category_scenarios(names, cats):
    """Geçmiş getiri göstergesi olan her kategori için ±%15 puan şoku (izlenen grup: kategorinin kendisi)."""
    out = {}
    for c in cats:
        if any(n.startswith(c + " ") and "getiri" in n for n in names):
            out[f"kat_{c}_dusus"] = (f"cat:{c}", -CAT_SHOCK, c, -1)
            out[f"kat_{c}_artis"] = (f"cat:{c}", CAT_SHOCK, c, +1)
    return out


def evaluate(expected_alloc, names, raw, mu, sd, mode, c, cats, allowed_groups=None, rows=None, M=None, A=None) -> dict:
    """expected_alloc(Zin) → (n, kategori) beklenen dağılım. raw: (n, g) durumlar (her satır bir profil×tarih için; profil
    girdisi expected_alloc içinde). allowed_groups: (n, grup) izinli mi (grubun en az bir kategorisi maskede açık)."""
    base_in = squash((raw - mu) / sd, mode, c)
    w0 = expected_alloc(base_in)
    res, wrong_all, right_all, n_all = {}, 0, 0, 0
    gi = {g: [cats.index(x) for x in v if x in cats] for g, v in GROUPS.items()} | {x: [cats.index(x)] for x in cats}
    scen = dict(SCENARIOS)
    if rows is not None:              # kategori şokları: izinli olma maskeden (rows, M, A)
        scen |= category_scenarios(names, cats)
        allowed_groups = dict(allowed_groups or {})
        for x in cats:
            allowed_groups[x] = (M[rows] & (A[:, cats.index(x)] > 0)[None]).any(1)
    for name, (kind, d, grp, sign) in scen.items():
        sc_in = squash((apply(names, raw, kind, d) - mu) / sd, mode, c)
        w1 = expected_alloc(sc_in)
        delta = (w1[:, gi[grp]].sum(1) - w0[:, gi[grp]].sum(1))
        ok = np.ones(len(delta), bool) if allowed_groups is None else allowed_groups[grp]
        dd = delta[ok]
        wrong = int(((dd * sign) < -TOL).sum())
        right = int(((dd * sign) > TOL).sum())
        vis = float(np.mean(np.abs(sc_in - base_in).sum(1) > 1e-9))
        res[name] = {"n": int(ok.sum()), "yanlis_yon": round(wrong / max(1, ok.sum()), 3), "dogru_tepki": round(right / max(1, ok.sum()), 3),
                     "ort_degisim": round(float(dd.mean()) if len(dd) else 0.0, 4), "girdi_gorunur": round(vis, 3)}
        wrong_all += wrong
        right_all += right
        n_all += int(ok.sum())
    worst = max(v["yanlis_yon"] for v in res.values())
    kat = [v for k, v in res.items() if k.startswith("kat_")]
    makro = [v for k, v in res.items() if not k.startswith("kat_")]
    agg = lambda L, key: round(sum(v[key] * v["n"] for v in L) / max(1, sum(v["n"] for v in L)), 3)
    return {"senaryolar": res, "makro": {"yanlis_yon": agg(makro, "yanlis_yon"), "dogru_tepki": agg(makro, "dogru_tepki")},
            **({"kategori": {"yanlis_yon": agg(kat, "yanlis_yon"), "dogru_tepki": agg(kat, "dogru_tepki"),
                             "en_kotu": max(v["yanlis_yon"] for v in kat)}} if kat else {}), "yanlis_yon": round(wrong_all / max(1, n_all), 3), "dogru_tepki": round(right_all / max(1, n_all), 3),
            "en_kotu_senaryo_yanlis": worst,
            "makul": bool(wrong_all / max(1, n_all) <= 0.10 and worst <= 0.20 and right_all / max(1, n_all) >= 0.25)}


def allowed_groups_from_masks(rows: np.ndarray, M: np.ndarray, A: np.ndarray, cats: list[str]) -> dict:
    """rows: her durumun profil kimliği; M: (profil, aksiyon) maskesi. Grup, profilde en az bir izinli aksiyonda pay alıyorsa izinli."""
    out = {}
    for g, v in GROUPS.items():
        has = A[:, [cats.index(x) for x in v if x in cats]].sum(1) > 0
        out[g] = (M[rows] & has[None]).any(1)
    return out
