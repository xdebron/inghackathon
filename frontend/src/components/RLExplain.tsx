import { useState } from 'react'

/** Kategori RL modelinin inanç başlığı (beklenen getiri) ve EBM vekil açıklamaları. */

export const CAT_NAME: Record<string, string> = {
  mevduat_tl: 'Mevduat (3 ay)', para_piyasasi: 'Para piyasası', borclanma: 'Borçlanma araçları', katilim: 'Katılım (kira sert.)',
  kiymetli_maden: 'Kıymetli maden', karma_degisken: 'Karma / değişken', fon_sepeti: 'Fon sepeti', hisse: 'Hisse senedi',
  yabanci_hisse: 'Yabancı hisse', eurobond_doviz: 'Eurobond / döviz', bist30_hisse: 'BIST 30 hisse (doğrudan)',
}
const cn = (c: string) => CAT_NAME[c] ?? c

export type Belief = { kategori: string; ortalama: number; std: number; izinli: boolean }
type Contrib = { ad: string; deger: number; katki: number }
type Local = { vekil_tahmin: number; sabit: number; katkilar: Contrib[]; r2: number; dusuk_sadakat: boolean }
export type Explanation = {
  yontem: string
  sadakat: { en_buyuk_kategori_uyumu: number; ortalama_dagilim_farki: number; agirlik_r2_ortalama: number | null }
  kategoriler: Record<string, { agirlik_puan: number; agirlik?: Local; inanc?: Local }>
  not?: string
}
export type GlobalExplanation = {
  yontem: string
  sadakat: Explanation['sadakat']
  kategoriler: Record<string, { r2: number; onem: { ad: string; onem: number }[]; sekiller: Record<string, { kesimler: number[]; skorlar: number[] } | null> }>
}
export type Calibration = {
  ornek: number; not: string
  kategoriler: Record<string, { korelasyon: number | null; hata_rmse: number; saf_tahmin_rmse: number; yon_isabeti: number; kapsama_1sigma: number }>
}

const num = (x: number, d = 1) => x.toFixed(d).replace('.', ',')
const RATE = /getiri|oynakl|faiz|TÜFE|değişim|TLREF|BIST/i

/** Değişkenin bugünkü değeri: kısıtlar var/yok, oran göstergeleri yüzde, diğerleri sayı. */
function fmtVal(name: string, v: number) {
  if (name.startsWith('Kısıt:')) return v >= 0.5 ? 'var' : 'yok'
  if (name.startsWith('Profil:')) return Number.isInteger(v) ? String(v) : num(v, 2)
  if (RATE.test(name)) return `%${num(v * 100)}`
  return num(v, 2)
}

function Bars({ rows, unit }: { rows: Contrib[]; unit: string }) {
  const max = Math.max(1e-9, ...rows.map((r) => Math.abs(r.katki)))
  return (
    <table className="xbars"><tbody>
      {rows.map((r) => (
        <tr key={r.ad}>
          <td className="small">{r.ad} <span className="muted">({fmtVal(r.ad, r.deger)})</span></td>
          <td className="xbarcell">
            <div className={`xbar ${r.katki >= 0 ? 'pos' : 'neg'}`} style={{ width: `${(Math.abs(r.katki) / max) * 100}%` }} />
          </td>
          <td className={`small num ${r.katki >= 0 ? '' : 'warntext'}`}>{r.katki >= 0 ? '+' : ''}{num(r.katki)} {unit}</td>
        </tr>
      ))}
    </tbody></table>
  )
}

export function BeliefTable({ rows }: { rows?: Belief[] | null }) {
  if (!rows) return null
  const max = Math.max(1e-9, ...rows.map((r) => Math.abs(r.ortalama) + r.std))
  return (
    <>
      <h3>Modelin piyasa beklentisi (inanç başlığı)</h3>
      <p className="muted small">Sonraki 3 ay için kategori başına vergi sonrası reel getiri beklentisi ve belirsizliği (±1σ). Ağın kararla birlikte
        öğrendiği iç temsildir; getiri tahmini ya da vaadi değildir. Gri satırlar bu profil için kapalı kategoriler.</p>
      <table className="belief"><tbody>
        {rows.map((r) => {
          const c = 50 + (r.ortalama / max) * 50, w = (r.std / max) * 50
          return (
            <tr key={r.kategori} className={r.izinli ? '' : 'muted'}>
              <td className="small">{cn(r.kategori)}</td>
              <td className="beliefcell">
                <div className="beliefaxis" />
                <div className="beliefband" style={{ left: `${Math.max(0, c - w)}%`, width: `${Math.min(100, 2 * w)}%` }} />
                <div className={`beliefdot ${r.ortalama >= 0 ? 'pos' : 'neg'}`} style={{ left: `${c}%` }} />
              </td>
              <td className="small num">%{num(r.ortalama)} ± {num(r.std)}</td>
            </tr>
          )
        })}
      </tbody></table>
    </>
  )
}

