#!/usr/bin/env bash
# WSL: v14 eğitim bütçesi seçimi (yalnızca doğrulama): holdout ortamında 12M ve 25M, 2 tohum. 6M = pilot_v14/tut10_s*, 100M = nnppo_v14_100m.
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
P="--algo nnppo --son-agirlik --ortam 512 --n-steps 8 --epoch 2 --lr 3.6375050703373456e-05 --ent 0.00414234475412522 --gizli 1024 --katman 3 --aktivasyon relu --aksiyon-gomme 512 --indirim 0.9668248404375974 --minibatch 4096 --persona-oran 0.3149177604114322 --kirpma-turu tanh --kirp 3 --senaryo-oran 0.3 --momentum 0.15 --tutarlilik 10 --tutarlilik-kategori 1 --ilerleme 500"
python train_gbrl.py $P --adim 6000000 --seed 0 --env "$M/rl_ortam_v13_v2_tum.npz" --out "$M/nnppo_v14_tum6m" > "$M/nnppo_v14_tum6m.log" 2>&1
wait
echo bitti
