"""Veri tabanından okuma (ve yazma) yardımcıları.

Okuma fonksiyonları, önceki dosya tabanlı biçimlerle aynı şekli döndürür (ör. manual_raw() eski manuel_veriler.json'un
şeklini), böylece kural ve puanlama kodu değişmeden veri tabanından beslenir. Kaynak alanları her kayıtta korunur.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import delete, func, select

from . import models as M
from .session import engine, init_db, session_scope

_ready = False


def ready() -> None:
    global _ready
    if not _ready:
        init_db()
        _ready = True


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def version(*prefixes: str) -> tuple:
    """Veri değişince değişen anahtar (önbellekler için): ilgili kaynaklardaki son başarılı çekimin kimliği.
    prefixes verilmezse tüm kaynaklar; örn. version("tefas", "aktarim:tefas") yalnızca TEFAS verisi değişince değişir."""
    ready()
    q = select(func.max(M.Cekim.id)).where(M.Cekim.durum.in_(("basarili", "kismi")))
    if prefixes:
        from sqlalchemy import or_
        q = q.where(or_(*[M.Cekim.kaynak.startswith(p) for p in prefixes]))
    with session_scope() as s:
        return (s.scalar(q) or 0,)


def _prov(r) -> dict:
    out = {"source": r.kaynak, "url": r.url, "as_of": r.as_of, "fetched_at": r.fetched_at}
    if r.notlar:
        out["not"] = r.notlar
    return out


# --- Okuma: eski dosya biçimleriyle aynı şekil --------------------------------------------------------------------

def manual_raw() -> dict:
    """Göstergeler, mevduat, stopaj, benchmark: eski manuel_veriler.json şekli (artık toplayıcıların yazdığı tablolardan)."""
    ready()
    with session_scope() as s:
        # "evds:", "tcmb:" gibi ad alanlı satırlar canlı kaynakların son başarılı çekimidir; burada değil kendi modüllerinde okunur
        g = {r.kod: {"deger": r.deger, **_prov(r)} for r in s.scalars(select(M.Gosterge)) if ":" not in r.kod}
        mev = [{"banka": r.banka, "tur": r.tur, "vade_gun": r.vade_gun, "oran": r.oran, "min_tutar": r.min_tutar, **_prov(r)}
               for r in s.scalars(select(M.Mevduat).where(M.Mevduat.para_birimi == "TRY").order_by(M.Mevduat.banka, M.Mevduat.vade_gun))]
        st = [{"kategori": r.kategori, "oran": r.oran, **_prov(r)} for r in s.scalars(select(M.Stopaj))]
        bm = [{"ad": r.ad, "kategoriler": r.kategoriler, "getiri_1y": r.getiri_1y, **_prov(r)} for r in s.scalars(select(M.Benchmark))]
    return {"gostergeler": g, "mevduat": mev, "stopaj": st, "fon_ek_bilgi": {}, "benchmarklar": bm}


_FON_ALANLARI = ("resmi_rd", "rd_belirsiz", "yonetim_ucreti_yillik", "giris_komisyonu", "cikis_komisyonu", "semsiye_turu", "isin",
                 "karsilastirma_olcutu", "nitelikli_yatirimci", "nitelikli_notu", "valor_satis_gun", "valor_kaynak", "uyari_notu",
                 "yontem", "toplayan")


def kap_funds() -> dict:
    """KAP fon bilgileri: {"fonlar": {kod: kayıt}} (eski kap_fon_bilgileri.json şekli)."""
    ready()
    with session_scope() as s:
        out = {}
        for r in s.scalars(select(M.FonBilgi)):
            rec = {k: getattr(r, k) for k in _FON_ALANLARI if getattr(r, k) is not None}
            out[r.kod] = {**rec, **_prov(r)}
    return {"fonlar": out} if out else None


def platform_funds(platform: str = "ING") -> dict | None:
    ready()
    with session_scope() as s:
        rows = list(s.scalars(select(M.PlatformFon).where(M.PlatformFon.platform == platform).order_by(M.PlatformFon.kod)))
    if not rows:
        return None
    return {**_prov(rows[0]), "kodlar": [r.kod for r in rows]}


def stopaj_history() -> dict | None:
    """Dönemlere göre stopaj (eski stopaj_tarihce.json şekli)."""
    ready()
    with session_scope() as s:
        rows = list(s.scalars(select(M.StopajDonem).order_by(M.StopajDonem.kategori, M.StopajDonem.sira)))
    if not rows:
        return None
    donemler, seen = [], set()
    for r in sorted(rows, key=lambda r: r.sira):
        if r.donem not in seen:
            seen.add(r.donem)
            donemler.append({"ad": r.donem, "baslangic": r.baslangic, "bitis": r.bitis})
    kat: dict[str, list] = {}
    for r in rows:
        kat.setdefault(r.kategori, []).append(r.oran)
    return {**_prov(rows[0]), "donemler": donemler, "kategoriler": kat}


def fx_deposit_rows(bank: str = "ING") -> list[dict]:
    """Döviz mevduat satırları (eski ing_deposit_rates.json 'rows' şekli)."""
    ready()
    with session_scope() as s:
        rows = list(s.scalars(select(M.Mevduat).where(M.Mevduat.banka == bank, M.Mevduat.para_birimi != "TRY")))
    return [{"currency": r.para_birimi, "capital": r.min_tutar, "day_count": r.vade_gun,
             "annual_gross_rate_pct": None if r.oran is None else r.oran * 100,
             "withholding_rate_pct": None if r.stopaj_orani is None else r.stopaj_orani * 100,
             "source": r.url, "kaynak": r.kaynak, "fetched_at": r.fetched_at, "status": "ok" if r.oran is not None else "missing"}
            for r in rows]


def series(kod: str) -> pd.DataFrame | None:
    """Zaman serisi: tarih + kolonlar (eski gecmis/<kod>.csv şekli). Yoksa None."""
    ready()
    q = select(M.SeriDeger.tarih, M.SeriDeger.kolon, M.SeriDeger.deger).where(M.SeriDeger.seri == kod)
    with engine.connect() as c:
        df = pd.read_sql(q, c)
    if df.empty:
        return None
    wide = df.pivot(index="tarih", columns="kolon", values="deger").reset_index().sort_values("tarih")
    wide.columns.name = None
    return wide


def series_codes(grup: str) -> list[str]:
    ready()
    with session_scope() as s:
        return [k for k in s.scalars(select(M.Seri.kod).where(M.Seri.grup == grup).order_by(M.Seri.kod))]


def series_meta(grup: str = "gecmis") -> dict:
    """{kod: meta} (eski gecmis/meta.json şekli). BIST/BYF serilerinde kod öneksizdir."""
    ready()
    with session_scope() as s:
        rows = list(s.scalars(select(M.Seri).where(M.Seri.grup == grup)))
    return {r.kod.split("/", 1)[-1]: r.meta for r in rows}


def get_indicator(kod: str) -> dict | None:
    """Bir göstergenin son kaydı (DataPoint alanlarıyla)."""
    ready()
    with session_scope() as s:
        r = s.get(M.Gosterge, kod)
        if r is None:
            return None
        return {"value": r.deger, "unit": r.birim, "source": r.kaynak, "url": r.url, "as_of": r.as_of,
                "fetched_at": r.fetched_at, "note": r.notlar}


def put_indicator(kod: str, value, unit, source, url, as_of, fetched_at, note, run: "Run | None" = None) -> None:
    upsert_rows(M.Gosterge, [{"kod": kod, "deger": value, "birim": unit, "kaynak": source, "url": url, "as_of": as_of,
                             "fetched_at": fetched_at, "notlar": note}], run)


def document(ad: str):
    ready()
    with session_scope() as s:
        r = s.get(M.Belge, ad)
        return None if r is None else r.icerik


def fund_prices(kaynak: str = "tefas_export", columns: tuple = ("tarih", "kod", "ad", "fiyat", "pay", "kisi", "buyukluk")) -> pd.DataFrame:
    ready()
    cols = ", ".join(columns)
    with engine.connect() as c:
        return pd.read_sql(f"SELECT {cols} FROM fon_fiyat WHERE kaynak = :k ORDER BY kod, tarih", c, params={"k": kaynak})


def fund_returns() -> pd.DataFrame:
    """En son dönem sonuna ait TEFAS Fon Getirileri satırları (eski tefas_karsilastirma.csv şekli)."""
    ready()
    with engine.connect() as c:
        return pd.read_sql("SELECT kod, ad, tur, resmi_rd_tefas, getiri_1a, getiri_yb, getiri_1y, getiri_5y, getiri_3a, getiri_3y, "
                           "getiri_6a, as_of FROM fon_getiri WHERE as_of = (SELECT MAX(as_of) FROM fon_getiri) ORDER BY kod", c)


def raw_table(tablo: str) -> pd.DataFrame | None:
    ready()
    with session_scope() as s:
        rows = [r.satir for r in s.scalars(select(M.HamTablo).where(M.HamTablo.tablo == tablo).order_by(M.HamTablo.sira))]
    return pd.DataFrame(rows) if rows else None


# --- Yazma ------------------------------------------------------------------------------------------------------

class Run:
    """Bir toplama çalışması: `with store.Run("kap_fon", "otomatik") as run:` → run.id satırlara yazılır, sonuç cekim'e kaydedilir."""

    def __init__(self, kaynak: str, yontem: str = "otomatik"):
        self.kaynak, self.yontem, self.satir, self.mesaj, self.durum = kaynak, yontem, 0, None, "basarili"

    def __enter__(self):
        ready()
        with session_scope() as s:
            c = M.Cekim(kaynak=self.kaynak, yontem=self.yontem, baslangic=now_iso(), durum="calisiyor")
            s.add(c)
            s.flush()
            self.id = c.id
        return self

    def __exit__(self, et, ev, tb):
        with session_scope() as s:
            c = s.get(M.Cekim, self.id)
            c.bitis = now_iso()
            c.satir = self.satir
            c.durum = "hata" if et else self.durum
            c.mesaj = f"{et.__name__}: {ev}" if et else self.mesaj
        return False


