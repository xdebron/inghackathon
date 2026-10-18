import { useEffect, useState } from 'react'
import { CAT_LABEL } from '../components/AllocBar'

// Brief 8.3 / 8.5 / 9.1: yöntem gerekçesi, test senaryoları, başarı göstergeleri, hata analizi ve yapılan iyileştirmeler.
// Senaryo sonuçları tools/degerlendir.py'nin son çalıştırmasından gelir (/api/degerlendirme).

interface Checks {
  rd_ihlali: string[]; dislanan_urun_listede: string[]; rl_kapali_kategori: string[]; guard_ihlali: string[]
  uyari_metni_var: boolean; kaynaksiz_veri: string[]; veri_tarihsiz: string[]; veri_noktasi: number
}
interface Scenario {
  grup: string; ad: string; gecti: boolean; durum: string; sure_sn: number; run_id: string
  kontroller?: Checks; kisa_liste?: number; dislanan?: number; tekrar_ayni_liste?: boolean; tekrar_ayni_rl?: boolean
  rl_dagilim?: Record<string, number> | Record<string, number>[]; rl_secilen_olasilik?: number | null
  uydurulmus_deger?: string[]; eksik_veya_bayat_isaretli?: number; uyari_sayisi?: number; sorular?: string[]
  rd_siniri?: number[]; uygulanan_kurallar?: string[]
}
interface Evaluation {
  calisma_zamani: string; senaryolar: Scenario[]
  ozet: { senaryo: number; gecen: number; kural_ihlali: number; guard_ihlali: number; kaynaksiz_veri: number; alinma_zamani_yok?: number; uydurulmus_deger: number; veri_noktasi: number; ortalama_sure_sn: number }
}

const METRICS: [string, string][] = [
  ['Açık kısıt ve risk profiline uyum', 'Kısa listede RD sınırını aşan, kuralla dışlanmış ürün ya da RL dağılımında kapalı kategori sayısı. Hedef 0: brief kural 5, tek bir ihlal açık kural ihlalidir.'],
  ['Güvenli metin', 'Guard ihlali (garanti / kesin getiri ifadesi, dışlanan ürün, kaynağı olmayan oran) ve zorunlu "RM onayı olmadan paylaşılamaz" uyarısı. Hedef: 0 ihlal, uyarı her metinde.'],
  ['Kaynak ve zaman damgası', 'Değeri olan her veri noktasında kaynak ve alınma zamanı var mı? Hedef: eksik 0 (brief kural 2).'],
  ['Veri kesintisinde dürüstlük', 'Kaynak kapatıldığında o kaynaktan "ok" görünen değer (uydurma) sayısı. Hedef 0; eksikler işaretlenmeli, akış durmamalı (kural 3).'],
  ['Eksik / çelişkili bilgi', 'Eksik veya çelişkili persona alanında öneri üretmeden RM teyit sorusu sorulması (brief 7.1).'],
  ['Tekrarlanabilirlik', 'Aynı girdiyle iki çalıştırmada kısa liste ve RL dağılımı aynı mı?'],
  ['İşlem süresi', 'Bir hazırlığın uçtan uca süresi (veri toplama dahil); RM hazırlık süresine katkının vekil ölçüsü.'],
]

