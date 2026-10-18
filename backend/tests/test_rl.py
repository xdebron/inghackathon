"""RL katmanı testleri: aksiyon maskesi, ödül/ceza, öğrenilen davranış, göstergelerin kullanımı ve tekrarlanabilirlik
(oyuncak piyasalarda)."""
from __future__ import annotations

import numpy as np

from app import rl


class P:
    def __init__(self, pid):
        self.product_id = pid


def toy_market(safe=0.01, up=0.12, down=-0.08, n=600, regime_feature=False):
    """A: her adım +%1 (reel). B: +%12 veya −%8. regime_feature=True ise o adımın B sonucunu önceden bildiren bir gösterge
    vardır (oyuncak test: DQN göstergeyi kullanabiliyor mu?); yoksa gösterge gürültüdür."""
    rng = np.random.default_rng(123)
    good = rng.random(n) < 0.5
    b = np.where(good, up, down)
    feat = np.where(good, 1.0, -1.0) if regime_feature else rng.normal(size=n)
    idx = np.arange(n - 70)
    return {"products": [P("FON:A"), P("FON:B")], "nominal": np.column_stack([np.full(n, safe), b]),
            "inflation": np.zeros(n), "tax": np.zeros(2), "features": feat[:, None], "features_raw": feat[:, None],
            "feature_names": ["rejim"], "feature_sd": np.ones(1), "fund_mask": np.array([False, False]),
            "train": idx, "val": idx, "test": idx, "step": 63}


def start_alloc(env, agent, idx=0):
    return env.actions[agent.act(0, 1.0, idx, None)]


def test_action_grid_caps():
    acts = rl.action_grid(4, 0.25, 0.5)
    assert np.allclose(acts.sum(axis=1), 1) and acts.max() <= 0.5 + 1e-9
    assert len(rl.action_grid(3, 0.25, None)) == 15
    capped = rl.action_grid(3, 0.25, None, np.array([True, True, False]), 0.5)  # fon başına ≤ %50, mevduat serbest
    assert (capped[:, :2] <= 0.5 + 1e-9).all() and capped[:, 2].max() == 1.0


def test_penalty_applies_only_below_principal_and_first_buy_costs():
    env = rl.Env(toy_market(), "Yüksek", 1, False, np.random.default_rng(0))
    a_b = int(np.argmax(env.actions[:, 1]))
    r = np.array([0.01, -0.08])
    w, rew = env.outcome_all(1.0, None, r)
    assert w[a_b] < 1 and rew[a_b] < (w[a_b] - 1) - 0.1
    r = np.array([0.01, 0.12])
    w, rew = env.outcome_all(1.0, None, r)
    assert abs(w[a_b] - (1.12 - env.cost)) < 1e-12  # nakitten ilk alım: tam devir maliyeti
    w2, _ = env.outcome_all(1.0, env.actions[a_b], r)
    assert abs(w2[a_b] - 1.12) < 1e-12  # aynı dağılımı korumak maliyetsiz


def test_tabular_low_protection_prefers_mean_high_protection_prefers_safe():
    m = toy_market()
    env = rl.Env(m, "Düşük", 1, False, np.random.default_rng(1))
    tab = rl.TabularAgent(env).train(m["train"], 20000, np.random.default_rng(1))
    assert start_alloc(env, tab)[1] >= 0.75
    env = rl.Env(m, "Yüksek", 1, False, np.random.default_rng(1))
    tab = rl.TabularAgent(env).train(m["train"], 20000, np.random.default_rng(1))
    assert start_alloc(env, tab)[0] >= 0.75


def test_dqn_uses_market_indicator_tabular_cannot():
    m = toy_market(regime_feature=True)
    env = rl.Env(m, "Orta", 1, False, np.random.default_rng(2))
    dqn = rl.DQNAgent(env, np.random.default_rng(3)).train(m["train"], np.random.default_rng(4))
    good = int(np.where(m["features"][:, 0] > 0)[0][0])
    bad = int(np.where(m["features"][:, 0] < 0)[0][0])
    assert start_alloc(env, dqn, good)[1] >= 0.75   # gösterge iyi diyorsa B
    assert start_alloc(env, dqn, bad)[0] >= 0.75    # gösterge kötü diyorsa A
    tab = rl.TabularAgent(env).train(m["train"], 20000, np.random.default_rng(5))
    ev = rl.evaluate(env, {"DQN": dqn, "TAB": tab}, np.array([0.5, 0.5]), np.array([0.0, 0.1]), m["test"], 300,
                     np.random.default_rng(6))
    assert ev["DQN"]["ortalama_reel_kazanc"] > ev["TAB"]["ortalama_reel_kazanc"] + 0.02


def test_sequential_paths_follow_history_and_jump():
    m = toy_market()
    env = rl.Env(m, "Orta", 1, False, np.random.default_rng(0))
    p = env.path(m["train"], 40, np.random.default_rng(1))
    steps = np.diff(p)
    assert (steps == env.step).mean() > 0.5 and (steps != env.step).any()


def test_training_is_deterministic_with_seed():
    m = toy_market(regime_feature=True)
    outs = []
    for _ in range(2):
        env = rl.Env(m, "Orta", 1, False, np.random.default_rng(7))
        dqn = rl.DQNAgent(env, np.random.default_rng(8)).train(m["train"], np.random.default_rng(9))
        outs.append(dqn.net.forward(dqn.x(0, 1.0, 0, None)))
    assert np.array_equal(outs[0], outs[1])
