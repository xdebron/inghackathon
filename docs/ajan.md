# Hazırlık Ajanı: Lokal LLM + Tool Calling

Brief'in istediği ajan bu: RM'in görüşme hazırlığını, verileri ve modelleri **araçlarla sorgulayarak** yazan bir LLM. Ajan karar vermez. Ürün listesi, dışlamalar ve RL dağılımı deterministik katmanın ve RL modelinin çıktısıdır; ajan bunları sorgular, yorumlar ve RM'in okuyacağı metne çevirir.

- **Model:** Qwen3.8-27B (NVFP4), WSL'de vLLM ile, OpenAI uyumlu uç `http://127.0.0.1:8001/v1`. Lokal çalışır (KVKK).
- **Kod:**
  - [backend/app/agent.py](../backend/app/agent.py): ajan döngüsü, araçlar, guard entegrasyonu
  - [backend/app/agent_api.py](../backend/app/agent_api.py): arka plan işleri
  - [backend/app/rl_tools.py](../backend/app/rl_tools.py): model araçları
- **Arayüz:** "Hazırlık metni" sekmesindeki "Hazırlık ajanı" paneli: yaz, sor, canlı araç izi.

## Akış

```
RM "Görüşme hazırlığı üret"  →  deterministik pipeline (~15 sn): kurallar, skor, kısa liste, RL kararı, şablon taslak
RM "Qwen ajanıyla yaz"        →  arka plan işi:
    Qwen ── tool call ──► veri araçları (profil, kısa liste, piyasa, teyitler, dışlananlar, müşteri soruları)
         ── tool call ──► model araçları (rl_karar, rl_neden = Shapley, rl_guvenilirlik, rl_esik, rl_ne_olursa, rl_benzer_donemler)
         ── metin ──► guard (kaynaksız oran, yasaklı ifade, dışlanan ürün kodu, zorunlu ibare)
                        ihlal → ihlaller modele geri bildirilir → düzeltme (en fazla 2 tur)
                        temiz → taslak kayda yazılır (RM onayı hâlâ zorunlu)
                        hâlâ ihlal → şablon taslak kalır, ajan metni ve ihlaller kayıtta görünür
RM "Ajana sor"                →  aynı araçlarla serbest soru (örn. "faiz 5 puan düşerse?")
```

Uzun süren LLM işi arka planda çalışır. Arayüz canlı izi 1,5 sn'de bir yoklar, böylece cloudflared gibi tünellerin ~100 sn'lik istek zaman aşımına takılmaz.

## İki yüz: RM çalışma alanı ve müşteri portalı

| | RM çalışma alanı (`#/rm`) | Müşteri portalı (`#/musteri`) |
|---|---|---|
| Kim kullanır | Relationship Manager | Müşteri (demo: profil seçimi) |
| Ajan ne yapar | Hazırlık notu yazar; serbest soruları ve what-if senaryolarını yorumlar | Müşteriye **doğrudan yanıt vermez** |
| What-if | Hazır senaryolar (faiz indirimi, enflasyon, kur şoku, borsa düşüşü), gösterge kaydırıcıları, persona değişikliği, eşik bandı; "Ajana yorumlat" | Yok |
| Müşteriye giden içerik | Ajan sade bir **müşteri özeti** ve müşteri sorularına **yanıt taslağı** yazar; RM düzenler ve onaylar | Yalnızca RM'in onayladığı özet ve yanıtlar görünür |

Bu yapı brief'in iki kuralını korur: "Müşteriyle doğrudan etkileşime giren chatbot" kapsam dışıdır ve "RM onayı olmadan hiçbir çıktı müşteriye iletilecek biçimde sunulmaz" (kural 4).

Müşteri tarafındaki ajan görevleri kısıtlı araçla çalışır: profil, kısa liste, piyasa ve RL kararı. İç değerlendirme verileri (skor, kural kimliği, veri kalitesi uyarıları) müşteri metnine girmez. Müşteri API'si (`/api/musteri/...`) yalnızca onaylı metni ve genel profil alanlarını döndürür. Bu durum testle doğrulanıyor: `tests/test_portal.py`.

**Hata analizi.** Müşteri sorusuna ("param ne zaman hesaba geçer?") ilk taslakta ajan hiç araç çağırmadan "genellikle 1–2 iş günü" yazdı. Bu bilgi yanlıştı: kısa listedeki fonların valörü T+0'dır. Yüzde içermediği için guard da yakalayamadı.
- *İyileştirme:* müşteri soru-cevabında `kisa_liste` ve `profil_ozeti` çağrısı zorunlu; araçta olmayan süre veya genel bilgi yasak.
- *Sonuç:* yanıt valörü araçtan alıyor (fonlar T+0, tahvil T+1).
- *Dikkat:* guard yalnızca yüzdeleri denetler. Gün ve süre gibi diğer sayılar istem kuralı ve RM onayıyla korunur.

## Araçlar

