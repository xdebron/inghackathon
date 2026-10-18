# Veri Kaynakları Checklist

Erişim testi 15.10.2026'da script (curl) ile yapıldı. "Kodda" sütunu bu kaynağın sistemde bugün desteklenip desteklenmediğini gösterir.

**Her kayıt için zorunlu alanlar** (brief kural 2-3): `source`, `url`, `as_of` (kaynaktaki veri tarihi). Bulunamayan değer boş bırakılır, tahmin girilmez.

## P0: Demo için şart (18 Ekim'e kadar)

| ✓ | Veri | Neden lazım | Kaynak | Erişim | Test sonucu | Kodda |
|---|---|---|---|---|---|---|
| [x] | USD/EUR/GBP kurları | Piyasa bağlamı, döviz ürünleri | TCMB `kurlar/today.xml` | Otomatik | ✅ 200 | ✅ |
| [ ] | Fon fiyat geçmişi (≥12 ay, günlük) | Getiri, volatilite, max düşüş, tahmini RD | TEFAS → Tarihsel Veriler → Excel'e Aktar | Manuel indir, arayüzden yükle | ⛔ Script engelli | ✅ |
| [ ] | TL mevduat oranları (3-5 banka; 32 / 92 / 181 gün) | Mevduat alternatifi, P01 ve P05 için kritik | Bankaların ürün/faiz sayfaları (ING, Garanti, İş, Akbank, YKB...) | Manuel, `manuel_veriler.json` → `mevduat` | ✅ ING sitesi erişilebilir | ✅ |
| [ ] | Stopaj oranları (fon türü ve mevduat bazında) | Net getiri; brief kural 7: koda sabit yazılmaz | Resmî Gazete (GVK Geçici 67. md. Cumhurbaşkanı kararları), GİB | Manuel, `manuel_veriler.json` → `stopaj` | ✅ Erişilebilir | ✅ |
| [ ] | Fon yönetim ücreti | Maliyet kriteri (BGP/DLY örneği) | KAP fon sayfası → izahname / yatırımcı bilgi formu | Manuel, kısa liste adaylarından başla (~20-30 fon) | ✅ KAP erişilebilir | ✅ |
| [ ] | Resmi risk değeri (RD 1-7) | Uygunluk kuralı; volatiliteden yapılan tahminin teyidi | KAP → yatırımcı bilgi formu | Manuel, `fon_ek_bilgi.resmi_rd` | ✅ | ✅ (çelişki kontrolü var) |
| [ ] | Alım/satım valörü | P05 likidite kısıtı | KAP → izahname | Manuel, `fon_ek_bilgi.valor_satis_gun` | ✅ | ✅ |
| [ ] | Nitelikli yatırımcıya özel mi | Serbest fonların dışlanması | KAP → izahname ("sadece nitelikli yatırımcılara...") | Manuel, `fon_ek_bilgi.nitelikli_yatirimci` | ✅ | ✅ |

## P1: Analiz kalitesini artıran

| ✓ | Veri | Neden lazım | Kaynak | Erişim | Test sonucu | Kodda |
|---|---|---|---|---|---|---|
| [ ] | TCMB politika faizi | Faiz ortamı bağlamı, mevduat/PPF karşılaştırması | tcmb.gov.tr veya EVDS | EVDS ya da manuel (`gostergeler.politika_faizi`) | ✅ | ✅ |
| [ ] | TÜFE yıllık enflasyon | **Reel getiri** (enflasyon üstü getiri), RM için güçlü anlatı | TÜİK veri portalı, EVDS | Manuel ya da EVDS | ✅ veriportali.tuik.gov.tr | ✅ reel getiri |
| [ ] | EVDS API anahtarı | Kur geçmişi, sektör ortalama mevduat faizi, TÜFE, altın tek API'den | evds2.tcmb.gov.tr (evds3'e yönleniyor) | Ücretsiz üyelik ve API key | ✅ `evds3.../igmevdsms-dis/` + `key` header doğrulandı; seri kodları anahtarla teyit edilecek | ✅ |
| [ ] | Sektör ortalama mevduat faizi (TCMB) | Banka oranlarının **çapraz kontrolü** (aykırı değer, çelişki tespiti) | EVDS (vadeye göre ağırlıklı ortalama) | EVDS (seri kodu teyit edilecek) veya manuel | ⚠️ | ✅ sapma uyarısı |
| [ ] | TEFAS Fon Karşılaştırma → getiri bazlı dışa aktarım | Tüm fonların dönemsel getirisi tek dosyada; kendi hesapladığımız getiriyle **ikinci kaynak** karşılaştırması | TEFAS → Fon Karşılaştırma → Excel | Manuel, arayüzden yükle (tür otomatik tanınır) | ⛔ Script engelli | ✅ çapraz kontrol |
| [ ] | TMSF mevduat sigortası limiti | Mevduatın anapara koruması tutara bağlı; RM uyarısı | tmsf.org.tr | Manuel (`gostergeler.tmsf_sigorta_limiti_tl`) | ✅ | ✅ mevduat uyarısı |
| [ ] | BIST endeksleri (BIST 100, tahvil endeksleri) | Hisse ve borçlanma fonları için benchmark | borsaistanbul.com veri sayfaları | Manuel (`benchmarklar`); lisans koşulları teyit edilmeli | ✅ site erişilebilir | ✅ benchmark farkı |

