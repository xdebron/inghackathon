# 1. Problem tanımı

## Problem cümlesi

Private Banking RM'i bir müşteri görüşmesine hazırlanırken faiz, fon getirisi, kur, vergi ve ürün koşulu bilgisini farklı kaynaklardan elle topluyor. Yüzlerce ürünü müşterinin risk profili, vadesi, likidite ihtiyacı ve açık kısıtlarıyla tek tek karşılaştırıyor. Hazırladığı önerinin hangi veriye, hangi varsayıma ve hangi gerekçeye dayandığını sonradan gösteremiyor.

**Çözmek istediğimiz:** RM'in görüşme öncesi hazırlığını kısaltmak ve hazırlığın her adımını izlenebilir kılmak. Bunu yaparken kararı RM'de tutuyoruz. Çözüm öneri, gerekçe, dikkat noktaları ve taslak bir görüşme notu üretir; müşteriye hiçbir şey göndermez.

## Bugünkü durumun dört sorunu (brief 3)

| Sorun | Bugün | Çözümümüzde |
|---|---|---|
| Dağınık veri | TCMB, TEFAS, KAP ve banka sayfaları elle taranır | TCMB, EVDS, TEFAS, KAP, Hazine ve ING kaynaklarından veri tek akışta toplanır. Her değer kaynak, URL, veri tarihi, alınma zamanı ve durumla (ok / bayat / eksik / çelişkili / varsayım) taşınır. |
| Zor karşılaştırma | 2.100'ü aşkın ürün getiri, risk, vade, likidite, ücret ve vergi boyutunda elle karşılaştırılamaz | Kurallar uygunsuz ürünleri gerekçesiyle dışlar. Kalanlar persona sinyallerinden türeyen ağırlıklarla puanlanır. Kriter katkıları görünür. |
| Genel teklif | Not müşteriye uyarlanmayabilir | Not personanın amacı, ufku, likiditesi, döviz tercihi ve kısıtlarına göre kurulur. Teyit edilmesi gereken noktalar ve müşteriye sorulacak sorular ayrı listelenir. |
| Zayıf izlenebilirlik | Önerinin dayanağı geriye dönük gösterilemez | Her çalıştırma kanıt kaydı olarak saklanır: veriler, kurallar, skorlar, model çıktısı, metin kontrolü ve RM kararı. |

## Kullanıcı ve sınırlar

- **Kullanıcı:** Private Banking RM'i. Çözüm yalnızca görüşme öncesi hazırlığı destekler.
- **Girdi:** Brief'teki 5 sentetik persona ve kamuya açık piyasa ve ürün verisi. Gerçek müşteri verisi kullanılmaz. Yeni persona üretilmez.
- **Kapsam dışı:**
  - müşteriye doğrudan çıktı,
  - alım-satım,
  - getiri tahmini veya vaadi,
  - bankanın iç sistemlerine bağlantı.

## Beklenen iş etkisi

| Etki | Nasıl ölçüyoruz | Bugünkü sonuç |
|---|---|---|
| Hazırlık süresi kısalır | Uçtan uca hazırlık süresi. Veri toplama, kurallar, puanlama, model ve metin taslağı dahil. | Ortalama 4,1 sn (değerlendirme takımı, 13 senaryo). Önbellek boşken ~30 sn. |
| Uyum riski düşer | Kısa listede risk profiline veya açık kısıta aykırı ürün sayısı | 0 (13 senaryo, persona değişiklikleri dahil) |
| Uydurma veri riski kalkar | Kaynak kapalıyken o kaynaktan "alınmış" görünen değer sayısı | 0 (4 kaynak kesintisi) |
| İzlenebilirlik | Kaynağı veya veri tarihi eksik veri noktası | 0 / 982. Alınma zamanı 971'inde var; 11'i elle ölçülen valör, yalnızca ölçüm tarihi taşıyor. |
| Belirsizlik görünür olur | Eksik ya da çelişkili bilgi varsa öneri yerine RM teyit sorusu | 3/3 senaryoda soru üretildi, öneri üretilmedi |

Hazırlık süresi kısalınca RM aynı sürede daha çok müşteriyle daha hazırlıklı görüşebilir. Uyum ve izlenebilirlik kazanımı ise denetim ve şikâyet durumunda önerinin dayanağını saniyeler içinde gösterebilmek demektir.
