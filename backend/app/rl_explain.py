"""Kategori DQN'inin EBM (Explainable Boosting Machine) vekil modelleriyle açıklanması.

DQN bir sinir ağıdır, kararını doğrudan okunur kılmaz. Bu modül ağın kararlarını toplamsal, cam kutu bir modele damıtır:
    kategori ağırlığı_c ≈ sabit_c + Σ_k f_ck(x_k)
x: karar anındaki piyasa göstergeleri (ham değer) ve profil alanları. Her f_ck tek değişkenli bir şekil fonksiyonudur
(EBM: gradyan artırmalı, döngüsel, değişken başına küçük ağaçlar). Politika profil × piyasa etkileşimine dayandığı için
sınırlı sayıda ikili etkileşim terimi f(x_i, x_j) de eklenir (EBM'in FAST seçimiyle; sayısı config'te). Böylece
  - global açıklama: "TLREF yükseldikçe model para piyasasına daha çok ağırlık veriyor" (şekil fonksiyonu),
  - yerel açıklama: bugünkü kararda her değişkenin katkısı (puan) ve toplamı
okunabilir. Aynı yöntem inanç başlığının beklenen getirisine de uygulanır (yalnızca piyasa göstergeleriyle).

Vekil model DQN'in kendisi değildir. Sadakati (R² ve en büyük kategori uyumu) ayrılmış durumlarda ölçülür ve her
açıklamayla birlikte gösterilir; düşük sadakatte açıklama "yaklaşık" olarak işaretlenir.
Eğitim (interpret paketi) çevrimdışıdır: tools/explain_rl.py. Çevrimiçi kullanım yalnızca kaydedilen tabloları okur.
"""
from __future__ import annotations

import json

import numpy as np

LEVEL = {"Düşük": 0.0, "Orta": 1.0, "Yüksek": 2.0}
PROFILE_FEATURES = [
    ("Profil: maks. risk değeri", lambda p: p["max_rd"]),
    ("Profil: yatırım ufku (yıl)", lambda p: p["ufuk_yil"]),
    ("Profil: anapara koruma (0 düşük-2 yüksek)", lambda p: LEVEL[p["anapara"]]),
    ("Profil: likidite ihtiyacı (0 düşük-2 yüksek)", lambda p: LEVEL[p["likidite"]]),
    ("Profil: döviz tavanı tercihi", lambda p: p["fx"]),
    ("Kısıt: yüksek riskli ürün yok", lambda p: float(p["yuksek_risk_yasagi"])),
    ("Kısıt: kilitli ürün yok", lambda p: float(p["kilit"])),
    ("Kısıt: döviz maruziyetini artırmama", lambda p: float(p["doviz_artirmama"])),
    ("Kısıt: yoğunlaşma sınırı", lambda p: float(p["yogunlasma"])),
]
MIN_R2 = 0.5          # bunun altında açıklama "yaklaşık / düşük sadakat" uyarısı alır


def feature_names(market_names: list[str]) -> list[str]:
    return list(market_names) + [n for n, _ in PROFILE_FEATURES]


def design_row(raw_market: np.ndarray, prof: dict) -> np.ndarray:
    return np.concatenate([np.asarray(raw_market, float), [f(prof) for _, f in PROFILE_FEATURES]])


# --- Tablolar: interpret EBM'den dışa aktarım ve çevrimiçi değerlendirme ----------------------------

def export_ebm(ebm, cols: list[int]) -> dict:
    """EBM'i bağımlılıksız tabloya çevirir. Terim: tek değişken (şekil fonksiyonu) veya değişken çifti (etkileşim yüzeyi).
    Her değişken için kesim noktaları; tek terimde ana çözünürlük (bins_[f][0]), çift terimde etkileşim çözünürlüğü
    (bins_[f][1], yoksa sonuncusu). Bin indeksi: eksik → 0, aksi halde searchsorted(kesimler, x, 'right') + 1.
    interpret 0.7'nin kendi tahminiyle birebir doğrulanır (tools/explain_rl.py). cols: EBM sütunu → tasarım matrisi sütunu."""
    terms = []
    for i, feats in enumerate(ebm.term_features_):
        lvl = len(feats) - 1
        cuts = [np.asarray(ebm.bins_[f][min(lvl, len(ebm.bins_[f]) - 1)], float).tolist() for f in feats]
        terms.append({"ad": ebm.term_names_[i], "sutunlar": [cols[f] for f in feats], "kesimler": cuts,
                      "skorlar": np.asarray(ebm.term_scores_[i], float).tolist()})
    return {"sabit": float(ebm.intercept_), "terimler": terms}


