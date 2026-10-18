"""Test fikstürleri. Buradaki ürünler SENTETİKTİR, yalnızca kural/skor davranışını test etmek için kullanılır;
uygulamanın veri dizinine yazılmaz ve gerçek veri gibi sunulmaz."""
from __future__ import annotations

import pytest

from app import universe
from app.models import DataPoint, Product

SRC = "TEST FIXTURE (sentetik)"


def dp(key, value, status="ok"):
    return DataPoint(key=key, value=value, source=SRC, as_of="2026-10-14", fetched_at="2026-10-15T10:00:00+03:00",
                     status=status)


def fund(code, cat, ret, vol, dd, rd, fee=0.01, valor=1, currency="TRY", size=1e9, people=5000, nitelikli=False):
    pid = f"FON:{code}"
    f = {
        "getiri_1y": dp(f"{pid}.getiri_1y", ret), "getiri_3a": dp(f"{pid}.getiri_3a", ret / 4),
        "volatilite": dp(f"{pid}.vol", vol), "max_dusus": dp(f"{pid}.dd", dd), "rd": dp(f"{pid}.rd", rd),
        "yonetim_ucreti": dp(f"{pid}.fee", fee), "valor_gun": dp(f"{pid}.valor", valor),
        "buyukluk": dp(f"{pid}.size", size), "kisi": dp(f"{pid}.kisi", people),
        "nitelikli_yatirimci": dp(f"{pid}.nit", nitelikli),
    }
    f.update(universe._category_fields(pid, cat))
    return Product(product_id=pid, name=f"{code} TEST {cat}", category=cat, currency=currency, fields=f)


def deposit(bank, vade, rate):
    pid = f"MEVDUAT:{bank}:{vade}"
    f = {
        "getiri_1y": dp(f"{pid}.oran", rate), "volatilite": dp(f"{pid}.vol", 0.0, "assumption"),
        "max_dusus": dp(f"{pid}.dd", 0.0, "assumption"), "rd": dp(f"{pid}.rd", 1, "assumption"),
        "yonetim_ucreti": dp(f"{pid}.fee", 0.0, "assumption"), "vade_gun": dp(f"{pid}.vade", vade),
        "valor_gun": dp(f"{pid}.valor", 0, "assumption"),
    }
    f.update(universe._category_fields(pid, "mevduat_tl"))
    return Product(product_id=pid, name=f"{bank} {vade} gün", category="mevduat_tl", currency="TRY", fields=f)


def synthetic_universe() -> list[Product]:
    return [
        fund("PPA", "para_piyasasi", 0.46, 0.004, -0.001, 1, fee=0.005, valor=1),
        fund("PPB", "para_piyasasi", 0.45, 0.004, -0.001, 1, fee=0.0145, valor=1),
        fund("BRC", "borclanma", 0.40, 0.03, -0.02, 3, valor=1),
        fund("KTL", "katilim", 0.35, 0.04, -0.03, 3, valor=2),
        fund("EUR", "eurobond_doviz", 0.30, 0.08, -0.05, 4, currency="USD", valor=2),
        fund("ALT", "kiymetli_maden", 0.50, 0.20, -0.12, 6, currency="XAU", valor=2),
        fund("KRM", "karma_degisken", 0.42, 0.12, -0.10, 5, valor=2),
        fund("FSP", "fon_sepeti", 0.38, 0.09, -0.07, 4, valor=2),
        fund("HSS", "hisse", 0.55, 0.28, -0.20, 7, valor=2),
        fund("SRB", "serbest", 0.60, 0.10, -0.05, 4, valor=3, nitelikli=True),
        fund("SRV", "serbest", 0.48, 0.06, -0.04, 4, valor=5, nitelikli=False),
        fund("KUC", "para_piyasasi", 0.47, 0.004, -0.001, 1, size=1e7),
        deposit("BANKA", 32, 0.40),
        deposit("BANKA", 181, 0.38),
    ]


@pytest.fixture
def synthetic(monkeypatch):
    def fake_build(disabled_sources=None):
        disabled = set(disabled_sources or [])
        prods = [] if "tefas" in disabled else synthetic_universe()
        return prods, [dp("kur.USDTRY", 41.0)], []

    monkeypatch.setattr(universe, "build", fake_build)
    from app import evidence, config
    monkeypatch.setattr(evidence, "EVIDENCE_DIR", config.EVIDENCE_DIR / "_test")
    (config.EVIDENCE_DIR / "_test").mkdir(exist_ok=True)
    return synthetic_universe()
