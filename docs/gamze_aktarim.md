# Gamze'nin Verilerinin Aktarımı

> **Tarihsel not.** Bu aktarım 9-15 Ekim'de yapıldı. Veriler sonra veri tabanına taşındı ([veri_tabani.md](veri_tabani.md));
> aktarım script'i ve snapshot dosyaları kaldırıldı. Aynı kaynaklar artık otomatik toplayıcılarla güncellenir: KAP → `kap_fon`,
> ING → `ing_mevduat` / `ing_stopaj` / `ing_fonlar`, TCMB → `tcmb_politika`. Aşağıdaki bulgular kayıt için duruyor.

Kaynak: `gamze_work/AGENTIC AI HACKATHON-ING/data/processed`. Veriyi Gamze Akkurt 9-14 Ekim 2026'da topladı. Aktarım script'i [backend/tools/import_gamze.py](../backend/tools/import_gamze.py).

- Tekrar çalıştırılabilir: `backend/.venv/Scripts/python backend/tools/import_gamze.py`
- **Hiçbir kaynağa yeni istek atmaz.** Yalnızca mevcut dosyaları dönüştürür.
- Elle girilmiş kayıtları ezmez; yalnızca kendi eklediği (`"_aktaran": "import_gamze"`) kayıtları yeniler.

## Ne aktarıldı

| Veri | Kapsam | Orijinal kaynak ve yöntem | Sistemdeki yeri |
|---|---|---|---|
| TEFAS fiyat geçmişi | 288 fon, 223.829 satır, 02.01.2023 → 09.10.2026 | 46 adet TEFAS "Fon Verileri" CSV'si, **elle dışa aktarım** (son dışa aktarım 09.10.2026 18:27) | `snapshots/tefas_fiyatlar.csv` (asıl kaynak) |
| TEFAS API fiyatları | 288 fon, 189.204 satır | `api/funds/fonFiyatBilgiGetir`, **script ile** (09.10.2026). Yeniden çalıştırılmaz | `snapshots/tefas_api_fiyatlar.csv`, **yalnızca çapraz kontrol** |
| KAP fon bilgileri | 1026 fon: resmi RD (1008), yönetim ücreti (1018), giriş/çıkış komisyonu, şemsiye türü, ISIN, karşılaştırma ölçütü | KAP "Fon Bilgileri" sayfaları, script ile (10.10.2026) | `snapshots/kap_fon_bilgileri.json` (elle girilen kayıt bunun önüne geçer) |
| Nitelikli yatırımcı | 1026 fon, 126'sı nitelikli yatırımcıya özel | KAP metin taraması | Aynı dosya, `nitelikli_yatirimci` |
| Satış valörü | 34 fon (KAP veya TEFAS valör simülatörü ölçümü) | KAP alım-satım esasları + TEFAS simülatörüne elle girilen 54 ölçüm | `valor_satis_gun`, alan bazında ayrı kaynak bilgisiyle |
| Stopaj | 8 fon kategorisi + TL mevduat (gerçek kişi, 09.07.2025 sonrası) | ING vergilendirme tablosu; mevduat için ING hesaplayıcısı. Eurobond ve yabancı fonlar için kaynakta oran yok, RM teyidine düşer | `manuel_veriler.json → stopaj` |
| ING mevduat oranları | TL, 32 / 46 / 92 / 181 / 365 gün, 1.000.000 TL kademesi | ING mevduat hesaplayıcısının uç noktası, script ile | `manuel_veriler.json → mevduat` |
| Politika faizi | %37, yürürlük 23.01.2026 | TCMB 1 hafta repo sayfası | `gostergeler.politika_faizi` (EVDS ile çapraz kontrol edilir) |
| TÜFE | %29,73 (2026-09) | TCMB enflasyon sayfası | `gostergeler.tufe_yillik` (EVDS ile çapraz kontrol: eşleşiyor) |
| BIST 100 | 1 yıllık %16,1 (13.10.2025 → 13.10.2026) | **Yahoo Finance chart API: resmi Borsa İstanbul kaynağı değil**, fiyat endeksi, temettü hariç | `benchmarklar` (yalnızca hisse fonlarında kıyas; skora girmez) |
| ING fon listesi | 293 fon | ING fon fiyatları sayfası | `ing_platformunda` bilgi alanı (skora girmez) |
| TEFAS kalite raporu | 46 dosya, 36.935 tekrar eden satır atıldı, 0 çelişen tekrar | Gamze'nin kontrolü | `snapshots/tefas_kalite_raporu.json` |
| Raporlar | `RM_Cozum_Teknik_Rehber.pdf`, `Test_ve_Ogrenimler.pdf` | Gamze | [docs/gamze/](gamze/) |

**Aktarılmayan:** `tools/tefas_profile_collector.js`. Çıktısı klasörde yok. Ayrıca kullanıcı oturum çerezleriyle TEFAS sayfalarını otomatik çektiği için kullanılmıyor.

## Bu aktarımla düzelen veya ortaya çıkan şeyler

