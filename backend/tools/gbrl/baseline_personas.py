"""Taban kuralın persona senaryo izlerindeki sonucu (GBRL sonuçlarıyla aynı izler)."""
import json
import sys

import numpy as np

sys.path.insert(0, "/mnt/c/Users/oof/Documents/inghackathon/backend/tools/gbrl")
from train_gbrl import Core, replay  # noqa: E402

d = dict(np.load(sys.argv[1]))
core = Core(d)
base = d["base"]
print(json.dumps({"taban_persona": [round(replay(core, d, f"persona{i}", lambda o, p: base[p])["ortalama_reel_kazanc"], 4)
                                    for i in range(len(d["persona_ids"]))]}))
