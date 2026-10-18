"""KAP "Fon Bilgileri" sayfaları: resmi risk değeri, yönetim ücreti, giriş/çıkış komisyonu, ISIN, şemsiye türü,
karşılaştırma ölçütü, alım-satım esasları (valör) ve nitelikli yatırımcı ibaresi.

Sayfa: https://www.kap.org.tr/tr/fon-bilgileri/genel/<kod>-<fon-adı-slug>. Veri Next.js yükünde 'fundDetail' dizisidir.
Daha önce doğrulanmış sayfa adresi varsa o kullanılır; yeni fonlar için adres TEFAS'taki fon adından türetilir.
Kibar çalışır: sıralı, istekler arası bekleme, 429'da geri çekilme, art arda hatada durma; en eski kayıtlar önce yenilenir
ve tek çalıştırmada en fazla `limit` fon istenir (kalanlar sonraki çalıştırmada). Sayfada olmayan alan boş kalır.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..db import models as M, store
from ..db.session import session_scope
from .base import Polite, Stop

BASE = "https://www.kap.org.tr/tr/fon-bilgileri/genel/"
SOURCE = "KAP Fon Bilgileri sayfası"
_TR = str.maketrans({"İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g", "Ü": "u", "ü": "u",
                     "Ö": "o", "ö": "o", "Ç": "c", "ç": "c"})
_ORD = {"birinci": 1, "ilk": 1, "ikinci": 2, "üçüncü": 3, "dördüncü": 4, "beşinci": 5}
QUALIFIED = re.compile(r"[^.]{0,160}nitelikli yatırımcı[^.]{0,200}\.", re.I)
_RD_SENTENCE = re.compile(r"risk\s+de[ğg]eri\w*\s*[:=\-]?\s*(?:olarak\s*)?([1-7])\b", re.I)
# Tek rakam 1-7: ondalık parçası (2,5 / 1.5), yüzde ve tarih değil. Virgülden sonra boşluk gelen ayraç ("TL 6, USD 3") rakamı dışlamaz.
_RD_DIGIT = re.compile(r"(?<![\d%/])(?<!\d[.,])([1-7])(?![\d%/]|[.,]\d)(?!\s*(?:y[ıi]l|gün|gun|ay\b|hafta|%))", re.I)


def slug(code: str, name: str) -> str:
    return f"{code.lower()}-" + re.sub(r"[^a-z0-9]+", "-", name.translate(_TR).lower()).strip("-")


def fund_detail(html: str) -> list[dict] | None:
    fl = "".join(json.loads(m.group(1)) for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html))
    i = fl.find('"fundDetail":[')
    if i < 0:
        return None
    arr, _ = json.JSONDecoder().raw_decode(fl[i + len('"fundDetail":'):])
    return arr


def _first(v):
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, dict) else None)


def _num(s):
    try:
        return float(str(s).replace(".", "").replace(",", ".")) if s not in (None, "") else None
    except ValueError:
        return None


def valor_from_text(text: str | None, kind: str) -> tuple[int | None, str | None]:
    """Valör (iş günü): yalnızca açık ifade varsa ('izleyen ikinci iş günü', 'T+2', 'ertesi iş günü', 'aynı gün')."""
    if not text:
        return None, None
    key = r"al[ıi]m" if kind == "buy" else r"sat[ıi]m"
    for sent in re.split(r"(?<!\.)\.(?!\.)\s+|[!?]\s+|\n+", text):
        s = sent.lower()
        if not re.search(key, s):
            continue
        if m := re.search(r"izleyen\s+(birinci|ilk|ikinci|üçüncü|dördüncü|beşinci)?\s*[iı]?ş\s*günü", s):
            return _ORD.get(m.group(1), 1), sent.strip()[:300]
        if m := re.search(r"\bt\s*\+\s*(\d)\b", s):
            return int(m.group(1)), sent.strip()[:300]
        if "ertesi iş günü" in s or "ertesi gün" in s:
            return 1, sent.strip()[:300]
        if re.search(r"aynı (iş )?gün", s):
            return 0, sent.strip()[:300]
    return None, None


def risk_info(v) -> tuple[int | None, list[int]]:
    """Düz rakam, cümle ya da pay sınıfına göre farklı değerler: birden çok değer varsa en yüksek (tutucu) alınır."""
    s = str(v or "").strip()
    if re.fullmatch(r"[1-7]", s):
        return int(s), [int(s)]
    if not s or s.startswith("$"):
        return None, []
    cands = {int(x) for x in _RD_SENTENCE.findall(s)} or {int(x) for x in _RD_DIGIT.findall(s)}
    return (max(cands), sorted(cands)) if cands else (None, [])


def to_record(detail: list[dict], url: str, fetched: str, prev: M.FonBilgi | None) -> dict:
    d = {it["itemKey"]: it["value"] for it in detail}
    fee = _first(d.get("kpy81_acc2_fon_ucret_kom_info")) or {}
    strat = _first(d.get("kpy81_acc1_amac_strateji")) or {}
    trade = _first(d.get("kpy81_acc1_temel_alim_satim_info")) or {}
    bench = d.get("kpy81_acc1_fon_karsilastirma")
    rd, cands = risk_info(strat.get("riskDegeri") or d.get("kpy81_acc1_fonun_risk_degeri"))
    q = QUALIFIED.search(json.dumps(detail, ensure_ascii=False))
    sell, evidence = valor_from_text(trade.get("alimSatimSaatleri"), "sell")
    pct = lambda x: None if _num(x) is None else _num(x) / 100  # noqa: E731
    rec = {
        "resmi_rd": rd, "rd_belirsiz": f"KAP'ta birden fazla RD adayı {cands}; muhafazakâr olan alındı" if len(cands) > 1 else None,
        "yonetim_ucreti_yillik": pct(fee.get("uygulananYonetimUcretiOranYillikYuzde")),
        "giris_komisyonu": pct(fee.get("girisKomisyonu")), "cikis_komisyonu": pct(fee.get("cikisKomisyonu")),
        "semsiye_turu": d.get("kpy81_acc1_fon_sem_tur"), "isin": d.get("kpy81_acc1_ISIN"),
        "karsilastirma_olcutu": [b.get("karsilastirmaOlcutu") for b in bench if isinstance(b, dict)] if isinstance(bench, list) else None,
        "nitelikli_yatirimci": bool(q), "nitelikli_notu": None if q else "KAP metninde 'nitelikli yatırımcı' ibaresi bulunamadı",
        "valor_satis_gun": sell, "valor_kaynak": ({"source": "KAP alım-satım esasları", "url": url, "as_of": fetched[:10],
                                                   "fetched_at": fetched, "kanit": evidence} if sell is not None else None),
        "yontem": "KAP fon sayfasından otomatik toplayıcı (app/collectors/kap_fon.py)", "toplayan": None,
        "kaynak": SOURCE, "url": url, "as_of": fetched[:10], "fetched_at": fetched, "notlar": None,
    }
    if sell is None and prev is not None and prev.valor_satis_gun is not None:   # KAP'ta yoksa önceki ölçümü kaynağıyla koru
        rec["valor_satis_gun"], rec["valor_kaynak"] = prev.valor_satis_gun, prev.valor_kaynak
    return rec


def run(limit: int = 300, max_age_days: int = 7, pause: float = 2.5, codes: list[str] | None = None, log=print) -> dict:
    from ..sources import tefas_import
    names = tefas_import.price_names()
    with session_scope() as s:
        prev = {r.kod: r for r in s.scalars(select(M.FonBilgi))}
    universe = sorted(codes or (set(names) | set(prev)))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    oldest = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def fetched(c):   # saat dilimi farklı zaman damgaları metin olarak değil, zaman olarak karşılaştırılır
        try:
            t = datetime.fromisoformat(prev[c].fetched_at) if c in prev and prev[c].fetched_at else oldest
        except ValueError:
            return oldest
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    todo = sorted(universe, key=fetched)                                         # hiç çekilmemiş ve en eski önce
    todo = [c for c in todo if fetched(c) <= cutoff][:limit]
    cli = Polite(pause=pause)
    ok, missing, stopped = 0, [], None
    with store.Run("kap_fon") as run_:
        try:
            for n, code in enumerate(todo, 1):
                p = prev.get(code)
                url = p.url if p is not None and p.url and p.url.startswith(BASE) else (BASE + slug(code, names[code]) if code in names else None)
                if url is None:
                    missing.append(f"{code}: ad yok")
                    continue
                r = cli.get(url)
                detail = fund_detail(r.text) if r is not None else None
                if not detail:
                    missing.append(f"{code}: sayfa/fundDetail yok")
                    continue
                store.upsert_rows(M.FonBilgi, [{"kod": code, **to_record(detail, str(r.url), store.now_iso(), p)}], run_)
                ok += 1
                if n % 25 == 0:
                    log(f"  {n}/{len(todo)}")
        except Stop as exc:
            stopped = str(exc)
        run_.durum = "kismi" if (missing or stopped) else "basarili"
        run_.mesaj = "; ".join(x for x in (stopped, f"{len(missing)} fon alınamadı" if missing else None) if x) or None
        log(f"  {ok}/{len(todo)} fon güncellendi (evren {len(universe)}, eşik {max_age_days} gün)"
            + (f"; {len(missing)} alınamadı" if missing else "") + (f"; DURDU: {stopped}" if stopped else ""))
    cli.close()
    return {"guncellenen": ok, "istenen": len(todo), "alinamayan": missing[:20], "durdu": stopped}