export function DecisionExplanation({ a }: { a?: Explanation | null }) {
  if (!a) return <p className="muted small">Bu model için açıklama (EBM vekili) üretilmemiş: <code>tools/explain_rl.py</code>.</p>
  return (
    <>
      <h3>Neden bu dağılım? (EBM vekil modeli)</h3>
      <p className="muted small">{a.yontem} Sadakat (ayrılmış durumlarda): en büyük kategori uyumu %{num(a.sadakat.en_buyuk_kategori_uyumu * 100, 0)},
        ortalama R² {a.sadakat.agirlik_r2_ortalama != null ? num(a.sadakat.agirlik_r2_ortalama, 2) : '—'}. Katkılar puan cinsinden; etkileşim
        terimlerinin katkısı iki değişkene yarı yarıya paylaştırıldı.</p>
      {a.not && <div className="banner warnbox small">{a.not}</div>}
      {Object.entries(a.kategoriler).map(([cat, row]) => (
        <div key={cat} className="xcat">
          <div className="row wrap">
            <strong>{cn(cat)} · %{num(row.agirlik_puan, 0)}</strong>
            {row.agirlik && <span className="muted small">vekil tahmini %{num(row.agirlik.vekil_tahmin)} (taban %{num(row.agirlik.sabit)}), R² {num(row.agirlik.r2, 2)}</span>}
            {row.agirlik?.dusuk_sadakat && <span className="small warntext">düşük sadakat: açıklama yaklaşık</span>}
          </div>
          {row.agirlik ? <Bars rows={row.agirlik.katkilar} unit="puan" /> : <p className="muted small">Bu kategori için vekil yok (model ağırlığı sabit).</p>}
          {row.inanc && (
            <details className="small"><summary>Beklenti (%{num(row.inanc.vekil_tahmin)}) hangi göstergelerden geliyor?</summary>
              <Bars rows={row.inanc.katkilar} unit="puan" />
            </details>
          )}
        </div>
      ))}
    </>
  )
}

function Shape({ s }: { s: { kesimler: number[]; skorlar: number[] } }) {
  const W = 260, H = 70
  const xs = s.kesimler, ys = s.skorlar.map((y) => y * 100)
  if (!xs.length) return null
  const lo = xs[0] - (xs[xs.length - 1] - xs[0]) * 0.05 || xs[0] - 1, hi = xs[xs.length - 1] + (xs[xs.length - 1] - xs[0]) * 0.05 || xs[0] + 1
  const ymin = Math.min(0, ...ys), ymax = Math.max(0, ...ys), sy = (y: number) => H - 4 - ((y - ymin) / (ymax - ymin || 1)) * (H - 8)
  const sx = (x: number) => ((x - lo) / (hi - lo || 1)) * W
  const edges = [lo, ...xs, hi]
  let d = ''
  ys.forEach((y, i) => { d += `${i ? 'L' : 'M'}${sx(edges[i])},${sy(y)} L${sx(edges[i + 1])},${sy(y)} ` })
  return (
    <svg width={W} height={H} className="shape">
      <line x1={0} x2={W} y1={sy(0)} y2={sy(0)} className="axis" />
      <path d={d} className="line" />
      <text x={2} y={10} className="small">{num(ymax)} puan</text>
      <text x={2} y={H - 2} className="small">{num(ymin)}</text>
    </svg>
  )
}

