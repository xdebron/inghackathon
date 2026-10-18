import { CurrentAllocInput } from './CurrentAllocInput'
import type { Persona } from '../types'

const TEXT_FIELDS: [keyof Persona, string][] = [
  ['risk_profili', 'Risk profili'],
  ['yatirim_ufku', 'Yatırım ufku'],
  ['likidite_ihtiyaci', 'Likidite ihtiyacı'],
  ['gelir_nakit_akisi_ihtiyaci', 'Gelir / nakit akışı ihtiyacı'],
  ['anapara_koruma_tercihi', 'Anapara koruma'],
  ['doviz_maruz_kalma_tercihi', 'Döviz maruziyeti tercihi'],
  ['yatirim_amaci', 'Yatırım amacı'],
  ['durum', 'Durum'],
]

const SOURCES: [string, string][] = [
  ['tcmb', 'TCMB kurları'],
  ['tefas', 'TEFAS fonları'],
  ['evds', 'EVDS göstergeleri'],
  ['ing', 'ING (mevduat, stopaj)'],
  ['kap', 'KAP fon bilgileri'],
  ['gecmis', 'Tarihçe serileri (EVDS, Hazine)'],
  ['bist', 'BIST hisseleri'],
]

interface Props {
  persona: Persona
  original: Persona
  onChange: (p: Persona) => void
  onReset: () => void
  disabled: string[]
  onToggleSource: (s: string) => void
  onRun: () => void
  running: boolean
  current: Record<string, number>
  onCurrent: (v: Record<string, number>) => void
}

export function PersonaPanel({ persona, original, onChange, onReset, disabled, onToggleSource, onRun, running, current, onCurrent }: Props) {
  const changed = (k: keyof Persona) => JSON.stringify(persona[k]) !== JSON.stringify(original[k])
  const anyChanged = (Object.keys(persona) as (keyof Persona)[]).some(changed)
  const set = (k: keyof Persona, v: unknown) => onChange({ ...persona, [k]: v === '' ? null : v })
  const list = (k: 'oncelikler' | 'acik_kisitlar', v: string) =>
    onChange({ ...persona, [k]: v.split('\n').map((s) => s.trim()).filter(Boolean) })

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>{persona.persona_id} · {persona.profil}, {persona.yas}</h2>
          <p className="muted">{persona.rm_notu}</p>
        </div>
        {anyChanged && (
          <div className="row">
            <span className="badge warn">Persona değiştirildi</span>
            <button className="ghost" onClick={onReset}>Orijinale dön</button>
          </div>
        )}
      </div>
      <div className="grid2">
        {TEXT_FIELDS.map(([k, label]) => (
          <label key={k} className={changed(k) ? 'field changed' : 'field'}>
            <span>{label}</span>
            <input value={(persona[k] as string) ?? ''} onChange={(e) => set(k, e.target.value)} />
          </label>
        ))}
        <label className={changed('oncelikler') ? 'field changed' : 'field'}>
          <span>Öncelikler (satır başına bir)</span>
          <textarea rows={3} value={(persona.oncelikler ?? []).join('\n')} onChange={(e) => list('oncelikler', e.target.value)} />
        </label>
        <label className={changed('acik_kisitlar') ? 'field changed' : 'field'}>
          <span>Açık kısıtlar (satır başına bir)</span>
          <textarea rows={3} value={(persona.acik_kisitlar ?? []).join('\n')} onChange={(e) => list('acik_kisitlar', e.target.value)} />
        </label>
      </div>
      <CurrentAllocInput value={current} onChange={onCurrent} />
      <div className="card-foot">
        <div className="row wrap">
          <span className="muted small">Veri kesintisi simülasyonu:</span>
          {SOURCES.map(([s, label]) => (
            <label key={s} className="check">
              <input type="checkbox" checked={disabled.includes(s)} onChange={() => onToggleSource(s)} /> {label} kapalı
            </label>
          ))}
        </div>
        <button className="primary" onClick={onRun} disabled={running}>
          {running ? 'Hazırlanıyor…' : 'Görüşme hazırlığı üret'}
        </button>
      </div>
    </section>
  )
}
