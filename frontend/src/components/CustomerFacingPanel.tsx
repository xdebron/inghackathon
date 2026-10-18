import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { RunRecord } from '../types'
import { MiniMarkdown } from './MiniMarkdown'

// RM tarafı: müşteriye gidecek her şey burada RM onayından geçer.
//  - Müşteri özeti: onaylı hazırlık paketinden ajan sade bir özet yazar → RM düzenler/onaylar → müşteri portalında görünür.
//  - Müşteri soruları: portaldan gelen soru → ajan yanıt taslağı → RM düzenler/onaylar → müşteri görür.

interface CustomerQ { id: string; soru: string; zaman: string; durum: 'bekliyor' | 'taslak' | 'yanitlandi' | 'reddedildi'; taslak?: string; taslak_ihlaller?: string[]; cevap?: string; yanitlayan?: string }
interface Summary { metin: string; durum: 'rm_onayi_bekliyor' | 'ihlal' | 'onaylandi' | 'reddedildi'; ihlaller: string[]; onaylayan?: string; onay_zamani?: string; sure_sn?: number }
type CRun = RunRecord & { musteri_ozeti?: Summary; musteri_gelen?: CustomerQ[] }

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = j.detail
    throw new Error(typeof d === 'string' ? d : d?.violations ? `${d.message}: ${d.violations.join('; ')}` : r.statusText)
  }
  return j as T
}

const rmName = () => { try { return localStorage.getItem('rm_name') ?? '' } catch { return '' } }

export function CustomerFacingPanel({ run, onUpdated }: { run: CRun; onUpdated: (r: RunRecord) => void }) {
  const [name, setName] = useState(rmName)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [edit, setEdit] = useState<Record<string, string>>({})
  const timer = useRef<number | null>(null)
  const approved = run.status === 'rm_onayladi'
  const mo = run.musteri_ozeti
  const qs = run.musteri_gelen ?? []

  useEffect(() => { try { localStorage.setItem('rm_name', name) } catch { /* depolama kapalı */ } }, [name])
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current) }, [])

  const job = async (key: string, path: string, body: unknown) => {
    setErr(null); setBusy(key)
    try {
      const { is_id } = await post<{ is_id: string }>(path, body)
      timer.current = window.setInterval(async () => {
        const j = await (await fetch(`/api/ajan/is/${is_id}`)).json()
        if (j.durum !== 'calisiyor') {
          window.clearInterval(timer.current!)
          setBusy(null)
          if (j.hata) setErr(j.hata)
          onUpdated(await api.getRun(run.run_id))
        }
      }, 1500)
    } catch (e) { setErr((e as Error).message); setBusy(null) }
  }

  const decide = async (path: string, karar: 'onay' | 'red', metin?: string) => {
    setErr(null)
    if (!name.trim()) return setErr('Onay için RM adı gerekli')
    try { onUpdated(await post<RunRecord>(path, { karar, rm_name: name, metin })) } catch (e) { setErr((e as Error).message) }
  }

  return (
    <section className="card customer-facing">
      <div className="card-head">
        <div>
          <h3 style={{ margin: 0 }}>Müşteriye gidecek içerik</h3>
          <div className="muted small">Müşteri portalında yalnızca burada onayladığınız metinler görünür.</div>
        </div>
        <input className="rm-name" placeholder="RM adı" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      {err && <div className="banner error">{err}</div>}

      <div className="cf-block">
        <h4>Müşteri özeti</h4>
        {!approved && <p className="muted small">Müşteri özeti, hazırlık notu RM tarafından onaylandıktan sonra hazırlanabilir.</p>}
        {approved && !mo && (
          <button className="primary" disabled={!!busy} onClick={() => job('ozet', '/api/ajan/musteri-ozeti', { run_id: run.run_id })}>
            {busy === 'ozet' ? 'Ajan yazıyor…' : 'Müşteri özetini ajanla hazırla'}
          </button>
        )}
        {mo && mo.durum === 'onaylandi' && (
          <>
            <div className="banner ok small">Yayında · {mo.onaylayan} · {mo.onay_zamani ? new Date(mo.onay_zamani).toLocaleString('tr-TR') : ''}</div>
            <div className="customer-preview"><MiniMarkdown text={mo.metin} /></div>
          </>
        )}
        {mo && (mo.durum === 'rm_onayi_bekliyor' || mo.durum === 'ihlal') && (
          <>
            {mo.ihlaller.length > 0 && <div className="banner warnbox small">Guard uyarısı (düzenleyin): {mo.ihlaller.join('; ')}</div>}
            <textarea rows={10} value={edit.ozet ?? mo.metin} onChange={(e) => setEdit({ ...edit, ozet: e.target.value })} />
            <div className="row">
              <button className="primary" onClick={() => decide(`/api/portal/runs/${run.run_id}/musteri-ozeti/karar`, 'onay', edit.ozet)}>Onayla ve yayınla</button>
              <button className="danger" onClick={() => decide(`/api/portal/runs/${run.run_id}/musteri-ozeti/karar`, 'red')}>Reddet</button>
            </div>
          </>
        )}
        {mo && (mo.durum === 'reddedildi' || mo.durum === 'onaylandi') && approved && (
          <button className="ghost small" disabled={!!busy} onClick={() => job('ozet', '/api/ajan/musteri-ozeti', { run_id: run.run_id })}>
            {busy === 'ozet' ? 'Ajan yazıyor…' : 'Yeniden hazırla'}
          </button>
        )}
      </div>

      <div className="cf-block">
        <h4>Müşteriden gelen sorular {qs.length > 0 && <span className="muted small">({qs.filter((q) => q.durum === 'bekliyor' || q.durum === 'taslak').length} bekliyor)</span>}</h4>
        {qs.length === 0 && <p className="muted small">Müşteri portalından gelen soru yok.</p>}
        {qs.slice().reverse().map((q) => (
          <div key={q.id} className="cq">
            <div><strong>Müşteri:</strong> {q.soru} <span className="muted small">· {new Date(q.zaman).toLocaleString('tr-TR')}</span></div>
            {q.durum === 'bekliyor' && (
              <button disabled={!!busy} onClick={() => job(q.id, '/api/ajan/musteri-cevap', { run_id: run.run_id, soru_id: q.id })}>
                {busy === q.id ? 'Ajan taslak yazıyor…' : 'Ajanla yanıt taslağı hazırla'}
              </button>
            )}
            {q.durum === 'taslak' && (
              <>
                {(q.taslak_ihlaller ?? []).length > 0 && <div className="banner warnbox small">Guard uyarısı: {q.taslak_ihlaller!.join('; ')}</div>}
                <textarea rows={5} value={edit[q.id] ?? q.taslak ?? ''} onChange={(e) => setEdit({ ...edit, [q.id]: e.target.value })} />
                <div className="row">
                  <button className="primary" onClick={() => decide(`/api/portal/runs/${run.run_id}/musteri-soru/${q.id}/karar`, 'onay', edit[q.id])}>Onayla ve gönder</button>
                  <button className="danger" onClick={() => decide(`/api/portal/runs/${run.run_id}/musteri-soru/${q.id}/karar`, 'red')}>Reddet</button>
                </div>
              </>
            )}
            {q.durum === 'yanitlandi' && <div className="cq-answer"><span className="badge status-rm_onayladi">gönderildi · {q.yanitlayan}</span><MiniMarkdown text={q.cevap ?? ''} /></div>}
            {q.durum === 'reddedildi' && <span className="badge status-oneri_yok">reddedildi</span>}
          </div>
        ))}
      </div>
    </section>
  )
}