export function GlobalExplanationView({ g }: { g?: GlobalExplanation | null }) {
  const cats = g ? Object.keys(g.kategoriler) : []
  const [cat, setCat] = useState<string>('')
  const [feat, setFeat] = useState<string>('')
  if (!g || !cats.length) return null
  const c = g.kategoriler[cat] ?? g.kategoriler[cats[0]]
  const f = c.sekiller[feat] ? feat : c.onem[0]?.ad
  const max = Math.max(1e-9, ...c.onem.map((o) => o.onem))
  return (
    <section className="card">
      <h2>Politikanın genel mantığı (EBM vekili)</h2>
      <p className="muted small">{g.yontem} En büyük kategori uyumu %{num(g.sadakat.en_buyuk_kategori_uyumu * 100, 0)}. Önem: değişkenin
        kategori ağırlığına ortalama mutlak katkısı (puan). Bir değişkene tıklayın: şekil fonksiyonu, değişkenin değeri arttıkça
        ağırlığa katkısının nasıl değiştiğini gösterir.</p>
      <div className="tabs">
        {cats.map((k) => <button key={k} className={k === (cat || cats[0]) ? 'active' : ''} onClick={() => { setCat(k); setFeat('') }}>{cn(k)}</button>)}
      </div>
      <p className="small muted">Vekil R² {num(c.r2, 2)}{c.r2 < 0.5 ? ' — düşük sadakat, yaklaşık' : ''}</p>
      <div className="row wrap" style={{ alignItems: 'flex-start' }}>
        <table className="xbars"><tbody>
          {c.onem.map((o) => (
            <tr key={o.ad} className={o.ad === f ? 'sel' : ''} onClick={() => setFeat(o.ad)} style={{ cursor: c.sekiller[o.ad] ? 'pointer' : 'default' }}>
              <td className="small">{o.ad}</td>
              <td className="xbarcell"><div className="xbar pos" style={{ width: `${(o.onem / max) * 100}%` }} /></td>
              <td className="small num">{num(o.onem, 2)}</td>
            </tr>
          ))}
        </tbody></table>
        {f && c.sekiller[f] && <div><div className="small">{f}</div><Shape s={c.sekiller[f]!} /></div>}
      </div>
    </section>
  )
}

export function CalibrationTable({ k }: { k?: { dogrulama?: Calibration; test?: Calibration } | null }) {
  const v = k?.dogrulama
  if (!v || !v.kategoriler) return null
  return (
    <section className="card">
      <h2>İnanç başlığı kalibrasyonu (doğrulama dönemi)</h2>
      <p className="muted small">{v.ornek} başlangıç tarihi. {v.not} Kapsama: gerçekleşen getirinin ±1σ aralığına düşme oranı (iyi kalibrasyonda ~%68).</p>
      <table><thead><tr><th>Kategori</th><th>Korelasyon</th><th>Hata (RMSE)</th><th>Saf tahmin hatası</th><th>Yön isabeti</th><th>±1σ kapsama</th></tr></thead>
        <tbody>{Object.entries(v.kategoriler).map(([c, r]) => (
          <tr key={c}><td>{cn(c)}</td><td className="num">{r.korelasyon == null ? '—' : num(r.korelasyon, 2)}</td>
            <td className={`num ${r.hata_rmse > r.saf_tahmin_rmse ? 'warntext' : ''}`}>%{num(r.hata_rmse * 100)}</td>
            <td className="num">%{num(r.saf_tahmin_rmse * 100)}</td><td className="num">%{num(r.yon_isabeti * 100, 0)}</td>
            <td className="num">%{num(r.kapsama_1sigma * 100, 0)}</td></tr>
        ))}</tbody></table>
    </section>
  )
}

export type Gate = {
  kaynak: 'model' | 'taban_kural'; model_tercihi: Record<string, number>; taban: Record<string, number>
  q_farki: number; esik: number; gerekce: string
  dogrulama?: { dogrulama_odul: number; dqn_odul: number; taban_odul: number; model_karar_orani: number }
}

const alloc = (w: Record<string, number>) => Object.entries(w).map(([k, v]) => `${cn(k)} %${num(v * 100, 0)}`).join(' · ')

/** Güvenli iyileştirme kapısı: model önerisi taban kuraldan güvenilir ölçüde iyi değilse taban kural uygulanır. */
export function GateBox({ g }: { g?: Gate | null }) {
  if (!g) return null
  const base = g.kaynak === 'taban_kural'
  return (
    <div className={`banner ${base ? 'warnbox' : ''} small`}>
      <strong>Güvenli iyileştirme: {base ? 'taban kural uygulandı' : 'model önerisi kullanıldı'}.</strong> {g.gerekce}
      <div>Model tercihi: {alloc(g.model_tercihi)}</div>
      <div>Taban kural: {alloc(g.taban)}</div>
      {g.dogrulama && <div className="muted">Doğrulamada (görülmemiş profiller): model {num(g.dogrulama.dqn_odul, 3)}, taban {num(g.dogrulama.taban_odul, 3)},
        kapılı {num(g.dogrulama.dogrulama_odul, 3)} ödül; kararların %{num(g.dogrulama.model_karar_orani * 100, 0)}'i modelden.</div>}
    </div>
  )
}
