# RM Hazırlık Asistanı: Private Banking Yatırım Karar Destek

ING Hubs Türkiye Agentic AI Hackathon projesi. Private Banking RM'inin müşteri görüşmesine hazırlanmasını destekler. Sistem kamuya açık piyasa ve ürün verisini toplar, ürünleri persona profiline göre filtreleyip sıralar, her kararı kaynak ve gerekçesiyle gösterir, RM için bir görüşme taslağı üretir. **Müşteriye hiçbir çıktı gitmez. Karar RM'indir.**

**Teslim paketi** (problem tanımı, analitik çözüm, tasarım, test ve öğrenimler, kanıt örneği, demo senaryosu): [docs/teslim](docs/teslim/README.md).

## Mimari

```
Persona ──► 1 persona_yorumlama (deterministik) ──► eksik / çelişkili / tanınmayan alan? ──► RM teyit soruları (akış durur)
                     │
                     ▼
            2 veri_toplama (deterministik)  TCMB canlı · TEFAS dışa aktarım · manuel kaynaklar (mevduat, stopaj, KAP)
                     │                       her değer: kaynak, url, veri tarihi, alınma zamanı, durum
                     ▼
            3 uygunluk_kurallari            risk profili ↔ RD, açık kısıt kuralları, nitelikli yatırımcı, veri yeterliliği
                     │                       her dışlama: kural id + gerekçe
                     ▼
            4 puanlama_secim                ağırlıklar persona alanlarından; kriter bazında katkı dökümü + güven skoru
                     │
                     ▼
            5 metin_taslagi (LLM)           yalnızca anlatım; ürün listesi ve sayılar 3-4'ten gelir
                     ▼
            6 metin_guard                   yasaklı ifade, dışlanan ürün, kaynağı olmayan oran kontrolü
                     ▼
            7 RM onayı                      onay / düzenleyerek onay / red ──► kanıt kaydı (backend/evidence/*.json)
```

- **LLM:** KVKK gereği nihai sürümde lokal bir model kullanılacak. Şu an `PlaceholderLLM` deterministik şablonla çalışıyor ([backend/app/llm.py](backend/app/llm.py)).
- **Tüm varsayımlar** (risk profili → RD üst sınırı, kategori özellikleri, ağırlıklar, eşikler) [backend/config/decision_config.json](backend/config/decision_config.json) dosyasında gerekçeleriyle duruyor. Açık kısıt → kural eşlemesi [backend/config/constraint_rules.json](backend/config/constraint_rules.json) dosyasında. Her kanıt kaydı bu dosyaların hash'ini taşır.

## Kurulum

Gereksinimler: Python 3.11+ ve Node 20+.

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m uvicorn app.main:app --port 8010
```

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (API isteklerini 8010'a yönlendirir)
```

**Tek port (dışarıya açmak için):** frontend derlendikten sonra backend arayüzü de aynı porttan sunar.

```bash
cd frontend && npm run build
```

```bash
cloudflared tunnel --url http://localhost:8010
```

Kodda değişiklik yapıldığında `npm run build` tekrar çalıştırılmalı. Geliştirme sırasında Vite (5173) de kullanılabilir; `/api` isteklerini 8010'a yönlendirir.

Testler:

```bash
cd backend
.venv/Scripts/python -m pytest -q
```

Değerlendirme takımı (brief kuralları uçtan uca: 5 persona + tekrar, veri kesintileri, eksik/çelişkili bilgi, jüri tipi değişiklikler). Sonuç arayüzde **Değerlendirme** sayfasında görünür:

```bash
cd backend
.venv/Scripts/python tools/degerlendir.py
```

Gerçek veri yüklemeden arayüzü denemek için backend'i `SENTETIK_DEMO=1` ile başlatın. Bu modda ürünler test fikstürüdür ve ekranda bu durum açıkça uyarı olarak gösterilir.

## Veri yükleme

Kaynakların tam listesi ve durumu: [docs/veri_kaynaklari.md](docs/veri_kaynaklari.md).

