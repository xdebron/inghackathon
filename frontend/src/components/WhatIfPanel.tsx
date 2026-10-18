import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { RunRecord } from '../types'
import { AllocBar, CAT_LABEL } from './AllocBar'
import { MiniMarkdown } from './MiniMarkdown'

// RM için what-if: piyasa göstergeleri ve persona alanları değişirse RL modelinin kararı ne olur?
// Hesap gerçek modelle yapılır (rl_ne_olursa / rl_esik); "Ajana yorumlat" aynı senaryoyu Qwen ajanına açıklatır.

interface Indicator {
  ad: string; bugun: number; egitim_ort: number; z: number
  gorunur_aralik?: [number, number] | null    // modelin ayırt edebildiği aralık (±kırpma·σ)
  gecmis_aralik?: [number, number] | null     // veride gerçekten görülmüş en düşük / en yüksek
  senaryo_araligi?: [number, number] | null   // ikisinin kesişimi: kaydırıcı bununla sınırlı
  gecmis_getiri?: boolean                     // geriye dönük gerçekleşmiş getiri / değişim (beklenti değil)
  kirpma_turu?: 'sert' | 'tanh'               // sert: kırpma sınırı dışı sınırla aynı; tanh: sıkıştırılır
}
interface Sensitivity { 'kararı_degistiren_gosterge': number; toplam_gosterge: number; rastgele_300_piyasada_farkli_karar: number; gostergeler: string[] }
interface WhatIf {
  uygulanan_degisiklikler: string[]
  onceki_dagilim: Record<string, number>
  yeni_dagilim: Record<string, number>
  karar_degisti: boolean
  degisen_kategoriler: { ad: string; once: number; sonra: number }[]
  yeni_dislanan_kategoriler: string[]
  uyarilar: string[]
  yeni_kararin_kaynagi?: string
  beklenen_dagilim?: { once: Record<string, number>; sonra: Record<string, number>; en_olasi_olasilik_once: number; en_olasi_olasilik_sonra: number; not: string } | null
  modelin_gordugu_degerler?: { ad: string; girilen: number; modelin_gordugu: number; kirpma_turu?: string }[]
}
interface Threshold { gosterge: string; bugun: number; bolgeler: { aralik: [number, number]; dagilim: Record<string, number> }[]; not: string }

const KEY = ['Politika faizi', 'TLREF', 'Mevduat faizi 1 ay', 'TÜFE yıllık', 'USD/TRY 3 ay değişimi', 'BIST 100 toplam getiri 3 ay']
// Hazır senaryolar tutarlı uygulanır: aynı olayın etkilediği tüm göstergeler birlikte değişir (model eğitimindeki what-if takımı)
const PRESETS: { ad: string; tur: string; aciklama: string }[] = [
  { ad: 'Faiz indirimi', tur: 'faiz_indirimi', aciklama: 'Politika faizi, TLREF, mevduat faizi ve reel faiz birlikte 5 puan düşer' },
  { ad: 'Enflasyon yükselişi', tur: 'enflasyon_artisi', aciklama: 'TÜFE göstergeleri 10 puan artar, reel faiz aynı ölçüde düşer' },
  { ad: 'Kur şoku', tur: 'kur_soku', aciklama: 'USD/TRY %15 değer kazanır; altın, yabancı hisse ve eurobond TL getirileri birlikte artar' },
  { ad: 'Borsa düşüşü', tur: 'borsa_dususu', aciklama: 'BIST 100, BIST 30 ve hisse fonu getirileri %15 düşer' },
]
const round = (d: Record<string, number>) => Object.fromEntries(Object.entries(d).map(([k, v]) => [k, Math.round(v)]).filter(([, v]) => (v as number) > 0))
const PERSONA_FIELDS: [string, string, string[]][] = [
  ['risk_profili', 'Risk profili', ['Muhafazakâr', 'Temkinli', 'Dengeli', 'Agresif']],
  ['yatirim_ufku', 'Yatırım ufku', ['0-1 yıl', '1-3 yıl', '3-5 yıl', '5+ yıl']],
  ['likidite_ihtiyaci', 'Likidite ihtiyacı', ['Düşük', 'Orta', 'Yüksek']],
  ['anapara_koruma_tercihi', 'Anapara koruma', ['Düşük', 'Orta', 'Yüksek']],
]
const pct = (x: number, nd = 1) => `${x < 0 ? '−' : ''}%${Math.abs(x * 100).toFixed(nd).replace('.', ',')}`

