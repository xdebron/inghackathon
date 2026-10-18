import { useEffect, useRef } from 'react'
import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip } from 'chart.js'
import { pct } from '../format'
import { CAT_LABEL } from './AllocBar'

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip)

export interface ShapRow { gosterge: string; tema: string; bugun: number; egitim_ort: number; z: number; katki_puan: number; hata_puan: number }
export interface ShapCat { taban_pay: number; bugun_pay: number; katkilar: ShapRow[] }

const TOP = 10

const label = (ad: string) => {
  const [code, ...rest] = ad.split(' ')
  return CAT_LABEL[code] ? `${CAT_LABEL[code]} ${rest.join(' ')}` : ad
}
const val = (x: number) => (Math.abs(x) < 5 ? (x < 0 ? '−' : '') + pct(Math.abs(x), 1) : x.toLocaleString('tr-TR', { maximumFractionDigits: 2 }))
const pp = (x: number) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(x).toFixed(1).replace('.', ',')}`

// Gösterge düzeyinde SHAP: her göstergenin, modelin bu kategoriye verdiği olasılık ağırlıklı paya katkısı (puan).
// Taban = göstergeler eğitim ortalamasında; çubukların toplamı + taban = bugünkü pay.
export function ShapChart({ cat, d }: { cat: string; d: ShapCat }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const top = d.katkilar.slice(0, TOP)
  const rest = d.katkilar.slice(TOP)
  const restSum = rest.reduce((s, r) => s + r.katki_puan, 0)

  useEffect(() => {
    if (!ref.current) return
    const rows = [...top.map((r) => ({ l: `${label(r.gosterge)} = ${val(r.bugun)}`, v: r.katki_puan, r })),
      ...(rest.length ? [{ l: `Diğer ${rest.length} gösterge`, v: restSum, r: null }] : [])]
    const css = getComputedStyle(document.documentElement)
    const ink = css.getPropertyValue('--muted').trim() || '#6b7385'
    const chart = new Chart(ref.current, {
      type: 'bar',
      data: {
        labels: rows.map((x) => x.l),
        datasets: [{ data: rows.map((x) => x.v), backgroundColor: rows.map((x) => (x.v >= 0 ? '#ff6200' : '#2f6fdf')), borderRadius: 3 }],
      },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (c) => {
                const x = rows[c.dataIndex]
                if (!x.r) return `${pp(x.v)} puan (toplam)`
                return [`${pp(x.v)} puan (±${x.r.hata_puan.toFixed(1).replace('.', ',')} örnekleme hatası)`,
                  `bugün ${val(x.r.bugun)} · eğitim ort. ${val(x.r.egitim_ort)} · z ${x.r.z.toFixed(2).replace('.', ',')}`, x.r.tema]
              },
            },
          },
        },
        scales: {
          x: { title: { display: true, text: 'Kategori payına katkı (puan)', color: ink }, ticks: { color: ink }, grid: { color: '#e6e8ee' } },
          y: { ticks: { color: ink, font: { size: 11 } }, grid: { display: false } },
        },
      },
    })
    return () => chart.destroy()
  }, [d]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="shap">
      <p className="small">
        Model ortalama piyasa koşullarında {CAT_LABEL[cat] ?? cat} kategorisine <strong>%{d.taban_pay.toString().replace('.', ',')}</strong>,
        bugünkü göstergelerle <strong>%{d.bugun_pay.toString().replace('.', ',')}</strong> olasılık ağırlıklı pay veriyor.
        Çubuklar farkı göstergelere paylaştırır: <span className="shap-pos">turuncu</span> payı artırır, <span className="shap-neg">mavi</span> azaltır.
      </p>
      <div style={{ height: 28 * (top.length + (rest.length ? 1 : 0)) + 50 }}><canvas ref={ref} /></div>
    </div>
  )
}