def replace_rows(model, rows: list[dict], where=None, run: Run | None = None) -> int:
    """Bir tablonun (ya da where ile bir kısmının) içeriğini verilen satırlarla değiştirir (tek işlemde)."""
    ready()
    with session_scope() as s:
        s.execute(delete(model).where(where) if where is not None else delete(model))
        if rows:
            if run is not None and hasattr(model, "cekim_id"):
                rows = [{**r, "cekim_id": run.id} for r in rows]
            keys = sorted({k for r in rows for k in r})          # toplu ekleme her satırda aynı alanları ister; eksik alan NULL
            rows = [{k: r.get(k) for k in keys} for r in rows]
            s.execute(model.__table__.insert(), rows)
    if run is not None:
        run.satir += len(rows)
    return len(rows)


def upsert_rows(model, rows: list[dict], run: Run | None = None) -> int:
    """Birincil anahtara göre ekler ya da günceller."""
    ready()
    if not rows:
        return 0
    with session_scope() as s:
        for r in rows:
            if run is not None and hasattr(model, "cekim_id"):
                r = {**r, "cekim_id": run.id}
            s.merge(model(**r))
    if run is not None:
        run.satir += len(rows)
    return len(rows)


def save_series(kod: str, grup: str, df: pd.DataFrame, meta: dict, run: Run | None = None) -> int:
    """Bir serinin tüm değerlerini yazar. df: 'tarih' + değer kolonları."""
    ready()
    long = df.melt(id_vars=["tarih"], var_name="kolon", value_name="_v")
    long["tarih"] = long["tarih"].astype(str).str[:10]
    long = long.astype(object).where(pd.notna(long), None)
    with session_scope() as s:
        s.merge(M.Seri(kod=kod, grup=grup, meta=meta, cekim_id=None if run is None else run.id))
        s.execute(delete(M.SeriDeger).where(M.SeriDeger.seri == kod))
        recs = [{"seri": kod, "tarih": t, "kolon": k, "deger": None if v is None else float(v)}
                for t, k, v in long[["tarih", "kolon", "_v"]].itertuples(index=False)]
        for i in range(0, len(recs), 50_000):
            s.execute(M.SeriDeger.__table__.insert(), recs[i:i + 50_000])
    if run is not None:
        run.satir += len(long)
    return len(long)


