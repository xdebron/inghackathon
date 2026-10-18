import { useState } from 'react'
import type { Question } from '../types'

const RULES = ['K_YUKSEK_RISK', 'K_LIKIDITE_KILIDI', 'K_DOVIZ_ARTIRMAMA', 'K_YOGUNLASMA', 'K_KISA_VADE_NAKIT_YOK']
const LIST_FIELDS = new Set(['oncelikler', 'acik_kisitlar'])
const ISSUE: Record<string, string> = {
  eksik: 'Eksik alan',
  celiski: 'Çelişki',
  taninmayan_deger: 'Tanınmayan değer',
  eslesmeyen_kisit: 'Eşleşmeyen kısıt',
}

function contradictionId(q: Question) {
  return q.detail.match(/^\[(C_[A-Z_]+)\]/)?.[1]
}

interface Props {
  questions: Question[]
  previous: Record<string, unknown>
  onSubmit: (answers: Record<string, unknown>) => void
}

export function QuestionsPanel({ questions, previous, onSubmit }: Props) {
  const [answers, setAnswers] = useState<Record<string, unknown>>({})
  const set = (k: string, v: unknown) => setAnswers((a) => ({ ...a, [k]: v }))

  return (
    <section className="card alert">
      <h2>RM teyidi gerekli</h2>
      <p className="muted">
        Sistem eksik, çelişkili veya yorumlanamayan bilgi için varsayım yapmaz. Aşağıdaki sorular yanıtlanmadan öneri üretilmez.
      </p>
      {questions.map((q, i) => {
        const cid = q.issue === 'celiski' ? contradictionId(q) : undefined
        return (
          <div key={i} className="question">
            <div className="row">
              <span className={`badge ${q.issue === 'celiski' ? 'warn' : 'info'}`}>{ISSUE[q.issue]}</span>
              <strong>{q.question}</strong>
            </div>
            <p className="muted small">{q.detail}</p>
            {q.issue === 'eslesmeyen_kisit' ? (
              <select onChange={(e) => set(q.field, e.target.value)} defaultValue="">
                <option value="" disabled>Kural seçin…</option>
                {RULES.map((r) => <option key={r} value={r}>{r}</option>)}
                <option value="yok">Uygulama (gerekçe kayda geçer)</option>
              </select>
            ) : (
              <div className="row wrap">
                <input
                  placeholder={`Yeni değer (${q.field})`}
                  onChange={(e) =>
                    set(q.field, LIST_FIELDS.has(q.field) ? e.target.value.split(',').map((s) => s.trim()).filter(Boolean) : e.target.value)
                  }
                />
                {cid && (
                  <label className="check">
                    <input type="checkbox" onChange={(e) => set(`onay.${cid}`, e.target.checked)} /> Mevcut değerlerle devam (bilerek onaylıyorum)
                  </label>
                )}
              </div>
            )}
          </div>
        )
      })}
      <button className="primary" onClick={() => onSubmit({ ...previous, ...Object.fromEntries(Object.entries(answers).filter(([, v]) => v !== '' && v !== false)) })}>
        Yanıtlarla devam et
      </button>
    </section>
  )
}