// 'para_piyasasi 3 ay getirisi' → 'Para piyasası · son 3 ay getirisi'
const indLabel = (ad: string) => {
  const [code, ...rest] = ad.split(' ')
  return CAT_LABEL[code] ? `${CAT_LABEL[code]} · son ${rest.join(' ')}` : ad
}

// Kaydırıcı sınırları (yüzde puan, 0,5 adım): geçmişte görülmüş ve modelin ayırt edebildiği aralık
const sliderRange = (g: Indicator): [number, number] => {
  const r = g.senaryo_araligi
  if (!r) return [-15, 15]
  return [Math.min(0, Math.ceil((r[0] - g.bugun) * 200) / 2), Math.max(0, Math.floor((r[1] - g.bugun) * 200) / 2)]
}
const clampTo = (v: number, [lo, hi]: [number, number]) => Math.min(hi, Math.max(lo, v))

async function rlTool<T>(ad: string, argumanlar: Record<string, unknown>): Promise<T> {
  const r = await fetch('/api/rl-araclar/cagir', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ad, argumanlar }) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : r.statusText)
  return j as T
}

function ThresholdBand({ t }: { t: Threshold }) {
  const lo = t.bolgeler[0]?.aralik[0] ?? 0
  const hi = t.bolgeler[t.bolgeler.length - 1]?.aralik[1] ?? 1
  const span = hi - lo || 1
  return (
    <div className="band">
      <div className="band-track">
        {t.bolgeler.map((b, i) => (
          <div key={i} className="band-seg" style={{ left: `${((b.aralik[0] - lo) / span) * 100}%`, width: `${((b.aralik[1] - b.aralik[0]) / span) * 100}%` }}
            title={`${pct(b.aralik[0])} – ${pct(b.aralik[1])}: ${Object.entries(b.dagilim).map(([c, v]) => `${CAT_LABEL[c] ?? c} %${v}`).join(', ')}`} />
        ))}
        <div className="band-today" style={{ left: `${((t.bugun - lo) / span) * 100}%` }} title={`Bugün ${pct(t.bugun)}`} />
      </div>
      <div className="band-scale small muted"><span>{pct(lo)}</span><span>bugün {pct(t.bugun)}</span><span>{pct(hi)}</span></div>
      <ul className="band-list small">
        {t.bolgeler.map((b, i) => (
          <li key={i}><strong>{pct(b.aralik[0])} – {pct(b.aralik[1])}</strong> <AllocBar d={b.dagilim} compact /></li>
        ))}
      </ul>
    </div>
  )
}

