#!/usr/bin/env bash
# WSL: sinir ağlı MaskablePPO'yu 96 ve 4.171 aksiyonlu ortamlarda paralel eğitir (GBRL ile aynı ortam ve izler).
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
python train_gbrl.py --algo nnppo --env "$M/rl_ortam_v5_g96.npz" --out "$M/nnppo_v5_g96" --adim 6000000 --ortam 512 \
    --n-steps 16 --epoch 4 --degerlendirme 20 > "$M/nnppo_v5_g96.log" 2>&1 &
python train_gbrl.py --algo nnppo --env "$M/rl_ortam_v5.npz" --out "$M/nnppo_v5_full" --adim 6000000 --ortam 512 \
    --n-steps 16 --epoch 4 --degerlendirme 20 > "$M/nnppo_v5_full.log" 2>&1 &
wait
tail -c 800 "$M/nnppo_v5_g96.log"; echo; tail -c 800 "$M/nnppo_v5_full.log"
