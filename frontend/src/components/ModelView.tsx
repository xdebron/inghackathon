import { useEffect, useMemo, useState } from 'react'
import { pct } from '../format'
import { CurrentAllocInput, toFractions } from './CurrentAllocInput'
import { BeliefTable, CalibrationTable, DecisionExplanation, GateBox, GlobalExplanationView } from './RLExplain'
import type { Belief, Calibration, Explanation, Gate, GlobalExplanation } from './RLExplain'

const CAT_LABEL: Record<string, string> = {
  mevduat_tl: 'Mevduat (3 ay)', para_piyasasi: 'Para piyasası', borclanma: 'Borçlanma araçları', katilim: 'Katılım (kira sert.)',
  kiymetli_maden: 'Kıymetli maden', karma_degisken: 'Karma / değişken', fon_sepeti: 'Fon sepeti', hisse: 'Hisse senedi',
  yabanci_hisse: 'Yabancı hisse', eurobond_doviz: 'Eurobond / döviz', bist30_hisse: 'BIST 30 hisse (doğrudan)',
}
const DONEM: Record<string, string> = { egitim: 'eğitim dönemi', dogrulama: 'doğrulama dönemi', test: 'test dönemi', diger: 'dönem dışı' }

type Metrics = { ortalama_reel_kazanc: number; medyan_reel_kazanc: number; kotu_yuzde5: number; anapara_alti_bitis_olasiligi: number; herhangi_an_anapara_alti_olasiligi: number; ortalama_odul: number }
type LogRow = { guncelleme?: number; gecen_dk?: number; dogrulama_odul?: number; holdout_odul?: number; dogrulama_anapara_alti?: number; eps?: number; en_iyi?: boolean; olay?: string }
type ModelInfo = {
  durum: { guncelleme?: number; toplam?: number; bitti?: boolean; gecen_dk?: number; tahmini_kalan_dk?: number; sure_dk?: number; en_iyi?: { skor: number; guncelleme: number } }
  meta: null | { olusturma: string; sure_dk: number; cihaz: string; guncelleme: number; en_iyi_guncelleme: number; donem: Record<string, string[]>
    profil_sayisi: { egitim: number; gorulmemis: number }; profil_istatistik?: { ham: number; celiskisiz: number; rl_farkli: number }
    kategori_rd: Record<string, number>; kategoriler?: string[]; inanc_kalibrasyon?: { dogrulama?: Calibration; test?: Calibration }; test: { egitim_profilleri: Record<string, Metrics>; gorulmemis_profiller: Record<string, Metrics>; personalar: Record<string, Record<string, Metrics>> } }
  aciklama?: GlobalExplanation | null
  canli_log: LogRow[]
}
type Options = { alanlar: Record<string, string[]>; kisitlar: string[]; kisit_bilgisi?: Record<string, { id: string; tip: string; aciklama: string; kaynak: string }>; personalar: Record<string, Record<string, string | string[]>>; tarihler: string[]; varsayilan_tarih: string; donemler: Record<string, string[]> }
type Prediction = { tarih: string; donem: string; kategori_dagilimi: Record<string, number>; notlar?: string[]; alternatifler: { sira: number; deger: number; devir?: number; dagilim: Record<string, number> }[]
  izinli_aksiyon: number; toplam_aksiyon: number; dislanan_kategoriler: string[]; dagilim_disi: { ad: string; z: number }[]
  gostergeler: { ad: string; deger: number; z: number; kirpildi: boolean }[]; uyari: string | null
  beklenen_getiri?: Belief[] | null; aciklama?: Explanation | null; guvenli_iyilestirme?: Gate | null }

const FIELDS: [string, string][] = [
  ['risk_profili', 'Risk profili'], ['yatirim_ufku', 'Yatırım ufku'], ['likidite_ihtiyaci', 'Likidite ihtiyacı'],
  ['anapara_koruma_tercihi', 'Anapara koruma'], ['doviz_maruz_kalma_tercihi', 'Döviz tercihi'],
]

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw Object.assign(new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message ?? r.statusText), { detail: body.detail })
  return body as T
}

function Alloc({ w }: { w: Record<string, number> }) {
  return (
    <div className="weights">
      {Object.entries(w).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
        <div key={k} className="wrow"><span className="wname">{CAT_LABEL[k] ?? k}</span>
          <div className="bar wide"><div style={{ width: `${v * 100}%` }} /></div><span>{pct(v, 0)}</span></div>
      ))}
    </div>
  )
}