export function WhatIfPanel({ run, onUpdated }: { run: RunRecord; onUpdated: (r: RunRecord) => void }) {
  const [model, setModel] = useState('canli')
  const base = useMemo(() => ({ persona: run.persona, rm_answers: run.rm_answers ?? {}, model }), [run.run_id, model])  // eslint-disable-line react-hooks/exhaustive-deps
  const [sens, setSens] = useState<Sensitivity | null>(null)
  const [all, setAll] = useState<Indicator[]>([])
  const [delta, setDelta] = useState<Record<string, number>>({})      // yüzde puan
  const [pchg, setPchg] = useState<Record<string, string>>({})
  const [res, setRes] = useState<WhatIf | null>(null)
  const [thr, setThr] = useState<Threshold | null>(null)
  const [thrInd, setThrInd] = useState('')
  const [busy, setBusy] = useState(false)
  const [preset, setPreset] = useState<(typeof PRESETS)[number] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [agent, setAgent] = useState<{ durum: string; cevap?: string; ihlaller?: string[] } | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    setRes(null); setThr(null); setSens(null)
    rlTool<{ piyasa_duyarliligi: Sensitivity }>('rl_guvenilirlik', base).then((g) => setSens(g.piyasa_duyarliligi)).catch(() => setSens(null))
    rlTool<{ temalar: { gostergeler: Indicator[] }[] }>('rl_neden', base)
      .then((n) => {
        setAll(n.temalar.flatMap((t) => t.gostergeler))
      })
      .catch((e) => setErr(e.message))
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [base])

  // Gösterilen kaydırıcılar: seçili modelde kararı tek başına değiştirebilen göstergeler (en fazla 6) + makro göstergeler
  const movers = useMemo(() => new Set(sens?.gostergeler ?? []), [sens])
  const inds = useMemo(() => {
    const pick = [...(sens?.gostergeler ?? []).slice(0, 6), ...KEY.map((k) => all.find((g) => g.ad.startsWith(k))?.ad)]
    const names = [...new Set(pick.filter(Boolean) as string[])]
    return names.map((n) => all.find((g) => g.ad === n)).filter(Boolean) as Indicator[]
  }, [all, sens])

  const changes = () => Object.entries(delta).filter(([, v]) => v !== 0).map(([ad, v]) => ({ ad, degisim: v / 100 }))
  const personaChanges = () => Object.fromEntries(Object.entries(pchg).filter(([k, v]) => v && v !== (run.persona as unknown as Record<string, string>)[k]))

  const runScenario = async () => {
    setBusy(true); setErr(null); setAgent(null)
    try {
      setRes(await rlTool<WhatIf>('rl_ne_olursa', { ...base, gostergeler: changes(), persona_degisiklikleri: personaChanges(), ...(preset ? { senaryo: { tur: preset.tur } } : {}) }))
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const runThreshold = async (ad: string) => {
    setThrInd(ad); setThr(null)
    if (!ad) return
    try { setThr(await rlTool<Threshold>('rl_esik', { ...base, gosterge: ad })) } catch (e) { setErr((e as Error).message) }
  }

  const describe = () => {
    const parts = [...(preset ? [`hazır senaryo ${preset.ad} (rl_ne_olursa senaryo.tur = ${preset.tur})`] : []), ...changes().map((c) => `${c.ad} ${c.degisim > 0 ? '+' : ''}${(c.degisim * 100).toFixed(1).replace('.', ',')} puan`),
      ...Object.entries(personaChanges()).map(([k, v]) => `${k} = ${v}`)]
    return parts.length ? parts.join('; ') : 'bugünkü koşullar (değişiklik yok)'
  }

  const askAgent = async () => {
    setAgent({ durum: 'calisiyor' })
    const soru = `What-if senaryosu: ${describe()}. rl_ne_olursa aracıyla modelin kararını bu senaryoda hesapla (faiz/getiri değişimlerini oran biriminde gir: 5 puan = 0.05), bugünkü kararla karşılaştır, gerekiyorsa rl_esik ile eşikleri kontrol et ve güvenilirlik uyarılarıyla birlikte RM için kısa bir yorum yaz.`
    try {
      const r = await fetch('/api/ajan/sor', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run_id: run.run_id, soru }) })
      const { is_id } = await r.json()
      timer.current = window.setInterval(async () => {
        const j = await (await fetch(`/api/ajan/is/${is_id}`)).json()
        if (j.durum !== 'calisiyor') {
          window.clearInterval(timer.current!)
          setAgent({ durum: j.durum, cevap: j.sonuc?.cevap ?? j.hata, ihlaller: j.sonuc?.ihlaller })
          onUpdated(await api.getRun(run.run_id))
        }
      }, 1500)
    } catch (e) { setAgent({ durum: 'hata', cevap: (e as Error).message }) }
  }

  const applyPreset = (p: (typeof PRESETS)[number]) => { setPreset(preset?.tur === p.tur ? null : p); setRes(null) }

  return (
    <div className="whatif">
      <p className="muted small lead">
        Piyasa koşulları veya müşteri profili değişirse modelin önerdiği dağılım nasıl değişir? Hesaplama eğitilmiş modelin kendisiyle yapılır; sonuç bir simülasyondur, getiri tahmini değildir.
      </p>
      <div className="row wrap whatif-model">
        <label className="row small">Model
          <select value={model} onChange={(e) => setModel(e.target.value)} style={{ width: 'auto' }}>
            <option value="canli">Canlı model (PPO v14 · tüm veri 6M)</option><option value="ppo_v11_tum100m">PPO v11 · tüm veri 100M (önceki canlı)</option>
            <option value="ppo_v11_100m">PPO v11 · 100M (2026 görülmemiş)</option>
            <option value="ppo_v9">PPO v9 (önceki)</option>
            <option value="v4">v4 (DQN, karşılaştırma)</option>
          </select>
        </label>
        {sens && (
          <span className={`badge ${sens['kararı_degistiren_gosterge'] === 0 ? 'warn' : 'info'}`}>
            Piyasa duyarlılığı: {sens['kararı_degistiren_gosterge']}/{sens.toplam_gosterge} gösterge kararı değiştirebiliyor
          </span>
        )}
      </div>
      {sens && sens['kararı_degistiren_gosterge'] === 0 && sens.rastgele_300_piyasada_farkli_karar <= 1 && (
        <div className="banner warnbox small">
          Bu model bu müşteri profili için piyasa koşullarına duyarsız: 300 farklı rastgele piyasa durumunda da aynı dağılımı öneriyor.
          Faiz, enflasyon veya kur senaryoları sonucu değiştirmez; profil değişiklikleri değiştirebilir. Diğer modellerle karşılaştırmak için yukarıdan model seçebilirsiniz.
        </div>
      )}
      <div className="whatif-grid">
        <section className="panel">
          <h4>Hazır senaryolar</h4>
          <div className="presets">
            {PRESETS.map((p) => (
              <button key={p.ad} className={`preset ${preset?.tur === p.tur ? 'active' : ''}`} onClick={() => applyPreset(p)} title={p.aciklama}>
                <strong>{p.ad}</strong><span className="muted small">{p.aciklama}</span>
              </button>
            ))}
          </div>
          {preset && <div className="banner info small">Seçili senaryo: <strong>{preset.ad}</strong>, ilgili tüm göstergelere birlikte uygulanır. Kaydırıcılar bunun üzerine ek değişiklik olarak eklenir.</div>}
          <h4>Piyasa göstergeleri <span className="muted small">(yüzde puan değişim)</span></h4>
          <p className="muted small">
            Kaydırıcılar, göstergenin geçmişte gerçekten aldığı ve modelin ayırt edebildiği değerlerle sınırlıdır.
            "Son 3 ay getirisi" göstergeleri fonların geçmiş 3 aydaki gerçekleşen getirisidir; model için gelecek getiri beklentisi değil, piyasa durumu sinyalidir.
          </p>
          {inds.length === 0 && !err && <p className="muted small">Göstergeler yükleniyor…</p>}
          {inds.map((g) => {
            const v = delta[g.ad] ?? 0
            const [mn, mx] = sliderRange(g)
            return (
              <div key={g.ad} className="slider-row">
                <div className="slider-label"><span title={g.ad}>{indLabel(g.ad)}{g.gecmis_getiri && <span className="muted small" title="Geriye dönük gerçekleşmiş değer; model için beklenti değil"> (geçmiş)</span>}{movers.has(g.ad) && <span className="badge info small" style={{ marginLeft: 6 }}>kararı etkileyebilir</span>}</span><span className="muted small">bugün {pct(g.bugun)}{v !== 0 && <> → <strong>{pct(g.bugun + v / 100)}</strong></>}</span>
                  {g.senaryo_araligi && (
                    <span className="range small" title={[
                      g.gecmis_aralik && `Veride görülmüş: ${pct(g.gecmis_aralik[0])}–${pct(g.gecmis_aralik[1])}`,
                      g.gorunur_aralik && (g.kirpma_turu === 'tanh'
                        ? `Kırpma sınırı: ${pct(g.gorunur_aralik[0])}–${pct(g.gorunur_aralik[1])}; dışındaki değerler model girdisinde sıkıştırılır`
                        : `Modelin ayırt edebildiği: ${pct(g.gorunur_aralik[0])}–${pct(g.gorunur_aralik[1])}; dışındaki değerler sınırla aynı görünür`),
                    ].filter(Boolean).join(' · ')}>
                      senaryo aralığı {pct(g.senaryo_araligi[0])}–{pct(g.senaryo_araligi[1])}</span>
                  )}
                </div>
                <input type="range" min={mn} max={mx} step={0.5} value={clampTo(v, [mn, mx])} disabled={mn === mx} onChange={(e) => { setDelta({ ...delta, [g.ad]: Number(e.target.value) }) }} />
                <span className={`delta ${v > 0 ? 'up' : v < 0 ? 'down' : ''}`}>{v > 0 ? '+' : ''}{v.toFixed(1).replace('.', ',')}</span>
              </div>
            )
          })}
          <h4>Müşteri profili</h4>
          <div className="pfields">
            {PERSONA_FIELDS.map(([k, label, opts]) => (
              <label key={k} className="field"><span>{label}</span>
                <select value={pchg[k] ?? (run.persona as unknown as Record<string, string>)[k] ?? ''} onChange={(e) => setPchg({ ...pchg, [k]: e.target.value })}>
                  {opts.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
            ))}
          </div>
          <div className="row wrap" style={{ marginTop: 10 }}>
            <button className="primary" onClick={runScenario} disabled={busy}>{busy ? 'Hesaplanıyor…' : 'Senaryoyu hesapla'}</button>
            <button className="ghost" onClick={() => { setDelta({}); setPchg({}); setRes(null); setAgent(null); setPreset(null) }}>Sıfırla</button>
          </div>
        </section>

        <section className="panel">
          <h4>Sonuç</h4>
          {err && <div className="banner error">{err}</div>}
          {!res && <p className="muted small">Bir senaryo seçin veya göstergeleri ayarlayıp "Senaryoyu hesapla"ya basın.</p>}
          {res && (
            <>
              <div className="cmp"><span className="muted small">Bugün</span><AllocBar d={res.onceki_dagilim} /></div>
              <div className="cmp"><span className="muted small">Senaryo</span><AllocBar d={res.yeni_dagilim} /></div>
              <div className={`banner ${res.karar_degisti ? 'warnbox' : 'ok'} small`}>
                {res.karar_degisti ? 'Model bu senaryoda dağılımı değiştiriyor.' : 'Model bu senaryoda dağılımı değiştirmiyor.'}
                {res.yeni_kararin_kaynagi === 'taban_kural' && ' Uygulanan dağılım güvenli kapı nedeniyle taban kuraldır.'}
              </div>
              {res.beklenen_dagilim && (
                <div className="expected">
                  <div className="small"><strong>Olasılık ağırlıklı beklenen dağılım</strong>
                    <span className="muted"> · en olası dağılımın olasılığı bugün %{res.beklenen_dagilim.en_olasi_olasilik_once.toString().replace('.', ',')}, senaryoda %{res.beklenen_dagilim.en_olasi_olasilik_sonra.toString().replace('.', ',')}</span></div>
                  <div className="cmp"><span className="muted small">Bugün</span><AllocBar d={round(res.beklenen_dagilim.once)} /></div>
                  <div className="cmp"><span className="muted small">Senaryo</span><AllocBar d={round(res.beklenen_dagilim.sonra)} /></div>
                  <div className="muted small">{res.beklenen_dagilim.not}</div>
                </div>
              )}
              {res.degisen_kategoriler.length > 0 && (
                <table className="small"><thead><tr><th>Kategori</th><th>Bugün</th><th>Senaryo</th></tr></thead>
                  <tbody>{res.degisen_kategoriler.map((c) => <tr key={c.ad}><td>{c.ad}</td><td>%{c.once}</td><td>%{c.sonra}</td></tr>)}</tbody></table>
              )}
              <div className="small muted">{res.uygulanan_degisiklikler.join(' · ')}</div>
              {(res.modelin_gordugu_degerler ?? []).map((m) => (
                <div key={m.ad} className="small warn-line">⚠ {indLabel(m.ad)}: girilen {pct(m.girilen)}, {m.kirpma_turu === 'tanh' ? <>model bunu sıkıştırarak {pct(m.modelin_gordugu)} gibi işliyor (yumuşak sınır).</> : <>model bunu {pct(m.modelin_gordugu)} olarak görüyor (girdi kırpma).</>}</div>
              ))}
              {res.yeni_dislanan_kategoriler.length > 0 && <div className="small">Yeni profilde kapanan kategoriler: {res.yeni_dislanan_kategoriler.join('; ')}</div>}
              {res.uyarilar.map((u) => <div key={u} className="small warn-line">⚠ {u}</div>)}
              <button onClick={askAgent} disabled={agent?.durum === 'calisiyor'} style={{ marginTop: 8 }}>
                {agent?.durum === 'calisiyor' ? 'Ajan yorumluyor…' : 'Ajana yorumlat'}
              </button>
              {agent && agent.durum !== 'calisiyor' && (
                <div className="agent-answer">
                  {agent.ihlaller && agent.ihlaller.length > 0 && <div className="banner warnbox small">Guard uyarısı: {agent.ihlaller.join('; ')}</div>}
                  <MiniMarkdown text={agent.cevap ?? ''} />
                  <div className="muted small">Yanıt "Ajan ve not" sekmesindeki soru-cevap geçmişine kaydedildi.</div>
                </div>
              )}
            </>
          )}
          <h4 style={{ marginTop: 18 }}>Eşik analizi</h4>
          <select value={thrInd} onChange={(e) => runThreshold(e.target.value)}>
            <option value="">Gösterge seçin…</option>
            {inds.map((g) => <option key={g.ad} value={g.ad}>{indLabel(g.ad)}</option>)}
          </select>
          {thr && <ThresholdBand t={thr} />}
          {thr && <p className="muted small">{thr.not}</p>}
        </section>
      </div>
    </div>
  )
}
