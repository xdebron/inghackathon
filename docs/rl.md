# Dinamik Dağılım: Pekiştirmeli Öğrenme

Sistemde iki RL katmanı var:

1. **Kategori modeli (profile koşullu DQN, çevrimdışı eğitim).** Ana katman; aşağıda ilk bölüm.
2. **Ürün düzeyi RL (çalıştırma başına eğitim).** Kısa listedeki ürünler üzerinde DQN ve tablo tabanlı Q. Kategori modeli yoksa metinde bu kullanılır; ikinci bölüm.

## 1. Kategori modeli: profile koşullu DQN

Kod: [backend/app/rl_category.py](../backend/app/rl_category.py). Eğitim: [backend/tools/train_rl.py](../backend/tools/train_rl.py). Testler: [backend/tests/test_rl_category.py](../backend/tests/test_rl_category.py). Parametreler: `decision_config.json` → `rl_kategori`.

- **Neden kategori düzeyi:** Her profilin kısa listesi farklı. Binlerce profilde tek bir modelin öğrenebilmesi ortak bir aksiyon uzayı gerektiriyor. Aksiyon, 10 varlık kategorisine %25 adımlı ağırlık (625 aksiyon, fon kategorisi başına en fazla %50). Çıkarımda her kategori personanın kısa listesindeki (yoksa uygun listesindeki) en yüksek skorlu ürüne eşlenir.
- **Kategori endeksleri:** TEFAS fiyat geçmişindeki ~2000 fonun günlük getirilerinin kategori bazında medyanı (aykırı değere dayanıklı). Mevduat: TCMB 3 aya kadar vadeli tasarruf mevduatı faizinin tarihsel serisi; başlangıçta kilitlenen oranla basit faiz.
- **Stopaj:** fon kategorilerinde dönemine göre (ING tablosu tarihçesi: para piyasası 2023'te %0 → 2025 sonrası %17,5). Mevduat, yabancı hisse ve eurobond için tarihsel oran yok; güncel oran uygulanır ve not edilir.
- **25 gösterge, hepsi karar anında bilinen ve gecikmeli:**
  - 9 fon kategorisinin 3 aylık getirisi ve oynaklığı.
  - USD/TRY 3 aylık değişimi (1 gün gecikmeli).
  - TÜFE 3 aylık eğilimi ve yıllık değeri (35 gün gecikmeli).
  - 1 aylık mevduat faizi ve 3 aylık değişimi (7 gün gecikmeli).
  - Politika faizi (45 gün gecikmeli).
  - Reel mevduat faizi.
- **Profiller:** brief kombinasyonları (aşağıda; [data/synthetic/profil_kombinasyonlari.json](../backend/data/synthetic/profil_kombinasyonlari.json)). Brief personalarındaki risk, ufuk, anapara koruması, likidite, döviz tercihi değerleri ve açık kısıtların çelişkisiz kombinasyonları; sayılar test edildi. Bunlar **müşteri ya da persona değildir**, ek müşteri verisi içermez (brief 7.1). Yalnızca politikanın profil parametrelerine göre genelleşmesi için kullanılır. Öneri yalnızca verilen 5 persona için üretilir. 2.160 profil eğitimde, 240 profil hiç görülmeden genelleme testinde kullanılır.
- **Maskeler (sert kısıt):**
  - Kategori RD'si (üye fonların resmi KAP RD medyanı) profil sınırını aşarsa kategori kapanır.
  - Yüksek risk yasağında RD ≥ 5 ve hisse kapanır.
  - Kilit/likidite yasağında 3 aylık mevduat ve valörü 2 günden uzun kategoriler kapanır.
  - Döviz artırmama yasağında döviz/altın kategorileri kapanır.
  - Döviz tercihine göre toplam döviz tavanı: Sınırlı %25, Kontrollü %50.
  - Yoğunlaşma kısıtında her kategori en fazla %50.
- **Eğitim (GPU, [backend/app/rl_gpu.py](../backend/app/rl_gpu.py)):**
  - PyTorch 2.11 + CUDA 12.8, RTX 5090.
  - Veri, tampon (2 milyon geçiş), ortam adımları ve hedef hesabı GPU'da.
  - 4096 paralel bölüm. Batch 8192 örnek; her örnekte alınan aksiyon + 15 rastgele izinli aksiyonun hedefi hesaplanır.
  - Ağ: MLP 2×256, Adam, Huber kaybı, gradyan kırpma, hedef ağ.
  - 400.000 güncelleme, yaklaşık 50 dakika (GPU kullanımı ~%80). Her 5.000 güncellemede doğrulama dönemi ödülüyle en iyi model saklanır.
  - Ağırlıklar numpy biçiminde kaydedilir; çevrimiçi çıkarım torch gerektirmez.
  - Aynı mantığın CPU/numpy sürümü `rl_category.train` testlerde ve GPU yokken kullanılır (`train_rl.py --device cpu`).
- **Kategori tespiti düzeltmeleri (eğitimden önce):**
  - Katılım hisse senedi fonları → hisse.
  - Çoklu varlık katılım fonları → karma/değişken.
  - Adında "yabancı" geçen fonlar (yabancı BYF fon sepetleri dahil) → yabancı hisse, yani döviz.
  - Altın fon sepetleri → kıymetli maden.

  Böylece döviz ve altın maruziyeti, kategori adına bakılarak gözden kaçmıyor.
- **Çıkarım:** pipeline modeli yükler, bugünkü göstergelerle anında karar verir (eğitim yok). Dağılım dışı göstergeleri ve ±1σ duyarlılığı raporlar.
- **Değerlendirme (test dönemi):** eğitim profilleri, görülmemiş profiller ve 5 persona üzerinde, üç sabit kıyas stratejisiyle aynı senaryolarda. Kıyaslar: izinli kategorilere eşit ağırlık, en düşük oynaklık, para piyasası + mevduat/borçlanma.

### Eğitim profilleri: brief kombinasyonları (10 bin rastgele yerine 2.400 eksiksiz)

İlk sürüm 10.000 rastgele profil örnekliyordu. Bunun iki sorunu vardı:
- RL açısından farklı profil sayısı zaten sınırlı; aynı profiller defalarca tekrarlanıyordu.
- Brief personalarında geçmeyen ufuklar (0-1, 3-5 yıl) da kullanılıyordu.

Şimdi brief'teki 5 personanın alan değerleri ve açık kısıtları eksiksiz listeleniyor:

| | Sayı |
|---|---|
| Ham kombinasyon (4 risk × 2 ufuk × 3 likidite × 3 gelir × 3 anapara × 4 döviz × 2⁵ kısıt alt kümesi) | 27.648 |
| Persona yorumlayıcısından (çelişki kuralları) geçen | 11.520 |
| RL açısından farklı (gelir ihtiyacı ve "kısa vadeli nakit" kısıtı yalnızca skorlamayı etkiler) | **2.400** |

Bunların 2.160'ı eğitimde kullanılır, 240'ı hiç görülmeden genelleme testine ayrılır. Yeni değer uydurulmaz.

### Aksiyon uzayı: %10 adım

- %10 adımlı tam ızgara 85.943 aksiyon eder. DQN hedef hesabı için GPU belleğine sığmaz.
- İnce ızgarada bir dağılım **en fazla 3 kategori** içerir: 2.458 aksiyon.
- Önceki %25 adımlı 625 dağılım (4 kategoriye kadar) korunur.
- Toplam **3.037 aksiyon**; yeni küme eskisini kapsar.
- Fon kategorisi başına en fazla %50.

### Kararın uygulanması: kategori → fon

1. **Fon seçimi:** model kategori ağırlığına karar verir, kategori içindeki fonu kural ve skor motoru seçer. Adaylar sırasıyla şunlardır:
   - önce kısa listedekiler,
   - sonra uygun listedekiler,
   - her grup kendi içinde deterministik skora göre.
2. **Takip farkı:** model eğitimde kategori endeksini (medyan fon) gördüğü için, aday fonun son 1 yılda bu endeksten yıllık sapması ölçülür.
   - Eşik: para piyasası, mevduat, borçlanma ve katılımda %3; oynak kategorilerde %12.
   - Eşiği aşan fon ancak başka aday yoksa seçilir; RM'e uyarı düşer.
   - Ekranda her fonun takip farkı görünür.
3. **Bölme:** %30 ve üzeri kategori payı tek fona yığılmasın diye en iyi 2 fona eşit bölünür.
4. **Mevcut portföy:** RM, müşterinin mevcut dağılımını kategori bazında girer. Model bunu başlangıç durumu olarak kullanır: devir maliyeti ödüle girer, model mevcut pozisyondan çıkmaya değip değmediğine karar verir. Ekranda önerilen dağılıma geçişin devir oranı gösterilir.
   - Girilmezse "nakitten başlangıç" varsayılır ve not düşülür.
   - Eğitimde bölümlerin yarısı rastgele bir mevcut portföyle başlar. Bu portföy izinsiz kategoriler de içerebilir; model o durumdan çıkışı da öğrenir.
5. **İşlem yok:** sistem hiçbir aşamada işlem yapmaz. Yüzdeler RM'e öneridir; tutarı RM görüşmede belirler.

### Hata analizi ve iyileştirme: göstergelere aşırı uyum

**Gözlem.** İlk GPU koşusunda (ham göstergeler) doğrulama ödülü sürekli düştü (0,57 → 0,22). Doğrulama döneminde anaparanın altında bitme olasılığı %0,9'dan %20'ye çıktı. En iyi model 5.000. güncellemede kaldı.

**Teşhis.** Ayrı bir teşhis koşusu, her 5.000 güncellemede açgözlü politikayı ölçtü:

| Ölçüt | 5 bin | 40 bin |
|---|---|---|
| Eğitim dönemi ödülü | 0,386 | 0,428 (iyileşiyor) |
| Eğitim dönemi anapara altı | %3,0 | %2,9 (sabit) |
| Q tahmini / gerçekleşen (eğitim) | 0,08 / 0,39 | 0,35 / 0,43 (aşırı tahmin yok) |
| **Doğrulama ödülü** | 0,532 | **0,257** |
| **Doğrulama anapara altı** | %0,8 | **%20,7** |
| Q tahmini / gerçekleşen (doğrulama) | 0,09 / 0,53 | **0,40 / 0,27 (aşırı tahmin)** |

Mekanik bir hata yok: eğitim verisinde öğrenme sağlıklı. Bozulma yalnızca doğrulama döneminin piyasa durumlarında oluşuyor.
- Eğitim başlangıçları yaklaşık 1,5 yıl (üst üste binen pencerelerle ~6 bağımsız çeyrek). Ağ "bu piyasa durumunda şunu seç" kalıplarını ezberliyor.
- 2025'in dezenflasyon ve faiz düşüşü rejiminde bu kalıplar yanlış kategoriye kaydırıyor.

**Ablasyon ve düzeltme** (40 bin güncelleme, aynı tohum):

| Varyant | Eğitim dönemi ödülü | Doğrulama ödülü | Doğrulama anapara altı |
|---|---|---|---|
| Ham göstergeler | 0,43 | 0,26 ↓ | %21 ↑ |
| Göstergesiz | 0,22 | 0,53 (sabit) | %0,7 |
| **Kırpma ±2σ + gürültü 0,5σ + AdamW 1e-4** | **0,43** | **0,57** (zirve 0,63) | **%0,5** |

Seçilen düzeltme son satır. Göstergelerin faydası korunuyor (eğitim dönemi ödülü göstergesize göre iki kat), aşırı uyum engelleniyor. Kırpma çıkarımda da aynen uygulanıyor.

### BIST 30: gösterge ve doğrudan hisse kategorisi (v2)

- **Gösterge:** v2 setine `BIST 30 endeksi 3 ay` eklendi. XU030 fiyat endeksinin (temettü hariç) 3 aylık değişimi, 1 gün gecikmeli. v2 artık 30 gösterge içeriyor.
- **Kategori `bist30_hisse`:** Doğrudan hisse senedi (fon değil). Kategori endeksi, 30 hissenin günlük toplam getirilerinin eşit ağırlıklı ortalaması. Getiriler düzeltilmiş kapanıştan hesaplanıyor, yani temettü ve bölünme dahil. Gün içinde 20'den az hissenin verisi varsa o gün eksik sayılıyor. Risk değeri endeks oynaklığından tahmin ediliyor (şu an RD 7). Bu yüzden kategoriyi yalnızca RD sınırı 7 olan profiller seçebiliyor. "Yüksek riskli ürün yok" kısıtı bu kategoriyi hisse fonu gibi kapatıyor.
- **Stopaj:** %0 varsayıldı (yerleşik gerçek kişinin BIST hisse alım-satım kazancı). Temettü stopajı ayrıca kesiliyor; RM teyit etmeli (`rl_kategori.bist30`).
- **Ürün eşlemesi:** Kategori payı en yüksek skorlu 5 hisseye eşit bölünüyor (örneğin %50 → 5 × %10). Takip farkı tek hisse için değil, seçilen 5 hisselik sepetin BIST 30 eşit ağırlıklı sepetinden sapması olarak ölçülüyor (eşik %12). Smoke testte bu sapma %16 çıktı ve RM'e uyarı düştü.
- **Ürün evreni:** 30 hisse `HISSE:<KOD>` olarak deterministik hatta da var. 1y/3a/1a getiri, volatilite ve maks. düşüş düzeltilmiş kapanıştan hesaplanıyor. RD tahmini, yönetim ücreti 0, valör T+2 ve stopaj alanları varsayım olarak işaretli. Kısa listeye girerlerse "tek şirket riski, resmi olmayan kaynak" notu ekleniyor.
- **Kaynak sınırları:** Fiyatlar Yahoo Finance'ten geliyor, resmi Borsa İstanbul kaynağı değil. Bileşim teyit edilmedi (DSTKF çıkışı ve Temmuz duyurusuyla çelişki var). Güncel bileşim 2021'e geriye uygulandığı için hayatta kalma yanlılığı var; geçmiş getiri olduğundan iyi görünebilir. Bu notlar veri seti notlarında ve RM uyarılarında yer alıyor.
- **Sürüm uyumu (BIST 30):** Kategori listesi gösterge setine bağlı: v1 10 kategori (canlı eski model), v2 ise BIST 30 ile 11 kategori. Çıkarım modelin `meta.kategoriler` listesini kullanıyor; eski model değişmeden çalışıyor. Aksiyon sayısı 11 kategoriyle 4.171'e çıktı.

### Beklenen getiri inanç başlığı

- **Yapı:** Q ağının ortak gövdesine (2 × 256 gizli katman) ikinci bir çıkış eklendi. Her kategori için sonraki 3 aylık adımın vergi sonrası reel getirisinin ortalamasını ve log standart sapmasını tahmin ediyor. Kayıp Gauss negatif log-olabilirlik; Q kaybına 0,5 ağırlıkla ekleniyor (`gpu.inanc_agirligi`).
- **Hedef:** O durumdan başlayan adımın gerçekleşen reel getirisi (ödülü hesaplayan veri). Göstergeler karar anında bilinen gecikmeli değerler, yani geleceğe bakma yok.
- **Neden:** (1) Gövdeyi getiri yapısını temsil etmeye zorlayan yardımcı görev. (2) RM'e modelin "piyasadan ne beklediğini" gösteriyor: karar ile beklenti arasındaki bağ okunabiliyor. Örneğin beklentiyi düşük olan bir kategoriye yine de pay veriliyorsa nedeni çeşitlendirme ya da anapara cezasıdır.
- **Kalibrasyon:** Eğitim sonunda doğrulama ve test havuzlarında kategori başına raporlanıyor (`meta.inanc_kalibrasyon`): korelasyon, RMSE, saf tahmin (eğitim ortalaması) RMSE'si, yön isabeti ve ±1σ kapsama. Pencereler örtüştüğü için bağımsız gözlem sayısı küçük; yüksek korelasyonlar iyimser okunmamalı. Smoke testte (4 bin güncelleme, eksik veri) mevduat ve para piyasası gibi sözleşme faizine bağlı kategorilerde hata saf tahminin altında çıktı. Eurobond'da ise saf tahminden kötüydü ve kapsama oynak kategorilerde düşük kaldı (std'ler dar).
- **Çıkarım:** numpy; `Wb/bb` anahtarları. Eski model (inanç başlığı yok) için alan `null` dönüyor.

### Açıklanabilirlik: EBM vekil modeli

DQN'in kararı ağırlıklardan okunamaz. Bu yüzden karar, cam kutu bir modele damıtılıyor (`tools/explain_rl.py`, interpret 0.7):

1. **Durum örnekleri:** Tüm geçerli karar tarihleri × 2.400 brief profil kombinasyonu ve 5 persona; nakitten başlangıç, yani görüşme anı. Her örnekte DQN'in maskeli kararı (kategori ağırlıkları) ve inanç başlığının beklentisi kaydediliyor.
2. **Vekil:** Kategori başına bir Explainable Boosting Machine, ağırlık ≈ sabit + Σ f(değişken) + 10 ikili etkileşim f(xᵢ, xⱼ). Değişkenler ham piyasa göstergeleri ve okunur profil alanları (maks. RD, ufuk, anapara, likidite, döviz tavanı, 4 kısıt). Risk seviyesi maks. RD ile aynı bilgiyi taşıdığı için çıkarıldı. İnanç başlığının beklentisine de yalnızca piyasa göstergeleriyle ayrı bir EBM uyduruluyor.
3. **Sadakat:** Ayrılmış %20 durumda kategori başına R², "en büyük kategori" uyumu ve ortalama dağılım farkı ölçülüyor. R² 0,5'in altındaki kategorilerde açıklama "düşük sadakat, yaklaşık" olarak işaretleniyor. Smoke modelde etkileşimsiz EBM'in ortalama R²'si 0,34'tü; 10 etkileşimle 0,66'ya çıktı (dağılım farkı 0,32 → 0,20). Politika büyük ölçüde profil × piyasa etkileşimine dayanıyor.
4. **Çevrimiçi kullanım:** Tablolar bağımlılıksız JSON'da (kesim noktaları + bin skorları); interpret'in kendi tahminiyle birebir doğrulanıyor. Yerel açıklamada etkileşim teriminin katkısı iki değişkene yarı yarıya paylaştırılıyor. Böylece eş doğrusal göstergelerin birbirini götüren büyük çift katkıları RM'e tek tek gösterilmiyor, toplam korunuyor. Açıklama başka bir model için üretilmişse (`model_olusturma` uyuşmazsa) kullanılmıyor.
5. **Arayüz:** Kararın yanında kategori başına katkı çubukları ("kilit kısıtı yok +20 puan, reel mevduat faizi +7 puan") ve beklentinin kaynağı gösteriliyor. Model sayfasında kategori başına değişken önemleri ve tıklanınca şekil fonksiyonu var.

Vekil, DQN'in kendisi değildir; tarif eder, karar vermez. Karar her zaman DQN'den gelir, sadakat her açıklamanın yanında gösterilir.

Başka seçenekler ve neden seçilmedikleri:
- **Politikayı doğrudan toplamsal yapmak** (Q = Σ f): Açıklama birebir olurdu, ama profil × piyasa etkileşimini kaybeder.
- **SHAP / integrated gradients:** Yerel; global şekil fonksiyonu vermez, RM'e "genel mantık" anlatmaz.
- **±1σ duyarlılık:** Zaten var; hangi göstergenin kararı çevirdiğini söyler, ne kadar katkı yaptığını söylemez.

### Deney serisi v2–v6, GBRL ve NN-PPO (Ekim 2026, TEFAS 2021+ verisi)

Her model aynı veride ve aynı senaryo izlerinde ölçüldü. Senaryolar `rl_category.evaluate` ile ve sabit tohumla üretildi, dış ortam için `tools/export_rl_env.py` ile aktarıldı. Test dönemi hiçbir modelin eğitiminde ya da seçiminde kullanılmadı.

**Deneyler ve teşhisler**

| Sürüm | Değişiklik | Teşhis |
|---|---|---|
| v2 | Tüm veri (2021-10+), reel anapara cezası | 2022–23'te reel faiz negatif; ceza neredeyse her adımda kesildi. Q değerleri şişti, politika TL'den kaçmayı öğrendi. |
| v3 | Nominal ceza, adım cezası tavanı 0,25 | Ödül ölçeği düzeldi, doğrulama yine salındı; kullanıcı durdurdu. |
| v4 | Nominal ceza, 2024+ pencereleri | Sabit kuralı geçti (2025-07 testi), canlı v1'in gerisinde kaldı. Kullanıcı kararıyla canlıya alındı. |
| v5 | Reel ceza, 2025+, öğrenme oranı 1e-4, test 2026-01 | En iyi nokta 5 bininci güncellemede; sonrası bozuldu. Durduruldu. |
| GBRL / NN-PPO | WSL'de PPO (`tools/gbrl/`), 96 ve 4.171 aksiyon | Uzun bölümlerde ağaç (GBRL) sinir ağından iyi genelleşti; sinir ağı P03'te %50 altına gidip −%68 kaybetti. |
| v6 düzeltmeleri | Aşağıdaki üç düzeltme | Tüm modeller yeniden ölçüldü. |

**v6 düzeltmeleri**
1. **Değerlendirme bölümleri en fazla 4 çeyrek** (`degerlendirme_max_ceyrek`). 20–40 çeyreklik bölümler 9 aylık test penceresini ~10 kez tekrar örnekleyip tek rejimi bileşikliyordu (P03 −%68, GBRL %172). Eğitim bölümleri tam ufukta.
2. **Oynak kategorilerde %40 tavan** (`kategori_max_agirlik`). Düşük oynaklıklılar %50, mevduat sınırsız (`category_caps`). Her profil uygulanabilir kalıyor; bunu bir test doğruluyor.
3. **En kötü dönem seçimi** (`secim_olcutu`). Kontrol noktası, doğrulama döneminin iki yarısının en kötüsüne göre seçiliyor.

**Sonuç: 2026-01 → 2026-10 testi, 4 çeyrek, görülmemiş 240 profil**

| Model | Reel kazanç | Kötü %5 | Reel anapara altı |
|---|---|---|---|
| Sabit kural (%50 PP + %50 mevduat) | %9,7 | %4,0 | %0,4 |
| GBRL v6 (ağaç, PPO, 237 aksiyon) | %9,75 | %3,3 | %1,0 |
| Canlı v1 (DQN) | %9,7 | %2,1 | %3,9 |
| NN-PPO v6 (sinir ağı, PPO, 237 aksiyon) | %9,3 | %0,1 | %4,9 |
| v4 (DQN, şu an canlı) / kapılı | %8,6 / %8,9 | %0,5 | %4,5 |
| DQN v6 / kapılı | %7,6 / %8,2 | −%4,9 | %7,7 |

Personalarda sabit kural P01–P04'te %11,0, P05'te %7,6 veriyor. P03 (agresif) hariç tüm modeller kurala yakın:

| P03 | GBRL v6 | v1 | NN-PPO v6 | v4 | DQN v6 |
|---|---|---|---|---|---|
| Reel kazanç | %12,2 | %0,9 | −%3,1 | −%8,8 | −%9,9 |

**Bulgular**
- **Kuralı güvenilir biçimde geçen RL modeli yok.** 2025–26 yüksek pozitif reel faiz rejiminde mevduat ve para piyasası her çeyrek güvenli getiri sağlıyor; riskli kategorilerin çoğu 2026'da negatif. Test penceresi yaklaşık 3 bağımsız çeyrek.
- **GBRL fiilen kuralı öğrendi.** P03 ve P04'te ilk kararı kuralla aynı.
- **Sinir ağlı modeller riskli tarafa kayıyor.** Bu, hem DQN hem PPO için geçerli; anapara altı bitiş %4–8.
- **Sinir ağlı DQN'in en iyi noktası hep eğitimin başında** (v5'te 5 bin, v6'da 10 bininci güncelleme). Az veriyle sonrası aşırı uyum.
- **Simülatör kopyası doğrulandı:** Sabit kural Windows'ta ve WSL'de aynı ortalamayı ve kötü %5'i verdi (%9,7, %4,0). Reel anapara altında küçük bir fark var (%0,4'e karşı %0,0), açıklanmadı.
- **GBRL'in sınırı:** Yalnızca 512 aksiyona kadar çalışıyor; ızgara buna göre küçültüldü (`--izgara 10:2+4:3`).

### Persona odaklı NN-PPO, risk duyarlı ödül ve erken durdurmasız HPO (v7)

**Kurallar**
- Düşük oynaklıklı kategoriler (para piyasası, borçlanma, katılım) artık %100'e kadar çıkabiliyor (`dusuk_oynaklik_max_agirlik`). Fon başına %50 kuralı korunuyor: eşleme, payı en az ceil(pay/%50) fona bölüyor; bunu bir test doğruluyor.
- Eğitim bölümlerinin %30'u persona profilleriyle başlıyor.

**Persona bazında kuralı geçme olanağı** (eğitim ve doğrulama verisiyle, test kullanılmadan)

| Persona | Kuralı geçen meşru seçenek | Not |
|---|---|---|
| P01, P02, P03 | %100 mevduat | Her dönemde kuraldan yüksek |
| P05 | %100 para piyasası | Kilit kısıtı mevduatı dışlıyor |
| P04 | yok | Yoğunlaşma kısıtı (kategori ≤ %50) altında kural (%50 mevduat + %50 PP) izinli en iyi dağılım. Diğer izinli kategoriler hiçbir dönemde PP'yi geçmiyor. |

**Risk duyarlı ödül denemesi** (−γ_profil/2 · wᵀΣw, Σ testten önceki nominal kovaryans, γ_temel 4 ve 10 önceden belirlendi): P03'ü düzeltmedi. Testten önceki veride altının getiri üstünlüğü çeyreklik +1,7 puan; bunu dengelemek için γ > 45 gerekiyor. Reel bakışta ise altın varyansı azaltıyor (2022–23'te mevduat reel olarak daha çok eridi). Ceza mevduatı da cezalandırdığı için P01, P02 ve P04'ü kötüleştirdi.

