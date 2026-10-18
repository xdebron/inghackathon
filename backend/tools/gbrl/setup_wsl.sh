#!/usr/bin/env bash
# WSL (Ubuntu 24.04, Python 3.12): GBRL deney ortamını sıfırdan kurar. sudo gerektirmez.
#   ~/gbrl-env  : sanal ortam (torch cu128 — RTX 50xx için, gbrl-gpu, stable-baselines3, sb3-contrib, gymnasium)
#   ~/gbrl_sb3  : NVlabs/gbrl_sb3 (pip paketi değil; algos/ ve policies/ sys.path ile kullanılır)
set -e
export TMPDIR=~/piptmp
mkdir -p "$TMPDIR"
rm -rf ~/gbrl-env
python3 -m pip install --user --break-system-packages -q virtualenv
python3 -m virtualenv -q ~/gbrl-env
source ~/gbrl-env/bin/activate
pip install --no-cache-dir -q --upgrade pip
pip install --no-cache-dir -q torch --index-url https://download.pytorch.org/whl/cu128
pip install --no-cache-dir -q gbrl-gpu "stable-baselines3>=2.3" "sb3-contrib>=2.3" gymnasium pyyaml
[ -d ~/gbrl_sb3 ] || git clone -q https://github.com/NVlabs/gbrl_sb3.git ~/gbrl_sb3
ls -la ~/gbrl-env/lib/python3.12/site-packages/nvidia/cudnn/lib/libcudnn.so.9
python - <<'EOF'
import torch, gbrl, sb3_contrib, stable_baselines3
print("torch", torch.__version__, "cuda", torch.cuda.is_available(), torch.cuda.get_device_name(0))
x = torch.randn(256, 256, device="cuda")
print("matmul ok", bool((x @ x).abs().sum() > 0))
print("sb3", stable_baselines3.__version__, "sb3_contrib", sb3_contrib.__version__)
import sys, os
sys.path.insert(0, os.path.expanduser("~/gbrl_sb3"))
from algos.ppo import PPO_GBRL
print("PPO_GBRL import ok")
EOF
