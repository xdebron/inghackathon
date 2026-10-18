# Veri tabanı ve otomatik toplama

Tüm piyasa ve ürün verisi tek bir veri tabanındadır. Teknoloji SQLAlchemy 2.0; varsayılan SQLite (`backend/data/veri.db`), `DATABASE_URL` ile örneğin PostgreSQL'e geçilebilir.

- **Elle düzenlenen veri dosyası yoktur.** Veri iki yoldan girer:
  - otomatik **toplayıcılar** ([backend/app/collectors](../backend/app/collectors)),
  - TEFAS dışa aktarım **yüklemesi** (arayüzde Veri sayfası).
- Uygulama yalnızca veri tabanından okur ([backend/app/db/store.py](../backend/app/db/store.py)).

## Kaynak bilgisi (brief kural 2)

Her veri satırı şunları taşır:

| Alan | Anlamı |
|---|---|
| `kaynak` | Kaynağın adı (ör. "KAP Fon Bilgileri sayfası", "TCMB EVDS (TP.TRYTAS.MT01)") |
| `url` | Değerin alındığı adres |
| `as_of` | Kaynaktaki veri tarihi |
| `fetched_at` | Alınma zamanı (saat dilimiyle) |
| `cekim_id` | Satırı yazan toplama çalışması |
| `notlar` | Varsayım, dönüşüm, teyit notu |

Kaynakta olmayan alan boş (`NULL`) kalır; değer uydurulmaz. Elle ölçülmüş ve zamanı bilinmeyen bir değer yalnızca tarihini taşır.

## Tablolar

| Tablo | İçerik | Yazan |
|---|---|---|
| `cekim` | Her toplama / yükleme / aktarım çalışması: kaynak, yöntem (otomatik, yükleme, aktarım), zaman, durum, satır, hata | Tümü |
| `gosterge` | Anlık göstergeler. `evds:` ve `tcmb:` önekli satırlar canlı kaynakların son başarılı çekimidir; canlı çekim başarısız olursa "bayat" etiketiyle kullanılır. | `tcmb_politika`, `evds_seriler`, canlı kaynaklar |
| `seri` + `seri_deger` | Zaman serileri (tarih, kolon, değer) ve meta (seri kodu, birim, sıklık, dönüşüm) | `evds_seriler`, `hazine_dibs`, `bist`, `turetilmis`, `tcmb_politika` |
| `mevduat` | Banka mevduat oranları (TL ve döviz, vade, tutar kademesi, kaynağın stopaj hesabı) | `ing_mevduat` |
| `stopaj`, `stopaj_donem` | Güncel fon stopajı ve dönemlere göre tarihçe | `ing_stopaj`, `ing_mevduat` |
| `fon_bilgi` | KAP fon bilgileri: resmi RD, ücret, komisyon, ISIN, valör, nitelikli yatırımcı | `kap_fon` |
| `fon_fiyat` | TEFAS günlük fiyat, pay, yatırımcı, büyüklük (dışa aktarım ve API ayrı kaynak) | TEFAS yüklemesi |
| `fon_getiri` | TEFAS Fon Getirileri (dönemsel getiri; ikinci kaynak, çapraz kontrol) | TEFAS yüklemesi |
| `benchmark` | Karşılaştırma ölçütleri (BIST 100) | `turetilmis` |
| `platform_fon` | ING platformunda sunulan fonlar | `ing_fonlar` |
| `belge` | Kaynak düzeyinde yapılandırılmış bilgi: BIST 30 bileşimi, TEFAS içe aktarım günlüğü, kalite raporu | Çeşitli |
| `ham_tablo` | Çevrimdışı araçların tabloları: DİBS ihale listesi, KAP bildirim geçmişi | `hazine_dibs`, `tools/fetch_kap_history.py` |

## Toplayıcılar

Hepsini çalıştırmak için:

```bash
cd backend
.venv/Scripts/python tools/veri_topla.py
```

