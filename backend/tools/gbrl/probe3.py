"""GBRL ActorCritic'i doğrudan dener: cihaz (cpu/cuda) × çıkış boyutu (5 / 4172) × büyüme politikası."""
import traceback

import numpy as np
import torch
from gbrl.models.actor_critic import ActorCritic

rng = np.random.default_rng(0)
for device in ("cuda",):
    for out_dim in (513, 562, 600):
        for grow in ("oblivious",):
            tag = f"{device} out={out_dim} {grow}"
            try:
                m = ActorCritic(tree_struct={"max_depth": 4, "grow_policy": grow, "n_bins": 256, "min_data_in_leaf": 0, "par_th": 2},
                                input_dim=53, output_dim=out_dim,
                                policy_optimizer={"policy_algo": "SGD", "policy_lr": 0.05, "policy_shrinkage": 0, "start_idx": 0, "stop_idx": out_dim - 1},
                                value_optimizer={"value_algo": "SGD", "value_lr": 0.02, "value_shrinkage": 0, "start_idx": out_dim - 1, "stop_idx": out_dim},
                                shared_tree_struct=True,
                                params={"split_score_func": "cosine", "generator_type": "Quantile", "control_variates": False},
                                device=device)
                x = torch.tensor(rng.normal(size=(1024, 53)), dtype=torch.float32, device=device)
                theta, v = m(x, requires_grad=True)
                loss = theta.square().mean() + v.square().mean()
                loss.backward()
                m.step(x)
                print(tag, "OK", tuple(theta.shape), m.get_num_trees(), flush=True)
            except Exception as e:  # noqa: BLE001
                print(tag, "HATA", type(e).__name__, str(e)[:160], flush=True)
                if device == "cpu" and out_dim == 5:
                    traceback.print_exc()
