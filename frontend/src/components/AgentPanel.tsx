import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { RunRecord } from '../types'
import { MiniMarkdown } from './MiniMarkdown'

// Lokal LLM ajanı (Qwen, vLLM): araçlarla veriyi ve RL modelini sorgulayıp hazırlık notu yazar veya RM'in sorusunu yanıtlar.
// Uzun süren iş arka planda çalışır; panel canlı izi yoklar (tünel zaman aşımına düşmemek için).

interface Step {
  adim: number
  tip: 'llm' | 'arac' | 'yanit' | 'duzeltme' | 'yonlendirme'
  ad?: string
  argumanlar?: Record<string, unknown>
  arac_istegi?: string[]
  basarili?: boolean
  sure_ms?: number
  sonuc_ozet?: string
  kalan_ihlal?: number
  not?: string
  karakter?: number
}

interface Job {
  id: string
  tur: 'yaz' | 'sor'
  durum: 'calisiyor' | 'bitti' | 'hata'
  iz: Step[]
  sonuc: { uygulandi?: boolean; ihlaller: string[]; metin?: string; cevap?: string; sure_sn: number; arac_sayisi?: number } | null
  hata: string | null
}

type AgentRun = RunRecord & {
  ajan?: { model: string; sure_sn: number; arac_sayisi: number; duzeltme_turu: number; uygulandi?: boolean; neden?: string | null;
           ihlaller: string[]; iz: Step[]; hata?: string; metin?: string; token?: { prompt_tokens: number; completion_tokens: number } }
  ajan_sorulari?: { soru: string; cevap: string; ihlaller: string[]; zaman: string; sure_sn: number; model?: string; iz?: Step[] }[]
}

