import { useEffect, useState } from 'react'
import type { Persona } from '../types'

// Müşteri ekleme: seçerek (form) ya da JSON içe aktarım. Seçenekler yorumlayıcının tanıdığı değerlerdir (config);
// eksik ya da tanınmayan alan bırakılırsa hazırlık RM'e teyit sorusu sorar.
interface Options {
  risk_profili: string[]; yatirim_ufku: string[]; likidite_ihtiyaci: string[]; gelir_nakit_akisi_ihtiyaci: string[]
  anapara_koruma_tercihi: string[]; doviz_maruz_kalma_tercihi: string[]; oncelikler: string[]
  acik_kisitlar: { metin: string; kural: string; aciklama: string }[]; sonraki_kimlik: string; ornek: Persona
}
type Form = Record<string, string>

const SELECTS: [keyof Options, string][] = [
  ['risk_profili', 'Risk profili'], ['yatirim_ufku', 'Yatırım ufku'], ['likidite_ihtiyaci', 'Likidite ihtiyacı'],
  ['gelir_nakit_akisi_ihtiyaci', 'Gelir / nakit akışı ihtiyacı'], ['anapara_koruma_tercihi', 'Anapara koruma'],
  ['doviz_maruz_kalma_tercihi', 'Döviz maruziyeti tercihi'],
]

async function post(url: string, body: BodyInit, json = true) {
  const r = await fetch(url, { method: 'POST', body, headers: json ? { 'Content-Type': 'application/json' } : undefined })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = j.detail
    throw new Error(typeof d === 'string' ? d : d?.hatalar ? d.hatalar.map((h: { sira: number; hata: string }) => `#${h.sira}: ${h.hata}`).join(' · ') : r.statusText)
  }
  return j
}

