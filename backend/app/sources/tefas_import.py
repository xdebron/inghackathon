"""TEFAS 'Tarihsel Veriler' dışa aktarımlarını (Excel/CSV) içeri alır ve fon metriklerini hesaplar.

TEFAS script erişimini engellediği için veri, sitenin kendi 'Excel'e aktar' çıktısından alınır.
Her içe aktarım dosya adı ve zamanıyla kayıt altına alınır; aynı fon/tarih için farklı fiyat gelirse çelişki işaretlenir.
"""
from __future__ import annotations

import io
import json
import math
import re

import pandas as pd

from ..config import SNAPSHOT_DIR, decision_config, norm_text, now_iso
from ..models import DataPoint

SOURCE = "TEFAS Tarihsel Veriler (manuel dışa aktarım)"
URL = "https://www.tefas.gov.tr/TarihselVeriler.aspx"
PRICES = SNAPSHOT_DIR / "tefas_fiyatlar.csv"
META = SNAPSHOT_DIR / "tefas_meta.json"

_COLUMNS = {
    "tarih": "tarih",
    "fonkodu": "kod",
    "fonadi": "ad",
    "fonunvani": "ad",
    "fiyat": "fiyat",
    "tedavuldekipaysayisi": "pay",
    "kisisayisi": "kisi",
    "yatirimcisayisi": "kisi",
    "fontoplamdeger": "buyukluk",
    "portfoybuyuklugu": "buyukluk",
}

_TR = str.maketrans("çğıöşüâ", "cgiosua")


def _key(col: str) -> str:
    return re.sub(r"[^a-z]", "", norm_text(str(col)).translate(_TR))


def _num(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_export(content: bytes, filename: str) -> pd.DataFrame:
    if filename.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(content))
    else:
        text = content.decode("utf-8-sig", errors="replace")
        sep = ";" if text.count(";") > text.count(",") else ","
        df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str)
    rename = {c: _COLUMNS[_key(c)] for c in df.columns if _key(c) in _COLUMNS}
    df = df.rename(columns=rename)
    missing = {"tarih", "kod", "fiyat"} - set(df.columns)
    if missing:
        raise ValueError(f"Beklenen sütunlar bulunamadı: {sorted(missing)}. Bulunan: {list(df.columns)}")
    for col in ("ad", "pay", "kisi", "buyukluk"):
        if col not in df.columns:
            df[col] = None
    df["tarih"] = _parse_dates(df["tarih"])  # açık biçimler: yyyy-aa-gg veya gg.aa.yyyy (tahmin yok)
    for col in ("fiyat", "pay", "kisi", "buyukluk"):
        df[col] = df[col].map(_num)
    df["kod"] = df["kod"].astype(str).str.strip().str.upper()
    df = df.dropna(subset=["tarih", "fiyat"])
    return df[["tarih", "kod", "ad", "fiyat", "pay", "kisi", "buyukluk"]]


def _load_meta() -> dict:
    if META.exists():
        return json.loads(META.read_text(encoding="utf-8"))
    return {"imports": [], "conflicts": []}


LOCK = SNAPSHOT_DIR / ".tefas_import.lock"
BACKUP_DIR = SNAPSHOT_DIR / "_yedek_tefas"
KEEP_BACKUPS = 5


class _ImportLock:
    """Süreçler arası kilit: aynı anda iki içe aktarımın birbirinin sonucunu ezmesini engeller."""

    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout

    def __enter__(self):
        import os
        import time
        start = time.monotonic()
        while True:
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()} {now_iso()}".encode())
                return self
            except FileExistsError:
                if time.monotonic() - start > self.timeout:
                    raise RuntimeError(f"TEFAS içe aktarım kilidi {self.timeout:.0f} sn içinde alınamadı ({LOCK}). "
                                       "Başka bir içe aktarım sürüyor olabilir; takılı kaldıysa kilit dosyasını silin.")
                time.sleep(0.5)

    def __exit__(self, *exc):
        import os
        os.close(self.fd)
        LOCK.unlink(missing_ok=True)