def save_document(ad: str, icerik, run: Run | None = None) -> None:
    ready()
    with session_scope() as s:
        s.merge(M.Belge(ad=ad, icerik=icerik, fetched_at=now_iso(), cekim_id=None if run is None else run.id))


def save_raw_table(tablo: str, df: pd.DataFrame, run: Run | None = None) -> int:
    rows = [{"tablo": tablo, "sira": i, "satir": {k: (None if pd.isna(v) else v) for k, v in r.items()}}
            for i, r in enumerate(df.to_dict("records"))]
    return replace_rows(M.HamTablo, rows, M.HamTablo.tablo == tablo, run)


def save_fund_prices(kaynak: str, df: pd.DataFrame, run: Run | None = None, replace: bool = True) -> int:
    """Fon fiyatlarını yazar (kaynak bazında tümüyle değiştirir). df: tarih, kod (+ ad, fiyat, pay, kisi, buyukluk)."""
    ready()
    cols = [c for c in ("tarih", "kod", "ad", "fiyat", "pay", "kisi", "buyukluk") if c in df.columns]
    d = df[cols].copy()
    d["tarih"] = d["tarih"].astype(str).str[:10]
    d["kaynak"] = kaynak
    d["cekim_id"] = None if run is None else run.id
    with engine.begin() as c:
        if replace:
            c.execute(delete(M.FonFiyat).where(M.FonFiyat.kaynak == kaynak))
        d.to_sql("fon_fiyat", c, if_exists="append", index=False, chunksize=3_000, method="multi")
    if run is not None:
        run.satir += len(d)
    return len(d)


def runs(limit: int = 50) -> list[dict]:
    ready()
    with session_scope() as s:
        rows = s.scalars(select(M.Cekim).order_by(M.Cekim.id.desc()).limit(limit))
        return [{k: getattr(r, k) for k in ("id", "kaynak", "yontem", "baslangic", "bitis", "durum", "satir", "mesaj")} for r in rows]


def table_counts() -> dict:
    ready()
    out = {}
    with session_scope() as s:
        for t in M.Base.metadata.sorted_tables:
            out[t.name] = s.scalar(select(func.count()).select_from(t))
    return out
