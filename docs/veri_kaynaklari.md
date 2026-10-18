# Veri kaynakları

Tüm piyasa ve ürün verisi veri tabanındadır: tablolar, kaynak alanları ve toplayıcılar [veri_tabani.md](veri_tabani.md) dosyasında. Bu sayfa kaynakları, nasıl alındıklarını ve kullanım koşullarıyla ilgili kararları özetler.

**Her kayıt için zorunlu alanlar** (brief kural 2-3):
- kaynak adı ve URL,
- kaynaktaki veri tarihi,
- alınma zamanı,
- çekim kaydı.

Bulunamayan değer boş bırakılır, tahmin girilmez. Kaynağa erişilemezse son başarılı çekim "bayat" etiketiyle ve kendi tarihiyle kullanılır.

## Kaynaklar

| Veri | Neden lazım | Kaynak | Nasıl alınır |
|---|---|---|---|
| Döviz kurları (USD, EUR, GBP) | Piyasa bağlamı, döviz ürünleri | TCMB `kurlar/today.xml` | Her hazırlıkta canlı |
| TÜFE, politika faizi, sektör mevduat faizleri, TLREF, fonlama maliyeti, altın, gümüş, BIST 100, eurobond stoku | Reel getiri, faiz ortamı, mevduat çapraz kontrolü, RL göstergeleri | TCMB EVDS resmi API | `evds_seriler` toplayıcısı (2021 → bugün) + anlık değerler her hazırlıkta canlı |
| Politika faizi karar tarihçesi | Güncel oran ve adım serisi | TCMB 1 hafta repo sayfası | `tcmb_politika` |
| DİBS ihale getirileri | Doğrudan tahvil alternatifi, borçlanma bağlamı | Hazine ve Maliye Bakanlığı duyuruları + resmi ihale sonucu PDF'leri | `hazine_dibs` |
| Fon fiyat geçmişi (2.068 fon, 2021 →) | Getiri, oynaklık, max düşüş, tahmini RD | TEFAS | Kullanıcının TEFAS'tan aldığı dışa aktarımlar Veri sayfasından yüklenir (aşağıya bakın) |
| TEFAS Fon Getirileri | İkinci kaynak: kendi hesapladığımız getirinin çapraz kontrolü, TEFAS'taki RD | TEFAS | Aynı yükleme alanı; tür otomatik tanınır |
| Resmi risk değeri, yönetim ücreti, komisyonlar, ISIN, valör, nitelikli yatırımcı ibaresi | Uygunluk kuralları, maliyet kriteri, likidite kısıtı | KAP fon sayfaları | `kap_fon` (kademeli, en eski kayıt önce) |
| Mevduat oranları (TL ve döviz) ve mevduat stopajı | Mevduat alternatifi, döviz mevduatı | ING mevduat hesaplayıcısı | `ing_mevduat` |
| Fon stopajı (güncel ve dönemsel) | Net getiri; kural 7: koda sabit yazılmaz | ING vergilendirme tablosu | `ing_stopaj` |
| ING platformundaki fonlar | Bilgi; `K_ING_PLATFORM` kısıtı | ING fon fiyatları sayfaları | `ing_fonlar` |
| BIST 30 hisseleri ve endeksler | Doğrudan hisse alternatifi, RL göstergesi | Yahoo Finance (resmi Borsa İstanbul kaynağı değil) | `bist`; notta teyit maddesi |
| BIST 100 benchmark, eurobond fiyat vekili, vadeli kur primi | Fon kıyası, döviz bağlamı | Hesaplama (veri tabanındaki serilerden) | `turetilmis`; "varsayım" etiketli |
| KAP bildirim geçmişi ve YBF'lerden RD/ücret tarihçesi | RL araştırması | KAP bildirim sorgusu | `tools/fetch_kap_history.py` (çevrimdışı araç) |

