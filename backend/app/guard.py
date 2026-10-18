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
    for m in PCT.findall(text):
        token = m.replace(" ", "").replace(".", ",")
        if token not in allowed_numbers:
            violations.append(f"Kaynağı olmayan oran: {m}")
    if cfg["zorunlu_ibare"] not in text:
        violations.append("Zorunlu 'yatırım tavsiyesi değildir / RM onayı' ibaresi eksik")
    return violations
