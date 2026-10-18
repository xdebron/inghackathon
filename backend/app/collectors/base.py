"""Toplayıcıların ortak araçları: kibar HTTP istemcisi ve seri yazıcı.

İstekler sıralı ve aralarında bekleme vardır; 429'da sunucunun söylediği kadar (yoksa artan sürede) beklenir, art arda
hatada durulur. Hiçbir toplayıcı bot korumasını aşmaya çalışmaz; uygulama TEFAS'a istek atmaz (TEFAS verisi yüklemeyle gelir).
"""
from __future__ import annotations

import time

import httpx
import pandas as pd

from ..db import store

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class Stop(RuntimeError):
    """Art arda hata: kaynak zorlanmıyor, toplayıcı durur (o ana kadar yazılanlar kalır)."""


class Polite:
    def __init__(self, pause: float = 1.0, max_consecutive: int = 5, headers: dict | None = None, timeout: float = 60):
        self.headers = {"User-Agent": UA, "Accept-Language": "tr", **(headers or {})}
        self.timeout = timeout
        self.c = httpx.Client(headers=self.headers, timeout=timeout, follow_redirects=True)
        self.pause, self.max_consecutive, self.errors = pause, max_consecutive, 0

    def request(self, method: str, url: str, **kw) -> httpx.Response | None:
        for attempt in range(1, 5):
            try:
                r = self.c.request(method, url, **kw)
                err = None if r.status_code < 400 else f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                r, err = None, type(exc).__name__
            time.sleep(self.pause)
            if r is not None and r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or 0) or 30 * attempt)
                continue
            if err is None:
                self.errors = 0
                return r
            if r is not None and r.status_code < 500:
                break                                              # 4xx: tekrar denemenin anlamı yok
            time.sleep(15 * attempt)                               # bağlantı kopması / 5xx: geri çekil
            self.c.close()
            self.c = httpx.Client(headers=self.headers, timeout=self.timeout, follow_redirects=True)
        self.errors += 1
        if self.errors >= self.max_consecutive:
            raise Stop(f"{self.errors} ardışık hata ({url}); kaynak zorlanmadan durduruldu")
        return None

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)

    def close(self):
        self.c.close()


def write_series(sid: str, df: pd.DataFrame, info: dict, run: store.Run, grup: str = "gecmis") -> dict:
    """Seriyi (tarih + değer kolonları) meta bilgisiyle yazar; meta: satır, ilk/son tarih, durum."""
    df = df.dropna(subset=[c for c in df.columns if c != "tarih"], how="all")
    df = df.drop_duplicates("tarih", keep="last").sort_values("tarih")
    meta = {**info, "satir": int(len(df)), "ilk": str(df["tarih"].min()) if len(df) else None,
            "son": str(df["tarih"].max()) if len(df) else None, "status": "ok" if len(df) else "missing"}
    store.save_series(sid, grup, df, meta, run)
    return meta


def load_series(sid: str) -> pd.Series:
    d = store.series(sid)
    if d is None or "deger" not in d:
        raise LookupError(f"seri yok: {sid}")
    return pd.Series(d["deger"].to_numpy(dtype=float), index=pd.to_datetime(d["tarih"])).sort_index()
