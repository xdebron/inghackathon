#!/usr/bin/env bash
# WSL pilot v14: tutarlılık düzenleyicisine tek kategori getiri şokları eklendi (v13 HPO kazananı, 6M, son 3 ay holdout).
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
P="--algo nnppo --son-agirlik --ortam 512 --adim 6000000 --n-steps 8 --epoch 2 --lr 3.6375050703373456e-05 --ent 0.00414234475412522 --gizli 1024 --katman 3 --aktivasyon relu --aksiyon-gomme 512 --indirim 0.9668248404375974 --minibatch 4096 --persona-oran 0.3149177604114322 --ilerleme 200 --kirpma-turu tanh --kirp 3 --senaryo-oran 0.3 --momentum 0.15 --tutarlilik-kategori 1"
mkdir -p "$M/pilot_v14"
for T in 10 20; do for S in 0 1; do
  python train_gbrl.py $P --tutarlilik $T --seed $S --env "$M/rl_ortam_v13_v2.npz" --out "$M/pilot_v14/tut${T}_s$S" > "$M/pilot_v14/tut${T}_s$S.log" 2>&1 &
done; done
wait
echo bitti
