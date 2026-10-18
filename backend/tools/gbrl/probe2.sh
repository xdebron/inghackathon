#!/usr/bin/env bash
# gbrl_sb3'ün PPO_GBRL için policy_kwargs'ı nasıl kurduğunu gösterir.
cd ~/gbrl_sb3
grep -rn "tree_struct\|tree_optimizer\|shrinkage\|grow_policy\|policy_algo" scripts/ utils/ | grep -v "^Binary" | head -40
sed -n '/def __init__/,/self.learner.reset/p' ~/gbrl-env/lib/python3.12/site-packages/gbrl/models/actor_critic.py | head -80
