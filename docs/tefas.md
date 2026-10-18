# TEFAS Veri İhtiyacı

TEFAS'tan hangi verinin, hangi sayfadan, ne amaçla alınacağının özeti.

## Erişim durumu (15.10.2026)

- TEFAS, F5 BIG-IP ASM (web uygulama güvenlik duvarı) arkasında. Tarayıcı dışındaki istekler "Request Rejected" sayfasıyla engelleniyor. Bu, çerezsiz sade bir GET isteğiyle de doğrulandı.
- Tarayıcı, JavaScript kontrolünü geçince `wid…` ve `xid…_session_` çerezlerini alıyor ve erişebiliyor.
- Site Next.js ile yenilenmiş. Eski `.aspx` sayfaları ve `/api/DB/...` uçları artık çalışmıyor olabilir. Aşağıdaki "Doğrulandı" sütunu bunu gösteriyor.

## Çekilecek veriler

### 1. Fon fiyat geçmişi: zorunlu

| | |
|---|---|
| **Amaç** | 1A / 3A / 1Y getiri, yıllık volatilite, son 1 yılın en büyük düşüşü, volatiliteden tahmini risk değeri (RD), veri kalitesi kontrolleri (fiyat sıçraması, bayat veri) |
| **Sayfa (yeni site)** | Tarihsel veriler sayfası. Yeni adresi henüz doğrulanmadı; eski karşılığı `TarihselVeriler.aspx` |
| **Eski API ucu** | `POST /api/DB/BindHistoryInfo` (`fontip=YAT`, `bastarih`, `bittarih`, `fonkod` boş = tüm fonlar). Script için engelli, kullanılmıyor |
| **Filtre** | Fon tipi: **Yatırım Fonları (YAT)**. Emeklilik (EMK) ve BYF kapsam dışı |
| **Dönem** | Son **13 ay** (1 yıllık getiri için 12 ay ve başlangıç fiyatı için tampon). Site tek seferde 3 ay veriyorsa 5 parça halinde indirilir; uygulama parçaları birleştirir ve aynı gün/fon için farklı fiyat gelirse çelişki kaydeder |
| **Gerekli sütunlar** | `Tarih`, `Fon Kodu`, `Fon Adı`, `Fiyat`, `Tedavüldeki Pay Sayısı`, `Kişi Sayısı`, `Fon Toplam Değer` |
| **Kodda** | `tefas_import.parse_export()`. Dosyada `Fiyat` sütunu varsa otomatik "tarihsel" olarak tanınır |

### 2. Fon getirileri: güçlü öneri (ikinci kaynak)

| | |
|---|---|
| **Amaç** | Tüm fonların dönemsel getirileri tek dosyada. (a) Fiyat geçmişinden hesapladığımız getiriyle **çapraz kontrol** (2 puandan fazla fark varsa "çelişkili" işaretlenir). (b) Fiyat geçmişi olmayan fonlar için getiri. (c) Resmi fon türü sınıflaması |
| **Sayfa (yeni site)** | `https://www.tefas.gov.tr/tr/fon-getirileri?fundType=YAT`. **Doğrulandı:** sayfa tarayıcıda açılıyor |
| **Eski API ucu** | `POST /api/DB/BindComparisonFundReturns`. Script için engelli, kullanılmıyor |
| **Filtre** | Fon tipi YAT, tüm şemsiye türleri |
| **Dönem** | İndirme günü itibarıyla. Uygulamaya yüklerken **dönem sonu tarihi** girilmeli, çünkü dosyada tarih sütunu yok |
| **Gerekli sütunlar** | `Fon Kodu`, `Fon Adı`, `Şemsiye Fon Türü`, `1 Ay (%)`, `3 Ay (%)`, `6 Ay (%)`, `Yılbaşı (%)`, `1 Yıl (%)`, `3 Yıl (%)`, `5 Yıl (%)` |
| **Kodda** | `tefas_import.parse_comparison()`. Getiri sütunları varsa otomatik "karşılaştırma" olarak tanınır. Değerler yüzde birimindedir (46,52 → 0,4652) |

### 3. Portföy dağılımı: opsiyonel (henüz kodda yok)

| | |
|---|---|
| **Amaç** | Fonun gerçek döviz, hisse ve altın payı. Böylece "döviz maruziyetini artırmama" (P02) ve "yoğunlaşmama" (P04) kısıtları ad tahminine değil fiilî içeriğe göre uygulanabilir |
| **Sayfa** | Tarihsel veriler → Portföy Dağılımı sekmesi (yeni sitede doğrulanmadı) |
| **Gerekli sütunlar** | Fon Kodu, Tarih ve varlık sınıfı yüzdeleri (hisse, DİBS, eurobond, döviz, kıymetli maden, repo, TPP vb.) |

### 4. Fon analiz / detay sayfası: opsiyonel

| | |
|---|---|
| **Amaç** | Fon türü, kurucu ve varsa risk değeri. Yönetim ücreti, resmi RD ve valör için birincil kaynağımız **KAP** (izahname / yatırımcı bilgi formu); TEFAS burada yalnızca teyit amaçlı |
| **Sayfa** | Eski adı `FonAnaliz.aspx?FonKod=XXX`; yeni sitede doğrulanmadı |
| **Kapsam** | Sadece kısa listeye girme ihtimali olan ~20-30 fon için, elle |

## Öncelik ve süre tahmini

| # | Veri | Öncelik | Tahmini indirme |
|---|---|---|---|
| 1 | Fon fiyat geçmişi (13 ay, YAT) | P0 | ~5 dosya × 2 dk |
| 2 | Fon getirileri (YAT) | P0/P1 | 1 dosya |
| 3 | Portföy dağılımı | P2 | 1 dosya; ayrıca kod gerekiyor |
| 4 | Fon detay sayfası | P2 | Fon başına elle |

## Yükleme

1. Dosyaları tarayıcıdan indirin. Ad önerisi: `tefas_tarihsel_2025-09_2025-12.xlsx`, `tefas_getiriler_2026-10-15.xlsx`.
2. Uygulamanın sol panelindeki **"TEFAS dışa aktarımı yükle"** alanından yükleyin. Getiriler dosyası için dönem sonu tarihini seçin.
3. Sol panelde fon sayısını ve tarih aralığını, sonra bir persona çalıştırıp "Veri ve uyarılar" sekmesini kontrol edin.

> **Not:** Yeni sitenin dışa aktarım sütun adları henüz görülmedi. İlk dosya geldiğinde importer gerçek formata göre doğrulanacak; sütun eşleşmezse yükleme, bulunan sütunları listeleyen bir hata mesajı döner.
