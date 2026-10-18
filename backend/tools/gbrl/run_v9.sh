#!/usr/bin/env bash
# WSL: profilin tamamı + kısıt kataloğu (v9). HPO'nun en iyi ayarları; önce eğitim/test ayrımlı 3 tohum (tahmin), sonra tüm veri.
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
P="--algo nnppo --son-agirlik --ortam 512 --adim 6000000 --n-steps 8 --epoch 2 --lr 3.6375050703373456e-05 --ent 0.00414234475412522 --gizli 256 --indirim 0.9668248404375974 --minibatch 4096 --persona-oran 0.3149177604114322"
for S in 0 1 2; do
  python train_gbrl.py $P --env "$M/rl_ortam_v9.npz" --out "$M/nnppo_v9/tohum$S" --seed $S > "$M/nnppo_v9_tohum$S.log" 2>&1 &
done
python train_gbrl.py $P --env "$M/rl_ortam_v9_tum.npz" --out "$M/nnppo_v9_tumveri" --seed 0 > "$M/nnppo_v9_tumveri.log" 2>&1 &
wait
for f in "$M"/nnppo_v9_tohum*.log "$M/nnppo_v9_tumveri.log"; do grep -q Traceback "$f" && { echo "HATA $f"; tail -5 "$f"; }; done
echo bitti
