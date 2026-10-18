"""Kısıt kataloğu: her örnek metin kendi kuralına eşleşir, parametreler metinden okunur, deterministik süzgeç ve RL maskesi
kuralı uygular, persona kısıtları değişmez."""
from __future__ import annotations

import numpy as np
import pytest

from app import rl_category as rc
from app.config import constraint_rules
from app.models import Persona, Product, DataPoint
from app.profile import interpret, match_constraint, rule_params
from app.rules import constraint_checks

RD = {"mevduat_tl": 1, "para_piyasasi": 1, "borclanma": 2, "katilim": 3, "kiymetli_maden": 6, "karma_degisken": 5,
      "fon_sepeti": 5, "hisse": 6, "yabanci_hisse": 6, "eurobond_doviz": 5, "bist30_hisse": 7}
CATS = rc.CATS_V2
A = rc.action_grid(CATS)
BASE = dict(risk_profili="Agresif", yatirim_ufku="5+ yıl", likidite_ihtiyaci="Orta", gelir_nakit_akisi_ihtiyaci="Orta",
            anapara_koruma_tercihi="Orta", doviz_maruz_kalma_tercihi="Açık", oncelikler=["büyüme"], yatirim_amaci="-")


def prof(*kisit):
    p = Persona(persona_id="T", acik_kisitlar=list(kisit), **BASE)
    view, qs = interpret(p, {})
    assert view is not None, qs
    return p, view, rc.profile_from_persona(p, view)


def test_every_catalog_example_matches_its_own_rule():
    for r in constraint_rules()["kurallar"]:
        assert match_constraint(r["ornek"])["id"] == r["id"], r["id"]


@pytest.mark.parametrize("text,key,val", [("riskli varlıklarda en fazla %25", "oran", 0.25),
                                          ("en az dört farklı varlık sınıfı", "min_kategori", 4),
                                          ("vadesi 2 ayı aşan mevduat istememe", "max_mevduat_vade_gun", 62)])
def test_parameters_are_read_from_text(text, key, val):
    assert rule_params(match_constraint(text), text)[key] == val


def test_category_exclusion_in_rules_and_mask():
    p, view, pr = prof("altın / kıymetli maden istememe")
    gold = Product(product_id="FON:ALT", name="Altın fonu", category="kiymetli_maden", currency="XAU",
                   fields={"rd": DataPoint(key="rd", value=6, source="t")})
    assert constraint_checks(gold, view, []).rule_id == "K_ALTIN_YOK"
    m, why = rc.profile_mask(pr, A, RD, CATS)
    assert A[m][:, CATS.index("kiymetli_maden")].max() == 0 and any("K_ALTIN_YOK" in w for w in why)


def test_whitelist_faizsiz():
    _, _, pr = prof("faizsiz (katılım esaslı) ürünler")
    m, _ = rc.profile_mask(pr, A, RD, CATS)
    used = A[m].max(axis=0) > 0
    assert {c for c, u in zip(CATS, used) if u} <= {"mevduat_tl", "katilim", "kiymetli_maden"}


def test_portfolio_level_constraints():
    _, _, pr = prof("riskli varlıklarda en fazla %20")
    m, _ = rc.profile_mask(pr, A, RD, CATS)
    risky = np.array([RD[c] >= 5 for c in CATS])
    assert m.any() and (A[m][:, risky].sum(1) <= 0.2 + 1e-9).all()
    _, _, pr = prof("portföyün en az %30'u hemen erişilebilir olsun")
    m, _ = rc.profile_mask(pr, A, RD, CATS)
    liq = np.array([c in ("para_piyasasi", "borclanma") for c in CATS])
    assert m.any() and (A[m][:, liq].sum(1) >= 0.3 - 1e-9).all()
    _, _, pr = prof("en az 4 farklı varlık sınıfı")
    m, _ = rc.profile_mask(pr, A, RD, CATS)
    assert m.any() and ((A[m] > 0).sum(1) >= 4).all()
    _, _, pr = prof("tek varlık sınıfına en fazla %30")
    m, _ = rc.profile_mask(pr, A, RD, CATS)
    assert m.any() and (A[m].max(1) <= 0.3 + 1e-9).all()


def test_product_level_filters():
    _, view, _ = prof("yıllık yönetim ücreti %2'yi aşan fon istememe")
    pricey = Product(product_id="FON:X", name="X", category="hisse", currency="TRY",
                     fields={"yonetim_ucreti": DataPoint(key="u", value=0.035, source="t")})
    assert constraint_checks(pricey, view, []).rule_id == "K_DUSUK_MALIYET"
    _, view, _ = prof("vadesi 1 ayı aşan mevduat istememe")
    dep = Product(product_id="MEVDUAT:ING:92", name="92 gün", category="mevduat_tl", currency="TRY",
                  fields={"vade_gun": DataPoint(key="v", value=92, source="t")})
    assert constraint_checks(dep, view, []).rule_id == "K_VADE_SINIRI"


def test_profile_vector_v3_has_catalog_flags_and_personas_unchanged():
    _, _, pr = prof("riskli varlıklarda en fazla %25")
    v = rc.profile_vector(pr, 3)
    n = len(rc.catalog_rules())
    assert len(v) == 21 + 2 * n
    i = [r["id"] for r in rc.catalog_rules()].index("K_RISKLI_PAY_SINIRI")
    assert v[21 + i] == 1.0 and abs(v[21 + n + i] - 0.25) < 1e-9
    import glob, json
    from app.config import PERSONA_DIR
    for f in glob.glob(str(PERSONA_DIR / "*.json")):
        p = json.load(open(f, encoding="utf-8"))
        assert all(match_constraint(k)["id"] in rc.LEGACY_RULES for k in p["acik_kisitlar"])
