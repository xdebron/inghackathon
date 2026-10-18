#!/usr/bin/env bash
# WSL: GBRL ortamının sağlık kontrolü ve gbrl_sb3 PPO arayüzünün incelenmesi.
source ~/gbrl-env/bin/activate
python - <<'EOF'
import torch, gbrl
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("gbrl", [n for n in dir(gbrl) if not n.startswith("_")])
EOF
ls ~/gbrl_sb3/algos ~/gbrl_sb3/policies
grep -n "^class\|def __init__" ~/gbrl_sb3/algos/ppo.py | head
sed -n '/def __init__/,/):/p' ~/gbrl_sb3/algos/ppo.py | head -60
grep -rn "mask" ~/gbrl_sb3/algos/ppo.py ~/gbrl_sb3/policies/*.py | head -8
grep -n "ppo_gbrl" -A40 ~/gbrl_sb3/config/defaults.yaml | head -60