**Hazır veri seti:** Gamze'nin 9-14 Ekim'de topladığı veriler snapshot'lara aktarıldı:
- TEFAS fiyat geçmişi: 288 fon
- KAP fon bilgileri: 1026 fon
- Stopaj ve ING mevduat oranları
- Göstergeler ve BIST 100

Yeniden aktarmak için:

```bash
backend/.venv/Scripts/python backend/tools/import_gamze.py
```

Ayrıntılar ve bulgular: [docs/gamze_aktarim.md](docs/gamze_aktarim.md).

| Kaynak | Nasıl | Not |
|---|---|---|
| TCMB döviz kurları | Otomatik (`today.xml`) | Canlı çekim başarısız olursa son önbellek "bayat" etiketiyle kullanılır |
| TCMB EVDS: TÜFE, sektör mevduat ortalamaları, altın | Otomatik, `EVDS_API_KEY` ortam değişkeni gerekli | Seri kodları [config/evds_series.json](backend/config/evds_series.json) dosyasında; anahtar yoksa manuel değer kullanılır |
| TEFAS Tarihsel Veriler (fiyat geçmişi) | tefas.gov.tr → **Excel'e Aktar**, ardından arayüzden yükle | TEFAS script erişimini engelliyor. 1 yıllık getiri için en az 12 ay veri gerekli |
| TEFAS Fon Getirileri (eski adı Fon Karşılaştırma; `tefas.gov.tr/tr/fon-getirileri`) | Aynı yükleme alanı; tür sütunlardan otomatik tanınır. Dönem sonu tarihini girin | İkinci kaynak: getiriler fiyat geçmişiyle çapraz kontrol edilir |
| Mevduat / katılım oranları, stopaj, KAP fon bilgileri, göstergeler (politika faizi, TÜFE, DİBS, TMSF limiti), benchmark'lar | [backend/data/snapshots/manuel_veriler.json](backend/data/snapshots/manuel_veriler.json) | Her kayıtta `source`, `url`, `as_of` zorunlu; eksikse değer kullanılmaz |

## Lokal LLM (vLLM, WSL)

KVKK gereği lokal model: **Qwen3.8-27B-NVFP4** (`nvidia/Qwen3.8-27B-NVFP4`), WSL'de vLLM ile. Adres `http://127.0.0.1:8001/v1`, model adı `qwen3.8-27b-nvfp4`. Sunucu tool calling'i destekler (`qwen3_coder` parser).

```bash
powershell -ExecutionPolicy Bypass -File scripts\vllm_ac.ps1
```

```bash
powershell -ExecutionPolicy Bypass -File scripts\vllm_kapat.ps1
```

- **Açma:** hazır olana kadar bekler; ilk açılışta GPU kernel'i derlendiği için ~8 dakika sürebilir.
- **GPU paylaşımı:** sunucu GPU'nun ~29 GB'ını kullanır. RL eğitimleri çalışırken açma script'i başlatmayı reddeder.
- **Ayrıntılar:** WSL'de `~/qwen38_vllm.md`.

## Kurallar ve güvenlik

- Veri çekilemezse sistem değer uydurmaz: alan "eksik" işaretlenir, kriter skora katılmaz ve güven skoru düşer.
- Açık kısıtlar deterministik kurallarla uygulanır. Eşleşmeyen bir kısıt gelirse akış durur ve RM'e sorulur.
- Stopaj oranları koda sabit yazılmaz. Yüklenmemişlerse getiriler brüt gösterilir ve RM'e teyit notu düşülür.
- Taslak metin guard kontrolünden geçmeden ve RM onayı alınmadan "onaylandı" durumuna geçemez.

## Jüri challenge'ı için

Arayüzde persona alanları düzenlenebilir: risk profili, kısıtlar, likidite ve diğer alanlar değiştirilebilir. "Veri kesintisi simülasyonu" ile herhangi bir kaynak kapatılabilir. Her değişiklik yeni bir kanıt kaydı oluşturur.
