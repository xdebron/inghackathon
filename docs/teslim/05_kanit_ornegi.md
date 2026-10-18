# 6. Kanıt kaydı örneği: P05 Hekim

Tam kayıt: [kanit_ornegi.json](kanit_ornegi.json). Aynı kayıt arayüzde P05 → "Kanıt" sekmesinde görünür ("Ham kanıt kaydı (JSON)").

| Alan | Değer |
|---|---|
| Kayıt | `20261018-174419-P05-eca966` |
| Oluşturma | 18.10.2026 17:44:19 (+03:00) |
| Varsayım dosyalarının hash'i | `95f587a3cd181d93`: `decision_config.json` ve `constraint_rules.json` |
| Metin üreticisi | Lokal LLM ajanı (`qwen3.8-27b-nvfp4`); guard'da 1 düzeltme turu, son ihlal 0 |
| RL modeli | `rl_ppo_v14_tum6m` (NN-PPO, tüm veri, 6M adım) |
| Durum | RM onayladı (düzenleyerek onay) |

> Bu örnekte RM kararı, kayıt formatını göstermek için **"Demo RM (örnek kayıt)"** adıyla verildi; gerçek bir RM onayı değildir.

## 1. Dayanılan veriler, kaynaklar ve zaman damgaları

Kısa listenin ilk ürünü **AK Portföy Üçüncü Para Piyasası (TL) Fonu (BGP)**. Puanı 92/100, güveni %87.

| Veri | Değer | Kaynak | Veri tarihi | Alınma zamanı | Durum |
|---|---|---|---|---|---|
| 1 yıllık getiri | %46,4 | TEFAS Tarihsel Veriler (manuel dışa aktarım), tefas.gov.tr | 15.10.2026 | 15.10.2026 19:08 | kaynaklı |
| Yıllık oynaklık | %1,4 | TEFAS Tarihsel Veriler | 15.10.2026 | 15.10.2026 19:08 | kaynaklı |
| Risk değeri | RD 1 | KAP Fon Bilgileri sayfası (BGP) | 15.10.2026 | 15.10.2026 12:38 | kaynaklı; not: "KAP ve TEFAS risk değerleri aynı" |
| Yönetim ücreti | %0,49 | KAP Fon Bilgileri sayfası | 15.10.2026 | 15.10.2026 12:38 | kaynaklı |
| Satış valörü | T+0 | TEFAS Valör Hesaplama Simülatörü (elle girilen ölçüm) | 10.10.2026 | — (elle ölçüm; zaman uydurulmadı) | kaynaklı; not: "1 ölçüm, en kötü durum alındı" |
| Stopaj | %17,5 | ING yatırım fonlarında vergilendirme tablosu (Para Piyasası, 09.07.2025 sonrası) | 09.10.2026 | 10.10.2026 02:20 | kaynaklı |

Piyasa bağlamından örnekler:

| Veri | Değer | Kaynak | Veri tarihi |
|---|---|---|---|
| USD/TRY | 49,4463 | TCMB gösterge kurlar (`today.xml`) | 16.10.2026 |
| Yıllık TÜFE | %29,73 | TCMB EVDS (TP.GENENDEKS.T1) | 2026-09 |
| Sektör TL mevduat faizi (1 aya kadar) | %44,0 | TCMB EVDS (TP.TRYTAS.MT01) | 09.10.2026 |

## 2. Varsayımlar

- **Risk sınırı:** Temkinli risk profili → RD üst sınırı 3 (`decision_config.json`, gerekçeli).
- **Açık kısıt:** "uzun süre kilitli veya düşük likiditeli seçeneklerden kaçınma" → `K_LIKIDITE_KILIDI`. Satış valörü 2 iş gününü aşan fonlar ve 32 günden uzun vadeli mevduat dışlanır.
- **Ufuk:** 1-3 yıl, model için 1 yıl olarak yorumlandı.
- **Teyit maddeleri (notta yazılı):**
  - Doğrudan DİBS'te valör 1 iş günü ve RD kategori varsayımı; DİBS stopajı veride yok.
  - OYAK borçlanma fonunun valörü kategori varsayımı.
  - Politika faizi Ağustos verisi; PPK sonrası teyit edilmeli.