const LEARNINGS: { hata: string; analiz: string; iyilestirme: string }[] = [
  { hata: 'Guard, fon adındaki "Garanti" kelimesini getiri garantisi sanıp metni reddetti.',
    analiz: 'Yasaklı ifade taraması özel adları ayırt etmiyordu; doğru metin yanlış pozitifle düşüyordu.',
    iyilestirme: 'Kısa listedeki ve RL eşlemesindeki ürün adları muaf tutuluyor; muaf adlar kanıt kaydına yazılıyor.' },
  { hata: 'Ajan (LLM) var olmayan ürün kimlikleriyle araç çağırdı.',
    analiz: 'Model kimlikleri tahmin ediyordu; hata mesajı geçerli seçenekleri göstermiyordu.',
    iyilestirme: 'Araç hatası geçerli kimlik listesini döndürüyor; 3 ardışık hatada döngü kesiliyor ve şablon metne düşülüyor.' },
  { hata: 'Ajan "faiz 5 puan düşerse" senaryosunda değişimi 5 (yani %500) olarak gönderdi.',
    analiz: 'Araç açıklaması birimi belirtmiyordu.',
    iyilestirme: 'Açıklamada oran birimi örnekle yazıldı; |değişim| ≥ 1 olan oran göstergesi otomatik yüzde puana çevrilip kayda not düşülüyor.' },
  { hata: 'What-if ekranında para piyasası 3 ay getirisi −%5,7 girilebiliyordu; model bunu %1,9 görüyordu ve sonucu değiştirmiyordu.',
    analiz: 'Değer tarihte hiç görülmemişti ve model girdiyi ±2σ\'da kırpıyordu; tek göstergeli şoklar ekonomik olarak tutarsızdı.',
    iyilestirme: 'Kaydırıcılar geçmişte görülmüş aralıkla sınırlandı, hazır senaryolar ilgili tüm göstergelere birlikte uygulanıyor; v14 modeli what-if tutarlılık düzenleyicisiyle eğitildi (makro şoklarda yanlış yön tepki %0).' },
  { hata: 'RL kartı tek bir dağılımı kesin karar gibi gösteriyordu; model o dağılımı %1–9 olasılıkla seçiyordu.',
    analiz: 'Politika birçok dağılım arasında düz; en olası dağılım (argmax) küçük girdi farklarına duyarlı.',
    iyilestirme: 'Not, RL kartı ve what-if olasılık ağırlıklı dağılımı ve seçim olasılığını gösteriyor; %20 altında açık belirsizlik uyarısı var.' },
  { hata: 'Ajan iki ayrı çalıştırmada "300 rastgele piyasada 13 farklı karar" alanını “13’ünde farklı karar” diye okudu.',
    analiz: 'Araç çıktısındaki alan adı iki şekilde okunabiliyordu; RM bunu notu düzenleyerek onaylarken yakaladı.',
    iyilestirme: 'Araç çıktısına alanın nasıl okunacağını söyleyen cümle eklendi ("bu bir senaryo sayısı değil, farklı dağılım sayısıdır").' },
  { hata: 'KAP risk değeri ayrıştırıcısı "TL 6, USD 3" gibi pay sınıfı ayracını ondalık sandı; 13 fonda RD olduğundan düşüktü (ör. 1 yerine 6).',
    analiz: 'Virgülden sonraki rakam dışlanıyordu; birden çok değerde tutucu (yüksek) değer seçilemiyordu. Veri tabanına geçişte yazılan toplayıcı testi buldu.',
    iyilestirme: 'Ayrıştırıcı düzeltildi, ham sayfalar yeniden okunup 13 kayıt notla güncellendi; etkilenen fon hiçbir kısa listeye girmemişti. Regresyon testi var.' },
  { hata: 'Eşzamanlı iki TEFAS içe aktarımı fiyat geçmişini kısalttı (veri kaybı).',
    analiz: 'Yazma işlemleri kilitsiz ve atomik değildi.',
    iyilestirme: 'Kilit, yedek (son 5), atomik yazma ve "satır sayısı azalıyorsa yazma" koruması eklendi; test_import_safety ile sınanıyor.' },
]

