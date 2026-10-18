#!/usr/bin/env bash
# WSL: risk duyarlı ödülle NN-PPO (persona odaklı). γ_temel önceden belirlendi: 4 (ana), 10 (duyarlılık).
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
for G in 4 10; do
  python train_gbrl.py --algo nnppo --env "$M/rl_ortam_v7.npz" --out "$M/nnppo_v7_risk$G" --adim 6000000 --ortam 512 \
      --n-steps 16 --epoch 4 --degerlendirme 20 --persona-oran 0.3 --persona-secim \
      --risk-kov "$M/rl_ortam_v7_nominal_kov.npy" --risk-gamma "$G" > "$M/nnppo_v7_risk$G.log" 2>&1 &
done
wait
for G in 4 10; do tail -c 600 "$M/nnppo_v7_risk$G.log"; echo; done