def _backup_and_write(df: pd.DataFrame, meta: dict) -> str | None:
    """Önce mevcut dosyaların yedeğini alır, sonra atomik yazar (yarım yazılmış dosya okunamaz)."""
    import os
    import shutil
    backup = None
    if PRICES.exists():
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = now_iso().replace(":", "").replace("+", "_")
        backup = BACKUP_DIR / f"tefas_fiyatlar_{stamp}.csv"
        shutil.copy2(PRICES, backup)
        if META.exists():
            shutil.copy2(META, BACKUP_DIR / f"tefas_meta_{stamp}.json")
        for old in sorted(BACKUP_DIR.glob("tefas_fiyatlar_*.csv"))[:-KEEP_BACKUPS]:
            old.unlink()
            old.with_name(old.name.replace("tefas_fiyatlar_", "tefas_meta_").replace(".csv", ".json")).unlink(missing_ok=True)
    tmp = PRICES.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, PRICES)
    tmp_meta = META.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_meta, META)
    return str(backup) if backup else None


def _parse_dates(s: pd.Series) -> pd.Series:
    """Saklanan dosyadaki tarihler: ISO (yyyy-aa-gg) veya TEFAS biçimi (gg.aa.yyyy). Biçim açıkça denenir; tahmin yok."""
    s = s.astype(str).str.strip()
    iso = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    tr = pd.to_datetime(s, format="%d.%m.%Y", errors="coerce")
    return iso.fillna(tr).dt.date


def import_export(content: bytes, filename: str, yontem: str | None = None) -> dict:
    """yontem: verinin nasıl toplandığı (örn. 'TEFAS sitesinden elle dışa aktarım'); meta kaydına yazılır."""
    new = parse_export(content, filename)
    with _ImportLock():
        return _import_locked(new, filename, yontem)


def _import_locked(new: pd.DataFrame, filename: str, yontem: str | None = None) -> dict:
    meta = _load_meta()
    conflicts = []
    if PRICES.exists():
        old = pd.read_csv(PRICES)
        old["tarih"] = _parse_dates(old["tarih"])
        merged = old.merge(new[["tarih", "kod", "fiyat"]], on=["tarih", "kod"], suffixes=("", "_yeni"))
        diff = merged[(merged["fiyat"] - merged["fiyat_yeni"]).abs() > 1e-9]
        for _, r in diff.iterrows():
            conflicts.append({"kod": r["kod"], "tarih": str(r["tarih"]), "eski": r["fiyat"], "yeni": r["fiyat_yeni"],
                              "dosya": filename})
        combined = pd.concat([old, new]).drop_duplicates(subset=["tarih", "kod"], keep="last")
        # Güvenlik: birleştirme mevcut (fon, gün) kayıtlarını asla azaltmamalı
        old_keys = len(old.drop_duplicates(subset=["tarih", "kod"]))
        if len(combined) < old_keys or old["tarih"].isna().any():
            raise ValueError(f"İçe aktarım iptal edildi: mevcut {old_keys} kayıt birleştirme sonrası {len(combined)} olurdu "
                             f"veya mevcut dosyada okunamayan tarih var. Dosyalara dokunulmadı.")
    else:
        combined = new
    entry = {
        "dosya": filename,
        "yontem": yontem,
        "iceri_alma_zamani": now_iso(),
        "satir": int(len(new)),
        "fon_sayisi": int(new["kod"].nunique()),
        "tarih_araligi": [str(new["tarih"].min()), str(new["tarih"].max())],
        "toplam_sonrasi": {"satir": int(len(combined)), "fon": int(combined["kod"].nunique()),
                           "tarih_araligi": [str(combined["tarih"].min()), str(combined["tarih"].max())]},
    }
    meta["imports"].append(entry)
    meta["conflicts"].extend(conflicts)
    entry["yedek"] = _backup_and_write(combined.sort_values(["kod", "tarih"]), meta)
    return {**entry, "celiskiler": conflicts}


# --- Fon Karşılaştırma (getiri bazlı) dışa aktarımı ---------------------------------------------

