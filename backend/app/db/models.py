"""Şema. Her veri satırı kaynağını taşır (brief kural 2): kaynak adı, URL, kaynaktaki veri tarihi (as_of), alınma zamanı
(fetched_at) ve satırı yazan toplama çalışması (cekim_id). Değer uydurulmaz: kaynakta olmayan alan boş (NULL) kalır.

Tablolar
  cekim          her toplama / içe aktarma çalışması: kaynak, zaman, durum, satır sayısı, hata
  gosterge       anlık göstergeler (politika faizi, TÜFE, sektör mevduat ortalamaları, DİBS, TMSF limiti...)
  seri           zaman serisi tanımı (EVDS, Hazine, Yahoo...) + seri_deger (tarih, kolon, değer)
  mevduat        banka mevduat / katılım ürünleri ve oranları (TL ve döviz)
  stopaj         güncel stopaj oranları + stopaj_donem (dönemlere göre tarihçe)
  fon_bilgi      KAP fon bilgileri (resmi RD, ücretler, valör, nitelikli yatırımcı...)
  fon_fiyat      TEFAS günlük fiyat, pay, yatırımcı, büyüklük (kaynak: dışa aktarım ya da API)
  fon_getiri     TEFAS Fon Getirileri dışa aktarımı (dönemsel getiriler; ikinci kaynak)
  benchmark      karşılaştırma ölçütleri (BIST 100 ...)
  platform_fon   ING platformunda sunulan fonlar
  belge          kaynak düzeyinde yapılandırılmış bilgi (BIST 30 bileşimi, KAP konu kodları, kalite raporları...)
  musteri        RM'in eklediği müşteriler (form / JSON içe aktarım)
  ham_tablo      yalnızca çevrimdışı araçların kullandığı tablolar (KAP bildirim geçmişi, DİBS ihale listesi...)
"""
from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Cekim(Base):
    __tablename__ = "cekim"
    id: Mapped[int] = mapped_column(primary_key=True)
    kaynak: Mapped[str] = mapped_column(String(64), index=True)      # toplayıcı kimliği, ör. "kap_fon", "ing_mevduat"
    yontem: Mapped[str] = mapped_column(String(32))                   # otomatik | yukleme | aktarim
    baslangic: Mapped[str] = mapped_column(String(32))
    bitis: Mapped[str | None] = mapped_column(String(32))
    durum: Mapped[str] = mapped_column(String(16), default="calisiyor")  # calisiyor | basarili | kismi | hata
    satir: Mapped[int] = mapped_column(Integer, default=0)
    mesaj: Mapped[str | None] = mapped_column(Text)


class _Kaynakli:
    """Her veri satırında bulunan kaynak alanları."""
    kaynak: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[str | None] = mapped_column(String(40))
    notlar: Mapped[str | None] = mapped_column(Text)
    cekim_id: Mapped[int | None] = mapped_column(ForeignKey("cekim.id"))


class Gosterge(_Kaynakli, Base):
    __tablename__ = "gosterge"
    kod: Mapped[str] = mapped_column(String(64), primary_key=True)
    deger: Mapped[float | None] = mapped_column(Float)
    birim: Mapped[str | None] = mapped_column(String(32))


class Seri(Base):
    __tablename__ = "seri"
    kod: Mapped[str] = mapped_column(String(96), primary_key=True)   # ör. "usdtry", "bist/AKBNK"
    grup: Mapped[str] = mapped_column(String(32), index=True)       # gecmis | bist | byf
    meta: Mapped[dict] = mapped_column(JSON, default=dict)          # ad, kaynak, url, birim, sıklık, seri kodu, alınma zamanı...
    cekim_id: Mapped[int | None] = mapped_column(ForeignKey("cekim.id"))


class SeriDeger(Base):
    __tablename__ = "seri_deger"
    seri: Mapped[str] = mapped_column(ForeignKey("seri.kod", ondelete="CASCADE"), primary_key=True)
    kolon: Mapped[str] = mapped_column(String(48), primary_key=True)  # "deger" ya da kapanis / duzeltilmis_kapanis / hacim
    tarih: Mapped[str] = mapped_column(String(10), primary_key=True)
    deger: Mapped[float | None] = mapped_column(Float)


