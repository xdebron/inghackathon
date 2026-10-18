"""GBRL oblivious + karışık yönlü monoton kısıt denemesi: aynı özellik, bazı çıkışlarda artan, bazılarında azalan.
Python API özellik başına tek yön kabul ettiği için process_monotonic_constraints yerine üçlü dizileri doğrudan veriyoruz."""
import numpy as np
import torch
import gbrl.learners.gbt_learner as GL
from gbrl.models.actor_critic import ActorCritic

TRIPLES = None
_orig = GL.process_monotonic_constraints


def patched(constraints, policy_dim, input_dim):
    if TRIPLES is not None:
        return TRIPLES
    return _orig(constraints, policy_dim, input_dim)


GL.process_monotonic_constraints = patched

rng = np.random.default_rng(0)
D, OUT = 6, 8
# özellik 0: çıkış 0-3 artan, 4-7 azalan
TRIPLES = (np.array([0] * 8, np.int32), np.arange(8, dtype=np.int32), np.array([1] * 4 + [-1] * 4, np.int32))
m = ActorCritic(tree_struct={"max_depth": 4, "grow_policy": "oblivious", "n_bins": 64, "min_data_in_leaf": 0, "par_th": 2},
                input_dim=D, output_dim=OUT + 1,
                policy_optimizer={"policy_algo": "SGD", "policy_lr": 0.1, "start_idx": 0, "stop_idx": OUT},
                value_optimizer={"value_algo": "SGD", "value_lr": 0.05, "start_idx": OUT, "stop_idx": OUT + 1},
                shared_tree_struct=True,
                params={"split_score_func": "cosine", "generator_type": "Quantile", "control_variates": False,
                        "monotonic_constraints": {0: ("increasing", [0])}},
                device="cuda")
for it in range(60):
    x = torch.tensor(rng.normal(size=(2048, D)), dtype=torch.float32, device="cuda")
    # hedef: monotonluğa ters bir sinyal (özellik 0 ile çıkış 0-3 AZALSIN, 4-7 ARTSIN) + özellik 1 ile gürültü
    tgt = torch.cat([-x[:, :1].repeat(1, 4), x[:, :1].repeat(1, 4)], 1) + 0.5 * x[:, 1:2]
    theta, v = m(x, requires_grad=True)
    loss = (theta - tgt).square().mean() + v.square().mean()
    loss.backward()
    m.step(x)
# monotonluk kontrolü: özellik 0'ı tarayıp diğerlerini sabit tut
base = torch.tensor(rng.normal(size=(200, D)), dtype=torch.float32, device="cuda")
viol_inc = viol_dec = 0
prev = None
for val in np.linspace(-3, 3, 25):
    xx = base.clone(); xx[:, 0] = float(val)
    th, _ = m(xx, requires_grad=False)
    th = th.detach().cpu().numpy() if hasattr(th, "detach") else np.asarray(th)
    if prev is not None:
        dlt = th - prev
        viol_inc += int((dlt[:, :4] < -1e-6).sum()); viol_dec += int((dlt[:, 4:] > 1e-6).sum())
    prev = th
print("ağaç", m.get_num_trees(), "ihlal artan", viol_inc, "ihlal azalan", viol_dec,
      "logit aralığı", float(np.ptp(prev[:, :4])), float(np.ptp(prev[:, 4:])), flush=True)
