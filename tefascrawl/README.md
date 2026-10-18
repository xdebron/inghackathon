# tefascrawl

[docs/tefas.md](../docs/tefas.md)'de tanımlanan TEFAS veri ihtiyaçlarını otomatik çeken araç.

## ⚠️ Politika notu (lütfen okuyun)

[docs/veri_kaynaklari.md](../docs/veri_kaynaklari.md), ekibin şu kararını kayıt altına
almıştı:

> **TEFAS API'sini script ile çekmek:** bot korumasıyla engelliyor; aşmaya
> çalışmıyoruz, yalnızca sitenin kendi dışa aktarımını kullanıyoruz.

Bu paket, **kullanıcının bu kararın aksine verdiği açık talimatla** yazıldı: TEFAS'ın
F5 BIG-IP ASM WAF korumasını gerçek bir tarayıcı (Playwright/Chromium) ile geçip,
sitenin yeni (Next.js) arayüzünün kullandığı iç JSON API uçlarına doğrudan istek atar.
Bunlar TEFAS'ın resmi/dokümante edilmiş bir API'si değildir — sitenin kendi önyüzünün
kullandığı, tarayıcı DevTools'u ile 15.10.2026'da tersine mühendislikle bulunan uçlardır.

Kullanmadan önce:
- `docs/veri_kaynaklari.md`'yi güncelleyin veya bu kararı gözden geçirin, böylece iki
  belge çelişmesin.
- Siteye makul bir yük bindirin (varsayılan: istekler arası ~0.6 sn + jitter, hata
  durumunda üstel geri çekilme). `--months` ve `page_size` değerlerini gereğinden
  büyük tutmayın.
- Bu, bir devlet kurumunun (Takasbank/SPK) sitesidir; kullanım şartlarına aykırılık
  riskini ve IP engellenme ihtimalini göz önünde bulundurun.

## Kapsam (docs/tefas.md ile birebir)

| # | tefas.md öğesi | Modül | Uç nokta | Durum |
|---|---|---|---|---|
| 1 | Fon fiyat geçmişi (13 ay, YAT) — P0 | `tarihsel.py` | `POST /api/funds/fonGnlBlgSiraliGetir` | ✅ Tarih, Fon Kodu, Fon Adı, Fiyat, Tedavüldeki Pay Sayısı, Kişi Sayısı, Fon Toplam Değer — hepsi tek uçtan. tefas.md 13 ay istiyordu ama **TEFAS gerçekte 5 yıla kadar** veriyor (`--months 60`); 15.10.2026'da tam 5 yıl (2021-10-15 → 2026-10-15, 1.719.892 satır, 2.068 fon) başarıyla çekilip backend'e aktarıldı |
| 2 | Fon getirileri (tüm fonlar) — P0/P1 | `getiriler.py` | `POST /api/funds/fonGetiriBazliBilgiGetir` | ✅ Tek çağrıda tüm fonlar (sayfalama yok) |
| 3 | Portföy dağılımı — opsiyonel | `portfoy.py` | `POST /api/funds/dagilimSiraliGetirT` | ⚠️ Çalışıyor ama TEFAS yalnızca **güncel günü** döndürüyor (geçmiş dağılım yok) ve varlık sınıfı kodlarının yalnızca bir kısmı doğrulanabildi (`KNOWN_FIELDS`) |
| 4 | Fon analiz/detay sayfası — opsiyonel, elle | — | — | ❌ Kapsam dışı (bkz. aşağıda) |

### Kapsam dışı: Fon analiz/detay sayfası (item 4)

