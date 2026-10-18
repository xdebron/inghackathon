"""Fon dışı alternatifler: doğrudan DİBS, kıymetli maden hesabı, döviz mevduatı ve piyasa bağlamı (gerçek snapshot'larla)."""
from __future__ import annotations

import pytest

from app import universe
from app.models import DataPoint

pytestmark = pytest.mark.skipif(not (universe.GECMIS_DIR / "meta.json").exists(), reason="gecmis/ snapshot yok")


def test_dibs_products_have_sourced_yield_and_assumed_rd():
    tufe = DataPoint(key="tufe", value=0.30, source="test", status="ok")
    flags = []
    prods = universe._dibs_products({}, {"tufe_yillik": tufe}, flags)
    assert prods, "DİBS ürünü üretilmedi"
    for p in prods:
        assert p.fields["getiri_1y"].value is not None and 0 < p.fields["getiri_1y"].value < 1
        assert p.fields["rd"].status == "assumption"
        assert p.fields["volatilite"].status == "missing"  # uydurulmaz
    real = [p for p in prods if p.category == "dibs_tufe"]
    if real:
        r = real[0].fields["reel_getiri_ihale"].value
        assert abs(real[0].fields["getiri_1y"].value - ((1 + r) * 1.30 - 1)) < 1e-9
    assert any("stopaj" in f.message.lower() for f in flags)


def test_metal_accounts_currency_and_rd_from_volatility():
    prods = universe._metal_account_products({}, [])
    cur = {p.currency for p in prods}
    assert cur <= {"XAU", "XAG"} and prods
    for p in prods:
        assert p.fields["rd"].value == universe.estimate_rd(p.fields["volatilite"].value)


def test_history_market_points_carry_provenance():
    pts = universe._history_market()
    assert pts
    for dp in pts:
        assert dp.source and dp.as_of and dp.value is not None
