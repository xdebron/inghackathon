#!/usr/bin/env bash
# WSL pilot 2: what-if tutarlılık düzenleyicisi (v11 mimarisi, 6M adım, son 3 ay holdout).
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
P="--algo nnppo --son-agirlik --ortam 512 --adim 6000000 --n-steps 8 --epoch 2 --lr 3.6375050703373456e-05 --ent 0.00414234475412522 --gizli 1024 --katman 3 --aktivasyon relu --aksiyon-gomme 512 --indirim 0.9668248404375974 --minibatch 4096 --persona-oran 0.3149177604114322 --ilerleme 100 --seed 0 --kirpma-turu tanh --kirp 3 --senaryo-oran 0.3"
mkdir -p "$M/pilot_v13"
run() { python train_gbrl.py $P --env "$M/rl_ortam_v13_$1.npz" --out "$M/pilot_v13/$2" "${@:3}" > "$M/pilot_v13/$2.log" 2>&1; }
run v2 tut2 --tutarlilik 2 &
run v2 tut10 --tutarlilik 10 &
run v2 tut50 --tutarlilik 50 &
run v3 v3tut10 --tutarlilik 10 &
wait
echo bitti
