#!/usr/bin/env bash
# WSL duman testi: v13 ortamı (son 3 ay holdout), tanh girdi + what-if senaryo artırımı, kısa koşu.
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
python train_gbrl.py --algo nnppo --son-agirlik --env "$M/rl_ortam_v13_v2.npz" --out "$M/nnppo_v13_smoke" --seed 0 \
  --ortam 512 --adim 300000 --n-steps 8 --epoch 2 --lr 3.6e-05 --ent 0.004 --gizli 256 --katman 2 --aktivasyon relu \
  --aksiyon-gomme 128 --indirim 0.967 --minibatch 4096 --persona-oran 0.3 --ilerleme 20 \
  --kirpma-turu tanh --kirp 3 --senaryo-oran 0.5 --momentum 0.15
