#!/usr/bin/env bash
# WSL: v13 soğuk HPO (son 3 ay holdout; what-if makullüğü + doğrulama getirisi)
source ~/gbrl-env/bin/activate
cd /mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl
M=/mnt/c/Users/oof/Documents/inghackathon/backend/models
python hpo_whatif.py --envdir "$M" --out "$M/hpo_v13" --deneme 12 --tohum 2 --paralel 3 --adim 6000000 --lambda 0.1 --baslangic "$M/hpo_v13_baslangic.json"
