"""fonGnlBlgSiraliGetir ve dagilimSiraliGetirT ortak sayfalama sözleşmesi:
`basSira`/`bitSira` ile 1-tabanlı satır aralığı istenir, yanıt `toplamSayi` ile
toplam satır sayısını döner."""
from __future__ import annotations

from typing import Callable, Optional


def paginate(fetch_page: Callable[[int, int], dict], page_size: int = 2000) -> list[dict]:
    rows: list[dict] = []
    bas = 1
    total: Optional[int] = None
    while total is None or bas <= total:
        bit = bas + page_size - 1
        data = fetch_page(bas, bit)
        page_rows = data.get("resultList") or []
        rows.extend(page_rows)
        total = data.get("toplamSayi")
        if not page_rows or total is None:
            break
        bas += page_size
    return rows
