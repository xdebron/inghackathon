"""Koşu klasörlerinin (sonuc.json) getiri ve what-if özetini tablo olarak yazar.
Kullanım: python ozet_whatif.py models/pilot_v13/*/"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
print(f"{'koşu':28s} {'pers.doğ.farkı (P01..P05)':42s} {'WI doğ: doğru/yanlış/enkötü':28s} {'WI test':18s} üstP")
for d in sys.argv[1:]:
    p = Path(d) / "sonuc.json"
    if not p.exists():
        continue
    r = json.loads(p.read_text())
    w, t = r.get("whatif_dogrulama", {}), r.get("whatif_test", {})
    pd = " ".join(f"{x*100:+5.1f}" for x in r["persona_dogrulama_farki"])
    f = lambda w: f"{w.get('dogru_tepki', 0)*100:3.0f}/{w.get('yanlis_yon', 0)*100:3.0f}/{w.get('en_kotu_senaryo_yanlis', 0)*100:3.0f}"
    print(f"{Path(d).name:28s} {pd:42s} {f(w):28s} {f(t):18s} {w.get('ust_olasilik')}")
    for k, v in w.get("senaryolar", {}).items():
        print(f"    {k:17s} doğru %{v['dogru_tepki']*100:3.0f} yanlış %{v['yanlis_yon']*100:3.0f}  Δ {v['ort_degisim']*100:+5.1f}p")
