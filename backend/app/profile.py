"""Persona doğrulama ve yorumlama.

Kural: eksik, çelişkili veya tanınmayan bir alan için sistem varsayım yapmaz. Teyit sorusu üretir ve
RM yanıtı (rm_answers) gelene kadar öneri üretmez.

rm_answers biçimi:
  "<alan>": yeni değer                     -> alanı RM'in verdiği değerle günceller
  "onay.<celiski_id>": true                -> RM çelişkiyi bilerek onaylar, mevcut değerlerle devam edilir
  "kisit.<kısıt metni>": "<kural id>"|"yok" -> eşleşmeyen kısıtı bir kurala bağlar ya da kayıt altına alarak yok sayar
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .config import constraint_rules, decision_config, norm_text
from .models import ConfirmationQuestion, Persona

REQUIRED = ["risk_profili", "yatirim_ufku", "likidite_ihtiyaci", "doviz_maruz_kalma_tercihi",
            "gelir_nakit_akisi_ihtiyaci", "anapara_koruma_tercihi", "acik_kisitlar", "oncelikler", "yatirim_amaci"]
LEVEL_FIELDS = ["likidite_ihtiyaci", "gelir_nakit_akisi_ihtiyaci", "anapara_koruma_tercihi"]

FIELD_LABELS = {
    "risk_profili": "risk profili", "yatirim_ufku": "yatırım ufku", "likidite_ihtiyaci": "likidite ihtiyacı",
    "doviz_maruz_kalma_tercihi": "döviz maruziyeti tercihi", "gelir_nakit_akisi_ihtiyaci": "gelir/nakit akışı ihtiyacı",
    "anapara_koruma_tercihi": "anapara koruma tercihi", "acik_kisitlar": "açık kısıtlar", "oncelikler": "öncelikler",
    "yatirim_amaci": "yatırım amacı",
}


class ProfileView(BaseModel):
    """Personanın karar motorunca kullanılan sayısal yorumu."""
    risk_level: float
    max_rd: int
    horizon_years: float
    levels: dict[str, float]
    fx_pref: float
    rules: list[dict]  # eşleşen kısıt kuralları (+ kaynak kısıt metni)
    ignored_constraints: list[str]
    acknowledged: list[str]
    qualified: bool | None = None   # nitelikli yatırımcı (RM teyidi); None = bilinmiyor


def apply_answers(persona: Persona, answers: dict[str, Any]) -> Persona:
    data = persona.model_dump()
    for k, v in answers.items():
        if k in data:
            data[k] = v
    return Persona(**data)


def _match_fx(text: str) -> float | None:
    n = norm_text(text)
    for p in decision_config()["doviz_tercihi_kaliplari"]["kaliplar"]:
        if p["anahtar"] in n:
            return p["deger"]
    return None


def _match_horizon(text: str) -> float | None:
    n = text.replace(" ", "")
    for p in decision_config()["ufuk_kaliplari"]["kaliplar"]:
        if p["anahtar"] in n:
            return p["yil"]
    return None


_NUM_WORDS = {"bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6}


def rule_params(rule: dict, text: str) -> dict:
    """Kısıt metninden kural parametresi: oran ('%30', '30%'), sayı ('en az üç', 'en az 3'), vade ('1 ay', '60 gün', 'bir yıl').
    Metinde değer yoksa kuralın varsayılanı kalır; çıkarılan değer kural kaydında görünür (izlenebilirlik)."""
    import re
    prm = dict(rule.get("parametre", {}))
    n = norm_text(text)
    if prm.get("oran_metinden"):
        m = re.search(r"%\s*(\d+(?:[.,]\d+)?)", n) or re.search(r"(\d+(?:[.,]\d+)?)\s*%", n)
        if m:
            prm["oran"] = float(m.group(1).replace(",", ".")) / 100
    if prm.get("sayi_metinden"):
        m = re.search(r"\ben az\s+(\d+)", n)
        val = int(m.group(1)) if m else next((v for w, v in _NUM_WORDS.items() if f"en az {w}" in n), None)
        if val:
            prm["min_kategori"] = val
    if prm.get("vade_metinden"):
        m = re.search(r"(\d+|bir|iki|üç|altı)\s*(gün|ay|yıl)", n)
        if m:
            k = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
            prm["max_mevduat_vade_gun"] = k * {"gün": 1, "ay": 31, "yıl": 366}[m.group(2)]
    return prm


def match_constraint(text: str) -> dict | None:
    n = norm_text(text)
    for rule in constraint_rules()["kurallar"]:
        if any(norm_text(k) in n for k in rule["anahtarlar"]):
            return rule
    return None


def interpret(persona: Persona, answers: dict[str, Any]) -> tuple[ProfileView | None, list[ConfirmationQuestion]]:
    cfg = decision_config()
    qs: list[ConfirmationQuestion] = []
    p = persona

    for f in REQUIRED:
        v = getattr(p, f)
        if v is None or v == "" or v == []:
            if f == "acik_kisitlar" and v == []:
                continue  # boş liste: açık kısıt yok demektir
            qs.append(ConfirmationQuestion(field=f, issue="eksik",
                                           question=f"Müşterinin {FIELD_LABELS[f]} bilgisi nedir?",
                                           detail=f"'{f}' alanı boş; sistem bu alan için varsayım yapmaz."))

    risk = cfg["risk_profilleri"].get(p.risk_profili or "") if p.risk_profili else None
    if p.risk_profili and risk is None:
        known = [k for k in cfg["risk_profilleri"] if not k.startswith("_")]
        qs.append(ConfirmationQuestion(field="risk_profili", issue="taninmayan_deger",
                                       question=f"'{p.risk_profili}' risk profili şu profillerden hangisine karşılık geliyor: {', '.join(known)}?",
                                       detail="Risk profili uygunluk eşlemesinde tanımlı değil."))
    levels: dict[str, float] = {}
    for f in LEVEL_FIELDS:
        v = getattr(p, f)
        if v is None:
            continue
        if v in cfg["seviye_degerleri"]:
            levels[f] = cfg["seviye_degerleri"][v]
        else:
            qs.append(ConfirmationQuestion(field=f, issue="taninmayan_deger",
                                           question=f"Müşterinin {FIELD_LABELS[f]} Düşük / Orta / Yüksek seviyelerinden hangisi?",
                                           detail=f"'{v}' değeri tanımlı seviyelerle eşleşmedi."))
    fx = _match_fx(p.doviz_maruz_kalma_tercihi) if p.doviz_maruz_kalma_tercihi else None
    if p.doviz_maruz_kalma_tercihi and fx is None:
        qs.append(ConfirmationQuestion(field="doviz_maruz_kalma_tercihi", issue="taninmayan_deger",
                                       question="Müşterinin döviz maruziyeti tercihi Sınırlı / Kontrollü / Çeşitlendirilmiş / Açık seçeneklerinden hangisine yakın?",
                                       detail=f"'{p.doviz_maruz_kalma_tercihi}' yorumlanamadı."))
    horizon = _match_horizon(p.yatirim_ufku) if p.yatirim_ufku else None
    if p.yatirim_ufku and horizon is None:
        qs.append(ConfirmationQuestion(field="yatirim_ufku", issue="taninmayan_deger",
                                       question="Yatırım ufku 0-1 yıl / 1-3 yıl / 3-5 yıl / 5+ yıl aralıklarından hangisi?",
                                       detail=f"'{p.yatirim_ufku}' yorumlanamadı."))

    rules, ignored = [], []
    for c in p.acik_kisitlar or []:
        override = answers.get(f"kisit.{c}")
        if override == "yok":
            ignored.append(c)
            continue
        rule = next((r for r in constraint_rules()["kurallar"] if r["id"] == override), None) if override else match_constraint(c)
        if rule:
            rules.append({**rule, "parametre": rule_params(rule, c), "kaynak_kisit": c})
        else:
            ids = ", ".join(r["id"] for r in constraint_rules()["kurallar"])
            qs.append(ConfirmationQuestion(field=f"kisit.{c}", issue="eslesmeyen_kisit",
                                           question=f"'{c}' kısıtı nasıl uygulanmalı? Tanımlı kurallar: {ids}",
                                           detail="Açık kısıt hiçbir deterministik kurala eşleşmedi; güvenli uygulama için RM teyidi gerekli."))

    rule_ids = {r["id"] for r in rules}
    acknowledged = []

    def contradiction(cid: str, field: str, question: str, detail: str):
        if answers.get(f"onay.{cid}"):
            acknowledged.append(f"{cid}: {detail}")
            return
        qs.append(ConfirmationQuestion(field=field, issue="celiski", question=question, detail=f"[{cid}] {detail}"))

    if risk:
        lv_ana = levels.get("anapara_koruma_tercihi")
        if risk["seviye"] >= 1.0 and lv_ana is not None and lv_ana >= 1.0:
            contradiction("C_RISK_ANAPARA", "anapara_koruma_tercihi",
                          "Müşteri agresif risk profilinde ama anapara korumasını yüksek öncelik görüyor. Hangisi esas alınmalı?",
                          "Agresif risk profili ile yüksek anapara koruma tercihi çelişiyor.")
        if risk["seviye"] <= 0.1 and lv_ana is not None and lv_ana <= 0.2:
            contradiction("C_MUHAFAZAKAR_ANAPARA", "anapara_koruma_tercihi",
                          "Muhafazakâr profilde anapara koruma tercihinin 'Düşük' olması doğru mu?",
                          "Muhafazakâr risk profili ile düşük anapara koruma tercihi çelişiyor.")
        if "K_YUKSEK_RISK" in rule_ids and risk["max_rd"] >= 5:
            src = next(r["kaynak_kisit"] for r in rules if r["id"] == "K_YUKSEK_RISK")
            contradiction("C_RISK_KISIT", "risk_profili",
                          f"Risk profili '{p.risk_profili}' yüksek riskli ürünlere izin veriyor ama müşteri yüksek riskli ürün istemiyor. Kısıt mı, profil mi güncel?",
                          f"Risk profili ile '{src}' kısıtı (K_YUKSEK_RISK) çelişiyor; kısıt yine de uygulanır.")
        if horizon is not None and horizon < 1 and risk["seviye"] >= 1.0:
            contradiction("C_UFUK_RISK", "yatirim_ufku",
                          "1 yıldan kısa ufukta agresif profil teyit edilsin mi?",
                          "Kısa yatırım ufku ile agresif risk profili çelişiyor.")
    lv_lik = levels.get("likidite_ihtiyaci")
    if "K_KISA_VADE_NAKIT_YOK" in rule_ids and lv_lik is not None and lv_lik >= 1.0:
        contradiction("C_LIKIDITE_KISIT", "likidite_ihtiyaci",
                      "Müşterinin kısa vadeli nakit ihtiyacı yok deniyor ama likidite ihtiyacı 'Yüksek'. Hangisi doğru?",
                      "Yüksek likidite ihtiyacı ile 'kısa vadeli nakit ihtiyacı varsayılmamalı' kısıtı çelişiyor.")
    if "K_LIKIDITE_KILIDI" in rule_ids and lv_lik is not None and lv_lik <= 0.2:
        contradiction("C_KILIT_LIKIDITE", "likidite_ihtiyaci",
                      "Müşteri kilitli ürün istemiyor ama likidite ihtiyacı 'Düşük'. Hangisi doğru?",
                      "Düşük likidite ihtiyacı ile 'kilitli/düşük likiditeli ürünlerden kaçınma' kısıtı çelişiyor (kısıt yine de uygulanır).")

    if qs:
        return None, qs
    return ProfileView(risk_level=risk["seviye"], max_rd=risk["max_rd"], horizon_years=horizon, levels=levels,
                       fx_pref=fx, rules=rules, ignored_constraints=ignored, acknowledged=acknowledged,
                       qualified=persona.nitelikli_yatirimci), []