Eksik kalan göstergeler (ör. DİBS ikincil piyasa getirisi, TMSF limiti) "eksik" olarak işaretlenir ve skora katılmaz.

## EVDS kurulumu

1. evds3.tcmb.gov.tr adresinden ücretsiz üye olun ve profil sayfasından API anahtarını alın.
2. Anahtarı `backend/.env` dosyasına `EVDS_API_KEY=...` satırı olarak yazın. Bu dosya git'e girmez.
3. Seri kodları [evds_series.json](../backend/config/evds_series.json) ve [evds_seriler.py](../backend/app/collectors/evds_seriler.py) içinde. 15.10.2026'da EVDS `serieList` uç noktasıyla doğrulandı.

İlk tahmin edilen kodlar hatalıydı:
- `TP.FG.J0` 2026 Ocak'ta durmuştu.
- `TP.TRY.MT02` "1 ay" değil "3 aya kadar" serisiydi ve tasarruf mevduatı değil toplam mevduatı ölçüyordu.

## Kullanım koşulları (brief kural 8)

**TEFAS.** Site bot korumalıdır ve kullanım koşulları otomatik çekimi kısıtlar.
- **Uygulama TEFAS'a hiç istek atmaz.** Fiyat geçmişi kullanıcının yüklediği dışa aktarımlardan gelir.
- **Veri tabanındaki geçmişin iki kaynağı vardır:**
  - Gamze Akkurt'un sitenin "Excel'e Aktar" çıktısıyla aldığı 46 dosya,
  - `tefascrawl/` paketiyle alınan 2021-2026 dışa aktarımı.
- **`tefascrawl` hakkında:** gerçek bir tarayıcıyla (Playwright) sitenin WAF'ını geçip iç JSON API'lerine istek atar. 15.10.2026'da kullanıcının açık talimatıyla yazıldı ve çalıştırıldı.
- **Doğrulama:** iki yöntem 223.829 ortak fon-günde sıfır çelişki verdi.
- **Risk:** bu yöntem sitenin kullanım koşullarıyla çelişebilir. Riskler ve gerekçe: [tefascrawl/README.md](../tefascrawl/README.md) "Politika notu".
- **Ekip kararı:** kullanım koşulları açısından teyit edilmedikçe yalnızca "Excel'e Aktar" dışa aktarımlarıyla güncellenmelidir.

**Yahoo Finance.** Kullanım koşulları otomatik çekime izin vermez; BIST 30 hisseleri için kullanıcı kararıyla kullanılıyor ve resmi kaynak olmadığı notta yazılıdır. Resmi Borsa İstanbul verisine geçilmesi önerilir.

**KAP, ING, TCMB, Hazine.** Kamuya açık sayfalar ve uç noktalar. Toplayıcılar kibar çalışır:
- istekler sıralıdır ve aralarında bekleme vardır,
- 429 yanıtında geri çekilir,
- art arda hatada durur.

**Kullanılmayanlar.** TSPB (bot korumalı); investing.com, Fintables, Rasyonet (ticari ya da otomatik çekime kapalı).

## Çapraz kontroller

1. **TEFAS getirisi:** kendi hesapladığımız getiri, Fon Getirileri dışa aktarımındakiyle karşılaştırılır. Fark eşiği aşarsa ürün "çelişkili" işaretlenir.
2. **TEFAS fiyatı:** dışa aktarım ile TEFAS API fiyatları ortak günlerde karşılaştırılır (ikincil kaynak).
3. **Risk değeri:** KAP'taki resmi değer, TEFAS'taki değer ve oynaklıktan yapılan tahmin karşılaştırılır. 2 ve üzeri kademe farkta muhafazakâr değer alınır.
4. **Mevduat oranları:** banka oranı TCMB sektör ortalamasıyla karşılaştırılır; aykırı değer RM'e uyarı olarak düşer.
5. **Bayat veri:** son fiyat tarihi 7 günden eskiyse kayıt "bayat" işaretlenir.