class Mevduat(_Kaynakli, Base):
    __tablename__ = "mevduat"
    __table_args__ = (UniqueConstraint("banka", "tur", "para_birimi", "vade_gun", "min_tutar"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    banka: Mapped[str] = mapped_column(String(64))
    tur: Mapped[str] = mapped_column(String(32))                     # mevduat | katilim
    para_birimi: Mapped[str] = mapped_column(String(8), default="TRY")
    vade_gun: Mapped[int] = mapped_column(Integer)
    oran: Mapped[float | None] = mapped_column(Float)                # yıllık brüt
    stopaj_orani: Mapped[float | None] = mapped_column(Float)        # kaynağın kendi stopaj hesabı (varsa)
    min_tutar: Mapped[float] = mapped_column(Float, default=0)


class Stopaj(_Kaynakli, Base):
    __tablename__ = "stopaj"
    kategori: Mapped[str] = mapped_column(String(48), primary_key=True)
    oran: Mapped[float | None] = mapped_column(Float)


class StopajDonem(_Kaynakli, Base):
    __tablename__ = "stopaj_donem"
    kategori: Mapped[str] = mapped_column(String(48), primary_key=True)
    donem: Mapped[str] = mapped_column(String(96), primary_key=True)
    baslangic: Mapped[str | None] = mapped_column(String(10))
    bitis: Mapped[str | None] = mapped_column(String(10))
    oran: Mapped[float | None] = mapped_column(Float)
    sira: Mapped[int] = mapped_column(Integer, default=0)


class FonBilgi(_Kaynakli, Base):
    __tablename__ = "fon_bilgi"
    kod: Mapped[str] = mapped_column(String(12), primary_key=True)
    resmi_rd: Mapped[int | None] = mapped_column(Integer)
    rd_belirsiz: Mapped[str | None] = mapped_column(Text)
    yonetim_ucreti_yillik: Mapped[float | None] = mapped_column(Float)
    giris_komisyonu: Mapped[float | None] = mapped_column(Float)
    cikis_komisyonu: Mapped[float | None] = mapped_column(Float)
    semsiye_turu: Mapped[str | None] = mapped_column(Text)
    isin: Mapped[str | None] = mapped_column(String(16))
    karsilastirma_olcutu: Mapped[list | str | None] = mapped_column(JSON)
    nitelikli_yatirimci: Mapped[bool | None] = mapped_column()
    nitelikli_notu: Mapped[str | None] = mapped_column(Text)
    valor_satis_gun: Mapped[int | None] = mapped_column(Integer)
    valor_kaynak: Mapped[dict | None] = mapped_column(JSON)          # valörün kendi kaynağı (KAP ya da TEFAS simülatörü ölçümü)
    uyari_notu: Mapped[str | None] = mapped_column(Text)
    yontem: Mapped[str | None] = mapped_column(Text)
    toplayan: Mapped[str | None] = mapped_column(String(64))


class FonFiyat(Base):
    __tablename__ = "fon_fiyat"
    kaynak: Mapped[str] = mapped_column(String(16), primary_key=True)  # tefas_export | tefas_api
    kod: Mapped[str] = mapped_column(String(12), primary_key=True)
    tarih: Mapped[str] = mapped_column(String(10), primary_key=True)
    ad: Mapped[str | None] = mapped_column(Text)
    fiyat: Mapped[float | None] = mapped_column(Float)
    pay: Mapped[float | None] = mapped_column(Float)
    kisi: Mapped[float | None] = mapped_column(Float)
    buyukluk: Mapped[float | None] = mapped_column(Float)
    cekim_id: Mapped[int | None] = mapped_column(ForeignKey("cekim.id"))


class FonGetiri(Base):
    __tablename__ = "fon_getiri"
    kod: Mapped[str] = mapped_column(String(12), primary_key=True)
    as_of: Mapped[str] = mapped_column(String(10), primary_key=True)
    ad: Mapped[str | None] = mapped_column(Text)
    tur: Mapped[str | None] = mapped_column(Text)
    resmi_rd_tefas: Mapped[float | None] = mapped_column(Float)
    getiri_1a: Mapped[float | None] = mapped_column(Float)
    getiri_3a: Mapped[float | None] = mapped_column(Float)
    getiri_6a: Mapped[float | None] = mapped_column(Float)
    getiri_yb: Mapped[float | None] = mapped_column(Float)
    getiri_1y: Mapped[float | None] = mapped_column(Float)
    getiri_3y: Mapped[float | None] = mapped_column(Float)
    getiri_5y: Mapped[float | None] = mapped_column(Float)
    cekim_id: Mapped[int | None] = mapped_column(ForeignKey("cekim.id"))


class Benchmark(_Kaynakli, Base):
    __tablename__ = "benchmark"
    ad: Mapped[str] = mapped_column(String(64), primary_key=True)
    kategoriler: Mapped[list] = mapped_column(JSON, default=list)
    getiri_1y: Mapped[float | None] = mapped_column(Float)


class PlatformFon(_Kaynakli, Base):
    __tablename__ = "platform_fon"
    platform: Mapped[str] = mapped_column(String(32), primary_key=True)  # ING
    kod: Mapped[str] = mapped_column(String(12), primary_key=True)


class Belge(Base):
    __tablename__ = "belge"
    ad: Mapped[str] = mapped_column(String(64), primary_key=True)
    icerik: Mapped[dict | list] = mapped_column(JSON)
    fetched_at: Mapped[str | None] = mapped_column(String(40))
    cekim_id: Mapped[int | None] = mapped_column(ForeignKey("cekim.id"))


class Musteri(Base):
    """RM'in eklediği müşteriler (form ya da JSON içe aktarım). Brief personaları (P01-P05) data/personas altında."""
    __tablename__ = "musteri"
    persona_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    veri: Mapped[dict] = mapped_column(JSON)
    yontem: Mapped[str] = mapped_column(String(16))          # form | json
    eklenme: Mapped[str] = mapped_column(String(40))


class HamTablo(Base):
    __tablename__ = "ham_tablo"
    tablo: Mapped[str] = mapped_column(String(64), primary_key=True)
    sira: Mapped[int] = mapped_column(Integer, primary_key=True)
    satir: Mapped[dict] = mapped_column(JSON)
    cekim_id: Mapped[int | None] = mapped_column(ForeignKey("cekim.id"))


Index("ix_fon_fiyat_kod_tarih", FonFiyat.kod, FonFiyat.tarih)
Index("ix_seri_deger_seri_tarih", SeriDeger.seri, SeriDeger.tarih)
