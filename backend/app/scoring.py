"""Şeffaf çok kriterli puanlama ve kısa liste seçimi.

Skor = Σ ağırlık_i × normalize_kriter_i (sadece verisi olan kriterler üzerinden, ağırlıklar yeniden ölçeklenir).
Güven = veri kalitesine göre ağırlıklandırılmış kapsama: ok=1, varsayım=0.7, bayat=0.6, çelişki=0.5, eksik=0.
"""
from __future__ import annotations

from .config import decision_config, norm_text
from .models import CriterionScore, Persona, Product, ScoredProduct
from .profile import ProfileView

QUALITY = {"ok": 1.0, "assumption": 0.7, "stale": 0.6, "conflict": 0.5, "missing": 0.0}
LABELS = {
    "getiri": "Getiri", "dusuk_oynaklik": "Düşük oynaklık", "likidite": "Likidite", "maliyet": "Düşük maliyet",
    "gelir": "Gelir üretimi", "anapara": "Anapara koruma", "doviz_uyumu": "Döviz tercihine uyum", "sadelik": "Ürün sadeliği",
}


def weights(persona: Persona, view: ProfileView) -> dict[str, dict]:
    """Kriter ağırlıkları ve her ağırlığın nereden geldiği."""
    cfg = decision_config()["puanlama"]
    r, lv = view.risk_level, view.levels
    w = {
        "getiri": [0.2 + 0.8 * r, f"risk seviyesi {r}"],
        "dusuk_oynaklik": [0.2 + 0.8 * (1 - r), f"risk seviyesi {r}"],
        "likidite": [lv.get("likidite_ihtiyaci", 0.5), f"likidite ihtiyacı '{persona.likidite_ihtiyaci}'"],
        "gelir": [lv.get("gelir_nakit_akisi_ihtiyaci", 0.5), f"gelir ihtiyacı '{persona.gelir_nakit_akisi_ihtiyaci}'"],
        "anapara": [lv.get("anapara_koruma_tercihi", 0.5), f"anapara koruma '{persona.anapara_koruma_tercihi}'"],
        "maliyet": [cfg["maliyet_taban"], "sabit taban"],
        "doviz_uyumu": [cfg["doviz_taban"], "sabit taban"],
        "sadelik": [cfg["sadelik_taban"], "sabit taban"],
    }
    if view.horizon_years >= 5:
        w["getiri"][0] += 0.2
        w["getiri"][1] += ", uzun ufuk (+0.2)"
    for prio in persona.oncelikler or []:
        n = norm_text(prio)
        for pat in cfg["oncelik_kaliplari"]:
            if pat["anahtar"] in n:
                w[pat["kriter"]][0] += pat["ek"]
                w[pat["kriter"]][1] += f", öncelik '{prio}' (+{pat['ek']})"
    for rule in view.rules:
        if rule["tip"] == "agirlik":
            k = rule["parametre"]["kriter"]
            w[k] = [rule["parametre"]["sabit_agirlik"], f"kısıt '{rule['kaynak_kisit']}' ({rule['id']}) ile sabitlendi"]
    total = sum(v[0] for v in w.values())
    return {k: {"weight": v[0] / total, "explanation": v[1]} for k, v in w.items()}


def _minmax(values: dict[str, float | None]) -> dict[str, float | None]:
    present = [v for v in values.values() if v is not None]
    if not present:
        return {k: None for k in values}
    lo, hi = min(present), max(present)
    return {k: (None if v is None else (0.5 if hi == lo else (v - lo) / (hi - lo))) for k, v in values.items()}


def _liquidity(p: Product) -> tuple[float | None, str]:
    vade = p.v("vade_gun")
    if vade is not None:
        s = 0.5 if vade <= 32 else 0.35 if vade <= 92 else 0.2
        return s, f"{vade} gün vadeli; vade bozulursa faiz kaybı"
    valor = p.v("valor_gun")
    if valor is None:
        return None, "valör bilinmiyor"
    s = {0: 1.0, 1: 0.9, 2: 0.75, 3: 0.5}.get(int(valor), 0.3)
    return s, f"satış valörü T+{valor}"


