# 4. Çözüm tasarımı

## Akış ve roller

Her adım bir aktörle etiketlenir: deterministik kod, RL modeli, LLM veya RM. Bu etiket kanıt kaydındaki "akış izi"nde süreleriyle birlikte görünür.

```
Persona (P01–P05, jüri değişiklikleri)
   │
   ▼
1 persona_yorumlama     [deterministik]  alanlar → risk seviyesi, RD sınırı, ufuk, kurallar
   │                                      eksik / çelişkili / eşleşmeyen kısıt?  ──►  RM TEYİT SORUSU (akış durur)  ◄── RM
   ▼
2 veri_toplama          [deterministik]  TCMB, EVDS, TEFAS dışa aktarım, KAP, ING, Hazine
   │                                      her değer: kaynak · URL · veri tarihi · alınma zamanı · durum
   ▼
3 uygunluk_kurallari    [deterministik]  RD sınırı + 23 kurallık kısıt kataloğu → dışlama (kural id + gerekçe)
   ▼
4 puanlama_secim        [deterministik]  8 kriter, persona kaynaklı ağırlıklar, güven skoru → kısa liste (5)
   ▼
5 rl_kategori_modeli    [rl]             NN-PPO v14: kategori dağılımı + seçim olasılığı + olasılık ağırlıklı dağılım
   ▼
6 metin_taslagi         [llm]            Qwen3.8-27B ajanı, araçlarla (yoksa deterministik şablon)
   ▼
7 metin_guard           [deterministik]  yasaklı ifade · dışlanan ürün · kaynaksız oran · zorunlu ibare
   ▼
8 RM onayı              [rm]             onay / düzenleyerek onay / red + gerekçe  ──►  kanıt kaydı (JSON)
   ▼
9 müşteri görünümü      [rm]             yalnızca RM'in ayrıca onayladığı özet ve yanıtlar
```

## Bileşenler

| Bileşen | Teknoloji | Görev |
|---|---|---|
| Backend | Python 3.11+, FastAPI | Akış, kurallar, puanlama, kanıt kaydı, API. Frontend'i aynı porttan sunar. |
| Frontend | React + TypeScript (Vite) | RM çalışma alanı, müşteri görünümü, model, veri, değerlendirme sayfaları |
| Veri tabanı | SQLAlchemy + SQLite (`app/db`) | Tüm piyasa ve ürün verisi; her satır kaynak, URL, veri tarihi, alınma zamanı ve çekim kimliği taşır ([docs/veri_tabani.md](../veri_tabani.md)) |
| Toplayıcılar | `app/collectors`, `tools/veri_topla.py` | TCMB, EVDS, Hazine, KAP, ING, BIST'ten otomatik ve kibar toplama; her çalıştırma bir çekim kaydı. TEFAS yüklemeyle. |
| Veri katmanı | `app/sources/*`, `app/universe.py` | Veri tabanından ürün evreni ve göstergeler; bayatlık, çapraz kontrol, kaynak kesintisi simülasyonu |
| Kural motoru | `app/rules.py`, `config/constraint_rules.json` | Kısıt metni → kural eşlemesi, dışlama, teyit sorusu |
| Puanlama | `app/scoring.py` | Ağırlık türetme, normalize kriterler, güven, kısa liste |
| RL modeli | `app/rl_category.py`, `app/rl_tools.py` | Numpy ile çevrimiçi çıkarım (torch gerekmez). Shapley, what-if, eşik ve benzer dönem araçları. |
| LLM ajanı | `app/agent.py`, vLLM (WSL) üzerinde Qwen3.8-27B | Araç çağıran döngü (en fazla 6 adım), guard düzeltme turları, şablon yedeği |
| Guard | `app/guard.py` | Metin güvenlik kontrolü; RM'e ve müşteri görünümüne giden her metin için |
| Kanıt | `app/evidence.py`, `backend/evidence/*.json` | Her çalıştırmanın tam kaydı, config hash'i, RM kararı |
| Değerlendirme | `tools/degerlendir.py` | Uçtan uca test takımı; sonuçlar arayüzde |