COMPARISON = SNAPSHOT_DIR / "tefas_karsilastirma.csv"
COMPARISON_SOURCE = "TEFAS Fon Karşılaştırma (manuel dışa aktarım)"
COMPARISON_URL = "https://www.tefas.gov.tr/tr/fon-getirileri"
_RETURN_COLS = {"1ay": "getiri_1a", "3ay": "getiri_3a", "6ay": "getiri_6a", "yilbasi": "getiri_yb", "yb": "getiri_yb",
                "1yil": "getiri_1y", "3yil": "getiri_3y", "5yil": "getiri_5y"}


def _key_digits(col: str) -> str:
    return re.sub(r"[^a-z0-9]", "", norm_text(str(col)).translate(_TR))


def _pct(x):
    """TEFAS karşılaştırma sütunları yüzde birimindedir (46,52 -> 0.4652)."""
    if isinstance(x, str):
        x = x.replace("%", "").strip()
    v = _num(x)
    return None if v is None else v / 100


def _read_table(content: bytes, filename: str) -> pd.DataFrame:
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(content))
    text = content.decode("utf-8-sig", errors="replace")
    sep = ";" if text.count(";") > text.count(",") else ","
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str)


def detect_kind(content: bytes, filename: str) -> str:
    cols = {_key(c) for c in _read_table(content, filename).columns}
    if "fiyat" in cols:
        return "tarihsel"
    digit_cols = {_key_digits(c) for c in _read_table(content, filename).columns}
    if any(any(k in c for k in _RETURN_COLS) for c in digit_cols):
        return "karsilastirma"
    raise ValueError("Dosya türü tanınmadı: ne 'Fiyat' sütunu (Tarihsel Veriler) ne de dönemsel getiri sütunları (Fon Karşılaştırma) var.")


def parse_comparison(content: bytes, filename: str) -> pd.DataFrame:
    df = _read_table(content, filename)
    rename = {}
    for c in df.columns:
        k, kd = _key(c), _key_digits(c)
        if k == "fonkodu":
            rename[c] = "kod"
        elif k in ("fonadi", "fonunvani"):
            rename[c] = "ad"
        elif "riskdegeri" in k:
            rename[c] = "resmi_rd_tefas"
        elif "fonturu" in k or "semsiye" in k:
            rename[c] = "tur"
        else:
            for pat, name in _RETURN_COLS.items():
                if kd.startswith(pat) or kd == pat or kd.replace("getiri", "").startswith(pat):
                    rename[c] = name
                    break
    df = df.rename(columns=rename)
    if "kod" not in df.columns:
        raise ValueError(f"'Fon Kodu' sütunu bulunamadı. Bulunan: {list(df.columns)}")
    keep = ["kod"] + [c for c in ("ad", "tur", "resmi_rd_tefas") if c in df.columns] + [c for c in set(_RETURN_COLS.values()) if c in df.columns]
    df = df[keep].copy()
    for c in set(_RETURN_COLS.values()) & set(df.columns):
        df[c] = df[c].map(_pct)
    df["kod"] = df["kod"].astype(str).str.strip().str.upper()
    return df.dropna(subset=["kod"])


def import_comparison(content: bytes, filename: str, as_of: str | None) -> dict:
    df = parse_comparison(content, filename)
    df["as_of"] = as_of
    with _ImportLock():  # meta dosyasını fiyat içe aktarımıyla aynı anda yazmamak için
        df.to_csv(COMPARISON, index=False)
        meta = _load_meta()
        entry = {"dosya": filename, "iceri_alma_zamani": now_iso(), "fon_sayisi": int(len(df)), "as_of": as_of,
                 "sutunlar": [c for c in df.columns if c.startswith("getiri_")]}
        meta.setdefault("karsilastirma_imports", []).append(entry)
        META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**entry, "satir": entry["fon_sayisi"], "celiskiler": []}


def import_any(content: bytes, filename: str, as_of: str | None = None) -> dict:
    kind = detect_kind(content, filename)
    if kind == "tarihsel":
        return {"tur": kind, **import_export(content, filename)}
    return {"tur": kind, **import_comparison(content, filename, as_of)}


