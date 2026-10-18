import { useEffect, useState } from 'react'
import { api } from '../api'
import { INDICATOR_LABEL } from '../format'
import type { DataStatusInfo } from '../types'

// Veri kaynakları ve TEFAS dışa aktarım yüklemesi (eskiden kenar çubuğundaydı).
export function DataPage() {
  const [status, setStatus] = useState<DataStatusInfo | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10))
  const refresh = () => api.dataStatus().then(setStatus).catch(() => setStatus(null))
  useEffect(() => { refresh() }, [])

  const upload = async (file: File) => {
    setMsg('Yükleniyor…')
    try {
      const r = await api.uploadTefas(file, asOf)
      const kind = r.tur === 'karsilastirma' ? 'Fon Karşılaştırma' : 'Tarihsel Veriler'
      setMsg(`${kind}: ${r.satir} satır, ${r.fon_sayisi} fon içeri alındı${r.celiskiler.length ? `, ${r.celiskiler.length} fiyat çelişkisi` : ''}.`)
      refresh()
    } catch (e) { setMsg(`Hata: ${(e as Error).message}`) }
  }

  if (!status) return <div className="page"><p className="muted">Veri durumu yükleniyor…</p></div>
  const last = status.tefas.imports[status.tefas.imports.length - 1]
  return (
    <div className="page">
      <h2>Veri kaynakları</h2>
      <div className="data-grid">
        <section className="card">
          <h3 className="card-title">TEFAS</h3>
          <div className="stat"><span>Fiyat geçmişi</span><strong>{status.tefas.fon_sayisi} fon</strong></div>
          {last && <div className="muted small">{last.dosya} · {last.tarih_araligi.join(' → ')}</div>}
          <div className="stat"><span>Karşılaştırma</span><strong>{status.tefas.karsilastirma_fon_sayisi} fon</strong></div>
          <label className="upload">
            TEFAS dışa aktarımı yükle (.xlsx / .csv)
            <span className="muted small">Tarihsel Veriler veya Fon Getirileri; tür otomatik tanınır.</span>
            <span className="row small">Karşılaştırma dönem sonu <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></span>
            <input type="file" accept=".xlsx,.xls,.csv" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </label>
          {msg && <div className="muted small">{msg}</div>}
        </section>
        <section className="card">
          <h3 className="card-title">Manuel ve KAP verileri</h3>
          <div className="stat"><span>Mevduat / katılım</span><strong>{status.manuel.mevduat}</strong></div>
          <div className="stat"><span>Stopaj kategorisi</span><strong>{status.manuel.stopaj}</strong></div>
          <div className="stat"><span>KAP fon bilgisi (elle)</span><strong>{status.manuel.fon_ek_bilgi}</strong></div>
          <div className="stat"><span>Benchmark</span><strong>{status.manuel.benchmark}</strong></div>
          <div className="stat"><span>EVDS anahtarı</span><strong>{status.evds_anahtari ? 'tanımlı' : 'yok'}</strong></div>
        </section>
        <section className="card wide">
          <h3 className="card-title">Göstergeler</h3>
          <div className="indicators">
            {Object.entries(status.gostergeler ?? {}).map(([k, g]) => (
              <span key={k} className={`chip st-${g.status}`} title={`${g.source}${g.as_of ? ' · ' + g.as_of : ''}${g.note ? '\n' + g.note : ''}`}>
                {INDICATOR_LABEL[k] ?? k}{g.as_of ? ` · ${g.as_of}` : ''}
              </span>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