## İnsan onay noktaları

1. **Teyit:** eksik, çelişkili veya tanınmayan persona bilgisi ya da eşleşmeyen kısıt varsa öneri üretilmez. RM soruyu yanıtlar veya "bilerek onaylıyorum" der. Yanıt kayda geçer.
2. **Not onayı:** taslak not, guard'dan geçse bile RM onayı olmadan "onaylandı" durumuna geçmez. RM metni düzenleyebilir; düzenlenmiş metin ve gerekçe saklanır.
3. **Müşteri görünümü:** müşteriye gösterilebilecek kısa özet ve müşterinin sorularına yanıtlar ayrı ayrı RM onayı gerektirir. Onaysız hiçbir içerik müşteri görünümüne çıkmaz (kural 4). Müşteri sorusu doğrudan cevaplanmaz; RM kuyruğuna düşer.
4. **What-if ve RL:** RM senaryoları kendisi çalıştırır. Model çıktısı karar değil, karar desteği olarak etiketlenir (kural 9).

## Neden bu tasarım?

- **Deterministik çekirdek, LLM kenarda.** Uyum (kısıt, RD, kaynak) kodla garanti edilir; LLM yalnızca anlatır. LLM kapalıyken de sistem uçtan uca çalışır, şablon metinle. Jüri challenge'ında davranış tekrarlanabilir: aynı girdi aynı listeyi verir (değerlendirmede 5/5).
- **Config'te gerekçeli varsayımlar.** Her eşik ve ağırlık gerekçesiyle tek yerde durur ve kanıt kaydına hash'lenir. Bir varsayım değişince hangi kaydın hangi varsayımla üretildiği bilinir.
- **Lokal LLM.** KVKK: müşteri profili dışarıya gönderilmez. 27B model araç çağırma için yeterli; guard ve araç sınırları küçük modelin hata payını kapatır.
- **Açıklamayı gerçek modelden almak.** Önce EBM ile vekil model (distillation) düşündük. Vekil model gerçek modelle her durumda aynı kararı vermez. Bunun yerine açıklamaları ağın kendisini sorgulayarak üretiyoruz: kesin Shapley, karşı-olgusal ve eşik.
- **Tek port.** Backend derlenmiş arayüzü de sunar; tek tünelle (cloudflared) demo yapılabilir.

## Trade-off'lar

| Seçim | Kazanç | Bedel |
|---|---|---|
| Kural tabanlı uygunluk | Garanti, açıklanabilirlik | Yeni kısıt türü kataloğa eklenmeli; tanınmayan kısıtta akış durur (bilinçli) |
| Min-max normalize puan | Basit, ürün evrenine göre göreli | Evren değişince puanlar kayar; mutlak değil göreli sıralama |
| RL kategori modeli | Piyasa koşuluna duyarlı senaryo, what-if | Basit kuralı her personada geçemiyor; politika düz → yardımcı görüş olarak konumlandı |
| TEFAS dışa aktarım yüklemesi | Uygulama TEFAS'a istek atmaz | Fiyat güncellemesi kullanıcının yüklemesiyle. Mevcut 2021-2026 geçmişinin bir kısmı, kullanıcı talimatıyla yazılmış ve sitenin WAF'ını geçen tarayıcı otomasyonuyla (`tefascrawl`) alındı; kullanım koşullarıyla çelişebilir, ekipçe teyit edilmeli |
| Veri tabanı + otomatik toplayıcılar | Elle düzenlenen dosya yok; her satırda kaynak ve çekim kaydı; tek tıkla güncelleme | Kaynak sayfa yapısı değişirse toplayıcı hata verir (kayda geçer, değer uydurulmaz) |
| Lokal LLM | KVKK | GPU gerektirir; kapalıyken şablon metin |
