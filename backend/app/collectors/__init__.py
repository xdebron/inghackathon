"""Otomatik veri toplayıcılar. Her biri kaynaktan çeker ve veri tabanına kaynak bilgisiyle yazar; her çalıştırma bir
'cekim' kaydı bırakır (kaynak, zaman, durum, satır, hata).

Sıra önemlidir: türetilmiş seriler EVDS'ye, KAP fon listesi TEFAS fon adlarına dayanır.
TEFAS fiyatları burada yoktur: site bot korumalıdır ve uygulama TEFAS'a istek atmaz; veri, dışa aktarım dosyası arayüzden
yüklenerek gelir (yöntem 'yukleme').
"""
from __future__ import annotations

from importlib import import_module

COLLECTORS: dict[str, str] = {
    "evds_seriler": "TCMB EVDS: kur, altın, BIST 100, faizler, mevduat, TÜFE serileri",
    "tcmb_politika": "TCMB politika faizi sayfası: güncel oran ve karar tarihçesi",
    "hazine_dibs": "Hazine ve Maliye Bakanlığı DİBS ihale sonuçları (PDF)",
    "bist": "BIST 30 hisseleri ve endeksler (Yahoo Finance; resmi kaynak değil)",
    "turetilmis": "Türetilmiş seriler ve BIST 100 benchmark (hesaplama, istek atmaz)",
    "ing_mevduat": "ING mevduat hesaplayıcısı: TL ve döviz oranları, mevduat stopajı",
    "ing_stopaj": "ING fon vergilendirme tablosu: güncel ve dönemsel stopaj",
    "ing_fonlar": "ING platformundaki fon listesi",
    "kap_fon": "KAP fon bilgileri: resmi RD, ücretler, valör, nitelikli yatırımcı (yavaş, kademeli)",
}


def run(name: str, log=print, **kw) -> dict:
    if name not in COLLECTORS:
        raise KeyError(f"Bilinmeyen toplayıcı: {name}. Seçenekler: {', '.join(COLLECTORS)}")
    return import_module(f".{name}", __name__).run(log=log, **kw)
