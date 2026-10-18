# 5. Test ve öğrenimler

"Çözümümüzün iyi çalıştığını nasıl gösterdik?" sorusunu üç düzeyde yanıtlıyoruz:

1. **Değerlendirme takımı** ([`tools/degerlendir.py`](../../backend/tools/degerlendir.py)). Brief kurallarını uçtan uca akış üzerinde ölçer. Sonuçlar arayüzde **Değerlendirme** sayfasındadır.
2. **Birim ve entegrasyon testleri.** 14 dosyada 145 test (`pytest`). Konular:
   - kurallar,
   - kısıt kataloğu,
   - veri içe aktarma güvenliği,
   - RL modeli ve açıklama araçları,
   - ajan ve guard,
   - müşteri portalı onay akışı.
3. **RL modeli için ayrı testler.** Zaman ayrımlı performans testi ve what-if makullük takımı ([docs/rl.md](../rl.md)).

## Başarı göstergeleri ve neden bunlar

| Gösterge | Hedef | Neden |
|---|---|---|
| Kural ihlali: kısa listede RD sınırını aşan ya da kuralla dışlanmış ürün, RL dağılımında kapalı kategori | 0 | Brief kural 5. Tek bir ihlal açık kural ihlalidir; ortalama bir kalite skoru bunu gizleyebilir. |
| Guard ihlali ve zorunlu uyarı ibaresi | 0 ihlal, ibare her metinde | Kural 4, 6, 9 |
| Kaynağı veya zaman damgası eksik veri noktası | 0 | Kural 2 |
| Kaynak kapalıyken uydurulmuş değer | 0 | Kural 3 |
| Eksik ya da çelişkili bilgide öneri yerine teyit sorusu | Her senaryoda | Brief 7.1 |
| Tekrarlanabilirlik: aynı girdi → aynı kısa liste ve RL dağılımı | %100 | Tutarlılık. Jüri challenge'ında davranışın açıklanabilmesi için. |
| Uçtan uca süre | Saniyeler | RM hazırlık süresine katkının vekil ölçüsü |
| RL: what-if yanlış yön tepki oranı | ~%0 | Modelin senaryo ekranında ekonomik olarak tutarlı olması |
| RL: test döneminde basit kurala göre reel kazanç | Kural ≥ | Modelin değer katıp katmadığını dürüstçe görmek |

## Test senaryoları ve sonuçlar

Son çalıştırma 18.10.2026 tarihlidir (veri tabanından, toplayıcıların güncellediği veriyle). Metin deterministik şablonla üretildi; ölçülen şey kural ve veri davranışıdır.

| Grup | Senaryo | Sonuç | Gözlem |
|---|---|---|---|
| Uçtan uca | P01–P05 hazırlık, her biri iki kez | 5/5 geçti | Her personada kısa liste 5 ürün. 1.323–1.942 ürün gerekçesiyle dışlandı. Kural ve guard ihlali 0. İki çalıştırmada liste ve RL dağılımı aynı. |
| Veri kesintisi | TCMB kurları kapalı (P05) | Geçti | Akış tamamlandı. 11 değer eksik veya bayat işaretlendi, 9 uyarı. Uydurulmuş değer 0. |
| Veri kesintisi | EVDS kapalı | Geçti | 13 değer eksik veya bayat. Göstergeler manuel kaynağa ve kendi tarihlerine düştü. |
| Veri kesintisi | TEFAS kapalı | Geçti | Fonlar evrenden çıktı; liste mevduat, DİBS ve diğer ürünlerle kuruldu. 8 eksik, 9 uyarı. |
| Veri kesintisi | ING (mevduat, stopaj) kapalı | Geçti | 11 eksik. Stopaj yokken getiriler brüt gösterildi ve teyit notu düşüldü; mevduat alternatifleri çıktı. |
| Veri kesintisi | KAP fon bilgileri kapalı | Geçti | 13 eksik veya varsayım. Resmi RD yerine oynaklıktan tahmin ('varsayım' etiketi), ücret ve valör eksik işaretlendi. |
| Eksik bilgi | P01 risk profili silindi | Geçti | Öneri üretilmedi. Soru: "Müşterinin risk profili bilgisi nedir?" |
| Çelişki | P01 risk "Agresif" yapıldı (anapara koruma Yüksek, yüksek risk yasağı var) | Geçti | Öneri üretilmedi, iki çelişki sorusu soruldu. |
| Jüri değişikliği | P03 risk Agresif → Dengeli | Geçti | RD sınırı 7 → 5. Liste yeni sınıra uyuyor, RL dağılımı değişti. |
| Jüri değişikliği | P04'e "yüksek riskli ürünlerden kaçınma" eklendi | Geçti | "Dengeli profil yüksek riske izin veriyor ama müşteri istemiyor" çelişkisi RM'e soruldu. |

