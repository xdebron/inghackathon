import { useEffect, useState } from 'react'
import { ModelView } from './components/ModelView'
import { CustomerPortal } from './pages/CustomerPortal'
import { DataPage } from './pages/DataPage'
import { EvaluationPage } from './pages/EvaluationPage'
import { RMWorkspace } from './pages/RMWorkspace'
import './App.css'
import './layout.css'

// İki ajan yüzü: RM çalışma alanı (çalışan) ve müşteri portalı (yalnızca RM onaylı içerik). Model ve veri sayfaları yardımcıdır.
const PAGES = [
  { id: 'rm', label: 'RM çalışma alanı' },
  { id: 'musteri', label: 'Müşteri portalı' },
  { id: 'model', label: 'Model' },
  { id: 'veri', label: 'Veri' },
  { id: 'degerlendirme', label: 'Değerlendirme' },
] as const
type PageId = (typeof PAGES)[number]['id']

const fromHash = (): PageId => {
  const h = window.location.hash.replace('#/', '') as PageId
  return PAGES.some((p) => p.id === h) ? h : 'rm'
}

export default function App() {
  const [page, setPage] = useState<PageId>(fromHash)
  const [llm, setLlm] = useState<{ erisilebilir: boolean; model?: string } | null>(null)

  useEffect(() => {
    const on = () => setPage(fromHash())
    window.addEventListener('hashchange', on)
    fetch('/api/ajan/durum').then((r) => r.json()).then(setLlm).catch(() => setLlm(null))
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const go = (id: PageId) => { window.location.hash = `/${id}`; setPage(id) }
  const customer = page === 'musteri'

  return (
    <div className={`app ${customer ? 'customer-mode' : ''}`}>
      <header className="topbar">
        <div className="brand-mark">
          <span className="logo">◆</span>
          <div>
            <strong>{customer ? 'Yatırım Danışmanlığı' : 'RM Hazırlık Ajanı'}</strong>
            <span>{customer ? 'Müşteri portalı' : 'Private Banking · karar destek'}</span>
          </div>
        </div>
        <nav className="topnav">
          {PAGES.map((p) => (
            <button key={p.id} className={page === p.id ? 'active' : ''} onClick={() => go(p.id)}>{p.label}</button>
          ))}
        </nav>
        {!customer && (
          <div className={`llm-status ${llm?.erisilebilir ? 'on' : 'off'}`} title="Lokal LLM sunucusu (vLLM)">
            <span className="dot" />{llm?.erisilebilir ? llm.model : 'LLM kapalı'}
          </div>
        )}
      </header>
      <main className="app-main">
        {page === 'rm' && <RMWorkspace />}
        {page === 'musteri' && <CustomerPortal />}
        {page === 'model' && <div className="page"><ModelView /></div>}
        {page === 'veri' && <DataPage />}
        {page === 'degerlendirme' && <EvaluationPage />}
      </main>
    </div>
  )
}
