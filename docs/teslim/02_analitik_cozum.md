# 3. Analitik çözüm

Temel soru (brief 6): *Verilen müşteri profili ve mevcut seçenekler içinde hangileri daha uygun görünüyor ve neden?*

Bu soruyu dört katmanda yanıtlıyoruz:
- uygunluk kuralları dışlar,
- şeffaf bir puan sıralar,
- RL kategori modeli dağılım konusunda yardımcı bir görüş verir,
- LLM yalnızca anlatır.

Her katmanın varsayımları ve parametreleri gerekçeleriyle iki dosyadadır: [`decision_config.json`](../../backend/config/decision_config.json) ve [`constraint_rules.json`](../../backend/config/constraint_rules.json). Her kanıt kaydı bu dosyaların hash'ini taşır.

## 3.1 Veri ve sinyaller

**Müşteri sinyalleri** (personanın yapılandırılmış alanları):
- risk profili,
- yatırım ufku,
- likidite ihtiyacı,
- gelir / nakit akışı ihtiyacı,
- anapara koruma tercihi,
- döviz maruziyeti tercihi,
- yatırım amacı ve öncelikler,
- açık kısıtlar.

**Ürün sinyalleri:** Evrende yaklaşık 2.100 ürün var: TEFAS fonları, ING mevduat (TL ve döviz), doğrudan DİBS ve BIST 30 hisseleri.

| Sinyal | Kaynak | Not |
|---|---|---|
| 1 yıllık ve 3 aylık getiri, yıllık oynaklık, son 1 yılın en büyük düşüşü | TEFAS fiyat geçmişi (≥ 60 gözlem) | Fon Karşılaştırma dışa aktarımıyla çapraz kontrol (fark > 2 puan → çelişki) |
| Resmi risk değeri (RD 1–7) | KAP fon bilgileri | Yoksa volatiliteden SPK bantlarıyla tahmin ("varsayım" etiketi). Resmi ve tahmin arasında 2+ kademe fark varsa muhafazakâr değer alınır. |
| Yönetim ücreti, giriş/çıkış komisyonu, satış valörü, nitelikli yatırımcı bilgisi | KAP | Valör yoksa kategori varsayımı ve "izahnameden teyit" notu |
| Stopaj | ING vergilendirme tablosu, mevduat hesaplayıcısı | Koda yazılmaz. Kaynakta yoksa RM teyit notu düşülür (kural 7). |
| Mevduat oranı ve vadesi | ING mevduat hesaplayıcısı | TCMB sektör ortalamasıyla çapraz kontrol (sapma > 5 puan → uyarı) |
| Reel getiri | Getiri ve TÜFE (EVDS) | |
| Fon büyüklüğü, yatırımcı sayısı | TEFAS | 50 mn TL ve 100 yatırımcı altı dışlanır |
| Piyasa bağlamı: politika faizi, TLREF, TÜFE, sektör mevduat faizleri, DİBS, kurlar, altın | TCMB, EVDS, Hazine ihale sonuçları | |

**Veri kalitesi:**
- Tüm veri bir veri tabanındadır (SQLAlchemy + SQLite) ve otomatik toplayıcılarla güncellenir; elle düzenlenen veri dosyası yoktur. Her satır, onu yazan toplama çalışmasının (çekim) kimliğini de taşır ([docs/veri_tabani.md](../veri_tabani.md)).
- Her değer `kaynak · URL · veri tarihi · alınma zamanı · durum` olarak taşınır. Durum ok, bayat, eksik, çelişkili veya varsayım olabilir.
- Veri çekilemezse değer uydurulmaz: alan "eksik" olur, kriter skora katılmaz ve güven skoru düşer.
- Önbellekten gelen değer "bayat" etiketiyle ve kendi alınma zamanıyla gösterilir.

## 3.2 Uygunluk: deterministik kurallar

1. **Risk profili → RD üst sınırı.**
   - Eşleme: Muhafazakâr 2, Temkinli 3, Dengeli 5, Agresif 7.
   - Gerekçe: yaygın banka uygunluk testi pratiği. Mentor kurum-spesifik tanım beklemediğini, gerekçenin yeterli olduğunu söyledi.
2. **Açık kısıtlar → kural kataloğu.** Personadaki serbest metin kısıtlar 23 kurallık kataloğa eşlenir. Örnekler:
   - `K_YUKSEK_RISK`: RD ≥ 5, hisse ve serbest fonlar dışlanır.
   - `K_LIKIDITE_KILIDI`: satış valörü > 2 iş günü ve 32 günden uzun mevduat dışlanır.
   - `K_DOVIZ_ARTIRMAMA`: döviz ve altın bazlı ürünler dışlanır.
   - `K_YOGUNLASMA`: kategori başına en fazla 1 ürün, en az 3 kategori.
   - `K_KISA_VADE_NAKIT_YOK`: likidite ağırlığı sabitlenir.