const STEP_LABEL: Record<Step['tip'], string> = {
  llm: 'Model araç istedi', arac: 'Araç', yanit: 'Metin yazıldı', duzeltme: 'Guard düzeltmesi', yonlendirme: 'Yönlendirme',
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/ajan${path}`, body === undefined ? undefined
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : r.statusText)
  return j as T
}

function Steps({ steps }: { steps: Step[] }) {
  return (
    <ol className="agent-steps">
      {steps.map((s, i) => (
        <li key={i} className={s.basarili === false ? 'fail' : ''}>
          <span className={`badge ${s.tip === 'arac' ? 'actor-deterministik' : s.tip === 'duzeltme' ? 'actor-rm' : 'actor-llm'}`}>
            {STEP_LABEL[s.tip]}
          </span>{' '}
          {s.tip === 'arac' && <><code>{s.ad}</code>{s.argumanlar && Object.keys(s.argumanlar).length > 0 &&
            <span className="muted small"> {JSON.stringify(s.argumanlar)}</span>}</>}
          {s.tip === 'llm' && <span className="small">{s.arac_istegi?.join(', ')}</span>}
          {s.tip === 'duzeltme' && <span className="small">kalan ihlal: {s.kalan_ihlal}</span>}
          {s.not && <span className="small muted"> {s.not}</span>}
          {s.sure_ms !== undefined && <span className="muted small"> · {(s.sure_ms / 1000).toFixed(1)} sn</span>}
          {s.basarili === false && <div className="small">{s.sonuc_ozet}</div>}
        </li>
      ))}
    </ol>
  )
}

export function AgentPanel({ run, onUpdated }: { run: AgentRun; onUpdated: (r: RunRecord) => void }) {
  const [srv, setSrv] = useState<{ erisilebilir: boolean; model?: string; sunucu: string; hata?: string } | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [q, setQ] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const timer = useRef<number | null>(null)
  const decided = !!run.rm_decision

  useEffect(() => { call<typeof srv>('/durum').then(setSrv).catch(() => setSrv(null)) }, [])
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current) }, [])

  const poll = (id: string) => {
    if (timer.current) window.clearInterval(timer.current)
    timer.current = window.setInterval(async () => {
      try {
        const j = await call<Job>(`/is/${id}`)
        setJob(j)
        if (j.durum !== 'calisiyor') {
          window.clearInterval(timer.current!)
          timer.current = null
          onUpdated(await api.getRun(run.run_id))
        }
      } catch (e) {
        setErr((e as Error).message)
        window.clearInterval(timer.current!)
      }
    }, 1500)
  }

  const start = async (path: string, body: unknown) => {
    setErr(null)
    try {
      const { is_id } = await call<{ is_id: string }>(path, body)
      setJob({ id: is_id, tur: path === '/yaz' ? 'yaz' : 'sor', durum: 'calisiyor', iz: [], sonuc: null, hata: null })
      poll(is_id)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const running = job?.durum === 'calisiyor'
  const a = run.ajan

  return (
    <section className="agent card">
      <div className="card-head">
        <div>
          <h3 style={{ margin: 0 }}>Hazırlık ajanı (lokal LLM + araçlar)</h3>
          <div className="muted small">
            Ajan kural motorunun ve RL modelinin çıktısını araçlarla sorgular; sayıları yalnızca araç çıktılarından alır, metin guard'dan geçmeden kayda yazılmaz.
          </div>
        </div>
        <span className={`badge ${srv?.erisilebilir ? 'status-rm_onayladi' : 'status-oneri_yok'}`}>
          {srv ? (srv.erisilebilir ? `${srv.model}` : 'LLM sunucusu kapalı') : '…'}
        </span>
      </div>

      {a && !running && (
        <div className={`banner ${a.hata || a.uygulandi === false ? 'warnbox' : 'ok'} small`}>
          {a.hata ? `Son ajan denemesi başarısız: ${a.hata}`
            : `Son ajan çalışması: ${a.model} · ${a.arac_sayisi} araç çağrısı · ${a.sure_sn} sn · ${a.duzeltme_turu} düzeltme turu · `
              + (a.uygulandi === false ? `taslağa uygulanmadı (${a.neden ?? 'guard ihlali'})` : 'yukarıdaki taslak ajanın metni')}
        </div>
      )}

      <div className="row wrap">
        <button className="primary" disabled={!srv?.erisilebilir || running || decided} onClick={() => start('/yaz', { run_id: run.run_id })}>
          {running && job?.tur === 'yaz' ? 'Ajan çalışıyor…' : 'Qwen ajanıyla yaz'}
        </button>
        {decided && <span className="muted small">RM kararı verilmiş kayıtta taslak değiştirilmez.</span>}
      </div>

      <div className="agent-ask">
        <textarea rows={2} placeholder="Ajana sor (örn. 'Politika faizi 5 puan düşerse model dağılımı nasıl değişir?')" value={q}
          onChange={(e) => setQ(e.target.value)} />
        <button disabled={!srv?.erisilebilir || running || !q.trim()} onClick={() => start('/sor', { run_id: run.run_id, soru: q })}>
          {running && job?.tur === 'sor' ? 'Yanıtlanıyor…' : 'Sor'}
        </button>
      </div>

      {err && <div className="banner error">{err}</div>}

      {job && (
        <div className="agent-job">
          <h4>{job.tur === 'yaz' ? 'Hazırlık notu yazımı' : 'Soru'} · {job.durum === 'calisiyor' ? 'çalışıyor' : job.durum}</h4>
          <Steps steps={job.iz} />
          {job.hata && <div className="banner error">{job.hata}</div>}
          {job.sonuc && job.tur === 'yaz' && (
            <div className={`banner ${job.sonuc.uygulandi ? 'ok' : 'warnbox'}`}>
              {job.sonuc.uygulandi ? 'Ajan metni guard\'dan geçti ve taslağa yazıldı.'
                : `Taslağa uygulanmadı. Kalan ihlaller: ${job.sonuc.ihlaller.join('; ') || '—'}`}
            </div>
          )}
          {job.sonuc && job.tur === 'sor' && (
            <div className="banner ok small">Cevap kaydedildi; aşağıdaki soru-cevap geçmişinde en üstte.</div>
          )}
        </div>
      )}

      {(run.ajan_sorulari?.length ?? 0) > 0 && (
        <div className="agent-history">
          <h4>Soru-cevap geçmişi ({run.ajan_sorulari!.length}) · kanıt kaydında saklanır</h4>
          {run.ajan_sorulari!.slice().reverse().map((x, i) => {
            const tools = [...new Set((x.iz ?? []).filter((s) => s.tip === 'arac').map((s) => s.ad))]
            return (
              <div key={x.zaman + i} className="agent-qa">
                <div className="agent-q"><strong>RM:</strong> {x.soru}</div>
                <div className="muted small">
                  {new Date(x.zaman).toLocaleString('tr-TR')} · {x.model ?? 'ajan'} · {x.sure_sn} sn
                  {tools.length > 0 && <> · araçlar: {tools.map((t) => <code key={t}>{t}</code>)}</>}
                </div>
                {x.ihlaller.length > 0
                  ? <div className="banner warnbox small">Guard uyarısı: {x.ihlaller.join('; ')}</div>
                  : <span className="badge status-rm_onayladi small">guard temiz</span>}
                <MiniMarkdown text={x.cevap} />
              </div>
            )
          })}
        </div>
      )}

      {a?.iz && !running && (
        <details>
          <summary className="small">Son ajan çalışmasının araç izi ({a.iz.filter((s) => s.tip === 'arac').length} araç)</summary>
          <Steps steps={a.iz} />
        </details>
      )}
    </section>
  )
}