function Curve({ rows }: { rows: LogRow[] }) {
  const pts = rows.filter((r) => r.dogrulama_odul !== undefined && r.guncelleme !== undefined)
  if (pts.length < 2) return <p className="muted small">Eğri için henüz yeterli değerlendirme yok.</p>
  const W = 640, H = 180, P = 30
  const xs = pts.map((p) => p.guncelleme!), ys = pts.flatMap((p) => [p.dogrulama_odul!, p.holdout_odul ?? p.dogrulama_odul!])
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys)
  const sx = (x: number) => P + ((x - x0) / Math.max(1, x1 - x0)) * (W - 2 * P)
  const sy = (y: number) => H - P - ((y - y0) / Math.max(1e-9, y1 - y0)) * (H - 2 * P)
  const line = (k: 'dogrulama_odul' | 'holdout_odul') => pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.guncelleme!)},${sy((p[k] ?? p.dogrulama_odul)!)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Doğrulama ödülü eğrisi">
      <line x1={P} y1={H - P} x2={W - P} y2={H - P} className="axis" />
      <path d={line('dogrulama_odul')} className="l1" />
      <path d={line('holdout_odul')} className="l2" />
      {pts.filter((p) => p.en_iyi).map((p) => <circle key={p.guncelleme} cx={sx(p.guncelleme!)} cy={sy(p.dogrulama_odul!)} r={3} className="best" />)}
      <text x={P} y={14} className="lbl">{y1.toFixed(3)}</text><text x={P} y={H - P - 4} className="lbl">{y0.toFixed(3)}</text>
      <text x={W - P} y={H - 8} className="lbl" textAnchor="end">{x1.toLocaleString('tr-TR')} güncelleme</text>
    </svg>
  )
}

function MetricsTable({ data }: { data: Record<string, Metrics> }) {
  return (
    <table>
      <thead><tr><th>Strateji</th><th>Ort. reel kazanç</th><th>Medyan</th><th>En kötü %5</th><th>Bitişte anapara altı</th></tr></thead>
      <tbody>{Object.entries(data).map(([k, m]) => (
        <tr key={k} className={k.startsWith('DQN') || k.startsWith('Model') ? 'hl' : ''}><td>{k}</td><td>{pct(m.ortalama_reel_kazanc)}</td><td>{pct(m.medyan_reel_kazanc)}</td>
          <td>{pct(m.kotu_yuzde5)}</td><td>{pct(m.anapara_alti_bitis_olasiligi)}</td></tr>))}</tbody>
    </table>
  )
}

