import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { AddClientPanel } from '../components/AddClientPanel'
import { PersonaPanel } from '../components/PersonaPanel'
import { QuestionsPanel } from '../components/QuestionsPanel'
import { ResultView } from '../components/ResultView'
import { toFractions } from '../components/CurrentAllocInput'
import { RUN_STATUS, fmtTime } from '../format'
import type { Persona, RunRecord } from '../types'

interface Overview {
  persona: Persona
  kayit_sayisi: number
  son: { run_id: string; created_at: string; status: string; llm?: string; musteri_ozeti?: string; bekleyen_musteri_sorusu: number } | null
}
interface ArchiveRow { run_id: string; created_at: string; status: string; rm_decision?: string }

const STATUS_DOT: Record<string, string> = {
  rm_onayladi: 'ok', rm_onayi_bekliyor: 'pending', rm_teyidi_bekliyor: 'warn', oneri_yok: 'warn', rm_reddetti: 'err',
}

export function RMWorkspace() {
  const [overview, setOverview] = useState<Overview[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState<Persona | null>(null)
  const [disabled, setDisabled] = useState<string[]>([])
  const [current, setCurrent] = useState<Record<string, number>>({})
  const [run, setRun] = useState<RunRecord | null>(null)
  const [archive, setArchive] = useState<ArchiveRow[]>([])
  const [running, setRunning] = useState(false)
  const [loading, setLoading] = useState(false)
  const [settings, setSettings] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState<Set<string>>(new Set())

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/portal/personalar')
      const rows: Overview[] = await r.json()
      setOverview(rows)
      const c = await (await fetch('/api/musteriler')).json()
      setAdded(new Set(c.filter((x: { eklenen: boolean }) => x.eklenen).map((x: Persona) => x.persona_id)))
      return rows
    } catch (e) { setError(`Backend'e ulaşılamadı: ${(e as Error).message}`) }
    return [] as Overview[]
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const loadArchive = async (pid: string) => {
    try { setArchive(await (await fetch(`/api/portal/arsiv/${pid}`)).json()) } catch { setArchive([]) }
  }

  const choose = async (o: Overview) => {
    const pid = o.persona.persona_id
    setSelected(pid); setDraft(structuredClone(o.persona)); setCurrent({}); setRun(null); setError(null); setSettings(false); setAdding(false)
    loadArchive(pid)
    if (o.son) {
      setLoading(true)
      try { setRun(await api.getRun(o.son.run_id)) } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
    }
  }

  const execute = async (rm_answers: Record<string, unknown> = {}) => {
    if (!draft) return
    setRunning(true); setError(null)
    try {
      const r = await api.run({ persona: draft, rm_answers, disabled_sources: disabled, mevcut_dagilim: toFractions(current) })
      setRun(r); setSettings(false)
      refresh(); loadArchive(draft.persona_id)
    } catch (e) { setError((e as Error).message) } finally { setRunning(false) }
  }

  const openRun = async (id: string) => {
    setLoading(true)
    try { const r = await api.getRun(id); setRun(r); setDraft(r.persona) } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }

  const onAdded = async (pid: string, open = true) => {
    const rows = await refresh()
    const o = rows.find((x) => x.persona.persona_id === pid)
    if (o && open) choose(o)
  }

  const removeClient = async () => {
    if (!selected || !window.confirm(`${selected} müşterisi silinsin mi? Kanıt kayıtları silinmez.`)) return
    const r = await fetch(`/api/musteriler/${selected}`, { method: 'DELETE' })
    if (!r.ok) { setError((await r.json()).detail ?? r.statusText); return }
    setSelected(null); setDraft(null); setRun(null); refresh()
  }

  const sel = overview.find((o) => o.persona.persona_id === selected)
  const original = sel?.persona ?? null

  return (
    <div className="ws">
      <aside className="ws-side">
        <div className="side-head">
          <div className="side-title">Müşteriler</div>
          <button className="ghost small" onClick={() => { setAdding(true); setSelected(null); setDraft(null); setRun(null) }}>+ Müşteri ekle</button>
        </div>
        {overview.map((o) => {
          const s = o.son
          return (
            <button key={o.persona.persona_id} className={`client ${o.persona.persona_id === selected ? 'active' : ''}`} onClick={() => choose(o)}>
              <span className={`dot ${s ? STATUS_DOT[s.status] ?? '' : 'none'}`} />
              <span className="client-main">
                <strong>{o.persona.profil}</strong>
                <span className="muted small">{o.persona.persona_id} · {o.persona.risk_profili} · {o.persona.yatirim_ufku}</span>
                <span className="small client-status">{s ? RUN_STATUS[s.status] ?? s.status : 'Hazırlık yok'}</span>
              </span>
              {!!s?.bekleyen_musteri_sorusu && <span className="pill" title="Müşteriden gelen bekleyen soru">{s.bekleyen_musteri_sorusu}</span>}
            </button>
          )
        })}
        <div className="side-note muted small">Her müşteri için en son hazırlık açılır; eski kayıtlar arşivdedir.</div>
      </aside>

      <section className="ws-main">
        {error && <div className="banner error">{error}</div>}
        {adding && <AddClientPanel onAdded={onAdded} onClose={() => setAdding(false)} />}
        {!draft && !adding && (
          <div className="empty-state">
            <h2>RM çalışma alanı</h2>
            <p className="muted">Soldan bir müşteri seçin. Son hazırlık paketi açılır; ajanla not yazdırabilir, what-if senaryolarını deneyebilir ve müşteriye gidecek içeriği onaylayabilirsiniz.</p>
          </div>
        )}
        {draft && original && (
          <>
            <header className="client-head">
              <div>
                <h2>{original.profil}, {original.yas}</h2>
                <div className="chips">
                  <span className="chip-lg">{draft.risk_profili}</span>
                  <span className="chip-lg">{draft.yatirim_ufku}</span>
                  <span className="chip-lg">Likidite: {draft.likidite_ihtiyaci}</span>
                  <span className="chip-lg">Anapara koruma: {draft.anapara_koruma_tercihi}</span>
                  {(draft.acik_kisitlar ?? []).map((k) => <span key={k} className="chip-lg warn">{k}</span>)}
                </div>
                <p className="muted small">{original.rm_notu}</p>
              </div>
              <div className="head-actions">
                {run && <span className={`badge status-${run.status}`}>{RUN_STATUS[run.status]}</span>}
                {archive.length > 1 && (
                  <select value={run?.run_id ?? ''} onChange={(e) => openRun(e.target.value)} title="Arşiv">
                    {archive.map((a) => <option key={a.run_id} value={a.run_id}>{fmtTime(a.created_at)} · {RUN_STATUS[a.status] ?? a.status}</option>)}
                  </select>
                )}
                {selected && added.has(selected) && <button className="ghost danger-text" onClick={removeClient}>Müşteriyi sil</button>}
                <button className="ghost" onClick={() => setSettings(!settings)}>{settings ? 'Ayarları kapat' : 'Profil ve veri ayarları'}</button>
                <button className="primary" onClick={() => execute()} disabled={running}>{running ? 'Hazırlanıyor…' : run ? 'Yeniden hazırla' : 'Hazırlık üret'}</button>
              </div>
            </header>

            {settings && (
              <PersonaPanel persona={draft} original={original} onChange={setDraft} onReset={() => setDraft(structuredClone(original))}
                disabled={disabled} onToggleSource={(s) => setDisabled((d) => (d.includes(s) ? d.filter((x) => x !== s) : [...d, s]))}
                onRun={() => execute()} running={running} current={current} onCurrent={setCurrent} />
            )}
            {loading && <p className="muted">Kayıt yükleniyor…</p>}
            {run?.status === 'rm_teyidi_bekliyor' && run.questions && (
              <QuestionsPanel key={run.run_id} questions={run.questions} previous={run.rm_answers} onSubmit={(a) => execute(a)} />
            )}
            {run && run.status !== 'rm_teyidi_bekliyor' && <ResultView key={run.run_id} run={run} onUpdated={(r) => { setRun(r); refresh() }} />}
            {!run && !loading && <div className="empty-state small"><p className="muted">Bu müşteri için henüz hazırlık yok. "Hazırlık üret" ile başlayın.</p></div>}
          </>
        )}
      </section>
    </div>
  )
}
