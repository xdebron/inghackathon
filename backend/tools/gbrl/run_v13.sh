#!/usr/bin/env bash
# WSL: v13 = HPO kazananı (v2, tanh c=3, senaryo artırımı 0,3, κ 0,15, what-if tutarlılık 10). Paralel:
#   1) teyit: holdout ortamında 3 yeni tohum × 6M (HPO'da kullanılmayan tohumlar 2,3,4)
#   2) holdout 100M (son 3 ay test tahmini)       3) TÜM VERİ 100M (canlı aday)
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
P="--algo nnppo --son-agirlik --ortam 512 --n-steps 8 --epoch 2 --lr 3.6375050703373456e-05 --ent 0.00414234475412522 --gizli 1024 --katman 3 --aktivasyon relu --aksiyon-gomme 512 --indirim 0.9668248404375974 --minibatch 4096 --persona-oran 0.3149177604114322 --kirpma-turu tanh --kirp 3 --senaryo-oran 0.3 --momentum 0.15 --tutarlilik 10"
mkdir -p "$M/nnppo_v13_teyit"
for S in 2 3 4; do
  python train_gbrl.py $P --adim 6000000 --ilerleme 200 --env "$M/rl_ortam_v13_v2.npz" --out "$M/nnppo_v13_teyit/tohum$S" --seed $S > "$M/nnppo_v13_teyit/tohum$S.log" 2>&1 &
done
python train_gbrl.py $P --adim 100000000 --ilerleme 500 --env "$M/rl_ortam_v13_v2.npz" --out "$M/nnppo_v13_100m" --seed 0 > "$M/nnppo_v13_100m.log" 2>&1 &
python train_gbrl.py $P --adim 100000000 --ilerleme 500 --env "$M/rl_ortam_v13_v2_tum.npz" --out "$M/nnppo_v13_tum100m" --seed 0 > "$M/nnppo_v13_tum100m.log" 2>&1 &
wait
for f in "$M"/nnppo_v13_teyit/*.log "$M/nnppo_v13_100m.log" "$M/nnppo_v13_tum100m.log"; do grep -q Traceback "$f" && { echo "HATA $f"; tail -5 "$f"; }; done
echo bitti