export function ModelView() {
  const [info, setInfo] = useState<ModelInfo | null>(null)
  const [opts, setOpts] = useState<Options | null>(null)
  const [prof, setProf] = useState<Record<string, string>>({})
  const [kis, setKis] = useState<string[]>([])
  const [persona, setPersona] = useState<string>('')
  const [tarih, setTarih] = useState<string>('')
  const [pred, setPred] = useState<Prediction | null>(null)
  const [err, setErr] = useState<{ msg: string; sorular?: { question: string; detail: string }[] } | null>(null)
  const [current, setCurrent] = useState<Record<string, number>>({})
  const [testTab, setTestTab] = useState<'gorulmemis_profiller' | 'egitim_profilleri' | string>('gorulmemis_profiller')

  useEffect(() => {
    let alive = true
    const load = () => getJSON<ModelInfo>('/api/rl/model').then((d) => alive && setInfo(d)).catch(() => {})
    load()
    const t = setInterval(load, 5000)  // canlı: eğitim sürerken ilerleme ve eğri güncellenir
    return () => { alive = false; clearInterval(t) }
  }, [])

  useEffect(() => {
    getJSON<Options>('/api/rl/secenekler').then((o) => {
      setOpts(o)
      setTarih(o.varsayilan_tarih)
      const p3 = o.personalar['P01']
      if (p3) { setPersona('P01'); setProf(Object.fromEntries(FIELDS.map(([k]) => [k, p3[k] as string]))); setKis(p3.acik_kisitlar as string[]) }
    }).catch((e) => setErr({ msg: e.message }))
  }, [])

  const body = useMemo(() => ({ profil: { ...prof, acik_kisitlar: kis }, tarih, mevcut_dagilim: toFractions(current) }), [prof, kis, tarih, current])

  useEffect(() => {
    if (!opts || !tarih || Object.keys(prof).length < FIELDS.length) return
    const ctl = new AbortController()
    getJSON<Prediction>('/api/rl/tahmin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ctl.signal })
      .then((p) => { setPred(p); setErr(null) })
      .catch((e) => { if (e.name !== 'AbortError') { setPred(null); setErr({ msg: e.message, sorular: e.detail?.sorular }) } })
    return () => ctl.abort()
  }, [body, opts, tarih, prof])

  const loadPersona = (id: string) => {
    setPersona(id)
    const p = opts?.personalar[id]
    if (p) { setProf(Object.fromEntries(FIELDS.map(([k]) => [k, p[k] as string]))); setKis(p.acik_kisitlar as string[]) }
  }

  const d = info?.durum, m = info?.meta
  const ppo = (m as unknown as { model_turu?: string })?.model_turu === 'ppo'
  const hpo = (m as unknown as { hpo?: { deneme?: number; tohum?: number } })?.hpo
  const running = d && !d.bitti && d.guncelleme !== undefined
  return (
    <div className="modelview">
      <section className="card">
        <div className="card-head">
          <div><h2>Kategori modeli ({(m as unknown as { model_adi?: string })?.model_adi ?? 'profile koşullu DQN'})</h2>
            <p className="muted small">Salt okunur canlı görünüm. Profil ve piyasa tarihini değiştirdikçe modelin kararı anında yeniden hesaplanır; eğitim sürerken ilerleme 5 saniyede bir yenilenir.</p></div>
          <span className={`badge ${running ? 'warn' : 'status-rm_onayladi'}`}>{running ? 'Eğitim sürüyor' : m ? 'Model hazır' : 'Model yok'}</span>
        </div>
        <div className="summary">
          {running && <div><span className="muted small">İlerleme</span><strong>{d!.guncelleme!.toLocaleString('tr-TR')} / {d!.toplam?.toLocaleString('tr-TR')}</strong></div>}
          {running && <div><span className="muted small">Kalan</span><strong>{d!.tahmini_kalan_dk} dk</strong></div>}
          {m && (ppo
            ? <div><span className="muted small">Eğitim</span><strong className="small">{m.guncelleme.toLocaleString('tr-TR')} ortam adımı · tüm veri · HPO ({hpo?.deneme ?? 12} ayar × {hpo?.tohum ?? 3} tohum)</strong></div>
            : <div><span className="muted small">Eğitim</span><strong className="small">{m.guncelleme.toLocaleString('tr-TR')} güncelleme · {m.sure_dk} dk · {m.cihaz}</strong></div>)}
          {d?.en_iyi && <div><span className="muted small">En iyi doğrulama</span><strong>{d.en_iyi.skor.toFixed(3)} <span className="small muted">@ {d.en_iyi.guncelleme.toLocaleString('tr-TR')}</span></strong></div>}
          {m?.profil_istatistik && <div><span className="muted small">Profil kombinasyonu</span><strong className="small">{m.profil_istatistik.ham.toLocaleString('tr-TR')} → {m.profil_istatistik.celiskisiz.toLocaleString('tr-TR')} → {m.profil_istatistik.rl_farkli.toLocaleString('tr-TR')}</strong></div>}
          {m && <div><span className="muted small">Eğitim / görülmemiş profil</span><strong>{m.profil_sayisi.egitim.toLocaleString('tr-TR')} / {m.profil_sayisi.gorulmemis}</strong></div>}
        </div>
        {!ppo && <><h3>Doğrulama ödülü (turuncu: eğitim profilleri, gri: görülmemiş profiller, nokta: yeni en iyi)</h3>
          <Curve rows={info?.canli_log ?? []} /></>}
        {ppo && <p className="muted small">Erken durdurma yok: ayarlar yalnızca doğrulamaya göre seçildi, model son ağırlıklarla kullanılıyor
          (ara kontrol noktası seçimi yapılmadı). Ödül ve durum profilin tamamını içerir.</p>}
      </section>

      <section className="card">
        <h2>Etkileşimli tahmin</h2>
        <div className="row wrap" style={{ margin: '8px 0 12px' }}>
          <span className="muted small">Personadan doldur:</span>
          {opts && Object.keys(opts.personalar).map((id) => (
            <button key={id} className={persona === id ? 'primary' : ''} onClick={() => loadPersona(id)}>{id}</button>
          ))}
        </div>
        <div className="grid3">
          {opts && FIELDS.map(([k, label]) => (
            <label key={k} className="field"><span>{label}</span>
              <select value={prof[k] ?? ''} onChange={(e) => { setPersona(''); setProf({ ...prof, [k]: e.target.value }) }}>
                {opts.alanlar[k].map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </label>
          ))}
          {opts && (
            <label className="field"><span>Piyasa tarihi ({opts.tarihler[0]} – {opts.tarihler[opts.tarihler.length - 1]})</span>
              <input type="date" value={tarih} min={opts.tarihler[0]} max={opts.tarihler[opts.tarihler.length - 1]} onChange={(e) => setTarih(e.target.value)} />
            </label>
          )}
        </div>
        {(['brief', 'katalog'] as const).map((src) => (
          <div key={src} className="kisitlar">
            <span className="muted small">{src === 'brief' ? 'Açık kısıtlar (brief):' : 'Ek kısıtlar (katalog):'}</span>
            {opts?.kisitlar.filter((k) => (opts.kisit_bilgisi?.[k]?.kaynak ?? 'brief') === src).map((k) => (
              <label key={k} className="check" title={opts.kisit_bilgisi?.[k]?.aciklama}><input type="checkbox" checked={kis.includes(k)}
                onChange={() => { setPersona(''); setKis(kis.includes(k) ? kis.filter((x) => x !== k) : [...kis, k]) }} /> {k}</label>
            ))}
          </div>
        ))}

        <CurrentAllocInput value={current} onChange={setCurrent} cats={m?.kategoriler} />
        {err && (
          <div className="banner warnbox">{err.msg}
            {err.sorular?.map((q, i) => <div key={i} className="small">• {q.question} <span className="muted">{q.detail}</span></div>)}
          </div>
        )}
        {pred && (
          <div className="predgrid">
            <div>
              <h3>Modelin kararı · {pred.tarih} ({DONEM[pred.donem]})</h3>
              {pred.uyari && <div className="banner warnbox small">{pred.uyari}</div>}
              {pred.notlar?.map((n, i) => <div key={i} className="banner warnbox small">{n}</div>)}
              {pred.dagilim_disi.length > 0 && (
                <div className="banner warnbox small">Dağılım dışı göstergeler (±3σ): {pred.dagilim_disi.map((o) => `${o.ad} (${o.z >= 0 ? '+' : ''}${o.z}σ)`).join(', ')}</div>
              )}
              <GateBox g={pred.guvenli_iyilestirme} />
              <Alloc w={pred.kategori_dagilimi} />
              <p className="muted small">İzinli aksiyon: {pred.izinli_aksiyon} / {pred.toplam_aksiyon}</p>
              <h3>En iyi alternatifler (modelin değer tahmini)</h3>
              <table><tbody>{pred.alternatifler.map((a) => (
                <tr key={a.sira} className={a.sira === 1 ? 'hl' : ''}><td>{a.sira}</td><td className="small">{Object.entries(a.dagilim).map(([k, v]) => `${CAT_LABEL[k] ?? k} ${pct(v, 0)}`).join(' · ')}</td><td>{a.deger.toFixed(3)}</td><td className="small muted">{a.devir !== undefined ? `devir ${pct(a.devir, 0)}` : ''}</td></tr>
              ))}</tbody></table>
              {pred.dislanan_kategoriler.length > 0 && (<><h3>Profil maskesiyle kapalı kategoriler</h3>
                <ul className="flags">{pred.dislanan_kategoriler.map((x) => <li key={x} className="small">{x}</li>)}</ul></>)}
              <DecisionExplanation a={pred.aciklama} />
              <BeliefTable rows={pred.beklenen_getiri} />
            </div>
            <div>
              <h3>Piyasa göstergeleri (o tarihte bilinen)</h3>
              <table><thead><tr><th>Gösterge</th><th>Değer</th><th>Sapma</th></tr></thead><tbody>
                {pred.gostergeler.map((g) => (
                  <tr key={g.ad} className={Math.abs(g.z) > 3 ? 'warnrow' : ''}><td className="small">{g.ad}</td><td>{pct(g.deger)}</td>
                    <td className="small">{g.z >= 0 ? '+' : ''}{g.z.toFixed(1)}σ{g.kirpildi ? ' (kırpıldı)' : ''}</td></tr>))}
              </tbody></table>
            </div>
          </div>
        )}
      </section>

      <GlobalExplanationView g={info?.aciklama} />
      <CalibrationTable k={m?.inanc_kalibrasyon} />

      {m && !('gorulmemis_profiller' in m.test) && (
        <section className="card"><h2>Test dönemi sonuçları</h2>
          <p className="muted">{(m.test as unknown as { not?: string }).not ?? 'Bu modelde test sonucu yok.'}</p></section>
      )}
      {m && 'gorulmemis_profiller' in m.test && (
        <section className="card">
          <h2>{ppo ? `Performans tahmini (test ${m.donem.test?.join(' → ')}; aynı ayarlarla, bu dönemi görmeden eğitilmiş modeller)`
            : `Test dönemi sonuçları (${m.donem.test?.join(' → ')}, eğitimde ve seçimde kullanılmadı)`}</h2>
          {(m.test as unknown as { not?: string }).not && <p className="muted small">{(m.test as unknown as { not?: string }).not}</p>}
          <nav className="tabs">
            {['gorulmemis_profiller', ...('egitim_profilleri' in m.test ? ['egitim_profilleri'] : []), ...Object.keys(m.test.personalar)].map((k) => (
              <button key={k} className={testTab === k ? 'active' : ''} onClick={() => setTestTab(k)}>
                {k === 'gorulmemis_profiller' ? `Görülmemiş profiller (${m.profil_sayisi.gorulmemis})` : k === 'egitim_profilleri' ? 'Eğitim profilleri' : k}
              </button>))}
          </nav>
          <MetricsTable data={testTab in m.test.personalar ? m.test.personalar[testTab] : (m.test as unknown as Record<string, Record<string, Metrics>>)[testTab]} />
        </section>
      )}
    </div>
  )
}
