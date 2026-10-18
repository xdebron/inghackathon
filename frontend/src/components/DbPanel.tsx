import { useEffect, useRef, useState } from 'react'

// Veri tabanı ve otomatik toplayıcılar: her kaynağın son çekimi (zaman, durum, satır, hata) ve elle tetikleme.
interface Run { id: number; kaynak: string; yontem: string; baslangic: string; bitis: string | null; durum: string; satir: number; mesaj: string | null }
interface Status {
  tablolar: Record<string, number>
  toplayicilar: { ad: string; aciklama: string; son: Run | null }[]
  yuklemeler: Record<string, Run | null>
  toplama: { calisiyor: boolean; adlar: string[]; baslangic: string | null; bitis: string | null; log: string[] }
}

const TABLE_LABEL: Record<string, string> = {
  fon_fiyat: 'Fon fiyatı (gün)', seri_deger: 'Seri değeri', fon_bilgi: 'KAP fon bilgisi', fon_getiri: 'Fon getirisi',
  platform_fon: 'ING platform fonu', mevduat: 'Mevduat oranı', stopaj: 'Stopaj', stopaj_donem: 'Stopaj dönemi', gosterge: 'Gösterge',
  seri: 'Zaman serisi', benchmark: 'Benchmark', belge: 'Kaynak belgesi', ham_tablo: 'Ham tablo satırı', cekim: 'Çekim kaydı', musteri: 'Eklenen müşteri',
}
const fmt = (s?: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')
const STATUS: Record<string, string> = { basarili: 'başarılı', kismi: 'kısmi', hata: 'hata', calisiyor: 'çalışıyor' }

export function DbPanel() {
  const [st, setSt] = useState<Status | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const load = () => fetch('/api/veri/durum').then((r) => r.json()).then((s: Status) => {
    setSt(s)
    if (s.toplama.calisiyor && !timer.current) timer.current = window.setInterval(load, 3000)
    if (!s.toplama.calisiyor && timer.current) { window.clearInterval(timer.current); timer.current = null }
  }).catch((e) => setErr(String(e)))
  useEffect(() => { load(); return () => { if (timer.current) window.clearInterval(timer.current) } }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const collect = async (adlar: string[]) => {
    setErr(null)
    const r = await fetch('/api/veri/topla', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ adlar }) })
    if (!r.ok) setErr((await r.json()).detail ?? r.statusText)
    load()
  }

  if (!st) return <section className="card wide"><p className="muted">Veri tabanı durumu yükleniyor…</p></section>
  const busy = st.toplama.calisiyor
  return (
    <section className="card wide db-panel">
      <div className="card-head">
        <div>
          <h3 className="card-title" style={{ margin: 0 }}>Veri tabanı ve otomatik toplayıcılar</h3>
          <p className="muted small">Tüm piyasa ve ürün verisi veri tabanındadır; her satır kaynağını, veri tarihini, alınma zamanını ve onu yazan çekimi taşır. Uygulama TEFAS'a istek atmaz; TEFAS fiyatları dışa aktarım yüklemesiyle gelir.</p>
        </div>
        <button className="primary" disabled={busy} onClick={() => collect([])}>{busy ? 'Toplanıyor…' : 'Hepsini topla'}</button>
      </div>
      {err && <div className="banner error small">{err}</div>}
      <table className="small">
        <thead><tr><th>Toplayıcı</th><th>Son çekim</th><th>Durum</th><th>Satır</th><th>Not</th><th /></tr></thead>
        <tbody>
          {st.toplayicilar.map((c) => (
            <tr key={c.ad}>
              <td><strong>{c.ad}</strong><div className="muted">{c.aciklama}</div></td>
              <td>{fmt(c.son?.bitis ?? c.son?.baslangic)}</td>
              <td>{c.son ? <span className={`badge ${c.son.durum === 'basarili' ? 'ok' : c.son.durum === 'hata' ? 'warn' : 'info'}`}>{STATUS[c.son.durum] ?? c.son.durum}</span> : <span className="muted">hiç çalışmadı</span>}</td>
              <td>{c.son?.satir?.toLocaleString('tr-TR') ?? '—'}</td>
              <td className="muted">{c.son?.mesaj ?? ''}</td>
              <td><button className="ghost small" disabled={busy} onClick={() => collect([c.ad])}>Topla</button></td>
            </tr>
          ))}
          {Object.entries(st.yuklemeler).map(([k, r]) => (
            <tr key={k}>
              <td><strong>{k}</strong><div className="muted">TEFAS dışa aktarım yüklemesi (yukarıdaki yükleme alanı)</div></td>
              <td>{fmt(r?.bitis ?? r?.baslangic)}</td>
              <td>{r ? <span className={`badge ${r.durum === 'basarili' ? 'ok' : 'warn'}`}>{STATUS[r.durum] ?? r.durum}</span> : <span className="muted">aktarımdan</span>}</td>
              <td>{r?.satir?.toLocaleString('tr-TR') ?? '—'}</td>
              <td className="muted">{r?.mesaj ?? ''}</td>
              <td />
            </tr>
          ))}
        </tbody>
      </table>
      {(busy || st.toplama.log.length > 0) && (
        <details open={busy}>
          <summary className="small">Toplama günlüğü {busy ? '(sürüyor)' : `(${fmt(st.toplama.bitis)})`}</summary>
          <pre className="json">{st.toplama.log.join('\n')}</pre>
        </details>
      )}
      <div className="db-counts">
        {Object.entries(st.tablolar).sort((a, b) => b[1] - a[1]).map(([t, n]) => (
          <div key={t} className="stat"><span>{TABLE_LABEL[t] ?? t}</span><strong>{n.toLocaleString('tr-TR')}</strong></div>
        ))}
      </div>
    </section>
  )
}
