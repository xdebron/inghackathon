"""Üretilen metin için güvenlik kontrolleri (LLM'den bağımsız, deterministik)."""
from __future__ import annotations

import re

from .config import decision_config, norm_text

PCT = re.compile(r"%\s?\d+(?:[.,]\d+)?")


def check(text: str, excluded_codes: list[str], allowed_numbers: set[str],
          allowed_names: list[str] | None = None) -> list[str]:
    """İhlal listesi döner; boş liste = metin güvenli.

    allowed_names: metinde geçmesi meşru olan özel adlar (kısa listedeki ürün adları). Yasaklı ifade taraması
    bunlar çıkarıldıktan sonra yapılır; örn. 'GARANTİ PORTFÖY ...' bir şirket adıdır, getiri garantisi değildir.
    """
    cfg = decision_config()["metin_guard"]
    violations = []
    n = norm_text(text)
    for name in sorted(allowed_names or [], key=len, reverse=True):
        n = n.replace(norm_text(name), " ")
    for phrase in cfg["yasakli_ifadeler"]:
        if norm_text(phrase) in n:
            violations.append(f"Yasaklı ifade: '{phrase}'")
    raw = text                                    # fon kodu taraması da meşru adlar çıkarılmış metinde (örn. '... ESG ... FONU' adı)
    for name in sorted(allowed_names or [], key=len, reverse=True):
        raw = raw.replace(name, " ")
    skip = set(cfg.get("kod_istisnalari", []))  # fon koduyla çakışan yaygın kısaltmalar (örn. 'PDF')
    for code in excluded_codes:
        if code in skip:
            continue
        if re.search(rf"\b{re.escape(code)}\b", raw):
            violations.append(f"Dışlanan ürün metinde geçiyor: {code}")
    allowed_numbers = {a.lstrip("−-") for a in allowed_numbers}
    for m in PCT.findall(text):
        token = m.replace(" ", "").replace(".", ",")
        if token not in allowed_numbers:
            violations.append(f"Kaynağı olmayan oran: {m}")
    if cfg["zorunlu_ibare"] not in text:
        violations.append("Zorunlu 'yatırım tavsiyesi değildir / RM onayı' ibaresi eksik")
    return violations


def structure(text: str) -> list[str]:
    """Hazırlık notunun biçim kontrolü: başlıkla başlar, yedi bölümün hepsi sırayla var, son bölüm dolu ve metin yarıda
    kesilmemiş (son içerik satırı tamamlanmış bir cümle/madde). Ajanın kendi ara cümleleri ("Notu yazıyorum") başta
    kalmamalı."""
    from .note import NOTE_SECTIONS, NOTE_TITLE
    v = []
    required = decision_config()["metin_guard"]["zorunlu_ibare"]
    lines = [l.rstrip() for l in text.strip().splitlines()]
    if not lines or not lines[0].lstrip("# *").startswith(NOTE_TITLE):
        v.append(f"Not '{NOTE_TITLE}' başlığıyla başlamıyor (başta ajan açıklaması ya da başka metin var)")
    pos = []
    for sec in NOTE_SECTIONS:
        num = sec.split(")")[0]
        i = next((k for k, l in enumerate(lines) if re.match(rf"^[#*\s]*{num}\)", l)), None)
        if i is None:
            v.append(f"Bölüm eksik: '{sec}'")
        pos.append(i)
    found = [p for p in pos if p is not None]
    if found != sorted(found):
        v.append("Bölümler sırasız")
    body = [l for l in lines if l.strip() and required not in l]
    if pos[-1] is not None:
        extra = [l for l in lines[pos[-1] + 1:] if l.strip() and required not in l and not re.match(r"^\s*([-•*]|\d+[.)])\s", l)]
        if extra:
            v.append(f"7. bölümden (sorular) sonra soru dışında metin var: '{extra[0][:60]}'")
    if pos[-1] is not None and (not body or body[-1] == lines[pos[-1]]):
        v.append("Son bölüm (müşteriye sorulabilecek sorular) boş")
    if body:
        last = body[-1].strip()
        if last.count("(") != last.count(")") or not re.search(r"[.?!:)%\d]$", last):
            v.append(f"Metin yarıda kesilmiş görünüyor (son satır: '{last[-60:]}')")
    return v