| Araç | Kaynak | Ne döndürür |
|---|---|---|
| `profil_ozeti` | profil yorumlayıcı | persona, RD sınırı, uygulanan kısıt kuralları, kriter ağırlıkları |
| `kisa_liste` | kural motoru + skorlama | her ürün: skor, güven, öne çıkan kriterler, göstergeler (değer, kaynak, tarih, durum), uyarılar |
| `urun_detay` | aynı | tek ürünün skor dökümü; geçersiz kimlikte geçerli kimlikleri listeler |
| `dislanan_ozeti` | kural motoru | dışlanan ürünler, kural bazında sayı ve örnek gerekçe (ad/kod içermez) |
| `piyasa_baglami` | TCMB, EVDS, Hazine vb. | göstergeler kaynak ve tarihiyle |
| `teyit_listesi` | veri kalitesi | RM'in teyit etmesi gerekenler |
| `musteri_sorulari` | profil | görüşmede sorulabilecek sorular |
| `rl_karar` | RL modeli (gerçek ağ) | dağılım, alternatifler, PPO olasılıkları / karar marjı, güvenli kapı |
| `rl_neden` | gerçek ağ, **tema Shapley** | ortalama piyasa → bugün karar farkının temalara kesin paylaştırılması |
| `rl_ne_olursa` | gerçek ağ | gösterge veya persona değişikliğinde yeni karar (birimler otomatik denetlenir) |
| `rl_esik` | gerçek ağ | kararın değiştiği gösterge eşikleri |
| `rl_benzer_donemler` | gerçek ağ + tarihsel veri | bugüne en benzer geçmiş anlar ve sonrasında gerçekleşen reel getiri |
| `rl_guvenilirlik` | model meta + bugünkü durum | dağılım dışı/kırpılan göstergeler, basit kurala karşı test, kalibrasyon |

## Güvenlik ve izlenebilirlik

- **Sayılar:** guard'ın izin listesi yalnızca o çalışmadaki araç çıktılarından üretilir. Metinde araçlarda olmayan bir oran geçerse ihlaldir.
- **Dışlanan ürünler:** metinde adları ve kodları geçemez. Model türü etiketi bu yüzden "NN-politika": "PPO" bir TEFAS fon koduyla çakışıyordu (bkz. hata analizi).
- **Zorunlu ibare:** "yatırım tavsiyesi değildir / RM onayı" ibaresini kod ekler, modele bırakılmaz.
- **Kanıt kaydı:** her ajan çalışması kayda girer: araç çağrıları (ad, argüman, süre, sonuç özeti), düzeltme turları, token kullanımı, model adı, uygulanıp uygulanmadığı. RM onayı ayrı ve zorunludur.

## Sonuçlar (16.10.2026, Qwen3.8-27B, RL modeli `rl_ppo_v9`)

| Persona | Araç çağrısı | Düzeltme | Süre | Guard | Taslağa uygulandı |
|---|---|---|---|---|---|
| P01 | 13 | 1 | 181 sn | temiz | evet |
| P02 | 14 | 1 | 193 sn | temiz | evet |
| P03 | 19 | 2 | 283 sn | temiz | evet |
| P04 | 17 | 2 | 285 sn | temiz | evet |
| P05 | 15 | 1 | 211 sn | temiz | evet |
| P05, soru: "faiz −5 puan / Dengeli olsaydı?" | 4 | 1 | 28 sn | temiz | (soru) |

5/5 persona guard'dan temiz geçti. Düzeltme turları kaynaksız oran ihlallerinden geldi; ajan geri bildirimle hepsini düzeltti.

Ajan her personada temel araçları (profil, kısa liste, piyasa, teyitler, dışlananlar, RL kararı, Shapley, güvenilirlik) çağırdı. Ek analizleri kendi seçti:
- P02 ve P03'te iki ayrı eşik taraması ve benzer dönem araması.
- P04'te kısa listedeki her ürünün skor dökümü.

## Hata analizi ve iyileştirmeler

1. **Uydurma ürün kimlikleri.**
   - *Gözlem:* ilk denemede ajan `urun_detay`'ı `FON:KATILIM:AKBANK` gibi kimliklerle 30 kez çağırdı. 43 araç çağrısı ve 320 sn harcadı, adım sınırına takıldı.
   - *İyileştirme:* geçersiz kimlikte geçerli kimlikler listelenir; 3 başarısız çağrıdan sonra araçlar kapatılıp ajan yazmaya yönlendirilir; istemde "kimlik uydurma" kuralı; adım sınırı 6.
   - *Sonuç:* 13-15 araç, ~3 dk.
2. **Fon koduyla çakışan model adı.**
   - *Gözlem:* guard "PPO"yu dışlanan bir TEFAS fon kodu sanıp metni reddetti. Ajan RL modelinin türünü yazmıştı.
   - *İyileştirme:* araç çıktılarında model etiketi "NN-politika" yapıldı.
3. **Birim hatası.**
   - *Gözlem:* "politika faizi 5 puan düşerse" sorusunda ajan `degisim: -5` gönderdi. Göstergeler oran birimindedir (0,37), yani bu −%463 demektir. Kırpma yüzünden sonuç "değişmez" çıktı. Ajan kırpma uyarısını dürüstçe yazdı, ama hesap yanlıştı.
   - *İyileştirme:* araç tanımına birim açıklaması eklendi ("5 puan = −0.05"). Oran birimli göstergede |değişim| ≥ 1 gelirse yüzde puan olarak yorumlanıp çevriliyor ve çıktıda açıkça belirtiliyor.
   - *Test:* `test_percentage_point_change_is_interpreted_in_ratio_units`.

## Testler

- [tests/test_agent.py](../backend/tests/test_agent.py) (sahte LLM ile):
  - araç döngüsü
  - kaynaksız oranda düzeltme turu
  - yasaklı ifade
  - hata döngüsünün kırılması
  - izin listesi
  - LLM sunucusu yokken şablona dönüş
- [tests/test_rl_tools.py](../backend/tests/test_rl_tools.py):
  - Shapley toplamı gerçek karar farkına eşit
  - karşı-olgusal, eşik ve benzer dönem tutarlılığı
  - birim yorumu