1. **SPK risk bantları düzeltildi.** Önceki varsayımımız ESMA SRRI bantlarıydı (0,5 / 2 / 5 / 10 / 15 / 25). SPK rehberindeki (25.08.2025) bantlar farklı: 2 / 5 / 10 / 15 / 20 / 30. Gamze'nin çıkarımıyla düzeltildi.
2. **Yabancı hisse fonları ayrı kategori oldu.** "YABANCI HİSSE SENEDİ" fonları (örn. AFA) TL bazlı hisse sayılıyordu; artık USD maruziyetli `yabanci_hisse` kategorisinde. P02'nin "döviz maruziyetini artırmama" kısıtı bunları doğru dışlıyor.
3. **Resmi ve tahmini RD farkı.** Gamze 233 fonda karşılaştırmış: 100'ü birebir, 181'i ±1 kademe içinde, 118'inde tahmin resmiden düşük. Bizim sistem resmi RD'yi kullanıyor; 2+ kademe farkta muhafazakâr değeri seçip RM'e uyarı düşüyor. "Tahmine güvenme, resmi değeri kullan" kararının kanıtı bu.
4. **TEFAS'taki iki kaynağın çapraz kontrolü.** 288 fonda 187.298 ortak gün karşılaştırıldı. Yalnızca 2 fonda uyumsuzluk çıktı:
   - **GA1:** 380 gün, dışa aktarımda 748,50, API'de 0,7485 (1000 kat).
   - **PTE:** 8 gün, 100 kat.

   Muhtemel neden pay bölünmesi veya birim değişikliği. Sistem bu fonları "çelişkili" işaretliyor. Çelişkili veri davranışı için demo örneği.
5. **ING mevduatı sektörün 8 puan altında.** ING 32 gün %36 (1 mn TL kademesi), TCMB tasarruf mevduatı ortalaması (1 aya kadar) %44. Sistem bu sapmayı otomatik olarak RM teyidine düşürüyor.
6. **Sıfır fiyatlar.** 60 fonun geçmişinde toplam 640 günlük 0 fiyat var; çoğu kuruluş ya da işlem görmeyen dönemlere ait. Kalite kontrolü artık yalnızca metrik penceresine (son 1 yıl) bakıyor ve 0 fiyatı eksik gün sayıyor. Önce 60 fonu gereksiz yere işaretliyordu, şimdi 6 fonu işaretliyor.

## Hata analizi örneği (Test ve öğrenimler için)

- **Gözlem:** P03 için üretilen taslak metin, guard'a "Yasaklı ifade: 'garanti'" ihlaliyle takıldı.
- **Neden:** Kısa listedeki "GARANTİ PORTFÖY YARI İLETKEN TEKNOLOJİLERİ DEĞİŞKEN FON". Bir şirket adı, getiri garantisi değil. Bu bir yanlış alarmdı.
- **İyileştirme:** Guard, kısa listedeki ürün adlarını tarama öncesinde metinden çıkarıyor. Aynı metinde gerçek bir "garanti eder" ifadesi hâlâ yakalanıyor. Regresyon testi: `test_guard_allows_shortlisted_fund_names_but_not_promises`.

## Yenileme: 15.10.2026

Gamze'nin script'leri kullanıcı onayıyla, yavaş ve sıralı olarak yeniden çalıştırıldı. Önceki veriler `gamze_work/.../data/processed/_yedek_2026-10-10/` ve `data/raw/kap_funds_yedek_2026-10-10/` altında duruyor.

- **ING mevduat:** 60 sorgu, aralarında 2 sn bekleme; 60/60 başarılı. 32 ve 46 günlük TL oranları 0,5 puan düştü (1 mn TL: %36 → %35,5). 92 gün ve üzeri değişmedi.
- **KAP:** 1026 fon sıralı ve 2,5 sn aralıkla çekildi; 1015'i başarılı.
  - 10 fonda KAP hız sınırı (HTTP 429) geldi; script geri çekildi ve zorlanmadı. GBG'nin sayfası bulunamadı.
  - Bu 11 fon (BVF, ESG, GBG, GPA, ICS, KMN, NHY, PIL, SFS, VCY, ZND) önceki çekimden alındı; `as_of` değerleri 2026-10-10 olarak kaldı.
- **Değişiklikler (5 günde):**
  - 6 fonun resmi RD'si değişti: FPG 6→7, FZP 2→3, KUA 5→4, **RPL 2→7**, ZCK 4→5, ZPC 5→6.
  - Ücret, komisyon, nitelikli yatırımcı ve valör bilgilerinde değişiklik yok.
- **TEFAS:** yenilenmedi; kullanıcı tarayıcıdan dışa aktaracak.
- **BIST 100:** yenilenmedi; kaynağın (Yahoo) kullanım koşulları otomatik çekime izin vermiyor.

## Dikkat edilecekler

- **Veri tazeliği:** veri 09.10.2026 tarihli. Sistemin 7 günlük "bayat" eşiği 16.10.2026'da devreye girer. Demodan önce yeni bir TEFAS dışa aktarımı yüklenmeli.
- **Script ile toplananlar:** TEFAS API, KAP, ING hesaplayıcısı ve Yahoo verileri script ile toplandı. Arayüzde ve kanıt kaydında kaynak ve yöntemleriyle görünüyorlar; kullanım koşulları jüri sorusuna hazırlıklı olunmalı.
- **Ham dosyalar:** ham TEFAS CSV'leri klasörde yok, yalnızca birleştirilmiş hali var. Kanıt için Gamze'den istenmeli.
