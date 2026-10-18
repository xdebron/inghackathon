# Gösterge Tarihçesi (2021 → bugün)

[backend/tools/fetch_history.py](../backend/tools/fetch_history.py) ile çekildi (15.10.2026).

- Kaynak: TCMB EVDS resmi API'si (anahtarlı). İstekler sıralı, aralarında 1 sn bekleme var.
- Dosyalar: `backend/data/snapshots/gecmis/<id>.csv` (`tarih`, `deger`). Her serinin kaynağı, kodu, birimi, alınma zamanı ve dönüşümü `meta.json` içinde.
- Yeniden çekmek için:

```bash
backend/.venv/Scripts/python backend/tools/fetch_history.py --start 01-01-2021
```

## Seriler

| id | Seri | Sıklık | Satır | Aralık |
|---|---|---|---|---|
| `usdtry`, `eurtry`, `gbptry` | TCMB döviz satış (`TP.DK.*.S.YTL`) | günlük | 1452 | 04.01.2021 → 15.10.2026 |
| `altin_gram_tl` | BİST altın kapanış, TL/kg ÷ 1000 (`TP.ALTINPIYASA.KAP02`) | iş günü | 1437 | → 14.10.2026 |
| `bist100_fiyat` | BIST 100 fiyat endeksi (`TP.MK.F.BILESIK`) | iş günü | 1446 | → 14.10.2026 |
| `bist100_getiri` | BIST 100 getiri endeksi, temettü dahil (`TP.MK.G.BILESIK`) | iş günü | 1446 | → 14.10.2026 |
| `tcmb_aofm` | TCMB ağırlıklı ortalama fonlama maliyeti (`TP.APIFON4`) | iş günü | 1451 | → 14.10.2026 |
| `tlref` | BIST TLREF gecelik referans faiz (`TP.BISTTLREF.ORAN`) | iş günü | 1427 | → 14.10.2026 |
| `mevduat_tl_1a` … `mevduat_tl_1y_ustu` | TL tasarruf mevduatı faizi, 5 vade bandı (`TP.TRYTAS.MT01-05`) | haftalık | 301 | → 02.10.2026 |
| `mevduat_usd_1a`, `mevduat_eur_1a` | USD/EUR tasarruf mevduatı, 1 aya kadar | haftalık | 301 | → 02.10.2026 |
| `tufe_endeks` | TÜFE genel endeks 2003=100 (`TP.GENENDEKS.T1`) | aylık | 69 | → 2026-09 |
| `tufe_yillik` | TÜFE yıllık değişim (endeksten hesaplandı) | aylık | 69 | → 2026-09 |
| `politika_faizi_bis` | TCMB politika faizi, BIS veri seti (`TP.BISPOLFAIZ.TUR`) | aylık | 68 | → 2026-08 |
| `politika_faizi_karar` | TCMB politika faizi kararları (yürürlük tarihli adım serisi; TCMB sayfası, Gamze) | karar | 29 | → 23.01.2026 |

Birimler:
- **Oran serileri:** 0-1 aralığında (%37 → 0,37).
- **Mevduat serileri:** cuma tarihli; o haftanın yeni açılan mevduatlarının ortalaması.
- **Politika faizi kararları:** her satır o tarihten itibaren geçerli oranı gösterir.

## Kalite kontrolü ve çapraz kontroller

- **Boşluklar:**
  - 5 günden uzun boşlukların hepsi resmi tatil: 2021-07 Kurban, 2022-05 Ramazan, 2023-07 Kurban Bayramı vb.
  - BIST'te ek olarak 2023-02-15 boşluğu var: depremden sonra borsanın kapalı kaldığı dönem.
