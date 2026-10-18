import { useEffect, useMemo, useState } from 'react'
import type { RunRecord } from '../types'

// RL kategori modelinin RM için özeti. Tüm içerik /api/rl-araclar uçlarından gelir; bu uçlar açıklamayı vekil bir modelden
// değil, gerçek ağı sorgulayarak üretir (karşı-olgusal hesaplar, tema düzeyinde kesin Shapley).

const CAT: Record<string, string> = {
  mevduat_tl: 'TL mevduat', para_piyasasi: 'Para piyasası', borclanma: 'Borçlanma araçları', katilim: 'Katılım',
  kiymetli_maden: 'Kıymetli maden', karma_degisken: 'Karma / değişken', fon_sepeti: 'Fon sepeti', hisse: 'Hisse fonu',
  yabanci_hisse: 'Yabancı hisse', eurobond_doviz: 'Eurobond / döviz', bist30_hisse: 'BIST 30 hisseleri',
}

interface Theme {
  tema: string
  etki_puan: number
  gostergeler: { ad: string; bugun: number; egitim_ort: number; z: number }[]
  kategori_katkilari: { kategori: string; ad: string; puan: number }[]
}

interface Summary {
  model: string
  metin: Record<string, string>
  araclar: {
    rl_karar: { kategori_dagilimi: Record<string, number>; alternatifler: { sira: number; dagilim: Record<string, number>; fark: number; olasilik?: number }[]; karar_tarihi: string
      secilen_olasilik?: number | null; beklenen_dagilim?: Record<string, number> | null }
    rl_neden: { temalar: Theme[]; ortalama_piyasada_karar: Record<string, number>; okuma_notu: string }
    rl_guvenilirlik: { uyarilar: string[] }
  }
}

