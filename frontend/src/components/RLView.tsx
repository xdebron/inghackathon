import { pct } from '../format'
import type { RLResult } from '../types'

const METRICS: [keyof RLResult['test'][string], string][] = [
  ['ortalama_reel_kazanc', 'Ort. reel kazanç'],
  ['medyan_reel_kazanc', 'Medyan'],
  ['kotu_yuzde5', 'En kötü %5'],
  ['anapara_alti_bitis_olasiligi', 'Bitişte anapara altı'],
  ['herhangi_an_anapara_alti_olasiligi', 'Herhangi bir an anapara altı'],
  ['ortalama_devir', 'Ort. devir / adım'],
]

function Weights({ w, names }: { w: Record<string, number>; names: Record<string, string> }) {
  return (
    <div className="weights">
      {Object.entries(w).map(([k, v]) => (
        <div key={k} className="wrow">
          <span className="wname" title={names[k] ?? k}>{names[k] ?? k}</span>
          <div className="bar wide"><div style={{ width: `${v * 100}%` }} /></div>
          <span>{pct(v, 0)}</span>
        </div>
      ))}
    </div>
  )
}

export function RLView({ rl }: { rl?: RLResult }) {
  if (!rl) return <p className="muted">Bu kayıtta RL sonucu yok.</p>
  if (rl.durum !== 'tamam') return <div className="banner error">RL simülasyonu çalışmadı: {rl.neden}</div>
  const names = Object.fromEntries(Object.entries(rl.urun_adlari).map(([k, v]) => [k, `${k.split(':')[1]} · ${v}`]))
  const strategies = Object.keys(rl.test)
  return (
    <div>
      <p className="muted small">
        İki ajan, kısa listedeki ürünler arasında her {rl.parametreler.adim_is_gunu} iş gününde (≈3 ay) dağılımı yeniden seçer:
        DQN (numpy sinir ağı, {rl.parametreler.dqn.gizli}×{rl.parametreler.dqn.gizli} gizli katman; piyasa göstergelerini görür) ve tablo tabanlı Q (göstergeleri görmez).
        Her fon en fazla {pct(rl.parametreler.fon_basina_max, 0)}. Senaryolar sıralı (ortalama {rl.parametreler.blok_ceyrek} çeyrekte bir atlama).
        Ödül: reel ve stopaj sonrası servet değişimi − anapara cezası (λ={rl.parametreler.ceza_lambda}, sabit={rl.parametreler.ceza_sabit}) − işlem maliyeti.
        Ufuk {rl.parametreler.ufuk_ceyrek[0]}–{rl.parametreler.ufuk_ceyrek[1]} çeyrek arasında rastgele ve ajana gizli.
        Eğitim {rl.donem.egitim.join(' → ')}{rl.donem.dogrulama ? `, doğrulama ${rl.donem.dogrulama.join(' → ')}` : ''}, test {rl.donem.test.join(' → ')}. Tohum {rl.parametreler.tohum}. Geçmiş verilerle simülasyondur; getiri tahmini değildir.
      </p>
      {rl.notlar.map((n, i) => <div key={i} className="banner warnbox">{n}</div>)}

      <div className="summary">
        <div><span className="muted small">Doğrulamada seçilen ajan</span><strong className="small">{rl.secilen_ajan}</strong></div>
        <div><span className="muted small">Başlangıç dağılımını veren</span><strong className="small">{rl.baslangic_ajani}</strong></div>
        <div><span className="muted small">Piyasa durumu tarihi</span><strong className="small">{rl.baslangic_tarihi}</strong></div>
        <div><span className="muted small">Dağılım dışı gösterge</span><strong className="small">{rl.dagilim_disi.length}</strong></div>
      </div>

      <h3>Başlangıç dağılımı</h3>
      <Weights w={rl.baslangic_dagilimi} names={names} />

      <h3>Doğrulama (ajan seçimi)</h3>
      <table>
        <thead><tr><th>Ajan</th>{METRICS.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
        <tbody>
          {Object.entries(rl.secim_dogrulama).map(([s, m]) => (
            <tr key={s} className={s === rl.secilen_ajan ? 'hl' : ''}>
              <td>{s}</td>
              {METRICS.map(([k]) => <td key={k}>{k === 'ortalama_devir' ? m[k].toFixed(2) : pct(m[k])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Bugünkü piyasa göstergeleri (ajan durumu)</h3>
      <table>
        <thead><tr><th>Gösterge</th><th>Bugün</th><th>Eğitim ort.</th><th>Eğitim std.</th><th>Sapma</th></tr></thead>
        <tbody>
          {rl.gostergeler.map((g) => {
            const z = g.bugun === null ? null : (g.bugun - g.egitim_ort) / g.egitim_std
            return (
              <tr key={g.ad} className={z !== null && Math.abs(z) > 3 ? 'warnrow' : ''}>
                <td>{g.ad}</td><td>{g.bugun === null ? '—' : pct(g.bugun)}</td><td>{pct(g.egitim_ort)}</td><td>{pct(g.egitim_std, 2)}</td>
                <td>{z === null ? '—' : `${z >= 0 ? '+' : ''}${z.toFixed(1)}σ`}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <h3>DQN duyarlılığı (gösterge ±1σ değişirse başlangıç dağılımı)</h3>
      <table>
        <thead><tr><th>Senaryo</th><th>Dağılım</th></tr></thead>
        <tbody>
          {rl.duyarlilik.map((d, i) => (
            <tr key={i} className={d.degisti ? 'hl' : ''}>
              <td className="small">{d.gosterge}{d.deger !== null ? ` (${pct(d.deger)})` : ''}</td>
              <td><Weights w={d.dagilim} names={names} /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Test dönemi karşılaştırması (aynı senaryolar, eğitimde görülmemiş dönem)</h3>
      <table>
        <thead><tr><th>Strateji</th>{METRICS.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
        <tbody>
          {strategies.map((s) => (
            <tr key={s} className={s.startsWith('RL') ? 'hl' : ''}>
              <td>{s}</td>
              {METRICS.map(([k]) => <td key={k}>{k === 'ortalama_devir' ? rl.test[s][k].toFixed(2) : pct(rl.test[s][k])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Eğitim dönemindeki RL ortalama reel kazancı: {pct(rl.egitim['RL politikası (dinamik)'].ortalama_reel_kazanc)} (testle fark aşırı uyum göstergesidir).</p>

      <h3>Tablo tabanlı ajanın politikası (vade sonunda karar)</h3>
      <table>
        <thead><tr><th>Ay</th><th>Reel servet / anapara</th><th>Seçilen dağılım</th><th>Deneyim</th></tr></thead>
        <tbody>
          {rl.politika.map((p, i) => (
            <tr key={i} className={p.guvenilir ? '' : 'dim'}>
              <td>{p.ay}</td><td>{p.servet_bandi}</td>
              <td><Weights w={p.agirliklar} names={names} /></td>
              <td className="small">{p.ziyaret.toLocaleString('tr-TR')}{p.guvenilir ? '' : ' (az)'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {rl.dislanan.length > 0 && (
        <>
          <h3>Simülasyona alınamayan ürünler</h3>
          <ul className="flags">{rl.dislanan.map((d) => <li key={d.product_id}><code>{d.product_id}</code> {d.neden}</li>)}</ul>
        </>
      )}

      <h3>DQN öğrenme eğrisi (ortalama bölüm ödülü)</h3>
      <div className="curve">
        {rl.dqn_egrisi.map((c) => {
          const max = Math.max(...rl.dqn_egrisi.map((x) => Math.abs(x.ortalama_odul)), 1e-9)
          return <div key={c.guncelleme} className={c.ortalama_odul < 0 ? 'neg' : ''} title={`${c.guncelleme} güncelleme: ödül ${c.ortalama_odul}, kayıp ${c.kayip}`} style={{ height: `${Math.max(2, (Math.abs(c.ortalama_odul) / max) * 60)}px` }} />
        })}
      </div>

      <h3>Tablo tabanlı ajanın öğrenme eğrisi</h3>
      <div className="curve">
        {rl.ogrenme_egrisi.map((c) => {
          const max = Math.max(...rl.ogrenme_egrisi.map((x) => Math.abs(x.ortalama_odul)), 1e-9)
          return <div key={c.bolum} title={`${c.bolum} bölüm: ${c.ortalama_odul}`} style={{ height: `${Math.max(2, (Math.abs(c.ortalama_odul) / max) * 60)}px` }} />
        })}
      </div>
      <p className="muted small">Enflasyon: {rl.enflasyon_kaynagi.source} ({rl.enflasyon_kaynagi.status}, son ay {rl.enflasyon_kaynagi.son_ay ?? '-'}). Aksiyon sayısı {rl.parametreler.aksiyon_sayisi}, ızgara {pct(rl.parametreler.izgara, 0)}{rl.parametreler.yogunlasma_siniri ? ', yoğunlaşma sınırı uygulandı' : ''}.</p>
    </div>
  )
}
