#!/usr/bin/env bash
# WSL pilot v15g: GBRL (ağaç PPO), 390 aksiyon, son 3 ay holdout. Greedy vs oblivious (simetrik) vs oblivious + monoton what-if kısıtı.
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
P="--algo gbrl --son-agirlik --ortam 512 --adim ${ADIM:-6000000} --n-steps 16 --epoch 2 --derinlik 4 --policy-lr 0.05 --value-lr 0.02 --ent 0.001 --indirim 0.9668248404375974 --persona-oran 0.3149177604114322 --ilerleme 100 --kirpma-turu tanh --kirp 3 --env $M/rl_ortam_v15g.npz"
mkdir -p "$M/pilot_v15g"
run() { python train_gbrl.py $P --out "$M/pilot_v15g/$1" "${@:2}" > "$M/pilot_v15g/$1.log" 2>&1; }
run greedy --buyume greedy --seed 0 &
run oblivious --buyume oblivious --seed 0 &
run obl_monoton --buyume oblivious --monoton 1 --seed 0 &
run obl_monoton_s1 --buyume oblivious --monoton 1 --seed 1 &
wait
echo bitti