def load_comparison() -> tuple[dict[str, dict], dict | None]:
    if not COMPARISON.exists():
        return {}, None
    df = pd.read_csv(COMPARISON)
    df = df.astype(object).where(pd.notna(df), None)
    meta = _load_meta().get("karsilastirma_imports", [])
    return {r["kod"]: r for r in df.to_dict("records")}, (meta[-1] if meta else None)


def _price_at_or_before(s: pd.Series, target) -> float | None:
    sub = s[s.index <= target]
    return None if sub.empty else float(sub.iloc[-1])


def compute_metrics(prices: pd.DataFrame) -> dict[str, dict]:
    """Fon bazında getiri, volatilite, max düşüş ve kalite kontrolleri."""
    q = decision_config()["veri_kalitesi"]
    out: dict[str, dict] = {}
    prices = prices.copy()
    prices["tarih"] = pd.to_datetime(prices["tarih"])
    for kod, g in prices.groupby("kod"):
        g = g.sort_values("tarih")
        s = g.set_index("tarih")["fiyat"].astype(float)
        last_date = s.index[-1]
        win_start = last_date - pd.Timedelta(days=365)
        issues = []
        # TEFAS, fonun işlem görmediği / yeni kurulduğu günlerde 0 fiyat yazabiliyor: eksik gün sayılır.
        # Kalite uyarısı yalnızca metriklerin hesaplandığı son 1 yıl penceresi için verilir.
        zeros = s[s <= 0]
        zeros_in_win = int((zeros.index >= win_start).sum())
        if zeros_in_win:
            issues.append(f"Son 1 yılda {zeros_in_win} günde 0 fiyat (eksik gün olarak atlandı)")
        s = s[s > 0]
        if s.empty:
            continue
        last_date = s.index[-1]
        rets = s.pct_change().dropna()
        rets = rets[rets.index >= win_start]
        jumps = rets[rets.abs() > q["max_gunluk_sicrama"]]
        if not jumps.empty:
            issues.append(f"{len(jumps)} günde %{q['max_gunluk_sicrama']*100:.0f} üzeri günlük fiyat sıçraması "
                          f"(ilk: {jumps.index[0].date()})")
        m = {
            "ad": next((a for a in reversed(g["ad"].tolist()) if isinstance(a, str) and a), kod),
            "gozlem": int(len(s)),
            "ilk_tarih": str(s.index[0].date()),
            "son_tarih": str(last_date.date()),
            "son_fiyat": float(s.iloc[-1]),
            "buyukluk": _num(g["buyukluk"].iloc[-1]),
            "kisi": _num(g["kisi"].iloc[-1]),
            "kalite_sorunlari": issues,
            "sifir_fiyat_gun_toplam": int(len(zeros)),
        }
        for label, days in (("getiri_1a", 30), ("getiri_3a", 91), ("getiri_6a", 182), ("getiri_1y", 365)):
            target = last_date - pd.Timedelta(days=days)
            if s.index[0] > target + pd.Timedelta(days=5):
                m[label] = None  # yeterli geçmiş yok
            else:
                p0 = _price_at_or_before(s, target)
                m[label] = None if not p0 else float(s.iloc[-1] / p0 - 1)
        window = s[s.index >= last_date - pd.Timedelta(days=365)]
        lr = (window.apply(math.log)).diff().dropna()
        m["volatilite_yillik"] = float(lr.std() * math.sqrt(252)) if len(lr) >= q["min_gozlem"] else None
        peak = window.cummax()
        m["max_dusus"] = float((window / peak - 1).min()) if len(window) > 1 else None
        out[kod] = m
    return out


_CACHE: dict = {}


def _mtime(p) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


def load() -> tuple[dict[str, dict], dict]:
    """Kayıtlı fiyat geçmişinden metrikleri ve içe aktarım metasını döndürür (dosya değişmedikçe önbellekten)."""
    meta = _load_meta()
    if not PRICES.exists():
        return {}, meta
    key = ("metrics", _mtime(PRICES))
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = compute_metrics(pd.read_csv(PRICES))
    return _CACHE[key], meta