**Özet:** 14/14 senaryo geçti. Kural ihlali 0, guard ihlali 0, uydurulmuş değer 0. 1.079 veri noktasının hepsinde kaynak ve veri tarihi var. 1.068'inde alınma zamanı da var; kalan 11'i TEFAS valör simülatöründen elle alınmış ölçümler, yalnızca ölçüm tarihini (10.10.2026) taşıyor ve zaman uydurulmuyor. Ortalama süre 5,3 sn; önbellek boşken ~30 sn.

**RL modeli** (test: son 3 ay, bu dönemi görmeden eğitilmiş modeller):

- **What-if makullüğü.** Makro şoklarda (faiz, TÜFE, kur, borsa) yanlış yön tepki oranı %0. Doğru yön tepki oranları:
  - faiz şoklarında %94,
  - kur şokunda %100,
  - enflasyon şoklarında ~%29. Model çoğu durumda tepki vermiyor; ters tepki vermiyor.
- **Reel kazanç.** Model P01 ve P02'de basit kuralı geçiyor: %19,4'e karşı %16,2 ve %18,9'a karşı %16,1. P03, P04, P05 ve görülmemiş profillerde geçemiyor; ayrıntı [02_analitik_cozum.md](02_analitik_cozum.md) §3.4'te.

## Hata analizleri ve yapılan iyileştirmeler