def _bin(cuts, v: np.ndarray, size: int) -> np.ndarray:
    idx = np.searchsorted(np.asarray(cuts), v, side="right") + 1
    return np.where(np.isnan(v), 0, np.minimum(idx, size - 1))


def term_contribs(table: dict, X: np.ndarray) -> np.ndarray:
    """(n, terim) katkı matrisi."""
    X = np.atleast_2d(X)
    out = np.zeros((len(X), len(table["terimler"])))
    for j, t in enumerate(table["terimler"]):
        sc = np.asarray(t["skorlar"])
        idx = tuple(_bin(c, X[:, col], sc.shape[k]) for k, (c, col) in enumerate(zip(t["kesimler"], t["sutunlar"])))
        out[:, j] = sc[idx]
    return out


def predict(table: dict, X: np.ndarray) -> np.ndarray:
    return table["sabit"] + term_contribs(table, X).sum(axis=1)


def shape(table: dict, term: str) -> dict | None:
    """Global şekil fonksiyonu: bin sınırları ve skorlar (eksik/görülmemiş binler hariç)."""
    t = next((t for t in table["terimler"] if t["ad"] == term and len(t["sutunlar"]) == 1), None)
    return None if t is None else {"kesimler": t["kesimler"][0], "skorlar": t["skorlar"][1:len(t["kesimler"][0]) + 2]}


# --- Yerel açıklama ------------------------------------------------------------------------------

def by_variable(table: dict, x_row: np.ndarray, names: list[str]) -> dict[int, float]:
    """Terim katkılarını değişkenlere toplar: etkileşim teriminin katkısı iki değişkene yarı yarıya paylaştırılır.
    Böylece eş doğrusal göstergelerin birbirini götüren çift terimleri RM'e tek tek gösterilmez; toplam korunur."""
    c = term_contribs(table, x_row)[0]
    out: dict[int, float] = {}
    for k, t in enumerate(table["terimler"]):
        for col in t["sutunlar"]:
            out[col] = out.get(col, 0.0) + c[k] / len(t["sutunlar"])
    return out


def explain(expl: dict, x_row: np.ndarray, chosen: dict[str, float], top: int = 5) -> dict:
    """Bugünkü durum için: seçilen her kategorinin ağırlığını ve (varsa) beklenen getirisini değişken katkılarına ayırır."""
    names = expl["degiskenler"]

    def local(table, scale):
        v = by_variable(table, x_row, names)
        total = table["sabit"] + sum(v.values())
        order = sorted(v, key=lambda k: -abs(v[k]))[:top]
        return {"vekil_tahmin": round(float(total * scale), 2), "sabit": round(table["sabit"] * scale, 2),
                "katkilar": [{"ad": names[k], "deger": float(x_row[k]), "katki": round(float(v[k] * scale), 2)}
                             for k in order if abs(v[k]) * scale >= 0.05]}

    out = {"yontem": expl["yontem"], "sadakat": expl["sadakat"], "kategoriler": {}}
    for cat, w in sorted(chosen.items(), key=lambda kv: -kv[1]):
        row = {"agirlik_puan": round(w * 100, 1)}
        m = expl["agirlik"].get(cat)
        if m:
            row["agirlik"] = {**local(m["tablo"], 100), "r2": m["r2"], "dusuk_sadakat": m["r2"] < MIN_R2}
        b = expl.get("inanc", {}).get(cat)
        if b:
            row["inanc"] = {**local(b["tablo"], 100), "r2": b["r2"], "dusuk_sadakat": b["r2"] < MIN_R2}
        out["kategoriler"][cat] = row
    return out


def global_summary(expl: dict, top: int = 8) -> dict:
    """Model sayfası için: kategori başına en önemli değişkenler ve şekil fonksiyonları."""
    out = {}
    for cat, m in expl["agirlik"].items():
        imp = m["onem"]
        order = sorted(imp, key=lambda k: -imp[k])[:top]
        out[cat] = {"r2": m["r2"], "onem": [{"ad": k, "onem": round(imp[k] * 100, 2)} for k in order],
                    "sekiller": {k: shape(m["tablo"], k) for k in order}}
    return {"yontem": expl["yontem"], "sadakat": expl["sadakat"], "kategoriler": out}


def load(path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