const APPROACH: [string, string][] = [
  ['Açık kısıtlar → deterministik kurallar', 'Uyum ihlali kabul edilemez ve geriye dönük gösterilebilmeli. Kural kimliği ve gerekçesiyle dışlama, LLM yorumuna bırakılmaz.'],
  ['Ürün sıralaması → şeffaf ağırlıklı puan', 'Ağırlıklar persona alanlarından gelir, her kriterin katkısı ve dayandığı veri gösterilir. Daha karmaşık bir model, RM\'in "neden bu ürün?" sorusuna bu kadar net yanıt veremez.'],
  ['Kategori dağılımı → profile koşullu RL (yardımcı görüş)', 'Geçmiş piyasa koşullarında dağılım kararlarını simüle eder. Açıklama vekil modelden değil, gerçek ağı sorgulayarak (kesin Shapley, karşı-olgusal, eşik) üretilir. Belirsizliği olasılıkla gösterilir; nihai öneri değildir.'],
  ['Metin ve soru-cevap → lokal LLM + araçlar', 'LLM yalnızca anlatır; sayıları araçlardan alır, guard kaynağı olmayan oranı ve yasaklı ifadeyi reddeder. KVKK nedeniyle model lokal çalışır.'],
  ['Her çıktı → RM onayı', 'Not, müşteri özeti ve müşteri sorularının yanıtları RM onaylamadan müşteri görünümüne çıkmaz; karar, düzenleme ve gerekçe kanıt kaydına yazılır.'],
]

const STATUS: Record<string, string> = { rm_onayi_bekliyor: 'RM onayı bekliyor', rm_teyidi_bekliyor: 'RM teyidi bekliyor', oneri_yok: 'Öneri yok' }
const dist = (d?: Record<string, number>) => d ? Object.entries(d).map(([c, v]) => `${CAT_LABEL[c] ?? c} %${Math.round(v * 100)}`).join(', ') : '—'

function detail(s: Scenario) {
  const c = s.kontroller
  const parts: string[] = []
  if (s.kisa_liste !== undefined) parts.push(`kısa liste ${s.kisa_liste}`)
  if (s.dislanan !== undefined) parts.push(`dışlanan ${s.dislanan}`)
  if (c) parts.push(`${c.veri_noktasi} veri noktası, kaynaksız ${c.kaynaksiz_veri.length}`)
  if (c) parts.push(`kural ihlali ${c.rd_ihlali.length + c.dislanan_urun_listede.length + c.rl_kapali_kategori.length}, guard ${c.guard_ihlali.length}`)
  if (s.tekrar_ayni_liste !== undefined) parts.push(`tekrar: liste ${s.tekrar_ayni_liste ? 'aynı' : 'FARKLI'}, RL ${s.tekrar_ayni_rl ? 'aynı' : 'FARKLI'}`)
  if (s.uydurulmus_deger) parts.push(`uydurulmuş değer ${s.uydurulmus_deger.length}, eksik/bayat işaretli ${s.eksik_veya_bayat_isaretli}, uyarı ${s.uyari_sayisi}`)
  if (s.rd_siniri) parts.push(`RD sınırı ${s.rd_siniri[0]} → ${s.rd_siniri[1]}`)
  if (s.uygulanan_kurallar) parts.push(`kurallar: ${s.uygulanan_kurallar.join(', ')}`)
  return parts.join(' · ')
}

