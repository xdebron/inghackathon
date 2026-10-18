import { useState } from 'react'
import { api } from '../api'
import { CRITERIA, FIELD_LABEL, STATUS_LABEL, fmtField, fmtTime, pct } from '../format'
import type { Criterion, DataPoint, RunRecord, Scored } from '../types'
import { RLView } from './RLView'
import { RLCategoryView } from './RLCategoryView'
import { RLSummaryCard } from './RLSummaryCard'
import { AllocBar, CAT_LABEL } from './AllocBar'
import { AgentPanel } from './AgentPanel'
import { WhatIfPanel } from './WhatIfPanel'
import { CustomerFacingPanel } from './CustomerFacingPanel'

const TABS = ['Ajan ve not', 'What-if', 'Öneriler', 'Model (RL)', 'Kanıt'] as const
type Tab = (typeof TABS)[number]

function Breakdown({ items }: { items: Criterion[] }) {
  const max = Math.max(...items.map((c) => c.weight), 0.0001)
  return (
    <table className="breakdown">
      <thead>
        <tr><th>Kriter</th><th>Ağırlık</th><th>Puan (0-1)</th><th>Katkı</th><th>Dayanak</th></tr>
      </thead>
      <tbody>
        {items.map((c) => (
          <tr key={c.criterion} className={c.normalized === null ? 'dim' : ''}>
            <td>{CRITERIA[c.criterion]}</td>
            <td><div className="bar"><div style={{ width: `${(c.weight / max) * 100}%` }} /></div>{c.weight.toFixed(2)}</td>
            <td>{c.normalized === null ? '—' : c.normalized.toFixed(2)}</td>
            <td><strong>{c.contribution.toFixed(3)}</strong></td>
            <td className="small">{c.explanation}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function SourceChip({ dp }: { dp: DataPoint }) {
  return (
    <span className={`chip st-${dp.status}`} title={`${dp.source}${dp.url ? ' · ' + dp.url : ''}\nVeri tarihi: ${dp.as_of ?? '—'}\nAlınma: ${fmtTime(dp.fetched_at)}${dp.note ? '\n' + dp.note : ''}`}>
      {STATUS_LABEL[dp.status]}
    </span>
  )
}

function ProductCard({ s, weight, note }: { s: Scored; weight?: number; note?: string | null }) {
  const [open, setOpen] = useState(false)
  const p = s.product
  const show = ['getiri_1y', 'reel_getiri_1y', 'getiri_1y_karsilastirma', 'benchmark_farki_1y', 'volatilite', 'max_dusus', 'rd',
    'valor_gun', 'vade_gun', 'yonetim_ucreti', 'giris_komisyonu', 'cikis_komisyonu', 'buyukluk', 'stopaj', 'sektor_farki',
    'tmsf_limiti', 'api_capraz_kontrol', 'ing_platformunda', 'uyari_notu']
  return (
    <div className="product">
      <div className="product-head" onClick={() => setOpen(!open)}>
        {weight !== undefined ? <span className="rank weight">%{Math.round(weight * 100)}</span> : <span className="rank">{s.rank}</span>}
        <div className="grow">
          <strong>{p.name}</strong>
          <div className="muted small">{p.category} · {p.currency}{weight !== undefined ? ` · kural/skor sırası ${s.rank}` : ''}</div>
        </div>
        <div className="score">
          <div>{(s.score * 100).toFixed(0)}<span className="small">/100</span></div>
          <div className={`small conf ${s.confidence < 0.7 ? 'low' : ''}`}>güven {(s.confidence * 100).toFixed(0)}%</div>
        </div>
      </div>
      {note && <div className="banner warnbox small">{note}</div>}
      <div className="facts">
        {show.filter((k) => p.fields[k]).map((k) => (
          <div key={k} className="fact">
            <span className="muted small">{FIELD_LABEL[k] ?? k}</span>
            <span>{fmtField(k, p.fields[k])} <SourceChip dp={p.fields[k]} /></span>
          </div>
        ))}
      </div>
      <button className="ghost small" onClick={() => setOpen(!open)}>{open ? 'Skor dökümünü gizle' : 'Skor dökümünü göster'}</button>
      {open && <Breakdown items={s.breakdown} />}
    </div>
  )
}

// Öneriler sekmesi RL kategori modelinden kurulur: model kategori dağılımını seçer, her kategori uygun (kurallardan geçmiş)
// ürünlere eşlenir. Kurallar yalnızca uygunluk filtresidir; kısıtı ihlal eden ürün RL'in seçebileceği havuza hiç girmez.
function RLRecommendations({ run }: { run: RunRecord }) {
  const k = run.rl_kategori
  if (!k || k.durum !== 'tamam') {
    return (
      <section className="card">
        <div className="banner warnbox small">RL dağılımı bu hazırlıkta yok{k?.neden ? ` (${k.neden})` : ''}. Aşağıdaki kural ve puanlama kısa listesi gösteriliyor.</div>
        {(run.shortlist ?? []).length === 0 && <p className="muted">Kısa listede ürün yok. "Kanıt" sekmesindeki uyarılara bakın.</p>}
      </section>
    )
  }
  const byId: Record<string, Scored> = { ...Object.fromEntries((run.shortlist ?? []).map((s) => [s.product.product_id, s])), ...(k.urun_ayrintilari ?? {}) }
  const rows = k.urun_eslemesi.filter((m) => m.agirlik > 0)
  const cats = [...new Set(rows.map((m) => m.kategori))]
  const pb = k.politika_belirsizligi
  const pctMap = Object.fromEntries(Object.entries(k.kategori_dagilimi).map(([c, v]) => [c, Math.round(v * 100)]))
  return (
    <section className="card">
      <h3 className="card-title">RL modelinin önerdiği dağılım</h3>
      <p className="muted small">Kategori ağırlıklarını RL kategori modeli (NN-PPO) seçer; her kategori yalnızca bu müşterinin kısıtlarından geçen ürünlere eşlenir (önce kısa listedekiler, sonra skora göre). Oranlar RM'in görüşmede değerlendireceği öneridir, müşteriye RM onayı olmadan gitmez.</p>
      <AllocBar d={pctMap} />
      {pb?.secilen_olasilik != null && (
        <div className={`banner ${pb.secilen_olasilik < 20 ? 'warnbox' : 'info'} small`}>
          {pb.secilen_olasilik < 20
            ? <>Model bu dağılımı yalnızca <strong>%{pb.secilen_olasilik.toString().replace('.', ',')}</strong> olasılıkla seçiyor; oranları kesin değil, yön göstergesi olarak okuyun.</>
            : <>Modelin bu dağılımı seçme olasılığı %{pb.secilen_olasilik.toString().replace('.', ',')}.</>}
          {pb.beklenen_dagilim && <div style={{ marginTop: 6 }}>Olasılık ağırlıklı ortalama dağılım:<AllocBar compact d={Object.fromEntries(Object.entries(pb.beklenen_dagilim).map(([c, v]) => [c, Math.round(v)]).filter(([, v]) => (v as number) > 0))} /></div>}
        </div>
      )}
      {cats.map((cat) => {
        const items = rows.filter((m) => m.kategori === cat)
        const alts = k.kategori_alternatifleri?.[cat] ?? []
        return (
          <div key={cat} className="rl-cat">
            <h4>{CAT_LABEL[cat] ?? cat} <span className="muted small">· kategori ağırlığı %{Math.round(items[0].kategori_agirligi * 100)}</span></h4>
            {items.map((m, i) => {
              const s = m.product_id ? byId[m.product_id] : undefined
              if (!s) return (
                <div key={i} className="product"><div className="product-head"><span className="rank weight">%{Math.round(m.agirlik * 100)}</span>
                  <div className="grow"><strong>{m.ad ?? 'Bu kategoride uygun ürün yok'}</strong>{m.takip_uyarisi && <div className="small muted">{m.takip_uyarisi}</div>}</div></div></div>
              )
              return <ProductCard key={m.product_id} s={s} weight={m.agirlik} note={m.takip_uyarisi} />
            })}
            {alts.length > 0 && <p className="muted small">Aynı kategoride uygun alternatifler: {alts.map((a) => `${a.ad} (${Math.round(a.skor * 100)})`).join(' · ')}</p>}
          </div>
        )
      })}
    </section>
  )
}

function DecisionPanel({ run, onUpdated }: { run: RunRecord; onUpdated: (r: RunRecord) => void }) {
  const [text, setText] = useState(run.draft_text ?? '')
  const [rm, setRm] = useState('')
  const [comment, setComment] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const decided = run.rm_decision
  const edited = text !== run.draft_text

  const decide = async (decision: string) => {
    setErr(null)
    if (!rm.trim()) return setErr('RM adı gerekli')
    try {
      onUpdated(await api.decide(run.run_id, {
        decision: decision === 'onay' && edited ? 'duzenleyerek_onay' : decision,
        rm_name: rm, comment: comment || undefined, edited_text: edited ? text : undefined,
      }))
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  return (
    <div>
      {run.guard && run.guard.violations.length > 0 && (
        <div className="banner error">Metin güvenlik kontrolü: {run.guard.violations.join('; ')}</div>
      )}
      <textarea className="draft" value={decided ? decided.edited_text || run.draft_text : text} readOnly={!!decided}
        onChange={(e) => setText(e.target.value)} rows={24} />
      <div className="muted small">Metin üreticisi: {run.llm}{edited && !decided ? ' · RM tarafından düzenlendi' : ''}</div>
      {decided ? (
        <div className={`banner ${decided.decision === 'red' ? 'error' : 'ok'}`}>
          {decided.decision === 'red' ? 'Reddedildi' : 'Onaylandı'}: {decided.rm_name} · {fmtTime(decided.decided_at)}
          {decided.comment ? ` · “${decided.comment}”` : ''}
        </div>
      ) : (
        <div className="decision">
          <input placeholder="RM adı" value={rm} onChange={(e) => setRm(e.target.value)} />
          <input className="grow" placeholder="Not / gerekçe (isteğe bağlı)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <button className="primary" onClick={() => decide('onay')}>{edited ? 'Düzenleyerek onayla' : 'Onayla'}</button>
          <button className="danger" onClick={() => decide('red')}>Reddet</button>
        </div>
      )}
      {err && <div className="banner error">{err}</div>}
    </div>
  )
}

export function ResultView({ run, onUpdated }: { run: RunRecord; onUpdated: (r: RunRecord) => void }) {
  const [tab, setTab] = useState<Tab>('Ajan ve not')
  const flags = run.flags ?? []
  const teyit = flags.filter((f) => f.level === 'teyit').length
  const rlCount = run.rl_kategori?.durum === 'tamam' ? run.rl_kategori.urun_eslemesi.filter((m) => m.product_id).length : undefined
  const counts: Partial<Record<Tab, number>> = { 'Öneriler': rlCount ?? run.shortlist?.length }

  return (
    <section className="result">
      {run.profile_view && (
        <div className="kpis">
          <div><span>Risk sınırı</span><strong>RD {run.profile_view.max_rd}</strong></div>
          <div><span>Kısa liste</span><strong>{run.shortlist?.length ?? 0}</strong></div>
          <div><span>Değerlendirilen ürün</span><strong>{run.universe_size}</strong></div>
          <div><span>Kurallarla dışlanan</span><strong>{run.excluded?.length ?? 0}</strong></div>
          <div className={teyit ? 'warn' : ''}><span>Teyit gereken</span><strong>{teyit}</strong></div>
          <div className="grow"><span>Uygulanan kısıtlar</span><strong className="small">{run.profile_view.rules.map((r) => r.kaynak_kisit || r.id).join(' · ') || '—'}</strong></div>
        </div>
      )}
      <nav className="tabs main-tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? 'active' : ''} onClick={() => setTab(t)}>
            {t}{counts[t] !== undefined ? ` (${counts[t]})` : ''}
          </button>
        ))}
      </nav>

      {tab === 'Ajan ve not' && (
        <div className="stack">
          <section className="card">
            <h3 className="card-title">Görüşme hazırlık notu</h3>
            <DecisionPanel key={run.run_id + (run.rm_decision?.decided_at ?? '') + (run.llm ?? '') + (run.draft_text?.length ?? 0)} run={run} onUpdated={onUpdated} />
          </section>
          <AgentPanel run={run} onUpdated={onUpdated} />
          <CustomerFacingPanel run={run} onUpdated={onUpdated} />
        </div>
      )}

      {tab === 'What-if' && <section className="card"><WhatIfPanel run={run} onUpdated={onUpdated} /></section>}

      {tab === 'Öneriler' && (
        <div className="stack">
          <RLRecommendations run={run} />
          <details className="card">
            <summary>Kural ve puanlama kısa listesi (karşılaştırma için, {run.shortlist?.length ?? 0} ürün)</summary>
            <p className="muted small">Uygun ürünlerin 8 kriterli puanlamayla sıralaması. RL dağılımı bu ürünleri öncelikli aday olarak kullanır; her ürünün skor dökümü burada.</p>
            {(run.shortlist ?? []).map((s) => <ProductCard key={s.product.product_id} s={s} />)}
            {(run.others ?? []).length > 0 && (
              <details>
                <summary className="small">Uygun ama kısa listeye girmeyen {run.others!.length} ürün</summary>
                <table>
                  <thead><tr><th>Sıra</th><th>Ürün</th><th>Skor</th><th>Neden listede değil</th></tr></thead>
                  <tbody>
                    {run.others!.slice(0, 50).map((o) => (
                      <tr key={o.product_id}><td>{o.rank}</td><td>{o.name}</td><td>{(o.score * 100).toFixed(0)}</td><td className="small">{o.reason}</td></tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </details>
        </div>
      )}

      {tab === 'Model (RL)' && (
        <div className="stack">
          <RLSummaryCard run={run} />
          <details className="card">
            <summary>Model ayrıntıları (kategori modeli, test sonuçları, göstergeler)</summary>
            <RLCategoryView r={run.rl_kategori} />
          </details>
          {run.rl && (
            <details className="card">
              <summary>Kısa liste üzerinde dinamik dağılım simülasyonu</summary>
              <RLView rl={run.rl} />
            </details>
          )}
        </div>
      )}

      {tab === 'Kanıt' && (
        <div className="stack">
          <section className="card">
            <h3 className="card-title">Uyarılar ve teyitler</h3>
            {flags.length === 0 && <p className="muted">Uyarı yok.</p>}
            <ul className="flags">
              {flags.map((f, i) => <li key={i} className={`flag-${f.level}`}><span className="badge">{f.level}</span> {f.message}</li>)}
            </ul>
            {!!run.hidden_flag_count && <p className="muted small">Kısa listede olmayan ürünlere ait {run.hidden_flag_count} uyarı gösterilmiyor.</p>}
          </section>
          <section className="card">
            <h3 className="card-title">Piyasa verileri</h3>
            <table>
              <thead><tr><th>Veri</th><th>Değer</th><th>Durum</th><th>Kaynak</th><th>Veri tarihi</th><th>Alınma zamanı</th></tr></thead>
              <tbody>
                {(run.market ?? []).map((m) => (
                  <tr key={m.key}>
                    <td>{m.key}</td><td>{m.value === null || m.value === undefined ? '—' : String(m.value)}</td>
                    <td><SourceChip dp={m} /></td>
                    <td className="small">{m.url ? <a href={m.url} target="_blank" rel="noreferrer">{m.source}</a> : m.source}</td>
                    <td>{m.as_of ?? '—'}</td>
                    <td className="small">{fmtTime(m.fetched_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <details className="card">
            <summary>Kurallarla dışlanan ürünler ({run.excluded?.length ?? 0})</summary>
            <table>
              <thead><tr><th>Ürün</th><th>Kategori</th><th>Kural</th><th>Gerekçe</th></tr></thead>
              <tbody>
                {(run.excluded ?? []).slice(0, 300).map((e) => (
                  <tr key={e.product_id}><td>{e.name}</td><td>{e.category}</td><td><code>{e.rule_id}</code></td><td className="small">{e.reason}</td></tr>
                ))}
              </tbody>
            </table>
          </details>
          {run.weights && (
            <details className="card">
              <summary>Puanlama ağırlıkları</summary>
              <table>
                <thead><tr><th>Kriter</th><th>Ağırlık</th><th>Nereden geldi</th></tr></thead>
                <tbody>
                  {Object.entries(run.weights).sort((a, b) => b[1].weight - a[1].weight).map(([k, w]) => (
                    <tr key={k}><td>{CRITERIA[k]}</td><td>{pct(w.weight, 0)}</td><td className="small">{w.explanation}</td></tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          <details className="card">
            <summary>Akış izi (deterministik adımlar, model, ajan, RM)</summary>
            <ol className="trace">
              {run.trace.map((t) => (
                <li key={t.step}>
                  <span className={`badge actor-${t.actor}`}>{t.actor}</span> <strong>{t.step}</strong>
                  <span className="muted small"> · {t.duration_ms} ms</span>
                  <div className="small">{t.summary}</div>
                </li>
              ))}
              <li>
                <span className="badge actor-rm">rm</span> <strong>rm_onayi</strong>
                <div className="small">{run.rm_decision ? `${run.rm_decision.decision} · ${run.rm_decision.rm_name}` : 'bekliyor'}</div>
              </li>
            </ol>
          </details>
          <details className="card">
            <summary>Ham kanıt kaydı (JSON) · {run.run_id} · {fmtTime(run.created_at)} · config {run.config_hash}</summary>
            <pre className="json">{JSON.stringify(run, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  )
}