tefas.md bunu açıkça **elle, ~20-30 fon için** ve birincil kaynağın **KAP** olduğunu
("TEFAS burada yalnızca teyit amaçlı") belirtiyor. Bu yüzden otomatikleştirilmedi.
İleride gerekirse `/tr/fon-detayli-analiz/{KOD}` sayfası + `fonFiyatBilgiGetir` +
`fonProfilDtyGetir` uçları tek fon bazında bu amaçla kullanılabilir (keşif notları
için bu PR'ın geçmişine bakın).

## Kurulum

```bash
pip install -r tefascrawl/requirements.txt
playwright install chromium
```

`--import-to-backend` kullanacaksanız bunu `backend/.venv` aktifken çalıştırın
(zaten `pandas`/`openpyxl` içeriyor):

```bash
# Windows
backend\.venv\Scripts\activate
pip install playwright
playwright install chromium
```

## Kullanım

```bash
# Sadece CSV üret (tefascrawl/out/ altına), elle yüklemek için
python -m tefascrawl

# 13 ay yerine daha kısa bir test çekimi
python -m tefascrawl --months 1

# TEFAS'ın izin verdiği maksimum geçmiş (5 yıl = 60 ay)
python -m tefascrawl --months 60 --import-to-backend

# Portföy dağılımını da çek
python -m tefascrawl --with-portfoy

# CSV'leri backend/data/snapshots içine doğrudan aktar (manuel yükleme adımını atlar)
python -m tefascrawl --import-to-backend

# Hata ayıklama: tarayıcıyı görünür çalıştır
python -m tefascrawl --headed
```

`--months 60` (5 yıl) ~60 pencere × sayfalama gerektirir ve hız sınırlaması/yeniden
denemelerle birlikte **30-60 dakika** sürebilir; arka planda çalıştırmanız önerilir.

Çıktı dosyaları docs/tefas.md'nin önerdiği adlandırmayla yazılır:
`tefas_tarihsel_<ilk-tarih>_<son-tarih>.csv`, `tefas_getiriler_<bugün>.csv`,
`tefas_portfoy_<bugün>.csv`. Sütun adları `backend/app/sources/tefas_import.py`'nin
beklediği Türkçe başlıklarla birebir eşleşir; bu dosyalar hem uygulamanın
"TEFAS dışa aktarımı yükle" alanından elle yüklenebilir hem de `--import-to-backend`
ile doğrudan `tefas_import.import_any()` çağrılarak işlenebilir.

## Nasıl çalışıyor

1. `session.TefasSession`, headless Chromium ile `/tr/fon-verileri` sayfasını açar;
   WAF'ın JS doğrulamasını geçer ve `wid…`/`xid..._session_` çerezlerini alır.
2. Sayfanın kendi isteklerinden `Authorization: Bearer ST-…` token'ı yakalanır
   (derlenmiş JS'e gömülü, herkese açık bir frontend token'ı; döneceği ihtimaline
   karşı sabit kodlanmadı).
3. Sonraki tüm `/api/funds/...` POST istekleri **aynı sayfa içinden `fetch()` ile**
   atılır (`page.evaluate`). Playwright'ın ayrı `APIRequestContext`'i (`context.request`)
   denendi ama WAF bunu farklı bir TLS/HTTP2 parmak izinden tanıyıp sürekli
   `ECONNRESET` ile engelliyordu; sayfa içi `fetch()` gerçek tarayıcının ağ yığınını
   kullandığı için sorunsuz geçiyor.
4. `fonGnlBlgSiraliGetir` ve `dagilimSiraliGetirT`, `basSira`/`bitSira` ile
   1-tabanlı satır aralığı + `toplamSayi` sözleşmesini kullanır; `pagination.py`
   bunu otomatik sayfalar.
5. Tarih aralığı **takvim ayı bazlı** sınırlı: `bitTarih`, `basTarih + 1 takvim ayını`
   aşamaz (sabit 30 gün değil — örn. 12 Şubat'tan başlayan bir pencere en geç 12
   Mart'a kadar gidebilir, 13 Mart "1 ayı aştı" hatası verir). `tarihsel._chunk_range`
   bunu `_shift_months` ile doğru hesaplıyor. Ayrıca **`basTarih` bugünden 5 yıldan
   eski olamaz** (`"Başlangıç Tarihi 5 yıldan eski olamaz"`) — 15.10.2026'da test
   edilen kesin sınır: `2021-10-15`.

### Kritik düzeltme: tarih formatı (dayfirst bozulması)

Backend'in `tefas_import.parse_export()`'u yüklenen dosyanın tarih sütununu
`pd.to_datetime(..., dayfirst=True)` ile okuyor (TEFAS'ın gerçek Excel
dışa aktarımı `GG.AA.YYYY` formatında olduğu için). Crawler ilk sürümde API'den
gelen tarihi olduğu gibi ISO (`YYYY-MM-DD`) yazıyordu; `dayfirst=True` bunu
**gün ≤ 12 olan tarihlerde ay/günü birbirine karıştırıyor, gün > 12 olanlarda ise
tamamen `NaT`'a düşürüp satırı sessizce siliyordu** — iki prod çekimi backend'e
kısmen bozuk veri yazdı (fark edilip temizlendi, bkz. git/PR geçmişi). Düzeltme:
`export.write_tarihsel_csv()` artık `tarihsel.to_dayfirst_display()` ile tarihi
`GG.AA.YYYY`'ye çeviriyor — tıpkı TEFAS'ın kendi Excel çıktısı gibi. **Bu modülde
tarih formatıyla oynarken bu noktayı unutmayın.**

## Bilinen sınırlamalar

- `dagilimSiraliGetirT`'nin döndürdüğü ~50 kısaltılmış alan kodundan yalnızca 12'si
  (`portfoy.KNOWN_FIELDS`) TEFAS'ın "Fon Varlık Dağılımı" panelindeki insan-okur
  etiketleriyle çapraz kontrol edilerek doğrulandı. Geri kalanı ham kod olarak kalır.
- Portföy dağılımı yalnızca **bugünün** anlık görüntüsü; TEFAS bu uçta geçmiş tarih
  desteklemiyor (denendi: aynı sözleşmeyle geçmiş tarih veriyor gibi görünse de
  `/tr/fon-verileri` arayüzü yalnızca güncel günü gösteriyor; geçmiş doğrulanmadı).
- `Authorization` bearer token'ı derlenmiş JS'e gömülü sabit bir değer; TEFAS bunu
  rotasyona sokarsa `session.py`'deki yakalama mekanizması (gerçek istekten okuma)
  yine çalışır, yalnızca `FALLBACK_BEARER` güncellenmemiş olur (zararsız, sadece
  warm-up isteği başarısız olursa devreye girer).
- WAF art arda hızlı isteklerde bazen bağlantıyı sıfırlıyor (`ERR_CONNECTION_RESET`);
  `TefasSession` bunu yeniden deneme + geri çekilme ile tolere ediyor ama sık
  tekrarlanırsa `request_delay`'i artırın.