def price_matrix(codes: list[str]) -> pd.DataFrame | None:
    """Verilen fonların günlük fiyatları (satır: tarih, sütun: fon kodu). Dosya değişmedikçe önbellekten."""
    if not PRICES.exists():
        return None
    key = ("prices", _mtime(PRICES))
    if key not in _PRICE_CACHE:
        _PRICE_CACHE.clear()
        df = pd.read_csv(PRICES, usecols=["tarih", "kod", "fiyat"], parse_dates=["tarih"])
        _PRICE_CACHE[key] = df.pivot_table(index="tarih", columns="kod", values="fiyat", aggfunc="last").sort_index()
    wide = _PRICE_CACHE[key]
    present = [c for c in codes if c in wide.columns]
    return wide[present].copy() if present else None


_PRICE_CACHE: dict = {}


def price_names() -> dict[str, str]:
    """Fon kodu → son bilinen fon adı."""
    if not PRICES.exists():
        return {}
    key = ("names", _mtime(PRICES))
    if key not in _PRICE_CACHE:
        df = pd.read_csv(PRICES, usecols=["tarih", "kod", "ad"])
        _PRICE_CACHE[key] = df.dropna(subset=["ad"]).sort_values("tarih").groupby("kod")["ad"].last().to_dict()
    return _PRICE_CACHE[key]


# --- İkincil kaynak: TEFAS API fiyatları (yalnızca çapraz kontrol) ----------------------------

API_PRICES = SNAPSHOT_DIR / "tefas_api_fiyatlar.csv"
API_META = SNAPSHOT_DIR / "tefas_api_meta.json"
API_TOL = 1e-4  # göreli fiyat farkı eşiği (kaynaklar 6 ondalıklı fiyat verir)


def api_cross_check() -> tuple[dict[str, dict], dict | None]:
    """Asıl kaynak (elle dışa aktarım) ile ikincil kaynak (API) fiyatlarını ortak günlerde karşılaştırır."""
    if not (PRICES.exists() and API_PRICES.exists()):
        return {}, None
    meta = json.loads(API_META.read_text(encoding="utf-8")) if API_META.exists() else None
    key = ("api", _mtime(PRICES), _mtime(API_PRICES))
    if key not in _API_CACHE:
        _API_CACHE.clear()
        a = pd.read_csv(PRICES, usecols=["tarih", "kod", "fiyat"])
        b = pd.read_csv(API_PRICES, usecols=["tarih", "kod", "fiyat"])
        m = a.merge(b, on=["kod", "tarih"], suffixes=("", "_api"))
        m["fark"] = (m["fiyat"] - m["fiyat_api"]).abs() / m["fiyat_api"].abs()
        out = {}
        for kod, g in m.groupby("kod"):
            bad = g[g["fark"] > API_TOL]
            out[kod] = {"karsilastirilan_gun": int(len(g)), "uyumsuz_gun": int(len(bad)),
                        "max_goreli_fark": float(g["fark"].max()),
                        "ornek": None if bad.empty else {"tarih": str(bad.iloc[0]["tarih"]), "dis_aktarim": float(bad.iloc[0]["fiyat"]),
                                                         "api": float(bad.iloc[0]["fiyat_api"])}}
        _API_CACHE[key] = out
    return _API_CACHE[key], meta


_API_CACHE: dict = {}


def provenance(meta: dict, as_of: str | None) -> dict:
    last = meta["imports"][-1] if meta.get("imports") else {}
    return {
        "source": SOURCE,
        "url": URL,
        "as_of": as_of,
        "fetched_at": last.get("iceri_alma_zamani"),
    }


def metric_point(kod: str, field: str, value, meta: dict, as_of: str, unit: str | None = None,
                 note: str | None = None) -> DataPoint:
    return DataPoint(key=f"fon.{kod}.{field}", value=value, unit=unit,
                     status="ok" if value is not None else "missing", note=note, **provenance(meta, as_of))