export function EvaluationPage() {
  const [ev, setEv] = useState<Evaluation | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    fetch('/api/degerlendirme').then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail); setEv(j) }).catch((e) => setErr(e.message))
  }, [])

  const groups = ev ? [...new Set(ev.senaryolar.map((s) => s.grup))] : []
  return (
    <div className="page eval">
      <h2>Değerlendirme</h2>
      <p className="muted">Çözümün iyi çalıştığını nasıl gösteriyoruz: seçilen başarı göstergeleri, test senaryoları ve sonuçları, hata analizleri ve bunlara dayanarak yapılan iyileştirmeler.</p>

      <section className="card">
        <h3 className="card-title">Test sonuçları</h3>
        {err && <div className="banner warnbox">{err}</div>}
        {!ev && !err && <p className="muted">Yükleniyor…</p>}
        {ev && (
          <>
            <div className="kpis">
              <div className={ev.ozet.gecen === ev.ozet.senaryo ? '' : 'warn'}><span>Geçen senaryo</span><strong>{ev.ozet.gecen}/{ev.ozet.senaryo}</strong></div>
              <div className={ev.ozet.kural_ihlali ? 'warn' : ''}><span>Kural ihlali</span><strong>{ev.ozet.kural_ihlali}</strong></div>
              <div className={ev.ozet.guard_ihlali ? 'warn' : ''}><span>Guard ihlali</span><strong>{ev.ozet.guard_ihlali}</strong></div>
              <div className={ev.ozet.kaynaksiz_veri ? 'warn' : ''}><span>Kaynak/tarihi eksik</span><strong>{ev.ozet.kaynaksiz_veri} / {ev.ozet.veri_noktasi}</strong></div>
              <div className={ev.ozet.alinma_zamani_yok ? 'warn' : ''}><span>Alınma zamanı eksik</span><strong>{ev.ozet.alinma_zamani_yok ?? '—'}</strong></div>
              <div className={ev.ozet.uydurulmus_deger ? 'warn' : ''}><span>Uydurulmuş değer</span><strong>{ev.ozet.uydurulmus_deger}</strong></div>
              <div><span>Ortalama süre</span><strong>{ev.ozet.ortalama_sure_sn.toString().replace('.', ',')} sn</strong></div>
            </div>
            <p className="muted small">Son çalıştırma {new Date(ev.calisma_zamani).toLocaleString('tr-TR')} · <code>backend/tools/degerlendir.py</code> · metin deterministik şablonla üretildi (ölçülen, kural ve veri davranışı). Kanıt kayıtları <code>evidence/_degerlendirme</code> altında.</p>
            {groups.map((g) => (
              <div key={g}>
                <h4>{g}</h4>
                <table className="small">
                  <thead><tr><th>Senaryo</th><th>Sonuç</th><th>Durum</th><th>Ayrıntı</th><th>Süre</th></tr></thead>
                  <tbody>
                    {ev.senaryolar.filter((s) => s.grup === g).map((s) => (
                      <tr key={s.ad}>
                        <td>{s.ad}{s.rl_dagilim && !Array.isArray(s.rl_dagilim) && <div className="muted">RL: {dist(s.rl_dagilim)}{s.rl_secilen_olasilik != null && ` (olasılık %${s.rl_secilen_olasilik.toString().replace('.', ',')})`}</div>}</td>
                        <td><span className={`badge ${s.gecti ? 'ok' : 'warn'}`}>{s.gecti ? 'geçti' : 'kaldı'}</span></td>
                        <td>{STATUS[s.durum] ?? s.durum}</td>
                        <td>{detail(s)}{s.sorular && s.sorular.length > 0 && <div className="muted">Sorulan: {s.sorular.join(' | ')}</div>}</td>
                        <td>{s.sure_sn.toString().replace('.', ',')} sn</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </>
        )}
      </section>

      <section className="card">
        <h3 className="card-title">Başarı göstergeleri ve neden bunlar</h3>
        <table className="small"><tbody>{METRICS.map(([k, v]) => <tr key={k}><td><strong>{k}</strong></td><td>{v}</td></tr>)}</tbody></table>
      </section>

      <section className="card">
        <h3 className="card-title">Hata analizi ve yapılan iyileştirmeler</h3>
        <table className="small">
          <thead><tr><th>Gözlenen hata</th><th>Analiz</th><th>İyileştirme</th></tr></thead>
          <tbody>{LEARNINGS.map((l) => <tr key={l.hata}><td>{l.hata}</td><td>{l.analiz}</td><td>{l.iyilestirme}</td></tr>)}</tbody>
        </table>
      </section>

      <section className="card">
        <h3 className="card-title">Neden bu yaklaşım?</h3>
        <table className="small"><tbody>{APPROACH.map(([k, v]) => <tr key={k}><td><strong>{k}</strong></td><td>{v}</td></tr>)}</tbody></table>
        <p className="muted small">Analitik skor, model ve LLM çıktıları RM için karar desteğidir; nihai yatırım tavsiyesi değildir.</p>
      </section>
    </div>
  )
}
