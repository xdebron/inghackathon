"""ING vadeli mevduat oranları: sitenin kendi "mevduat hesaplama" aracının uç noktası (anahtarsız).

  POST https://www.ing.com.tr/ProxyManagement/SiteManagerService_Script.aspx/DepositCalculationComposition

Yanıt; yıllık brüt oran, brüt faiz, STOPAJ ve net faizi içerir. Stopaj oranı kaynağın kendi hesabıdır (koda sabit
yazılmaz, kural 7). TL için Private Banking kademesi (1.000.000 TL) ürün olarak yazılır; döviz için tüm kademeler yazılır
(ürün katmanı TL karşılığı ~1 mn TL olan kademeyi seçer). Boş/bozuk yanıt atlanır, değer uydurulmaz.
"""
from __future__ import annotations

import json

from ..db import models as M, store
from .base import Polite

ENDPOINT = "https://www.ing.com.tr/ProxyManagement/SiteManagerService_Script.aspx/DepositCalculationComposition"
PAGE = "https://www.ing.com.tr/tr/sizin-icin/mevduat"
SOURCE = "ING mevduat hesaplayıcısı"
CURRENCIES = ("TL", "USD", "EUR")
DAY_COUNTS = (32, 46, 92, 181, 365)
CAPITALS = {"TL": (1_000_000,), "USD": (10_000, 100_000, 1_000_000), "EUR": (10_000, 100_000, 1_000_000)}
TL_TIER = 1_000_000


def _tr(s):
    if s is None:
        return None
    s = str(s).replace("%", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_response(payload: dict) -> dict | None:
    d = payload.get("d")
    if not d:
        return None
    items = json.loads(d)
    if not items:
        return None
    a = items[0]
    gross, tax = _tr(a.get("grossInterest")), _tr(a.get("incomeTax"))
    return {"rate_pct": _tr(a.get("interestRate")),
            "withholding_pct": round(tax / gross * 100, 2) if gross and tax is not None else None}


def run(pause: float = 0.5, log=print) -> dict:
    cli = Polite(pause=pause, headers={"Content-Type": "application/json; charset=utf-8", "Origin": "https://www.ing.com.tr",
                                       "Referer": PAGE})
    rows, missing = [], 0
    with store.Run("ing_mevduat") as run_:
        for cur in CURRENCIES:
            for days in DAY_COUNTS:
                for cap in CAPITALS[cur]:
                    r = cli.post(ENDPOINT, json={"CalculationType": 1, "RateType": "520", "Currency": cur, "DayCount": str(days),
                                                 "Capital": str(cap)})
                    p = parse_response(r.json()) if r is not None else None
                    if not p or p["rate_pct"] is None:
                        missing += 1
                        continue
                    now = store.now_iso()
                    rows.append({"banka": "ING", "tur": "mevduat", "para_birimi": "TRY" if cur == "TL" else cur, "vade_gun": days,
                                 "oran": p["rate_pct"] / 100, "min_tutar": float(cap),
                                 "stopaj_orani": None if p["withholding_pct"] is None else p["withholding_pct"] / 100,
                                 "kaynak": SOURCE, "url": ENDPOINT, "as_of": now[:10], "fetched_at": now,
                                 "notlar": (f"{cap:,} {cur} kademesi" + (" (Private Banking için varsayım)" if cur == "TL" else "")).replace(",", ".")})
        if not rows:
            raise RuntimeError("ING hesaplayıcısından hiç oran alınamadı")
        store.replace_rows(M.Mevduat, rows, M.Mevduat.banka == "ING", run_)
        tl = [r for r in rows if r["para_birimi"] == "TRY" and r["stopaj_orani"] is not None]
        if tl:   # mevduat stopajı: hesaplayıcının kendi hesabı (RM teyit eder)
            store.replace_rows(M.Stopaj, [{"kategori": "mevduat_tl", "oran": tl[0]["stopaj_orani"],
                                           "kaynak": "ING mevduat hesaplayıcısı (stopaj satırı)", "url": ENDPOINT,
                                           "as_of": tl[0]["as_of"], "fetched_at": tl[0]["fetched_at"],
                                           "notlar": "Hesaplayıcının kendi stopaj hesabı; RM teyit etmeli."}],
                               M.Stopaj.kategori == "mevduat_tl", run_)
        run_.durum = "kismi" if missing else "basarili"
        run_.mesaj = f"{missing} sorgu boş döndü" if missing else None
        log(f"  {len(rows)} oran yazıldı ({sum(r['para_birimi'] == 'TRY' for r in rows)} TL, "
            f"{sum(r['para_birimi'] != 'TRY' for r in rows)} döviz); boş {missing}")
    cli.close()
    return {"oran": len(rows), "bos": missing}
