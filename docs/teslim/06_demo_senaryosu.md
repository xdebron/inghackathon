# 7. Sunum ve demo senaryosu (7 dk + 3 dk soru-cevap)

Slaytlar: "RM Hazırlık Ajanı – Sunum" (claude.ai artifact; PowerPoint/PDF olarak indirilebilir). Konuşma notları her slaytın altında.

## Hazırlık (sunumdan 15 dk önce)

1. Lokal LLM'i aç. İlk açılış ~10 dk sürebilir; hazır olunca arayüzün sağ üstünde model adı yeşil görünür.
   ```bash
   powershell -ExecutionPolicy Bypass -File scripts\vllm_ac.ps1
   ```
2. Backend'i aç: `cd backend && .venv/Scripts/python -m uvicorn app.main:app --port 8010`. Dışarıdan erişim gerekiyorsa `cloudflared tunnel --url http://localhost:8010`.
3. Tarayıcıda `http://localhost:8010` aç. P05 ve P03 için "Hazırlık üret"e bir kez basıp önbelleği ısıt (ilk çalıştırma ~30 sn, sonrakiler birkaç saniye).
4. Değerlendirme sayfasının dolu olduğunu kontrol et (14/14).

## Akış

| Süre | Slayt / ekran | Ne gösterilir |
|---|---|---|
| 0:00 | Kapak, Problem | Dört sorun; hedef: hız, izlenebilirlik, RM kontrolü |
| 0:55 | Çözüm tasarımı | 9 adım, aktörler, RM'in üç kapısı |
| 1:40 | **Canlı: P05 Hekim** | Kısa liste (5 ürün) → bir ürünün skor dökümü ve güven skoru → "Kanıt" sekmesinde dışlananlar: 69 ürün "satış valörü 3 iş günü (eşik 2)" kuralıyla → piyasa verisi tablosunda kaynak, veri tarihi, alınma zamanı |
| 2:40 | **Canlı: not ve ajan** | "Ajan ve not" sekmesinde görüşme notu (teyit maddeleri, müşteriye sorulacaklar). Ajana soru: "Neden mevduat değil de para piyasası fonu?" → araç izi |
| 3:20 | **Canlı: P03 what-if** | "Kur şoku" hazır senaryosu → olasılık ağırlıklı dağılımda döviz/altın payının artışı; belirsizlik uyarısı |
| 3:50 | **Canlı: onay** | RM adı yaz, "Onayla" → Kanıt sekmesinde akış izi ve ham JSON |
| 4:10 | Uyum, Veri, Sıralama | Kural kataloğu ve teyit sorusu; 1.079/1.079 kaynaklı ve tarihli veri; ağırlıkların persona alanlarından gelişi |
| 5:25 | RL, LLM | Modelin dürüst sonucu (kurala karşı), neden yardımcı görüş; lokal LLM + araçlar + guard |
| 6:10 | Testler, Hatalar | 14/14, sıfır tolerans göstergeleri; beş hata ve iyileştirmesi |
| 6:45 | Kapanış | Özet ve bilinen sınırlar |

## Jüri challenge'ı için hazır hamleler

| Jüri ne değiştirebilir | Nerede | Beklenen davranış |
|---|---|---|
| Risk profili (ör. P03 Agresif → Dengeli) | "Profil ve veri ayarları" → alan → "Görüşme hazırlığı üret" | RD sınırı 7 → 5; liste ve RL dağılımı değişir; ağırlık dökümü nedenini gösterir |
| Yeni kısıt (ör. "yüksek riskli ürünlerden kaçınma") | Açık kısıtlar alanına satır ekle | Kataloğa eşlenir; profille çelişirse RM'e soru sorulur |
| Tanınmayan kısıt (ör. "kripto istemiyorum") | Açık kısıtlar | Akış durur, RM kurala eşler ya da "uygulama" der (gerekçe kayda geçer) |
| Eksik bilgi (risk profilini sil) | Alanı boşalt | Öneri üretilmez; "Müşterinin risk profili bilgisi nedir?" |
| Veri kesintisi (ör. TEFAS kapalı) | "Veri kesintisi simülasyonu" kutuları | Fonlar evrenden çıkar, uyarılar artar, değer uydurulmaz |
| Piyasa senaryosu (faiz −5 puan) | What-if sekmesi | İlgili tüm göstergeler birlikte değişir; dağılım ve olasılık yeniden hesaplanır |
| LLM kapalı | vLLM'i kapat | Not deterministik şablonla üretilir, akış durmaz |

## Soru-cevap için kısa yanıtlar

- **Neden RL değil de kural + puan ana öneri?** Uyumu kod garanti etmeli. Test döneminde RL basit kuralı her personada geçemedi; bu yüzden yardımcı senaryo aracı.
- **LLM hata yaparsa?** Sayılar araçlardan gelir; guard garanti dilini, dışlanan ürünü, kaynaksız oranı reddeder; RM onayı olmadan hiçbir şey kesinleşmez.
- **Müşteri portalı brief dışında değil mi?** Müşteriye otomatik içerik gitmez: yalnızca RM'in ayrıca onayladığı özet ve yanıtlar görünür; müşteri sorusu RM kuyruğuna düşer (kural 4).
- **Veri ne kadar güncel?** Her değerin veri tarihi ve alınma zamanı ekranda; eski veri "bayat" etiketi alır.