Arayüzde aynı işlem **Veri → Hepsini topla** düğmesiyle yapılır. `--liste` son çalıştırmaları gösterir.

| Toplayıcı | Kaynak | Not |
|---|---|---|
| `evds_seriler` | TCMB EVDS resmi API | `EVDS_API_KEY` gerekli; 2 yıllık parçalar |
| `tcmb_politika` | TCMB 1 hafta repo sayfası | Güncel oran + karar tarihçesi |
| `hazine_dibs` | HMB duyuru API'si + ihale sonucu PDF'leri | PDF'ler kanıt olarak `data/raw/hmb_ihale` altında |
| `bist` | Yahoo Finance chart API | Resmi kaynak değil; bileşim teyit edilmedi (notta) |
| `turetilmis` | Veri tabanındaki seriler | Hesaplama; istek atmaz, "varsayım" etiketli |
| `ing_mevduat` | ING mevduat hesaplayıcısı uç noktası | TL (1 mn TL kademesi) ve döviz |
| `ing_stopaj` | ING vergilendirme sayfası | Kategori ↔ satır eşlemesi yorumdur, RM teyit eder |
| `ing_fonlar` | ING fon fiyatları sayfaları | Ad eşleşmesiyle kod; eşleşmeyen ad listeye girmez |
| `kap_fon` | KAP fon sayfaları | Kademeli: en eski kayıt önce, istekler arası 2,5 sn, tek çalıştırmada en fazla 300 fon |

**Kibar çalışma.**
- İstekler sıralıdır ve aralarında bekleme vardır.
- 429 yanıtında sunucunun istediği kadar beklenir.
- Art arda hatada toplayıcı durur, o ana kadar yazılanlar kalır.
- Hiçbir toplayıcı bot korumasını aşmaya çalışmaz; uygulama TEFAS'a istek atmaz, TEFAS verisi yüklemeyle gelir (mevcut geçmişin nasıl alındığı ve kullanım koşulları notu: [veri_kaynaklari.md](veri_kaynaklari.md)).

## Kurulum, tohum ve geçiş

| Araç | Ne yapar |
|---|---|
| `tools/veri_kur.py` | Temiz kurulumda veri tabanını repodaki tohumdan (`data/seed/veri_tohum.db.gz`, Git LFS) açar. `--topla` ardından toplayıcıları çalıştırır, `--sifirla` mevcut veri tabanını yedekleyip yeniden kurar. |
| `tools/veri_tohum.py` | Güncel veri tabanından tohumu yeniden üretir. Tohum, veri tabanının dökümüdür; elle düzenlenen bir dosya değildir. |
| `tools/db_aktar.py` | Tek seferlik geçiş: eski snapshot dosyalarını (JSON/CSV) kaynak alanlarıyla veri tabanına aktardı. Geçiş tamamlandı, dosyalar repodan kaldırıldı. |

## Geçişte bulunan iki hata

1. **KAP risk değeri ayrıştırıcısı** pay sınıfına göre verilen değerlerde ("TL 6, USD 3") virgülden sonraki ayracı ondalık sandı ve yalnızca düşük değeri aldı.
   - Etki: 13 fonda resmi RD olduğundan düşük görünüyordu, örneğin BSD ve TLU'da 1 yerine 6.
   - Bu fonlar hiçbir kısa listeye, RL eşlemesine ya da nota girmemişti; yalnızca bazı Dengeli/Agresif profillerin "uygun ama listede değil" tablosunda görünüyorlardı.
   - Düzeltme: ayrıştırıcı düzeltildi, ham KAP sayfaları yeniden okunarak 13 kayıt güncellendi ve her biri not aldı. Regresyon testi: `tests/test_collectors.py`.
2. **KAP yenileme sırası** zaman damgalarını metin olarak karşılaştırıyordu (+03:00 ile UTC). Bu yüzden "eski kayıt" seçimi yanlış olabiliyordu. Artık zaman olarak karşılaştırılıyor; testi var.