3. **Eşleşmeyen kısıt veya eksik/çelişkili alan:** sistem tahmin yürütmez. Akış "RM teyidi bekliyor" durumunda durur ve soru üretir. Örnek: "Müşteri agresif risk profilinde ama anapara korumasını yüksek öncelik görüyor. Hangisi esas alınmalı?"
4. **Diğer dışlamalar:** nitelikli yatırımcıya özel ya da bu bilgisi teyit edilmemiş serbest fonlar, getiri verisi olmayan ürünler, riski belirlenemeyen ürünler.

Her dışlama `kural kimliği + gerekçe` ile kayda geçer ve arayüzde listelenir. Örnek: "Risk değeri RD 5, müşteri profilinin üst sınırı RD 3."

**Neden kural?** Uyum ihlali kabul edilemez ve geriye dönük gösterilebilmeli. Olasılıksal bir model veya LLM yorumu bu garantiyi veremez.

## 3.3 Sıralama: şeffaf ağırlıklı puan

Uygun ürünler 8 kriterde 0–1 aralığına normalize edilir (min-max, ürün evreni içinde):
- getiri,
- düşük oynaklık,
- likidite,
- düşük maliyet,
- gelir üretimi,
- anapara koruma,
- döviz uyumu,
- sadelik.

**Ağırlıklar** persona alanlarından türetilir ve toplamı 1 olacak şekilde normalize edilir:
- getiri = 0,2 + 0,8 × risk seviyesi; düşük oynaklık = 0,2 + 0,8 × (1 − risk seviyesi)
- likidite, gelir ve anapara ağırlıkları ilgili persona alanının seviyesi
- ufuk ≥ 5 yıl → getiri +0,2
- öncelik metinleri anahtar kalıplarla ilgili kritere ek ağırlık verir (ör. "likidite" +0,5)

**Puan ve güven:**
- Puan = Σ ağırlık × kriter skoru / Σ (verisi olan kriterlerin ağırlığı).
- Güven skoru, kriterleri besleyen verilerin kalitesinin ağırlıklı ortalamasıdır. Bayat, varsayım veya eksik veri güveni düşürür. %70 altı arayüzde vurgulanır.

**Kısa liste:** 5 ürün, kategori başına en fazla 2. Uygun ama listeye girmeyen her ürün için "neden listede değil" gerekçesi gösterilir; örneğin aynı kategoriden daha yüksek skorlu iki ürün var.

**Neden bu yaklaşım?**
- RM'in "neden bu ürün, neden şu değil?" sorusuna kriter düzeyinde yanıt verir.
- Ağırlıkların her biri persona alanına bağlanır, bu yüzden jüri bir alanı değiştirdiğinde sıralamanın neden değiştiği görülür.
- Daha karmaşık bir öğrenen sıralayıcı için etiketli "doğru öneri" verisi yok. Olsaydı bile açıklanabilirlik kaybı bu kullanım için kabul edilemezdi.

## 3.4 Kategori dağılımı: profile koşullu RL (yardımcı görüş)

Kısa liste "hangi ürünler?" sorusunu yanıtlar. RM ayrıca "bugünkü piyasa koşullarında bu profil için kategoriler arasında nasıl bir dağılım geçmişte işe yarardı?" sorusunu da sorar. Bunun için bir pekiştirmeli öğrenme modeli eğittik.

**Ortam:**
- Dönem: 2021–2026 günlük veriler.
- 11 varlık kategorisi: TEFAS kategori medyanları, mevduat, BIST 30.
- Her 3 ayda bir yeniden dengeleme.
- Ödül: enflasyondan arındırılmış, stopaj sonrası servet değişimi − anapara altına düşme cezası − işlem maliyeti.

**Durum:**
- 30 piyasa göstergesi. Hepsi karar anında bilinen ve yayın gecikmesi uygulanmış değerlerdir: kategori getirileri ve oynaklıkları, faiz, TÜFE, kur, BIST.
- Profil vektörü.
- Mevcut dağılım.

**Aksiyon ve maskeler:**
- Aksiyon: 4.830 hazır dağılımdan biri.
- Profilin RD sınırı ve açık kısıtları maskeyle uygulanır; model izinsiz kategoriyi seçemez.

**Profiller:**
- Brief personalarındaki değerlerin çelişkisiz kombinasyonlarından 2.400 profil türetildi.
- 2.160'ı eğitimde kullanıldı, 240'ı hiç görülmeden test edildi.
- Bunlar persona değildir, yalnızca genelleme içindir. Öneri yalnızca 5 persona için üretilir.