## 3. Gerekçe

**Kriter ağırlıkları.** Her ağırlığın kaynağı kayıtta yazılı:

| Kriter | Ağırlık | Nereden geldi |
|---|---|---|
| Likidite | %25 | likidite ihtiyacı "Yüksek" + öncelik "yüksek likidite" (+0,5) |
| Anapara koruma | %25 | anapara koruma "Yüksek" + öncelik "sermayenin korunması" (+0,5) |
| Düşük oynaklık | %13 | risk seviyesi 0,3 |
| Sadelik | %10 | taban + öncelik "sade ürün yapısı" (+0,5) |
| Gelir | %8 | gelir ihtiyacı "Orta" |
| Getiri | %7 | risk seviyesi 0,3 |
| Maliyet, döviz uyumu | %5'er | sabit taban |

**BGP'nin skor dökümü:**

| Kriter | Katkı | Dayanak |
|---|---|---|
| Likidite | 0,254 | T+0 valör |
| Anapara | 0,242 | kategori yapısı + son 1 yılda %0 düşüş |
| Düşük oynaklık | 0,112 | |
| Sadelik | 0,102 | |
| Gelir | 0,068 | |
| Getiri | 0,054 | |
| Döviz uyumu | 0,046 | |
| Maliyet | 0,043 | |

**Dışlamalar.** Değerlendirilen 2.117 üründen 1.912'si kuralla dışlandı. Her birinin kural kimliği ve gerekçesi kayıtta. Örnek: `R_RISK_PROFILI`: "Risk değeri RD 5, müşteri profilinin üst sınırı RD 3." Dağılım:
- 717 ürün RD sınırı,
- 1.048 serbest fon (nitelikli yatırımcı bilgisi teyit edilmemiş),
- 69 ürün likidite kilidi kısıtı,
- 62 küçük fon,
- 8 ürün getiri verisi yok,
- 8 ürün riski belirlenemedi.

Uygun 200 ürün listeye girmedi; her biri için "neden listede değil" yazılı.

**RL görüşü (yardımcı).**
- Model %100 para piyasası seçiyor, ama yalnızca %5,9 olasılıkla.
- Olasılık ağırlıklı dağılım: para piyasası %56, borçlanma %31, katılım %13.
- Test döneminde bu personada basit kural modelden iyi: %12,3'e karşı %11,6. Not bunu açıkça yazıyor.

## 4. RM onayı

| Alan | Değer |
|---|---|
| Karar | `duzenleyerek_onay` |
| RM | Demo RM (örnek kayıt) |
| Zaman | 18.10.2026 17:49:35 (+03:00) |
| Gerekçe | "Örnek kanıt kaydı. Duyarlılık satırı düzeltildi: 13 senaryo değil, 13 farklı dağılım. RL dağılımı yardımcı görüş; görüşmenin ana gündemi kısa listedeki para piyasası fonları ve 1 yıllık DİBS." |
| Düzenleme | Ajanın notundaki "rastgele 300 piyasa senaryosunda 13'ünde farklı karar" ifadesi "toplam 13 farklı dağılım önerdi" olarak düzeltildi. Ajan metni ve RM'in düzenlenmiş metni ayrı ayrı saklanır. |

Bu düzeltme bir hata analizine dönüştü: ajan aynı alanı iki ayrı çalıştırmada aynı biçimde yanlış okudu. Araç çıktısına alanın nasıl okunacağını söyleyen bir açıklama eklendi (`rl_guvenilirlik` → `piyasa_duyarliligi.okuma`).

## 5. Akış izi

| Adım | Aktör | Süre |
|---|---|---|
| persona_yorumlama | deterministik | <1 ms |
| veri_toplama | deterministik | 5,2 sn |
| uygunluk_kurallari | deterministik | 8 ms |
| puanlama_secim | deterministik | 7 ms |
| rl_dagilim (kısa liste simülasyonu) | rl | 10,9 sn |
| rl_kategori_modeli | rl | 2,3 sn |
| metin_taslagi + metin_guard | llm + deterministik | ajan çalışması ayrıca kayıtlı (araç izi, süre, token) |
| rm_onayi | rm | 18.10.2026 17:49 |