export function AddClientPanel({ onAdded, onClose }: { onAdded: (pid: string, open?: boolean) => void; onClose: () => void }) {
  const [opt, setOpt] = useState<Options | null>(null)
  const [tab, setTab] = useState<'form' | 'json'>('form')
  const [f, setF] = useState<Form>({})
  const [prio, setPrio] = useState<string[]>([])
  const [prioExtra, setPrioExtra] = useState('')
  const [cons, setCons] = useState<string[]>([])
  const [consExtra, setConsExtra] = useState('')
  const [json, setJson] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { fetch('/api/musteriler/secenekler').then((r) => r.json()).then(setOpt).catch(() => setMsg({ ok: false, text: 'Seçenekler alınamadı' })) }, [])
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }))
  const toggle = (list: string[], setList: (v: string[]) => void, v: string) => setList(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  const extra = (s: string) => s.split(/[\n;,]/).map((x) => x.trim()).filter(Boolean)

  const submitForm = async () => {
    setBusy(true); setMsg(null)
    try {
      const body = { ...f, yas: f.yas ? Number(f.yas) : null, oncelikler: [...prio, ...extra(prioExtra)], acik_kisitlar: [...cons, ...extra(consExtra)] }
      const r = await post('/api/musteriler', JSON.stringify(body))
      onAdded(r.persona.persona_id)
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }

  const submitJson = async (file?: File) => {
    setBusy(true); setMsg(null)
    try {
      let r
      if (file) {
        const fd = new FormData(); fd.append('dosya', file)
        r = await post('/api/musteriler/ice-aktar', fd, false)
      } else {
        try { JSON.parse(json) } catch (e) { throw new Error(`Geçerli bir JSON değil: ${(e as Error).message}`) }
        r = await post('/api/musteriler/ice-aktar', json)
      }
      const ids = r.eklenen.map((x: { persona: Persona }) => x.persona.persona_id)
      const warn = r.eklenen.flatMap((x: { persona: Persona; tanınmayan_alanlar: string[] }) => x.tanınmayan_alanlar.map((a) => `${x.persona.persona_id}: '${a}' alanı tanınmadı, atlandı`))
      const errs = r.hatalar.map((h: { sira: number; hata: string }) => `#${h.sira}: ${h.hata}`)
      setMsg({ ok: true, text: [`${ids.length} müşteri eklendi: ${ids.join(', ')}`, ...warn, ...errs].join(' · ') })
      // Hata ya da atlanan alan varsa rapor görünür kalsın: liste yenilenir, panel açık kalır
      if (ids.length) onAdded(ids[ids.length - 1], !(errs.length || warn.length))
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(false) }
  }

  const sample = () => {
    if (!opt) return
    const { persona_id: _ignored, ...rest } = opt.ornek  // eslint-disable-line @typescript-eslint/no-unused-vars
    setJson(JSON.stringify([{ ...rest, profil: 'Emekli öğretmen', yas: 66, rm_notu: 'Toplu ikramiyesini değerlendirmek istiyor.' }], null, 2))
  }

  return (
    <section className="card add-client">
      <div className="card-head">
        <div>
          <h2 style={{ margin: 0 }}>Müşteri ekle</h2>
          <p className="muted small">Kimlik verilmezse {opt?.sonraki_kimlik ?? 'sıradaki kimlik'} atanır. Boş ya da tanınmayan alan bırakılırsa hazırlık öneri üretmez, RM'e teyit sorusu sorar.</p>
        </div>
        <button className="ghost" onClick={onClose}>Kapat</button>
      </div>
      <nav className="tabs">
        <button className={tab === 'form' ? 'active' : ''} onClick={() => setTab('form')}>Seçerek ekle</button>
        <button className={tab === 'json' ? 'active' : ''} onClick={() => setTab('json')}>JSON içe aktar</button>
      </nav>
      {msg && <div className={`banner ${msg.ok ? 'ok' : 'error'} small`}>{msg.text}</div>}

      {tab === 'form' && opt && (
        <div className="stack">
          <div className="grid2">
            <label className="field"><span>Profil (kısa tanım) *</span><input value={f.profil ?? ''} onChange={(e) => set('profil', e.target.value)} placeholder="ör. Emekli öğretmen" /></label>
            <label className="field"><span>Yaş</span><input type="number" min={18} max={110} value={f.yas ?? ''} onChange={(e) => set('yas', e.target.value)} /></label>
            {SELECTS.map(([k, label]) => (
              <label key={k} className="field"><span>{label}</span>
                <select value={f[k] ?? ''} onChange={(e) => set(k, e.target.value)}>
                  <option value="">— seçilmedi —</option>
                  {(opt[k] as string[]).map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
            ))}
            <label className="field"><span>Yatırım amacı</span><input value={f.yatirim_amaci ?? ''} onChange={(e) => set('yatirim_amaci', e.target.value)} /></label>
            <label className="field"><span>Durum</span><input value={f.durum ?? ''} onChange={(e) => set('durum', e.target.value)} placeholder="ör. Varlığı TL mevduat ağırlıklı" /></label>
            <label className="field"><span>Kimlik (isteğe bağlı)</span><input value={f.persona_id ?? ''} onChange={(e) => set('persona_id', e.target.value.toUpperCase())} placeholder={opt.sonraki_kimlik} /></label>
          </div>
          <div>
            <div className="field-title">Öncelikler</div>
            <div className="choice-list">
              {opt.oncelikler.map((o) => (
                <label key={o} className={`choice ${prio.includes(o) ? 'on' : ''}`}><input type="checkbox" checked={prio.includes(o)} onChange={() => toggle(prio, setPrio, o)} />{o}</label>
              ))}
            </div>
            <input value={prioExtra} onChange={(e) => setPrioExtra(e.target.value)} placeholder="Başka öncelik (virgülle ayırın)" />
          </div>
          <div>
            <div className="field-title">Açık kısıtlar <span className="muted small">(kısıt kataloğundan; her biri bir kurala bağlı)</span></div>
            <div className="choice-list">
              {opt.acik_kisitlar.map((c) => (
                <label key={c.kural} className={`choice ${cons.includes(c.metin) ? 'on' : ''}`} title={c.aciklama}>
                  <input type="checkbox" checked={cons.includes(c.metin)} onChange={() => toggle(cons, setCons, c.metin)} />{c.metin}
                </label>
              ))}
            </div>
            <input value={consExtra} onChange={(e) => setConsExtra(e.target.value)} placeholder="Başka kısıt (serbest metin; kataloğa eşlenmezse RM'e sorulur)" />
          </div>
          <label className="field"><span>RM notu</span><textarea rows={2} value={f.rm_notu ?? ''} onChange={(e) => set('rm_notu', e.target.value)} /></label>
          <div className="row"><button className="primary" disabled={busy || !f.profil} onClick={submitForm}>{busy ? 'Ekleniyor…' : 'Müşteriyi ekle'}</button></div>
        </div>
      )}

      {tab === 'json' && (
        <div className="stack">
          <p className="muted small">Persona biçiminde tek nesne ya da nesne listesi: <code>profil, yas, risk_profili, yatirim_ufku, likidite_ihtiyaci, gelir_nakit_akisi_ihtiyaci, anapara_koruma_tercihi, doviz_maruz_kalma_tercihi, yatirim_amaci, durum, oncelikler[], acik_kisitlar[], rm_notu</code> (<code>persona_id</code> isteğe bağlı). Her kayıt ayrı doğrulanır; hatalı olanlar raporlanır, diğerleri eklenir.</p>
          <textarea className="json-input" rows={14} value={json} onChange={(e) => setJson(e.target.value)} placeholder='[{"profil": "...", "risk_profili": "Dengeli", ...}]' />
          <div className="row wrap">
            <button className="primary" disabled={busy || !json.trim()} onClick={() => submitJson()}>{busy ? 'İçe aktarılıyor…' : 'İçe aktar'}</button>
            <button className="ghost" onClick={sample} disabled={!opt}>Örnekle doldur</button>
            <label className="upload-inline">.json dosyası seç <input type="file" accept=".json,application/json" onChange={(e) => e.target.files?.[0] && submitJson(e.target.files[0])} /></label>
          </div>
        </div>
      )}
    </section>
  )
}