interface WhatIf {
  onceki_dagilim: Record<string, number>
  yeni_dagilim: Record<string, number>
  karar_degisti: boolean
  uyarilar: string[]
  uygulanan_degisiklikler: string[]
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`/api/rl-araclar${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : r.statusText)
  return j as T
}

function Dist({ d }: { d: Record<string, number> }) {
  return (
    <div className="rls-dist">
      {Object.entries(d).sort((a, b) => b[1] - a[1]).map(([c, v]) => (
        <div key={c} className="rls-seg" style={{ flexGrow: v }} title={`${CAT[c] ?? c} %${v}`}>
          <span>{CAT[c] ?? c} %{v}</span>
        </div>
      ))}
    </div>
  )
}

export function RLSummaryCard({ run }: { run: RunRecord }) {
  const [model, setModel] = useState('canli')
  const [data, setData] = useState<Summary | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [ind, setInd] = useState('')
  const [sigma, setSigma] = useState(1)
  const [wi, setWi] = useState<WhatIf | null>(null)
  const base = useMemo(() => ({ persona: run.persona, rm_answers: run.rm_answers ?? {}, mevcut_dagilim: run.rl_kategori?.mevcut_dagilim, model }),
    [run.run_id, model])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setData(null); setErr(null); setWi(null)
    post<Summary>('/ozet', base).then(setData).catch((e) => setErr(e.message))
  }, [base])

  const indicators = data?.araclar.rl_neden.temalar.flatMap((t) => t.gostergeler.map((g) => g.ad)) ?? []

  const runWhatIf = async () => {
    if (!ind) return
    try {
      setWi(await post<WhatIf>('/cagir', { ad: 'rl_ne_olursa', argumanlar: { ...base, gostergeler: [{ ad: ind, sigma }] } }))
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const header = (
    <div className="card-head">
      <div>
        <h3 style={{ margin: 0 }}>RL modeli ne diyor? (RM özeti)</h3>
        <div className="muted small">Açıklamalar gerçek modeli sorgulayarak hesaplanır; vekil model kullanılmaz.</div>
      </div>
      <label className="row small">Model
        <select value={model} onChange={(e) => setModel(e.target.value)} style={{ width: 'auto' }}>
          <option value="canli">Canlı model (PPO v14 · tüm veri 6M)</option><option value="ppo_v11_tum100m">PPO v11 · tüm veri 100M (önceki canlı)</option><option value="ppo_v11_100m">PPO v11 · 100M (2026 görülmemiş)</option><option value="ppo_v9">PPO v9</option><option value="v4">v4 (DQN)</option>
        </select>
      </label>
    </div>
  )

  if (err && !data) return <section className="rls card">{header}<div className="banner error">RL özeti alınamadı: {err}</div></section>
  if (!data) return <section className="rls card">{header}<p className="muted">Model sorgulanıyor…</p></section>

  const k = data.araclar.rl_karar
  const n = data.araclar.rl_neden
  const maxEff = Math.max(...n.temalar.map((t) => t.etki_puan), 1)

  return (
    <section className="rls card">
      {header}

      <div className="rls-block">
        <h4>1. Karar</h4>
        <Dist d={k.kategori_dagilimi} />
        <p>{data.metin.karar}</p>
        {k.beklenen_dagilim && k.secilen_olasilik != null && (
          <div className={`banner ${k.secilen_olasilik < 20 ? 'warnbox' : 'info'} small`}>
            <div>
              {k.secilen_olasilik < 20
                ? <>Model bu dağılımı yalnızca <strong>%{k.secilen_olasilik.toString().replace('.', ',')}</strong> olasılıkla seçiyor; birçok dağılım arasında kararsız. Tek dağılımı kesin öneri değil, yön göstergesi olarak okuyun.</>
                : <>Modelin bu dağılımı seçme olasılığı %{k.secilen_olasilik.toString().replace('.', ',')}.</>}
            </div>
            <div style={{ marginTop: 6 }}>Olasılık ağırlıklı dağılım:</div>
            <Dist d={Object.fromEntries(Object.entries(k.beklenen_dagilim).map(([c, v]) => [c, Math.round(v)]).filter(([, v]) => (v as number) > 0))} />
          </div>
        )}
        <details>
          <summary className="small">En iyi alternatifler</summary>
          <table>
            <thead><tr><th>#</th><th>Dağılım</th><th>{k.alternatifler[0]?.olasilik != null ? 'Olasılık' : 'Değer farkı'}</th></tr></thead>
            <tbody>
              {k.alternatifler.map((a) => (
                <tr key={a.sira}><td>{a.sira}</td><td><Dist d={a.dagilim} /></td><td>{a.olasilik != null ? `%${a.olasilik.toString().replace('.', ',')}` : a.fark.toFixed(3)}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Olasılık: politikanın o dağılımı seçme olasılığı. Değer farkı (DQN): modelin bu alternatifi seçilen karardan ne kadar düşük değerlendirdiği (göreli birim).</p>
        </details>
      </div>

      <div className="rls-block">
        <h4>2. Neden bu dağılım?</h4>
        <p>{data.metin.neden}</p>
        <div className="rls-themes">
          {n.temalar.map((t) => (
            <div key={t.tema}>
              <button className="ghost rls-theme" onClick={() => setOpen(open === t.tema ? null : t.tema)}>
                <span className="rls-tname">{t.tema}</span>
                <span className="bar"><span style={{ width: `${(t.etki_puan / maxEff) * 100}%` }} /></span>
                <span className="small">{t.etki_puan.toFixed(1).replace('.', ',')} puan</span>
              </button>
              {open === t.tema && (
                <div className="rls-detail small">
                  <div>{t.kategori_katkilari.map((c) => `${c.ad} ${c.puan > 0 ? '+' : '−'}${Math.abs(c.puan).toFixed(1).replace('.', ',')}`).join(' · ') || 'Katkı yok'}</div>
                  <div className="muted">{t.gostergeler.map((g) => `${g.ad}: ${g.z > 0 ? '+' : ''}${g.z.toFixed(1).replace('.', ',')}σ`).join(' · ')}</div>
                </div>
              )}
            </div>
          ))}
        </div>
        <p className="muted small">{n.okuma_notu}</p>
      </div>

      <div className="rls-block">
        <h4>3. Ne değişirse karar değişir?</h4>
        <p>{data.metin.esikler}</p>
        <div className="row wrap small">
          <select value={ind} onChange={(e) => setInd(e.target.value)} style={{ maxWidth: 320 }}>
            <option value="">Gösterge seçin…</option>
            {indicators.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
          <label className="row">değişim
            <input type="range" min={-2} max={2} step={0.5} value={sigma} onChange={(e) => setSigma(Number(e.target.value))} style={{ width: 140 }} />
            <span>{sigma > 0 ? '+' : ''}{sigma}σ</span>
          </label>
          <button onClick={runWhatIf} disabled={!ind}>Ne olur?</button>
        </div>
        {wi && (
          <div className={`banner ${wi.karar_degisti ? 'warnbox' : 'ok'}`}>
            <div className="small">{wi.uygulanan_degisiklikler.join('; ')}</div>
            {wi.karar_degisti ? <><div>Karar değişiyor:</div><Dist d={wi.yeni_dagilim} /></> : <div>Karar değişmiyor.</div>}
            {wi.uyarilar.map((u) => <div key={u} className="small muted">{u}</div>)}
          </div>
        )}
      </div>

      <div className="rls-block">
        <h4>4. Ne kadar güvenilir?</h4>
        {data.araclar.rl_guvenilirlik.uyarilar.length
          ? <ul className="flags">{data.araclar.rl_guvenilirlik.uyarilar.map((u) => <li key={u} className="flag-teyit"><span className="badge">dikkat</span> {u}</li>)}</ul>
          : <p>Belirgin bir güvenilirlik uyarısı yok.</p>}
        {data.metin.benzer && <p className="small">{data.metin.benzer}</p>}
      </div>

      <div className="rls-block muted small">
        <h4>Bu nedir, ne değildir?</h4>
        <p>{data.metin.ne_degildir}</p>
      </div>
    </section>
  )
}