def score(eligible: list[Product], persona: Persona, view: ProfileView) -> tuple[list[ScoredProduct], dict]:
    W = weights(persona, view)
    ids = [p.product_id for p in eligible]
    by_id = {p.product_id: p for p in eligible}

    def ret(p):
        r1 = p.v("getiri_1y")
        if r1 is not None:
            return r1
        r3 = p.v("getiri_3a")
        return None if r3 is None else (1 + r3) ** 4 - 1

    n_ret = _minmax({i: ret(by_id[i]) for i in ids})
    n_vol = _minmax({i: by_id[i].v("volatilite") for i in ids})
    n_fee = _minmax({i: by_id[i].v("yonetim_ucreti") for i in ids})
    n_dd = _minmax({i: (abs(by_id[i].v("max_dusus")) if by_id[i].v("max_dusus") is not None else None) for i in ids})

    scored = []
    for p in eligible:
        i = p.product_id
        fx_product = p.currency != "TRY"
        liq, liq_note = _liquidity(p)
        ana_attr = p.v("anapara_koruma")
        ana = None if ana_attr is None else (ana_attr if n_dd[i] is None else 0.5 * ana_attr + 0.5 * (1 - n_dd[i]))
        crit = {
            "getiri": (ret(p), n_ret[i], ["getiri_1y" if p.v("getiri_1y") is not None else "getiri_3a"],
                       "1 yıllık getiri" if p.v("getiri_1y") is not None else "3 aylık getiri yıllıklandırıldı"),
            "dusuk_oynaklik": (p.v("volatilite"), None if n_vol[i] is None else 1 - n_vol[i], ["volatilite"], "yıllık volatilite (düşük daha iyi)"),
            "likidite": (p.v("vade_gun") or p.v("valor_gun"), liq, ["vade_gun" if p.v("vade_gun") is not None else "valor_gun"], liq_note),
            "maliyet": (p.v("yonetim_ucreti"), None if n_fee[i] is None else 1 - n_fee[i], ["yonetim_ucreti"], "yıllık yönetim ücreti (düşük daha iyi)"),
            "gelir": (p.v("gelir_uretimi"), p.v("gelir_uretimi"), ["gelir_uretimi"], "kategori yapısı"),
            "anapara": (p.v("max_dusus"), ana, ["anapara_koruma", "max_dusus"], "kategori yapısı + son 1 yıl en büyük düşüş"),
            "doviz_uyumu": (p.currency, view.fx_pref if fx_product else 1 - 0.5 * view.fx_pref, [],
                            f"{p.currency} bazlı ürün, müşterinin döviz tercihi {view.fx_pref}"),
            "sadelik": (p.v("yapi_sadeligi"), p.v("yapi_sadeligi"), ["yapi_sadeligi"], "ürün yapısı"),
        }
        breakdown, num, wsum, conf = [], 0.0, 0.0, 0.0
        for k, (raw, norm, keys, expl) in crit.items():
            w = W[k]["weight"]
            if norm is not None:
                num += w * norm
                wsum += w
                quals = [QUALITY[p.fields[x].status] for x in keys if x in p.fields] or [1.0]
                conf += w * min(quals)
            breakdown.append(CriterionScore(criterion=k, raw=raw, normalized=None if norm is None else round(norm, 4),
                                            weight=round(w, 4), contribution=round(w * norm, 4) if norm is not None else 0.0,
                                            explanation=expl if norm is not None else f"{expl}: VERİ YOK, kriter skora katılmadı"))
        s = num / wsum if wsum else 0.0
        scored.append(ScoredProduct(product=p, score=round(s, 4), confidence=round(conf, 3), breakdown=breakdown))
    scored.sort(key=lambda x: (-x.score, x.product.product_id))
    for n, sp in enumerate(scored, 1):
        sp.rank = n
    return scored, W


def select(scored: list[ScoredProduct], view: ProfileView) -> list[str]:
    """Kısa listeyi seçer; seçilmeyenlere nedenini yazar. Uyarı mesajlarını döndürür."""
    cfg = decision_config()["secim"]
    concentration = any(r["id"] == "K_YOGUNLASMA" for r in view.rules)
    cap = cfg["yogunlasma_kisiti_kategori_basina_max"] if concentration else cfg["kategori_basina_max"]
    # en az N farklı kategori: yoğunlaşma kısıtı (config) ve/veya K_MIN_CESIT (metindeki sayı)
    min_cat = max([cfg["yogunlasma_kisiti_min_kategori"]] * concentration +
                  [r["parametre"].get("min_kategori", 3) for r in view.rules if r["id"] == "K_MIN_CESIT"] or [0])
    if any(r["id"] == "K_MIN_CESIT" for r in view.rules):
        cap = min(cap, cfg["yogunlasma_kisiti_kategori_basina_max"])
        concentration = True
    size = cfg["kisa_liste_boyutu"]
    counts: dict[str, int] = {}
    chosen = 0
    for sp in scored:
        c = sp.product.category
        if chosen >= size:
            sp.not_selected_reason = f"Kısa liste {size} ürünle doldu (sıra {sp.rank})."
        elif counts.get(c, 0) >= cap:
            sp.not_selected_reason = (f"'{c}' kategorisinden zaten {cap} ürün seçildi"
                                      + (" (yoğunlaşma kısıtı)." if concentration else " (çeşitlendirme sınırı)."))
        else:
            sp.selected = True
            counts[c] = counts.get(c, 0) + 1
            chosen += 1
    warnings = []
    if concentration and len(counts) < min_cat:
        warnings.append(f"Yoğunlaşma/çeşitlilik kısıtı en az {min_cat} farklı kategori istiyor; "
                        f"uygun ürünlerle yalnızca {len(counts)} kategori oluşturulabildi. RM değerlendirmeli.")
    if chosen == 0:
        warnings.append("Uygunluk kurallarını geçen ürün yok; öneri listesi boş.")
    return warnings