**Erken durdurmasız HPO** (`tools/gbrl/hpo_nnppo.py`)
- Optuna TPE; 12 ayar × 3 tohum.
- Her koşu sıfırdan eğitilip son ağırlıklarla değerlendiriliyor; ara kontrol noktası seçimi yok.
- Seçim yalnızca doğrulamaya dayanıyor: persona farkı, kayıplar 3 kat cezalı, tohumlar arası std cezası.
- Kazanan ayar (#08): öğrenme oranı 3,6e-5, entropi 0,004, epoch 2, gizli katman 256, indirim 0,967, rollout 8, minibatch 4.096, persona oranı 0,31, 6M adım.

**Kazanan ayarın test sonucu** (bir kez okundu; 2026, 4 çeyrek, kurala göre fark, puan)

| | P01 | P02 | P03 | P04 | P05 |
|---|---|---|---|---|---|
| 3 tohum ortalaması | +2,59 | +2,59 | −16,32 | 0,00 | +0,61 |
| En kötü tohum | +2,59 | +2,59 | −16,58 | 0,00 | +0,44 |

P03'teki kayıp, testten önceki tüm veride en iyi görünen altının (%40) 2026'da çeyreklik −%10 düşmesinden geliyor.

**Son model:** `models/nnppo_son_tumveri/model_nnppo.zip`. Aynı ayarlarla tüm veride (2025-01 → 2026-07 başlangıçlı 303 pencere) eğitildi. 2026 düşüşünü gördüğü için P03'e %100 mevduat veriyor; P01 ve P02'ye %100 mevduat, P05'e %100 PP, P04'e kuralın kendisini. Bu modelin test dönemi sonuçları örneklem içidir, performans tahmini değildir. Performans tahmini yukarıdaki tablodur.

### Profilin tamamı state'te ve ödülde (v8)

**State:** Profil vektörü sürüm 2'de 21 boyutlu (`profile_vector(p, 2)`): eski 10 alan + gelir/nakit ihtiyacı + "kısa vadeli nakit" kısıtı + 8 kriter ağırlığı + çeşitlendirme.

**Kriter ağırlıkları:** Deterministik skorlamanın `weights()` fonksiyonundan geliyor (risk, ufuk, likidite, gelir, anapara, öncelikler, ağırlık kısıtları); yatırım amacı metni de aynı öncelik kalıplarından geçiyor. Çeşitlendirme RL'e özel: brief önceliği, skorlamada karşılığı yok.

**Ödül:** r = reel getiri − Σ c_k·w_k·ceza_k.

| Kriter | Ceza |
|---|---|
| Düşük oynaklık | wᵀΣw (Σ: testten önceki nominal kovaryans) |
| Likidite | valör/vade ölçeği |
| Gelir | 1 − gelir üretimi |
| Sadelik | 1 − yapı sadeliği |
| Çeşitlendirme | Σw² |
| Döviz | döviz payı − ½·döviz tavanı (yalnızca aşım) |
| Maliyet | devir maliyeti çarpanı |

Katsayılar deneyden önce belirlendi (`rl_kategori.profil_odulu`); anapara cezası korunuyor.

**Profiller:** Brief alan değerleri × kısıt alt kümeleri × 5 personanın (öncelikler, amaç) demeti = 57.600 çelişkisiz, birbirinden farklı profil.

**Test (2026, 4 çeyrek; HPO'nun en iyi ayarları; 3 tohum ortalaması, parantezde en kötü tohum; kurala göre fark, puan)**

| | P01 | P02 | P03 | P04 | P05 |
|---|---|---|---|---|---|
| Reel kazanç | +2,59 (+2,59) | +2,59 (+2,59) | −16,21 | −0,15 (−0,45) | +0,50 (+0,29) |
| Profil ödülü | +3,23 (+3,23) | +1,43 (+1,43) | −16,57 | +0,02 (0,00) | +0,87 (+0,34) |

- **P04:** Bir tohum %50 mevduat / %25 PP / %25 borçlanma seçti. Profil ödülü biraz arttı (çeşitlendirme), reel getiri azaldı.
- **P03:** Testten önceki veride altın hep en iyi göründüğü için hâlâ %40 altın tutuyor.

**Son model:** `models/nnppo_v8_tumveri/model_nnppo.zip`, tüm veride eğitildi. Kararları: P01–P03 %100 mevduat; P04 %50 mevduat / %25 PP / %25 borçlanma; P05 %80 PP / %20 katılım.

### Kısıt kataloğu (v9, canlı)

Brief'teki 5 kısıta ek olarak 18 katalog kısıtı eklendi (`config/constraint_rules.json`, toplam 23 kural). Katalog persona üretmez; RM'in seçebileceği kısıtlardır. Her kuralda dört alan var:
- **`anahtarlar`:** Metinden eşleşme.
- **`parametre`:** Deterministik süzgeç.
- **`rl`:** RL maskesindeki karşılık.
- **`ornek`:** Arayüzdeki seçilebilir metin.

Oran ("%30"), sayı ("en az üç") ve vade ("1 ay") metinden okunur (`profile.rule_params`).

| Tür | Kurallar |
|---|---|
| Ürün/kategori dışlama | faizsiz (yalnızca katılım + kıymetli maden), yalnızca TL, altın yok, hisse yok, doğrudan hisse yok, yurt dışı yok, karmaşık ürün yok, gelir üretmeyen yok |
| Ürün düzeyi süzgeç | yönetim ücreti ≤ %2, fon büyüklüğü ≥ 500 mn TL, yalnızca ING platformu, mevduat vadesi sınırı |
| Portföy düzeyi (RL dağılımı) | riskli (RD ≥ 5) pay ≤ %X, döviz+altın payı ≤ %X, hemen erişilebilir (PP+borçlanma) ≥ %X, mevduat+PP ≥ %X, kategori başına ≤ %X, en az N kategori |

- **Deterministik taraf:** Dışlama kuralları için genel bir süzgeç var (`rules._generic_exclusion`); "en az N kategori" kuralı kısa liste seçimine bağlandı.
- **RL maskesi:** Katalog kısıtları maskeye ekleniyor. Kısıtlar birlikte sağlanamazsa çıkarım ve API açık bir hata döndürüyor, kapanan kategorileri listeliyor.
- **Profil sürüm 3 (57 boyut):** Sürüm 2'nin 21 alanına, her katalog kuralı için bir bayrak ve bir parametre eklendi.
- **Eğitim profilleri:** 57.600 brief kombinasyonu + 40.000 katalog varyantı (rastgele 1–3 kısıt; oran ±%10; boş maskeli 6.819 varyant atıldı) + 5 persona.
- **Aksiyon ızgarası:** 4.830 (%10 adım ≤ 3 kategori ∪ %20/%25 adım ≤ 4 kategori). "En az 4 kategori" gibi kısıtlar artık sağlanabiliyor. Bazı birleşimler hâlâ sağlanamıyor (örn. "en az %60 güvenli" + "kategori başına ≤ %30" için %10 adımla 4 kategori gerekir).

**Model:** NN-PPO, HPO ayarları, tüm veriyle eğitildi (`models/rl_ppo_v9`, canlı).

**Performans tahmini** (aynı ayarlar, test 2026, 3 tohum, kurala göre reel fark, puan): P01 +2,42, P02 +2,42, P03 −11,07, P04 −0,57, P05 +0,33.

**Öneri:** Canlıda sabit kural esas, model yalnızca güvenli iyileştirme kapısıyla devrede. "Model bu rejimde kuraldan güvenilir bir fark bulamadı ve kurala döndü" bilgisi RM'e açıkça gösterilmeli.

### What-if makullüğü (v13–v14)

**Sorun:** v11 ve v12 what-if senaryolarına ya tepki vermiyordu ya da ters tepki veriyordu. Örnek: borsa düşünce hisse payı artıyordu; PP'nin geçmiş getirisi −15 puan olunca PP'ye yığılıyordu.

**Ölçüm:** `tools/gbrl/whatif.py` iki iş yapıyor:

- **Tutarlı şok uygulaması.** Bir senaryo, aynı olayın etkilediği tüm göstergelere birlikte uygulanıyor. Faiz şoku politika faizini, TLREF'i, mevduat faizini, bunların değişim göstergelerini ve reel faizi değiştiriyor. TÜFE şoku reel faizi düşürüyor. Kur ve borsa şokları ilgili tüm getiri göstergelerine çarpımsal yansıyor.
- **Yön kontrolleri.** Ölçülen şey, modelin olasılık ağırlıklı beklenen dağılımı. Tolerans ±2 puan. Bir senaryo yalnızca grubun profilde izinli olduğu durumlarda sayılıyor.

| Şok | Beklenen yön |
|---|---|
| Faiz ↓ / ↑ | TL nakit (mevduat + PP) payı ↓ / ↑ |
| TÜFE ↑ / ↓ | TL sabit getiri payı ↓ / ↑ |
| Kur ↑ / ↓ | Döviz + altın payı ↑ / ↓ |
| Borsa ↓ / ↑ | Hisse payı ↓ / ↑ |
| Bir kategorinin geçmiş getirisi ±15 puan | O kategorinin payı aynı yönde (ya da değişmez) |

"Makul" sayılmak için üç koşul var:
- Yanlış yön oranı %10 veya altında.
- Hiçbir senaryoda yanlış yön %20'yi aşmıyor.
- Doğru yönde tepki %25 veya üzerinde.

Başlangıç ölçümü (son 60 gün × persona ve görülmemiş profiller):
- v11 tüm veri 100M: doğru tepki %3; borsa düşüşünde yanlış yön %64.
- v12 100M: doğru tepki %1.

**Neden?** 2021–26 verisinde BIST düşüşlerinin ardından toparlanma gelmiş, model de dibinden almayı öğrenmiş. ±2σ kesme bunu daha da kötüleştiriyordu: bugünkü faizler zaten −4,9σ civarında, dolayısıyla faiz indirimi girdide hiç görünmüyordu.

**Yapılanlar.** Her biri `train_gbrl.py` içinde bir seçenek:

1. **Yumuşak girdi sınırı (`--kirpma-turu tanh --kirp 3`):** c·tanh(z/c). Monoton olduğu için uç değerler de birbirinden ayırt edilebiliyor. Meta alanı `kirpma_turu`; çıkarımda `rl_category.model_input` kullanılıyor.
2. **Senaryo artırımı (`--senaryo-oran`, `--momentum`):** Adımların bir kısmında durum şoklanıyor ve o adımın getirileri mekanizmaya uygun biçimde değiştiriliyor:
   - Faiz şoku TL taşıma getirisini 3 aylık oranda değiştiriyor.
   - TÜFE şoku enflasyonu değiştiriyor; döviz bazlı varlıklar bunu TL'de tam, hisseler yarı yansıtıyor.
   - Kur ve borsa şokları momentum katsayısı κ ile devam ediyor.

   Pilotta bu tek başına yetmedi: veri sinyali öncülden güçlüydü.
3. **What-if tutarlılık düzenleyicisi (`--tutarlilik`, `--tutarlilik-kategori`):** Her rollout'tan sonra 4 ek gradyan adımı atılıyor. Her adımda 2.048 rastgele eğitim durumu × rastgele şok (makro ya da tek kategori) kullanılıyor. Kayıp relu(−s·Δg) + 0,1·relu(0,02 − s·Δg); burada Δg, izlenen grubun beklenen payındaki değişim. Kredi skorlamasındaki monoton kısıtlara benzer bir öncül bilgi kısıtı. Ölçüm, eğitimde kullanılmayan tarihlerde (doğrulama, test, son 60 gün), görülmemiş profillerde ve sabit senaryo büyüklükleriyle yapılıyor.

**Veri ayrımı.** Son 3 ay (2026-07-15 → 2026-10-15) holdout. `export_rl_env.py --test-baslangic 2026-07-15 --test-bitis`: test havuzu, sonucu holdout dönemine uzanan 60 pencere. Eğitim ve doğrulama pencereleri 2026-07-15'ten önce bitiyor.

**Cold HPO** (`tools/gbrl/hpo_whatif.py`, 12 deneme × 2 tohum × 6M adım, yalnızca son ağırlıklar):
- **Arama uzayı:** Gösterge seti (v2/v3), kırpma türü ve sınırı, artırım oranı, κ, düzenleyici ağırlığı, lr, entropi, persona oranı.
- **Seçim ölçütü:** Yalnızca doğrulama; getiri skoru + 0,1·what-if skoru.
- **Kazanan:** v2, tanh 3, artırım 0,3, κ 0,15, düzenleyici ağırlığı 10. Diğer ayarlar v11'deki gibi.
- **Gözlemler:**
  - Bütün denemeler makro senaryolarda makul çıktı; ayarları getiri ayırdı.
  - v3 göstergeleri ve sert kesme getiride tutarlı biçimde geride kaldı.
  - Teyit için hiç kullanılmamış 3 tohum (2–4) koşuldu; HPO sonucu doğrulandı.

**v14:** Tek kategori şokları test kümesine eklenince v13'ün açığı görüldü: PP getirisi −15 puan olunca durumların %95'inde PP payı arttı. Bu şoklar düzenleyiciye eklendi. 6M adımlı pilotta 4/4 koşu bütün testleri geçti (doğrulama ve test tarihlerinde makro ve kategori yanlış yönü %0). Doğrulama getirisi düşmedi; personaların kurala göre farkları ≈ +2,8 / +1,5 / +3,5 / −0,5 / +0,5 puan.

**Eğitim bütçesi (yalnızca doğrulamayla seçildi).** Holdout 100M koşusu da what-if'te makuldü, ama doğrulama skoru daha uzun eğitimde düştü:

| Bütçe | Tohum | Skor | Persona doğrulama farkı (puan, P01–P05) |
|---|---|---|---|
| 6M | 2 | +0,083 | +2,8 / +1,5 / +3,9 / −0,5 / +0,5 |
| 12M | 2 | +0,052 | +2,6 / +1,1 / +2,6 / −0,8 / 0,0 |
| 25M | 2 | +0,055 | +2,6 / +1,0 / +2,1 / −0,6 / −0,3 |
| 100M | 1 | +0,037 | +2,3 / +0,5 / +5,7 / −1,0 / −0,6 |

Düzenleyiciyle uzun eğitim aşırı uyum yapıyor; seçilen bütçe 6M.

**Test (son 3 ay, 6M, 2 tohum, bir kez okundu).**
- Kurala göre reel fark: P01 +3,3, P02 +2,7, P03 −4,1 (en kötü tohum −11,4; altın), P04 −0,6, P05 −0,7 puan.
- Görülmemiş profiller: model %14,2, kural %14,4.
- What-if (test tarihleri): makro ve kategori senaryolarında yanlış yön %0.

Not: 100M holdout testi post-hoc olarak da okundu ve kötüydü (P03 −33,8; görülmemiş profiller %8,5). Bu, doğrulamanın gösterdiği aşırı uyumla tutarlı.

**Canlı model:** `models/rl_ppo_v14_tum6m` (tüm veri, 6M adım, ≈5M parametre). Windows tarafındaki kontroller:
- `tools/rl_whatif_eval.py`, son 60 gün: yanlış yön %0, doğru tepki %81.
- `tools/rl_kategori_sok.py`: ters yönlü 0/26.
- `tools/rl_duyarlilik.py`: personaya göre 2–21 farklı karar.

Kullanıcının bulduğu durum düzeldi: P05'te PP'nin 3 aylık getirisi −15 puan olunca, eski model PP'yi %90'dan %100'e çıkarıyordu; v14 katılıma geçiyor. Bugünkü (şoksuz) kararlar v11 ile neredeyse aynı.

**UI doğrulaması** (What-if paneli, canlı v14):
- P05 faiz indirimi: PP %100 → PP %50 + borçlanma %50.
- P05, PP'nin 3 aylık getirisi %9,3 → %3,8: PP %100 → borçlanma %100.

**Argmax ölçümü.** UI tek en olası kararı (argmax) gösteriyor. What-if makullüğü argmax ile de ölçüldü:
- v14: yanlış yön %0,1, doğru tepki %34.
- v11: yanlış yön %1,7, doğru tepki %3, en kötü senaryoda %33.

**Dağınık politika ve hazır senaryolar.** P03 gibi profillerde politika çok dağınık: en olası aksiyonun olasılığı ≈ %1.
- **Sorun:** Hazır senaryolar tek göstergeyi değiştirdiğinde argmax gürültüye dönüyordu. Kur şoku P03'ü mevduata geçiriyordu, oysa beklenen döviz payı artıyordu.
- **Denenen ve reddedilen:** Karar kuralını beklenen dağılıma en yakın aksiyona çevirmek (`tools/gbrl/karar_kurali.py`). Doğrulamada argmax'tan kötü çıktı (tohum skoru ≈ −0,027'ye karşı ≈ 0), bu yüzden argmax kaldı.
- **Çözüm:** Hazır senaryolar artık `whatif.apply` ile tutarlı uygulanıyor (`rl_ne_olursa senaryo.tur`).

UI kontrolü (canlı v14, argmax, 4 hazır senaryo × P03/P04/P05):
- **Yanlış yönde tepki yok.**
- **P03:** Kur şokunda +%20 kıymetli maden. Faiz indirimi ve enflasyon artışında mevduat %60'tan %20'ye iniyor.
- **P04:** Kur şokunda +%40 eurobond/döviz.
- **P05:** Faiz indiriminde PP'den borçlanmaya geçiyor.

### GBRL: simetrik ağaç ve monoton what-if kısıtı (v15g, deneme)

GBRL iki ilgili seçenek sunuyor:
- **Simetrik (oblivious) ağaç:** `tree_struct.grow_policy = "oblivious"`. Önceki GBRL koşuları varsayılan "greedy" ile çalışmıştı.
- **Monoton kısıt:** Yalnızca simetrik ağaçta ve SGD ile çalışıyor.

Python API bir özellik için tek yön kabul ediyor. C++ katmanı ise (özellik, çıkış, yön) üçlüleri alıyor ve karışık yönleri destekliyor. `probe_monoton.py` ile doğrulandı: monotonluğa ters bir hedefle eğitince bile ihlal 0.

`train_gbrl.py --buyume oblivious --monoton 1` what-if öncülünü logit'lerde yapıyla kuruyor (`monotone_triples`):
- **Faiz göstergeleri:** Nakit payı ≥ %50 olan aksiyonların logit'i faizle artıyor, nakit içermeyenlerinki azalıyor.
- **Diğer gruplar:** TÜFE, kur, borsa ve kategori geçmiş getirileri için aynı mantık.
- **Toplam:** 6.800 kısıt (20 gösterge × 390 aksiyon).

Pilot (390 aksiyonlu ızgara `10:2+4:3`, son 3 ay holdout, 3M adım):
- **Simetrik GBRL, 116 dk:** Doğrulamada kuralın altında (persona farkları −1,4 / −2,2 / −0,1 / −0,3 / 0,0). What-if'te hiç tepki yok (doğru tepki %0). Politika neredeyse düz (en olası aksiyon olasılığı 0,09); piyasa göstergelerini kullanmayı öğrenemedi.
- **Simetrik + monoton:** Kısıt izdüşümü eğitimi yaklaşık 3 kat yavaşlattı (3M adım ≈ 4,5 saat). Bölüm ödülü 0,8M adımda simetrik koşuyla aynıydı (≈ −1,67), bu yüzden durduruldu.
- **Neden durduruldu:** Monoton kısıt tepkinin yönünü garanti ediyor ama tepkiyi kendisi yaratmıyor. Öğrenemeyen bir politikada yön garantisi tek başına işe yaramıyor.

### Tahmin + optimizasyon: CatBoost (deneme, `tools/catboost_karar.py`)

RL yerine iki adımlı bir yaklaşım denendi.

**1. Tahmin.** Her kategori için bir CatBoost regresyonu, sonraki 3 ayın vergi sonrası reel getirisini tahmin ediyor.
- Simetrik ağaç ve monoton kısıt var. Kısıtlar göreli: senaryo göstergesi hedef grubun tahminini öncül yönünde, diğer kategorilerinkini ters yönde hareket ettirebiliyor.
- **Yapısal temel:**
  - TL kategorilerinde nominal getiri = k·faiz·3/12; k eğitimdeki ortalamalar oranı (mevduat ← mevduat faizi, diğerleri ← TLREF).
  - Diğer kategorilerde reel getiri 0.
  - Reel çevirme TÜFE eğilimiyle yapılıyor.
- CatBoost yalnızca artığı öğreniyor; büzülme parametresi λ.

**2. Optimizasyon.** Profil ve maske kısıtları altında skoru en yüksek aksiyon seçiliyor:
- wᵀμ̂ − γ_p/2·wᵀΣw − profil cezaları − devir maliyeti − beklenen anapara cezası.
- Ödül terimleri RL ile aynı.

Ortam, senaryo izleri ve what-if takımı v14 ile birebir aynı. 48 ayar (ağaç × λ × γ) yalnızca doğrulamayla seçildi.

**Sonuç:**
- **Yapısal temelsiz sürüm:** Ağaçlar ekstrapolasyon yapamadığı için mevduatın 2025 reel getirisini %0,5 tahmin etti (gerçekleşen %2,4). Personalar kuralın 2–4 puan altında kaldı.
- **Yapısal temelle doğrulamanın seçimi:** λ=0, γ=0; yani CatBoost artığı hiç kullanılmıyor. Artık tahmininin R²'si negatif (≈ −0,64) ve ağaç sayısından bağımsız.
- **Doğrulama:** Persona farkları +2,3 / +0,8 / +4,9 / +0,1 / +0,8; görülmemiş profillerin iki yarısı +1,8 / +0,5.
- **Test (son 3 ay, bir kez okundu):** P01 +3,2, P02 +0,6, P03 −29,0, P04 −0,6, P05 +0,7 puan. Görülmemiş profiller %12,5, kural %14,4.
- **What-if:** Yanlış yön %0 ama doğru tepki %14; makul değil. Kategori şoklarına tepki yok.

**Değerlendirme:** Getiri tahmini bu veri boyutunda (≈ 20 bağımsız çeyrek) taşıma getirisinin ötesine geçemiyor. Optimizasyon tahmin hatalarını büyütüyor; P03'te eğitim dönemindeki altın ve hisse getirisine yığılıyor. NN-PPO v14 daha iyi. Canlıda v14 kalıyor.

**Sonuç (GBRL):** Bu ayarlarla GBRL, NN-PPO v14'ün gerisinde. v14 aynı holdout'ta doğrulamada +2,8 / +1,5 / +4,3 / −0,4 / +0,4 ve what-if'te yanlış yön %0, doğru tepki %66 veriyor. Canlı model v14 olarak kalıyor. GBRL için ayrı bir HPO gerekirdi (daha yüksek politika öğrenme oranı, derinlik, daha küçük ızgara).

## 2. Ürün düzeyi RL (çalıştırma başına)

Kod: [backend/app/rl.py](../backend/app/rl.py). Parametreler ve gerekçeleri: [decision_config.json](../backend/config/decision_config.json) → `rl`. Testler: [backend/tests/test_rl.py](../backend/tests/test_rl.py).

## Ne yapıyor

Kural motoru ve skorlama kısa listeyi çıkarır. RL katmanı bu kısa liste içinde, her vade sonunda (≈3 ay, 63 iş günü) dağılımı yeniden seçer. Sert kısıtlar RL'den önce uygulanır. Ajan yalnızca izinli ağırlık ızgarasından seçebilir (action masking):

- %25 adımlı ağırlıklar.
- Her fon en fazla %50.
- P04'ün yoğunlaşma kısıtında her ürün en fazla %50.

| Bileşen | Tasarım |
|---|---|
| Ödül | Reel servet değişimi (stopaj sonrası, o dönemin gerçek TÜFE'siyle indirgenmiş). Anapara koruması Yüksek ise anaparanın altında her adım için −(5 × açık + 0,02); Orta ise −(1 × açık); Düşük ise ceza yok. Yeniden dengeleme için %0,2 × devir maliyeti. |
| Ufuk | Persona ufkuna göre rastgele (1-3 yıl → 4-12 çeyrek) ve ajana gizli. Q hedefinde beklenen devam olasılığıyla yer alır. |
| Senaryolar | Sıralı durağan blok bootstrap: çeyrekler tarihteki sırayla ilerler, ortalama 4 çeyrekte bir başka tarihe atlanır. |
| Durum (DQN) | Ürünlerin son 3 ay getirisi; son 3 ay enflasyonu (TÜFE, 35 gün gecikmeli); TCMB 1 ay tasarruf mevduatı faizi (7 gün gecikmeli); geçen süre; reel servet; mevcut dağılım. Gecikmeler, karar anında henüz açıklanmamış verinin kullanılmasını (geleceğe bakış) önler. |
| DQN | Numpy MLP (2×32, ReLU), Adam, Huber kaybı, deneyim tamponu, hedef ağ. Piyasa ajanın kararından bağımsız (dışsal) olduğu için her geçişte tüm aksiyonların hedefi hesaplanır. |
| Tablo tabanlı Q | Durum = (geçen süre, reel servet bandı); göstergeleri görmez. Karşılaştırma tabanı ve dağılım dışı durumda yedek. |
| Seçim | Eğitim döneminin son %20'si doğrulama. İki ajan arasındaki seçim burada yapılır; test dönemi (2025-07 →) seçimde kullanılmaz. |
| Güvenlik | Bugünkü göstergelerden biri eğitim dağılımının 3σ dışındaysa DQN ekstrapolasyon yapar; bu durumda başlangıç dağılımı tablo ajanından alınır ve RM'e gerekçesiyle gösterilir. |
| Tekrarlanabilirlik | Sabit tohum; aynı girdi aynı sonucu verir (test edildi). |

## Sonuçlar (gerçek veri, test dönemi 2025-07-01 → 2026-10-15)

| Persona | Seçilen ajan | Başlangıç dağılımı | Seçilen ajan | Tablo Q | Eşit ağırlık | En iyi sabit |
|---|---|---|---|---|---|---|
| P01 | DQN | ING 32g %75, 46g %25 | %6,2 | %6,0 | %6,2 | %6,2 |
| P02 | DQN | BGP %50, GBL %50 (tablo, dağılım dışı) | %11,0 | %11,0 | %7,9 | %11,1 |
| P03 | DQN | BGP %50, AIS %50 | **%54,7** | %39,2 | %33,9 | %54,7 |
| P04 | DQN | BGP %50, ING 32g %50 | %34,0 | %34,0 | %34,0 | %34,0 |
| P05 | DQN | BGP %50, GBL %50 (tablo, dağılım dışı) | %10,9 | %11,0 | %9,0 | %11,1 |

Değerler test dönemindeki ortalama reel kazançtır.

## Bulgular

1. **Piyasa göstergeleri işe yarayabiliyor.** P03'te göstergeleri gören DQN, görmeyen tablo ajanını testte belirgin geçiyor (%54,7'ye %39,2). Oyuncak piyasa testi de göstergeyi kullandığını doğruluyor.
2. **Rejim değişikliği.** P02 ve P05'in eğitim dönemi (2024-09 → 2025-06) yüksek enflasyon ve yüksek faiz dönemi. Bugün enflasyon eğilimi ve mevduat faizi eğitim ortalamasının 3-5σ altında (dezenflasyon). DQN bu bölgede güvenilir değil. Sistem bunu tespit edip tablo ajanına düşüyor; RM bunu notta görüyor.
3. **Dinamik yeniden dengelemenin ek faydası sınırlı.** Çoğu personada en iyi sabit dağılım RL kadar iyi. Elimizdeki 3,8 yıllık veri, piyasa durumuna göre sistematik bir avantaj çıkarmak için kısa.
4. **%50 fon sınırı çeşitlendirmeyi zorunlu kılıyor.** Sınır olmadan ajan her personada tek fona (BGP) %100 yatırıyordu. Ödülde çeşitlendirmenin karşılığı yok, sınır bunu kural olarak getiriyor.
5. **Tek ürüne kalan evren.** P01 ve P04'te kısa listedeki fonların bir kısmının fiyat geçmişi kısa olduğu için RL evreninde 2 ürün kalıyor. Karşılaştırma anlamsızlaşıyor ve sistem bunu açıkça not ediyor.

## Sınırlar

- Bu bir getiri tahmini değil, geçmiş verilerle yapılan bir simülasyon.
- Mevduat getirisi güncel sözleşme oranıyla sabit varsayılıyor. Faiz yolu modellenmiyor.
- Her adımda tam yeniden dengeleme varsayılıyor, stopaj her adımda pozitif kazanca uygulanıyor. Bu muhafazakâr bir yaklaşım; gerçekte elde tutma süresine bağlı.
- Bir çalıştırma 10-20 saniye sürüyor (eğitim + üç değerlendirme). Aynı girdiler için sonuç önbellekten geliyor.