**Canlı model: NN-PPO v14.** Tüm veride 6M adım eğitildi ve "what-if tutarlılığı" düzenleyicisi içerir. Bu düzenleyici, model faiz indirimi gibi bir şoka ekonomik olarak ters yönde tepki verirse cezalandırır.

**Açıklama vekil modelle değil, gerçek ağ sorgulanarak üretilir:**
- tema düzeyinde kesin Shapley (2^G ileri geçiş),
- karşı-olgusal ("faiz 5 puan düşerse"),
- eşik taraması,
- en benzer geçmiş dönemler,
- piyasa duyarlılığı.

**Belirsizlik:**
- Model bugünkü dağılımı personaya göre yalnızca %1–25 olasılıkla seçiyor; politika birçok dağılım arasında kararsız.
- Bu yüzden not ve arayüz tek dağılımın yanında olasılık ağırlıklı dağılımı da gösterir. Olasılık %20'nin altındaysa açık uyarı verir.
- Model girdisi geçmişte görülmemiş bir değerse ya da eğitim dağılımının dışındaysa uyarı düşer.

**Dürüst sonuç (test: son 3 ay, 15.07.2026 sonrası; aynı ayarlarla bu dönemi görmeden eğitilmiş 2 tohumun ortalaması; ortalama reel kazanç):**

| | Model | Basit kural (para piyasası + mevduat/borçlanma) |
|---|---|---|
| P01 | %19,4 | %16,2 |
| P02 | %18,9 | %16,1 |
| P03 | %12,1 | %16,1 |
| P04 | %15,5 | %16,1 |
| P05 | %11,6 | %12,3 |
| Görülmemiş 240 profil | %14,2 | %14,4 |

Model iki personada kuralı geçiyor, diğerlerinde ve genellemede geçemiyor. Bu nedenle RL çıktısı **nihai öneri değil**, RM için yardımcı bir senaryo aracıdır. Asıl değeri what-if ekranındadır: faiz, enflasyon, kur ve borsa şoklarında tepki yönü tutarlı. Makro şoklarda yanlış yön tepki oranı %0, faiz şoklarında doğru yön tepki oranı %94.

Tek bir test dönemi ve 2 tohum küçük bir örneklemdir. Bu farklar istatistiksel olarak güçlü değildir.

Ayrıntı: [docs/rl.md](../rl.md).

## 3.5 Metin: lokal LLM ajanı + guard

- **Model:** Qwen3.8-27B. KVKK gereği lokal çalışır (vLLM, WSL).
- **Kullanım:** yalnızca anlatım ve RM soru-cevabı.
- **Sayılar:** araçlardan gelir. Araçlar: kısa liste, ürün ayrıntısı, RL kararı, Shapley, what-if, eşik, güvenilirlik.
- **Guard:** her metni kontrol eder ve şunları reddeder:
  - yasaklı ifade ("garanti", "kesin getiri", "tavsiye ediyorum"...),
  - dışlanan ürün kodu,
  - araç çıktılarında geçmeyen yüzde,
  - zorunlu "RM onayı olmadan paylaşılamaz" ibaresinin eksikliği.

  İhlalde ajan düzeltme turuna girer. Düzeltemezse deterministik şablon metne düşülür.

## 3.6 Varsayımlar ve belirsizlik yönetimi

| Varsayım / belirsizlik | Nasıl ele alınıyor |
|---|---|
| Risk profili → RD eşlemesi kuruma özel değil | Config'de gerekçesiyle; jüri challenge'ında değiştirilebilir |
| Valör bilinmeyen fon | Kategori varsayımı + "izahnameden teyit" notu; likidite kilidi kısıtında dışlanmaz, teyit istenir |
| Resmi RD yok | Volatiliteden SPK bandıyla tahmin, "varsayım" etiketi, güven skoru düşer |
| Stopaj kaynakta yok (ör. doğrudan DİBS) | Brüt getiri + RM teyit notu |
| BIST 30 fiyatları resmi kaynak değil (Yahoo) | Notta açık teyit maddesi |
| Mevduat kademesi (1 mn TL) | Private Banking için varsayım, notta yazılı |
| Kaynak erişilemiyor | Değer uydurulmaz; önbellek "bayat" etiketiyle ya da alan "eksik" |
| Persona çelişkisi / eksik alan / tanınmayan kısıt | Öneri üretilmez, RM teyit sorusu |
| RL politikası düz | Seçim olasılığı ve olasılık ağırlıklı dağılım gösterilir |
