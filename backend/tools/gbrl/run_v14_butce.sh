#!/usr/bin/env bash
# WSL: v14 eğitim bütçesi seçimi (yalnızca doğrulama): holdout ortamında 12M ve 25M, 2 tohum. 6M = pilot_v14/tut10_s*, 100M = nnppo_v14_100m.
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
P="--algo nnppo --son-agirlik --ortam 512 --n-steps 8 --epoch 2 --lr 3.6375050703373456e-05 --ent 0.00414234475412522 --gizli 1024 --katman 3 --aktivasyon relu --aksiyon-gomme 512 --indirim 0.9668248404375974 --minibatch 4096 --persona-oran 0.3149177604114322 --kirpma-turu tanh --kirp 3 --senaryo-oran 0.3 --momentum 0.15 --tutarlilik 10 --tutarlilik-kategori 1 --ilerleme 500"
mkdir -p "$M/nnppo_v14_butce"
for B in 12 25; do for S in 0 1; do
  python train_gbrl.py $P --adim ${B}000000 --seed $S --env "$M/rl_ortam_v13_v2.npz" --out "$M/nnppo_v14_butce/b${B}m_s$S" > "$M/nnppo_v14_butce/b${B}m_s$S.log" 2>&1 &
done; done
wait
echo bitti