## P2: Opsiyonel

| ✓ | Veri | Neden lazım | Kaynak | Nereye girilir | Kodda |
|---|---|---|---|---|---|
| [ ] | DİBS gösterge faizleri / Hazine ihale sonuçları | Borçlanma fonları için faiz bağlamı | hmb.gov.tr | `gostergeler.dibs_gosterge_2y` | ✅ piyasa bağlamı |
| [ ] | Gram altın / ons fiyatı | Kıymetli maden fonları bağlamı | EVDS, Borsa İstanbul KMP | EVDS ya da `gostergeler.altin_gram_tl` | ✅ piyasa bağlamı |
| [ ] | SPK bülteni | Fon ücret ve izahname değişikliklerini yakalamak | spk.gov.tr | `fon_ek_bilgi.<KOD>.uyari_notu` | ✅ RM teyit uyarısı |
| [ ] | Katılım bankası kâr payı oranları | Katılım alternatifi | Katılım bankalarının ürün sayfaları | `mevduat` kaydı, `"tur": "katilim"` | ✅ ayrı ürün kategorisi |

## EVDS kurulumu

1. evds3.tcmb.gov.tr adresinden ücretsiz üye olun ve profil sayfasından API anahtarını alın.
2. Anahtarı `backend/.env` dosyasına `EVDS_API_KEY=...` satırı olarak yazın. Bu dosya git'e girmez. Alternatif olarak ortam değişkeni de kullanılabilir.
3. Seri kodları 15.10.2026'da EVDS `serieList` uç noktasıyla doğrulandı ([evds_series.json](../backend/config/evds_series.json)):

| Gösterge | Seri | Son değer (15.10.2026 çekimi) |
|---|---|---|
| TÜFE yıllık | `TP.GENENDEKS.T1`, formül 3 (yıllık % değişim) | %29,73 (2026-9) |
| Politika faizi | `TP.BISPOLFAIZ.TUR` (BIS veri seti, aylık, gecikmeli) | %37,00 (2026-8) |
| Tasarruf mevduatı 1A / 3A / 6A / 1Y | `TP.TRYTAS.MT01`–`MT04` | %44,00 / %43,82 / %42,03 / %37,75 (02-10-2026) |
| Gram altın | `TP.ALTINPIYASA.KAP02` ÷ 1000 | 6.500 TL (14-10-2026) |

Notlar:
- İlk tahmin edilen kodlar hatalıydı. `TP.FG.J0` 2026 Ocak'ta durmuştu. `TP.TRY.MT02` ise "1 ay" değil "3 aya kadar" serisiydi ve tasarruf mevduatı değil toplam mevduatı ölçüyordu.
- Tasarruf mevduatı değerleri, Gamze'nin Slack'te TCMB'den aktardığı rakamlarla birebir eşleşiyor.

## Kullanmayacağımız kaynaklar

Brief kural 8 gereği kaynakların kullanım koşullarına uyuyoruz.

- ~~**TEFAS API'sini script ile çekmek:** bot korumasıyla engelliyor; aşmaya çalışmıyoruz, yalnızca sitenin kendi dışa aktarımını kullanıyoruz.~~
  **Güncelleme (15.10.2026):** Bu karar kullanıcının açık talimatıyla değiştirildi.
  `tefascrawl/` paketi, WAF'ı gerçek bir tarayıcıyla (Playwright) geçip sitenin iç
  JSON API'lerine (fonGetiriBazliBilgiGetir, fonGnlBlgSiraliGetir, dagilimSiraliGetirT)
  doğrudan istek atıyor. Riskler ve gerekçe için [tefascrawl/README.md](../tefascrawl/README.md)'deki
  "Politika notu" bölümüne bakın.
- **TSPB:** bot koruması var.
- **investing.com, Fintables, Rasyonet vb.:** ticari veya kullanım koşulları otomatik çekime izin vermiyor.

## Çapraz kontrol fikirleri

Bunlar "çelişkili veri" kriteri için puan getirir:

1. **TEFAS getirisi:** kendi hesapladığımız getiri ile Fon Karşılaştırma dışa aktarımındaki getiri karşılaştırılır. Fark eşiği aşarsa ürün `conflict` olarak işaretlenir.
2. **RD:** volatiliteden yapılan tahmin ile KAP'taki resmi değer karşılaştırılır. Bu kontrol kodda zaten var.
3. **Mevduat oranları:** banka oranı ile TCMB sektör ortalaması karşılaştırılır; aykırı değer RM'e uyarı olarak düşer.
4. **Bayat veri:** son fiyat tarihi 7 günden eskiyse kayıt `stale` olarak işaretlenir. Bu kontrol kodda zaten var.

## İş bölümü önerisi

- **Gamze:** mevduat oranları, stopaj ve KAP fon bilgileri. Slack'teki araştırması bunların çoğunu zaten kapsıyor.
- **Hakkı:** TEFAS dışa aktarımları (12 ay, ilgili fon türleri), EVDS anahtarı, TÜFE ve sektör ortalaması entegrasyonu.
