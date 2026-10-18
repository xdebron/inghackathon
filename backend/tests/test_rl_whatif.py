import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "gbrl"))

import whatif  # noqa: E402
from app import rl_category as rc  # noqa: E402

NAMES = ["Politika faizi (karar)", "TLREF", "Reel mevduat faizi", "TÜFE yıllık", "USD/TRY 3 ay değişimi",
         "kiymetli_maden 3 ay getirisi", "kiymetli_maden 3 ay oynaklığı", "hisse 3 ay getirisi", "BIST 100 toplam getiri 3 ay"]


def test_rate_shock_moves_all_rate_indicators_together():
    x = np.full((2, len(NAMES)), 0.1)
    y = whatif.apply(NAMES, x, "rate", -0.05)
    for n in ("Politika faizi (karar)", "TLREF", "Reel mevduat faizi"):
        assert np.allclose(y[:, NAMES.index(n)], 0.05)
    assert np.allclose(y[:, NAMES.index("TÜFE yıllık")], 0.1)


def test_inflation_shock_lowers_real_rate():
    x = np.zeros((1, len(NAMES)))
    y = whatif.apply(NAMES, x, "infl", 0.1)
    assert y[0, NAMES.index("TÜFE yıllık")] == 0.1 and y[0, NAMES.index("Reel mevduat faizi")] == -0.1


def test_fx_and_equity_shocks_are_multiplicative_and_skip_volatility():
    x = np.zeros((1, len(NAMES)))
    y = whatif.apply(NAMES, x, "usd", 0.15)
    assert np.isclose(y[0, NAMES.index("USD/TRY 3 ay değişimi")], 0.15)
    assert np.isclose(y[0, NAMES.index("kiymetli_maden 3 ay getirisi")], 0.15)
    assert y[0, NAMES.index("kiymetli_maden 3 ay oynaklığı")] == 0
    y = whatif.apply(NAMES, x, "equity", np.array([-0.15]))
    assert np.isclose(y[0, NAMES.index("hisse 3 ay getirisi")], -0.15)
    assert np.isclose(y[0, NAMES.index("BIST 100 toplam getiri 3 ay")], -0.15)


def test_tanh_input_is_monotone_beyond_clip():
    z = np.array([-6.0, -4.0, -2.0, 0.0, 2.0, 4.0])
    hard = rc.model_input(z, {"ozellik_kirp": 2.0})
    soft = rc.model_input(z, {"ozellik_kirp": 3.0, "kirpma_turu": "tanh"})
    assert hard[0] == hard[1]                         # sert kesmede uç değerler ayırt edilemez
    assert np.all(np.diff(soft) > 0) and np.abs(soft).max() < 3.0


def test_evaluate_flags_wrong_direction():
    cats = ["mevduat_tl", "para_piyasasi", "hisse"]
    names = ["Politika faizi (karar)"]
    raw = np.zeros((4, 1))

    def contrarian(zin):               # faiz düştükçe mevduata yığılan (yanlış) politika
        w = np.clip(0.5 - zin[:, :1], 0, 1)
        return np.column_stack([w, np.zeros_like(w), 1 - w])
    r = whatif.evaluate(contrarian, names, raw, np.zeros(1), np.full(1, 0.05), "sert", 3.0, cats)
    assert r["senaryolar"]["faiz_indirimi"]["yanlis_yon"] == 1.0 and not r["makul"]
