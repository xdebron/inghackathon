"""İnanç başlığı ve EBM vekil açıklaması: dışa aktarılan tablolar interpret ile birebir, katkılar toplamı korur,
inanç başlığının numpy çıkarımı torch ile aynı."""
from __future__ import annotations

import numpy as np
import pytest

from app import rl_category as rc, rl_explain as rx


@pytest.fixture(scope="module")
def ebm_table():
    glass = pytest.importorskip("interpret.glassbox")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 4))
    X[:, 3] = rng.integers(0, 2, 2000)
    y = np.sin(X[:, 0]) + 0.5 * X[:, 1] * X[:, 3] + 0.2 * X[:, 2] + rng.normal(0, 0.05, 2000)
    ebm = glass.ExplainableBoostingRegressor(feature_names=list("abcd"), feature_types=["continuous"] * 4, interactions=3,
                                             outer_bags=2, max_bins=32, random_state=0)
    ebm.fit(X, y)
    cols = [0, 1, 2, 3]
    return ebm, rx.export_ebm(ebm, cols), X


def test_exported_tables_match_interpret_including_interactions(ebm_table):
    ebm, table, X = ebm_table
    assert any(len(t["sutunlar"]) == 2 for t in table["terimler"])        # etkileşim terimleri de dışa aktarıldı
    Xt = X[:300].copy()
    Xt[:3, 0] = table["terimler"][0]["kesimler"][0][:3]                    # tam kesim noktaları
    assert np.abs(rx.predict(table, Xt) - ebm.predict(Xt)).max() < 1e-9


def test_variable_attribution_preserves_total(ebm_table):
    _, table, X = ebm_table
    v = rx.by_variable(table, X[5], list("abcd"))
    assert abs(table["sabit"] + sum(v.values()) - rx.predict(table, X[5:6])[0]) < 1e-9
    assert set(v) <= {0, 1, 2, 3}


def test_explain_reports_fidelity_and_flags_low_r2(ebm_table):
    _, table, X = ebm_table
    expl = {"yontem": "t", "sadakat": {}, "degiskenler": list("abcd"),
            "agirlik": {"para_piyasasi": {"tablo": table, "r2": 0.3, "onem": {}}}, "inanc": {}}
    out = rx.explain(expl, X[0], {"para_piyasasi": 0.6, "mevduat_tl": 0.4})
    pp = out["kategoriler"]["para_piyasasi"]
    assert pp["agirlik_puan"] == 60.0 and pp["agirlik"]["dusuk_sadakat"]
    assert "agirlik" not in out["kategoriler"]["mevduat_tl"]               # vekili olmayan kategori yalnızca ağırlıkla


def test_profile_design_row_has_no_duplicate_risk_encoding():
    names = [n for n, _ in rx.PROFILE_FEATURES]
    assert "Profil: maks. risk değeri" in names and not any("risk seviyesi" in n for n in names)   # eş doğrusal alan çıkarıldı
    p = {"max_rd": 5, "ufuk_yil": 3.0, "anapara": "Yüksek", "likidite": "Düşük", "fx": 0.4, "yuksek_risk_yasagi": True,
         "kilit": False, "doviz_artirmama": False, "yogunlasma": True}
    row = rx.design_row(np.array([0.1, 0.2]), p)
    assert row.tolist() == [0.1, 0.2, 5, 3.0, 2.0, 0.0, 0.4, 1.0, 0.0, 0.0, 1.0]


def test_belief_head_numpy_matches_torch_and_trains():
    torch = pytest.importorskip("torch")
    from app import rl_gpu
    from app.rl import MLP
    net = rl_gpu.Net(12, 16, 7, n_cat=3)
    w = net.numpy_state()
    assert {"Wb", "bb"} <= set(w)
    m = MLP(12, 16, 7, np.random.default_rng(0), 0.0)
    m.load_state_dict(w)
    m.belief_W = (w["Wb"], w["bb"])
    x = np.random.default_rng(1).normal(size=(5, 12))
    mu_n, sd_n = rc.belief(m, x)
    mu_t, sd_t = net.belief(torch.tensor(x, dtype=torch.float32))
    assert np.abs(mu_n - mu_t.detach().numpy()).max() < 1e-5 and np.abs(sd_n - sd_t.detach().numpy()).max() < 1e-5
    y = torch.zeros((64, 3))
    xb = torch.randn(64, 12)
    opt = torch.optim.Adam(net.parameters(), 1e-2)
    first = None
    for _ in range(200):
        mu, ls = net.belief_from(net.trunk(xb))
        loss = rl_gpu.belief_nll(mu, ls, y)
        first = float(loss.detach()) if first is None else first
        opt.zero_grad()
        loss.backward()
        opt.step()
    assert float(loss.detach()) < first                                              # NLL öğreniyor (sabit hedefe yaklaşır)
    rc_net = rl_gpu.Net(12, 16, 7, n_cat=3)
    rc_net.load_numpy_state(net.numpy_state())
    assert torch.allclose(rc_net.belief(xb)[0], net.belief(xb)[0])


def test_model_without_belief_head_returns_none():
    from app.rl import MLP
    m = MLP(4, 8, 3, np.random.default_rng(0), 0.0)
    assert rc.belief(m, np.zeros((1, 4))) is None
    assert rc.belief_table(m, np.zeros((1, 4)), ["a"], np.ones(1, bool), np.ones((1, 1))) is None


def test_ppo_numpy_policy_matches_torch_mlp_and_masks():
    """PPOPolicy (numpy) = SB3 MlpPolicy aktörü (tanh, 2 gizli katman); maskeli olasılıklar izinsiz aksiyona 0 verir."""
    torch = pytest.importorskip("torch")
    rng = np.random.default_rng(0)
    d_in, h, n = 7, 5, 4
    net = torch.nn.Sequential(torch.nn.Linear(d_in, h), torch.nn.Tanh(), torch.nn.Linear(h, h), torch.nn.Tanh(), torch.nn.Linear(h, n))
    w = {"P0W": net[0].weight.detach().numpy().T, "P0b": net[0].bias.detach().numpy(),
         "P1W": net[2].weight.detach().numpy().T, "P1b": net[2].bias.detach().numpy(),
         "AW": net[4].weight.detach().numpy().T, "Ab": net[4].bias.detach().numpy()}
    pol = rc.PPOPolicy(w)
    x = rng.normal(size=(3, d_in))
    assert np.abs(pol.forward(x) - net(torch.tensor(x, dtype=torch.float32)).detach().numpy()).max() < 1e-5
    mask = np.array([[True, False, True, True]] * 3)
    p = pol.probs(x, mask)
    assert np.allclose(p.sum(1), 1) and (p[:, 1] == 0).all()