- **Sıçramalar:** gerçek piyasa olayları.
  - Kur ve altında 21-22.12.2021 (KKM'nin açıklandığı dönemdeki kur şoku).
  - Faiz serilerinde 2023 Haziran-Kasım parasal sıkılaştırması.
- **TÜFE:** endeksten hesaplanan yıllık değişim (2026-09: %29,73), EVDS'nin kendi yıllık değişim formülüyle birebir aynı.
- **Politika faizi:** BIS aylık serisi ile TCMB karar tarihçesi 68 ayın 68'inde aynı.
- **BIST 100:** EVDS ile Gamze'nin Yahoo Finance serisi 1249 ortak günün hepsinde aynı (en büyük oran farkı %0). Son 1 yıl (13.10.2025 → 13.10.2026):
  - fiyat endeksi: EVDS %16,05, Yahoo %16,1
  - getiri endeksi (temettü dahil): %17,99

## Kıymetli madenler (EVDS, BİST)

| id | Seri | Satır | Not |
|---|---|---|---|
| `altin_usd_ons` | Altın kapanış USD/ons (`TP.ALTINPIYASA.KAP03`) | 1428 | |
| `gumus_gram_tl` | Gümüş kapanış, TL/kg ÷ 1000 (`TP.GUMUSPIYASA.KAP02`) | 1037 | BİST gümüş piyasası sığ; işlem olmayan günler veride yok |
| `gumus_usd_ons` | Gümüş kapanış USD/ons (`TP.GUMUSPIYASA.KAP03`) | 1160 | |

Kontroller:
- **Çapraz kontrol:** gümüş TL/gr fiyatı, USD/ons × USD/TRY ÷ 31,1035 ile medyanda %0,6 farklı. 840 günün 23'ünde fark %5'i aşıyor; sığ piyasa nedeniyle.
- **Büyük günlük hareketler:** 22.12.2021 (kur şoku), 02.02.2026 (+%23) ve 23.03.2026 (+%16). Son ikisi sığ piyasa kaynaklı olabilir; kullanırken dikkat.
- **Altın/gümüş oranı:** son değer 69,8.

## DİBS getirileri (Hazine ihale sonuçları)

[backend/tools/fetch_dibs.py](../backend/tools/fetch_dibs.py) ile çekildi.

- **Kaynak:** Hazine ve Maliye Bakanlığı "Kamu Finansmanı" duyuruları (sitenin kendi portal API'si) ve her duyurudaki resmi ihale sonucu PDF'i. 2021-2026 arası 253 PDF; ham dosyalar `backend/data/raw/hmb_ihale/` altında.
- **Neden EVDS değil:** EVDS'deki "DİBS gösterge değerleri" tahvil türüne göre fiyat, birim değer ya da reel getiri olarak karışık birimlerde geliyor. Güvenilir bir getiri eğrisi kurulamıyor.

| Dosya | İçerik | Satır |
|---|---|---|
| `dibs_ihaleler.csv` | İhale × senet: ISIN, senet türü, ihale/valör/vade tarihleri, vade (yıl), ortalama yıllık bileşik ve basit faiz (gerçekleşme), ortalama fiyat, satış (mn TL), kaynak PDF | 425 |
| `dibs_tl_sabit_1y` / `2y` / `5y` / `10y` | Sabit faizli nominal TL senetler (kuponsuz + sabit kuponlu); vade bantları <1,5 / 1,5-3,5 / 3,5-7,5 / >7,5 yıl | 54 / 66 / 67 / 47 ihale günü |
| `dibs_tufe_reel_2y` / `5y` / `10y` | TÜFE'ye endeksli senetlerin reel bileşik faizi | 9 / 33 / 23 |

**Senet türleri:** 183 sabit kuponlu, 51 kuponsuz, 65 TÜFE'ye endeksli, 59 TLREF'e endeksli, 56 değişken faizli, 11 diğer.

**Kontroller:**
- 425 satırda boş faiz ya da vade yok.
- 2 yıllık getirinin yıllık ortalamaları dönemle uyumlu:

| Yıl | 2 yıllık DİBS | Politika faizi | Yorum |
|---|---|---|---|
| 2021 | %18,2 | %17,8 | |
| 2022 | %21,1 | %13,3 | Negatif reel faiz döneminde piyasa daha yüksek faiz istedi |
| 2023 | %22,4 | %19,1 | |
| 2024 | %43,0 | %48,5 | İndirim beklentisiyle ters eğri |
| 2025 | %41,2 | %43,4 | |
| 2026 | %40,0 | %37,1 | |

- TÜFE'ye endeksli 5 yıllık reel getiri %-10,9 ile %+6,3 arasında.

**Sınırlamalar:**
- Bunlar birincil piyasa (ihraç) getirileri, ikincil piyasa getiri eğrisi değil.
- Seriler yalnızca ihale yapılan günlerde gözleniyor; günlük değil.
- Aynı gün aynı bantta birden çok ihale varsa satış miktarıyla ağırlıklı ortalama alınıyor.

Yeniden çekmek için (indirilmiş PDF'ler tekrar indirilmez):

```bash
backend/.venv/Scripts/python backend/tools/fetch_dibs.py --after 2021-01-01
```

## Fon dışı alternatifler için ek veriler

| Dosya | İçerik | Kaynak | Satır |
|---|---|---|---|
| `eurobond_hazine_piyasa_degeri`, `eurobond_hazine_yazili_deger` | Genel yönetim eurobond stoku, piyasa ve nominal değer (mn USD) | EVDS `TP.EBONDPIYDEG.ST`, `TP.EBONDYAZDEG.ST` | 302 (haftalık, 01.2021 → 09.10.2026) |
| `eurobond_hazine_fiyat` | Ortalama fiyat = piyasa / nominal × 100 | Hesaplama | 302 |
| `hedge_prim_usd_1a`, `hedge_prim_eur_1a` | Yıllık vadeli kur primi ≈ (1+r_TL)/(1+r_döviz)−1 | Hesaplama (TCMB 1 aya kadar tasarruf mevduatı faizleri) | 301 (haftalık) |
| `forward_usdtry_1a` | 1 aylık vadeli USD/TRY tahmini | Hesaplama (spot + faiz paritesi) | 300 |
| `dibs_doviz_ihraclar` | Yurt içi döviz cinsi DİBS ihraçları (13 USD, 4 EUR): ISIN, valör, itfa | Hazine duyuruları | 17 (**kısmi**) |
| `byf/kap_byf_listesi.json` | Yürürlükteki 36 borsa yatırım fonu (altın, gümüş, USD, BIST endeks, TLREF, temettü, katılım) | KAP resmi listesi | 36 |

Türetilmiş serilerin değerleri dönemle uyumlu:
- **Eurobond ortalama fiyatı:** yıllık ortalama 2021'de 99,4, 2022'de 87,8 (en düşük 79,6, 15.07.2022), 2024-2026'da 98-99.
- **USD hedge primi:** yıllık ortalama 2021-22'de %15, 2024'te %50, 2026'da %45.

**Sınırlamalar:**
- **Eurobond:** EVDS serisi tüm stokun fiyatıdır; getiri değil fiyat vekilidir, yeni ihraç ve itfa kompozisyonu oranı etkiler. Uluslararası eurobond ihraç getirileri Hazine duyurularında bulunamadı.
- **Hedge primi ve forward:** kapalı faiz paritesi tahminidir; bankanın gerçek forward kotasyonu değildir. EVDS'deki "TL uzlaşmalı vadeli döviz" serileri fiyat değil TCMB pozisyon hacmidir.
- **Döviz cinsi DİBS ihraçları:** kupon oranları duyuru metninde çoğunlukla yok (17 ihracın 16'sında), bağlı PDF eklerinde. Tutar sütunu teklif/satış ayrımı belirsiz olduğu için çıkarıldı.
- **BYF fiyatları:** Yahoo Türk BYF'lerinin çoğu için yalnızca 1-5 günlük veri tutuyor; bu yüzden kullanılmadı. **BYF ve emeklilik (EMK) fon fiyatları TEFAS'ta ilgili fon türleri altında bulunuyor; TEFAS çekimine bu türler eklenmeli.**
- **Özel sektör borçlanma araçları:** EVDS'de yalnızca stok tutarları var. Getiri ve fiyat Borsa İstanbul borçlanma araçları piyasası verisinde, o da datastore üzerinden sunuluyor; bu turda çekilmedi.

Türetilmiş serileri yeniden üretmek için:

```bash
backend/.venv/Scripts/python backend/tools/derive_series.py
```

## Pipeline entegrasyonu (15.10.2026)

**TEFAS fiyat dosyası yeniden kuruldu** ([backend/tools/rebuild_tefas.py](../backend/tools/rebuild_tefas.py)):
- **Kapsam:** 1.719.892 satır, 2068 yatırım fonu, 15.10.2021 → 15.10.2026.
- **Kaynaklar:** taban olarak Gamze'nin 46 elle dışa aktarımı, üstüne `tefascrawl` çıktısı. tefascrawl, kullanıcının açık talimatıyla yazılmış bir Playwright tarayıcı otomasyonu; yöntemi meta kaydında yazılı.
- **Doğrulama:** iki bağımsız yöntem 223.829 ortak fon-günde **sıfır çelişki** verdi.
- **Yedek:** hasarlı önceki dosya `snapshots/_yedek_tefas/` altında.
- **Getiri dosyası:** TEFAS getiri dosyası (1071 fon) "Fonun Risk Değeri" sütunuyla birlikte alındı.

**Resmi RD artık iki kaynaktan:** KAP ve TEFAS.
- 751 fonda iki kaynak birebir aynı, **hiç çelişki yok**.
- 55 fonun RD'si yalnızca TEFAS'tan geliyor.
- İki kaynak farklı olsaydı muhafazakâr olan alınıp RM'e teyit uyarısı düşecekti.

**Piyasa bağlamına eklenenler:** TLREF, TCMB fonlama maliyeti, 2 ve 10 yıllık sabit faizli DİBS, 5 yıllık TÜFE'ye endeksli reel getiri, USD hedge primi, gram gümüş, Hazine eurobond ortalama fiyatı. Hepsi kaynak, tarih ve sıklığa göre bayatlık durumuyla birlikte geliyor.

**Yeni ürün türleri:**

| Kategori | Ürünler | Getiri kaynağı | RD |
|---|---|---|---|
| `dibs_tl` | 1 / 2 / 5 / 10 yıl sabit faizli devlet tahvili | Son Hazine ihalesinin ortalama bileşik faizi | Varsayım: vadeye göre 2 / 3 / 4 / 5 |
| `dibs_tufe` | 5 / 10 yıl TÜFE'ye endeksli devlet tahvili | (1 + son ihale reel getirisi) × (1 + TÜFE) − 1 | Varsayım: 4 / 5 |
| `maden_hesabi` | Altın, gümüş hesabı | BİST altın/gümüş TL/gr, son 1 yıl | Fiyat volatilitesinden (altın 6, gümüş 7) |
| `mevduat_doviz` | ING USD/EUR, 32 / 92 / 181 gün (≈1 mn TL kademesi) | ING mevduat hesaplayıcısı | Kur volatilitesinden |

**Bilinen sınırlamalar:**
- **USD mevduat RD'si:** USD/TRY'nin günlük oynaklığı düşük (%1,3), bu yüzden RD 1 çıkıyor. TL'nin istikrarlı trendle değer kaybetmesi volatilite olarak görünmez; döviz maruziyeti zaten ayrıca, döviz tercihi kriteri ve P02 kısıtıyla ele alınıyor.
- **DİBS:** ikincil piyasa fiyatı olmadığı için volatilite ve en büyük düşüş eksik; o kriterler skora katılmıyor ve güven skoru düşüyor.
- **Stopaj ve maden makası:** DİBS stopajı ve maden hesabı makası yüklenmedi; ikisi de RM teyidine düşüyor.
- **BYF ve emeklilik fonları:** TEFAS çekimi yalnızca YAT türünü içeriyor; bu ikisi henüz yok.

**Guard'da düzeltilen yanlış alarm:** TEFAS'ta "PDF" kodlu bir fon var; DİBS kaynağının adındaki "(PDF)" dışlanan ürün sayılıyordu. Fon koduyla çakışan yaygın kısaltmalar (PDF, PPF, TL, USD…) artık istisna. Regresyon testi: `test_guard_ignores_acronyms_that_collide_with_fund_codes`.

## KAP bildirim geçmişi: RD ve ücret tarihçesi

[backend/tools/fetch_kap_history.py](../backend/tools/fetch_kap_history.py) ile çekildi (15.10.2026).
- **Kaynak:** KAP'ın kendi bildirim sorgu API'si. İstekler sıralı ve 4-5 sn aralıklı; KAP'ın bağlantı kesmelerinde artan sürelerle geri çekilindi.
- **Önbellek:** bildirim listesi ve ham PDF'ler `backend/data/raw/kap_liste/` ve `kap_ybf/` altında; tekrar çalıştırmada indirilmiş dosyalar atlanır.

| Dosya | İçerik |
|---|---|
| `gecmis/kap/kap_bildirimler.csv` | 1026 fonun YBF / İzahname / İzahname (Değişiklik) / Toplam Gider Oranı bildirimleri, 2021 → bugün |
| `gecmis/kap/ybf_rd_ucret.csv` | 288 ING fonunun 1831 YBF PDF'inden okunan RD, yıllık yönetim ücreti, yıllık azami toplam gider oranı; her değerin metin kanıtı ve kaynak bildirim bağlantısı |
| `gecmis/kap/ybf_degisimler.csv` | Fon bazında değişim zaman çizelgesi: **100 fonda 240 değişiklik** (157 RD, 83 ücret) |
| `gecmis/kap/ybf_dogrulama.csv` | Her fonun en son YBF değeri ile KAP fon sayfasının 15.10 değeri |

**Kapsam ve doğruluk:**

| | Bulunan belge | Doğrulama (en son YBF = KAP 15.10) |
|---|---|---|
| RD | 855 / 1831 (%47) | 116 / 132 fon (%88) |
| Yönetim ücreti | 1002 / 1831 (%55) | 149 / 160 fon (%93) |

**Neden eksik:**
- **Metinsiz PDF'ler:** belgelerin yaklaşık dörtte biri taranmış görüntü PDF'i; metin yok, OCR gerekir.
- **Grafik ölçek:** pek çok YBF'de RD yalnızca 1-7 grafik ölçeğinde gösteriliyor; metinde "… risk değerine sahiptir" cümlesi yok. Bunlar boş bırakıldı, tahmin yapılmadı.
- **Pay grupları:** 338 belgede ücret pay grubuna göre farklı (A/B grubu); hangi grubun esas olduğu belirsiz olduğu için boş bırakıldı.

**Ayrıştırıcıda yapılan iyileştirme (hata analizi):**
- **Sorun:** ilk sürüm ücreti yalnızca "%" işaretli değerlerden okuyordu; yalnızca 304 belgede bulabildi.
- **Neden:** tabloların çoğunda değer "%" işaretsiz yazılmış ("Fon Dağıtım Kuruluşu 1,00 * * Saklama…"). Ayrıca ilk "Yönetim ücreti" geçişi çoğu zaman dipnot cümlesiydi.
- **Düzeltme:** işaretsiz sayılar da okunuyor, tüm geçişler deneniyor, pencere performans tablosuna taşmadan kesiliyor, pay grubu belirsizliği ve %6 üstü değerler reddediliyor.
- **Sonuç:** kapsam 304 → 1002 belge; doğruluk %85 → %93.

## Tarihçesi olmayan kaynaklar

| Kaynak | Neden | Yerine |
|---|---|---|
| ING mevduat oranları | Hesaplayıcı yalnızca güncel oranı veriyor | `mevduat_tl_*` sektör serileri (TCMB) |
| KAP fon bilgileri (RD, ücret) | Sayfa yalnızca güncel değeri gösteriyor | 10.10 ve 15.10 çekimleri karşılaştırılabilir (RD değişiklikleri: [gamze_aktarim.md](gamze_aktarim.md)) |
| Stopaj | Dönem tablosu | `snapshots/stopaj_tarihce.json` (ING tablosu, 6 dönem) |
| DİBS gösterge faizi (ikincil piyasa) | EVDS değerleri karışık birimli | Hazine ihale getirileri (yukarıda) |
| TEFAS fon fiyatları | Kullanıcı ayrıca çekiyor | — |

## BIST 30 ve bileşen hisseler

[backend/tools/fetch_bist.py](../backend/tools/fetch_bist.py) ile çekildi (15.10.2026). İstekler sıralı, aralarında 2 sn bekleme var. Dosyalar `backend/data/snapshots/gecmis/bist/<KOD>.csv`:

| Sütun | Anlamı |
|---|---|
| `kapanis` | Bölünme / bedelsiz sermaye artırımına göre düzeltilmiş kapanış |
| `duzeltilmis_kapanis` | Bölünme + temettüye göre düzeltilmiş kapanış. **Toplam getiri için bu kullanılmalı.** |
| `hacim` | Lot |

Temettü ve bölünme olayları `bist/meta.json` içinde, hisse bazında.

- **Kaynak:** Yahoo Finance chart API. **Resmi Borsa İstanbul kaynağı değil**; Yahoo'nun kullanım koşulları otomatik çekime izin vermiyor. Kullanıcı kararıyla kullanılıyor.
- **Doğrulama:** aynı kaynaktan çekilen BIST 100, resmi EVDS serisiyle 1442 ortak günde en fazla **%0,004** farklı.
- **Kapsam:** XU030 (BIST 30 endeksi), XU100 ve 30 hisse; 04.01.2021 → 14.10.2026.
  - ASTOR (2023-01) ve DSTKF (2025-02) halka arz tarihlerinden itibaren.
  - Bugünkü seans içi satırlar yazılmaz.
- **Kalite:** düzeltilmiş seride %25'ten büyük günlük hareket yok. XU030 ile XU100'ün günlük getiri korelasyonu 0,987.
- **Bileşim (teyit edilmedi):**
  - Liste: AEFES, AKBNK, ASELS, ASTOR, BIMAS, DSTKF, EKGYO, ENKAI, EREGL, FROTO, GARAN, GUBRF, ISCTR, KCHOL, KRDMD, MGROS, PETKM, PGSUS, SAHOL, SASA, SISE, TAVHL, TCELL, THYAO, TOASO, TRALT, TTKOM, TUPRS, VAKBN, YKBNK.
  - Kaynak: Borsa İstanbul'un resmi tam listesine erişilemediği için bir haber derlemesi (09.09.2026).
  - **Teyit gereken noktalar:**
    - Haberlere göre 1 Ekim-31 Aralık 2026 döneminde DSTKF çıkıyor, TR Anadolu Metal Madencilik giriyor. Giren şirketin kodu doğrulanamadığı için liste değiştirilmedi.
    - Borsa İstanbul'un 19.06.2026 duyurusu, 1 Temmuz-30 Eylül döneminde BIST 30'a PASEU, HALKB ve ULKER'in girdiğini gösteriyor; bu, haber listesiyle çelişiyor.
  - Bugünkü bileşim 2021'e geriye uygulanıyor. Bu, hayatta kalma yanlılığı yaratır: geriye dönük getiriler olduğundan iyi görünebilir.

Yeniden çekmek için:

```bash
backend/.venv/Scripts/python backend/tools/fetch_bist.py --start 2021-01-01
```

## Benchmark kaynağı değişti

Fon kıyasında kullanılan BIST 100 artık Yahoo'dan değil, EVDS'nin resmi **getiri endeksinden** (`TP.MK.G.BILESIK`, temettü dahil) geliyor. Son 1 yıl (14.10.2025 → 14.10.2026): %19,4. EVDS dosyası yoksa aktarım Gamze'nin Yahoo serisine düşer.