| # | Gözlenen hata | Analiz | İyileştirme |
|---|---|---|---|
| 1 | Guard, fon adındaki "Garanti" kelimesini getiri garantisi sanıp doğru metni reddetti. Benzer şekilde "PDF" gibi kaynak kısaltmaları dışlanan fon kodu sanıldı. | Yasaklı ifade ve kod taraması özel adları ayırt etmiyordu. | Kısa listedeki ve RL eşlemesindeki ürün adları muaf tutuluyor. Kısaltma istisna listesi config'te gerekçesiyle duruyor. Muaf adlar kanıt kaydına yazılıyor. |
| 2 | Ajan var olmayan ürün kimlikleriyle araç çağırdı. | Hata mesajı geçerli seçenekleri göstermiyordu; model tahmin ediyordu. | Araç hatası geçerli kimlik listesini döndürüyor. 3 ardışık hatada döngü kesiliyor ve şablon metne düşülüyor. |
| 3 | Ajan "faiz 5 puan düşerse" senaryosunda değişimi 5, yani %500, olarak gönderdi. | Araç açıklaması birimi belirtmiyordu. | Açıklamaya oran birimi örnekle yazıldı. |değişim| ≥ 1 olan oran göstergesi yüzde puana çevriliyor ve kayda not düşülüyor. |
| 4 | Müşteri sorusuna verilen taslak yanıtta "1–2 iş günü" gibi kaynağı olmayan bir süre uyduruldu. | Müşteri soru-cevap ajanı araç çağırmadan yanıt verebiliyordu. | Müşteri yanıt ajanı önce kısa liste ve profil araçlarını çağırmak zorunda. Yanıt yine RM onayından geçiyor. |
| 5 | What-if ekranında para piyasası 3 ay getirisi −%5,7 girilebiliyordu; model bunu %1,9 olarak görüyordu. | Değer tarihte hiç görülmemişti; model girdiyi ±2σ'da kırpıyordu. Tek göstergeli şok ekonomik olarak tutarsızdı. | Kaydırıcılar geçmişte görülmüş aralıkla sınırlandı. Hazır senaryolar ilgili tüm göstergelere birlikte uygulanıyor (tutarlı şok). RL ekibi v14'ü what-if tutarlılık düzenleyicisiyle eğitti; yanlış yön tepki %0. |
| 6 | RL kartı tek bir dağılımı kesin karar gibi gösteriyordu; model o dağılımı yalnızca %1–25 olasılıkla seçiyordu. | Politika düz. En olası dağılım (argmax) küçük girdi farklarına duyarlı. | Not, RL kartı ve what-if seçim olasılığını ve olasılık ağırlıklı dağılımı gösteriyor. %20 altında açık belirsizlik uyarısı var. |
| 7 | RL v9 modeli bir personada 300 farklı rastgele piyasa durumunda aynı dağılımı öneriyordu; piyasaya duyarsızdı. | Model profili ezberlemişti. | Piyasa duyarlılığı ölçümü eklendi ve arayüzde gösteriliyor. Sonraki sürümler bu ölçüyle seçildi. |
| 8 | Eşzamanlı iki TEFAS içe aktarımı fiyat geçmişini kısalttı (veri kaybı). | Yazma kilitsiz ve atomik değildi. | Kilit, yedek (son 5), atomik yazma ve "satır sayısı azalıyorsa yazma" koruması eklendi; `test_import_safety` ile sınanıyor. |
| 9 | Değerlendirme takımı KAP, stopaj ve ING mevduat değerlerinde alınma zamanı olmadığını buldu (kural 2). | İçe aktarma, toplayan script'in çekim zamanını yalnızca güne kırpıp saklıyordu. | Gerçek çekim zamanları korunuyor. Elle girilmiş kayıtta zaman yoksa alan boş kalır, uydurulmaz. Elle ölçülen valörün fon kaydının KAP zamanını miras aldığı da bu sırada bulunup düzeltildi. Sonuç: alınma zamanı olmayan yalnızca 11 elle ölçüm kaldı (yalnızca tarihli). |
| 10 | Ajan iki ayrı çalıştırmada "300 rastgele piyasada 13 farklı karar" alanını "13'ünde farklı karar" diye okudu. RM, kanıt örneğinde bunu düzenleyerek onayladı. | Araç çıktısındaki alan adı (`rastgele_300_piyasada_farkli_karar`) iki şekilde okunabiliyordu. | Araç çıktısına okuma cümlesi eklendi: "toplam N farklı dağılım önerdi; bu bir senaryo sayısı değil". |
| 11 | Veri tabanına geçişte yazılan toplayıcı testi, KAP risk değeri ayrıştırıcısının pay sınıfı ayracını ("TL 6, USD 3") ondalık sandığını gösterdi: 13 fonda RD olduğundan düşüktü (BSD, TLU: 1 yerine 6). | Virgülden sonra boşluk/harf gelse de rakam dışlanıyordu; birden çok değerde tutucu (yüksek) değer seçilemiyordu. | Ayrıştırıcı düzeltildi; ham KAP sayfaları yeniden okunup 13 kayıt güncellendi ve not aldı. Etkilenen fonların hiçbiri kısa listeye, RL eşlemesine ya da nota girmemişti. Regresyon testi eklendi. |
| 12 | KAP yenileme sırası, farklı saat dilimindeki zaman damgalarını (+03:00 ve UTC) metin olarak karşılaştırdığı için eski kayıtları atlayabiliyordu. | Zaman damgası metin karşılaştırması. | Zaman olarak karşılaştırılıyor; testi var. |

## Öğrenimler ve sınırlar

- **Kural ve LLM ayrımı doğru çıktı.** Uyum ihlali hiçbir senaryoda görülmedi. LLM'in hataları (2, 3, 4) guard, araç sınırları ve RM onayıyla müşteriye ulaşmadan yakalandı.
- **RL modeli basit kurala karşı sınırlı fayda gösteriyor.** Bunu gizlemek yerine RL'i yardımcı senaryo aracı olarak konumladık. Asıl öneri kural ve puan katmanından gelir.
- **Test dönemi kısa.** Son 3 ay ve 2 tohum küçük bir örneklem; RL performans farkları istatistiksel olarak güçlü değil.
- **Bazı veriler resmi kaynaktan değil ya da varsayım:**
  - BIST 30 fiyatları Yahoo'dan geliyor,
  - bazı fonlarda valör kategori varsayımı,
  - doğrudan DİBS için stopaj yok.

  Hepsi notta "teyit" maddesi olarak RM'e gösteriliyor.
- **TEFAS verisi yüklemeyle güncelleniyor; uygulama TEFAS'a istek atmıyor.** Mevcut geçmişin bir kısmı kullanıcı talimatıyla yazılmış ve sitenin WAF'ını geçen tarayıcı otomasyonuyla (`tefascrawl`) alındı. Bu, kullanım koşullarıyla (kural 8) çelişebilir; teyit edilene kadar güncellemeler yalnızca sitenin "Excel'e Aktar" çıktısıyla yapılmalı.
