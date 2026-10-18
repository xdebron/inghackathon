import { pct } from '../format'
import type { RLCategoryResult, RLMetrics } from '../types'
import { BeliefTable, DecisionExplanation, GateBox } from './RLExplain'

const CAT_LABEL: Record<string, string> = {
  mevduat_tl: 'Mevduat (3 ay)', para_piyasasi: 'Para piyasası', borclanma: 'Borçlanma araçları', katilim: 'Katılım (kira sert.)',
  kiymetli_maden: 'Kıymetli maden', karma_degisken: 'Karma / değişken', fon_sepeti: 'Fon sepeti', hisse: 'Hisse senedi',
  yabanci_hisse: 'Yabancı hisse', eurobond_doviz: 'Eurobond / döviz', bist30_hisse: 'BIST 30 hisse (doğrudan)',
}

const METRICS: [keyof RLMetrics, string][] = [
  ['ortalama_reel_kazanc', 'Ort. reel kazanç'],
  ['medyan_reel_kazanc', 'Medyan'],
  ['kotu_yuzde5', 'En kötü %5'],
  ['anapara_alti_bitis_olasiligi', 'Bitişte anapara altı'],
  ['herhangi_an_anapara_alti_olasiligi', 'Herhangi bir an anapara altı'],
]

function MetricsTable({ data, highlight }: { data: Record<string, RLMetrics>; highlight?: string }) {
  return (
    <table>
      <thead><tr><th>Strateji</th>{METRICS.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
      <tbody>
        {Object.entries(data).map(([s, m]) => (
          <tr key={s} className={s === highlight ? 'hl' : ''}>
            <td>{s}</td>{METRICS.map(([k]) => <td key={k}>{pct(m[k])}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function RLCategoryView({ r }: { r?: RLCategoryResult }) {
  if (!r) return <p className="muted">Bu kayıtta kategori modeli sonucu yok.</p>
  if (r.durum !== 'tamam') return <div className="banner warnbox">Kategori modeli kullanılamadı: {r.neden}</div>
  const ppo = r.model.model_turu === 'ppo'
  const DQN = ppo ? 'Model (PPO, profile koşullu)' : 'DQN (profile koşullu)'
  return (
    <div>
      <p className="muted small">
        {ppo
          ? <>Tek bir profile koşullu PPO politikası (sinir ağı), brief değerlerinin {r.model.profil_sayisi?.egitim?.toLocaleString('tr-TR')} kombinasyonu ve 5 persona
            ile, {r.model.egitim_verisi ?? 'tüm veride'} eğitildi. Durum ve ödül profilin tamamını içerir (risk, ufuk, likidite, gelir ihtiyacı, anapara,
            döviz, öncelikler, yatırım amacı, kısıtlar). Kombinasyonlar müşteri ya da persona değildir;</>
          : <>Tek bir profile koşullu DQN, {r.model.profil_sayisi?.egitim?.toLocaleString('tr-TR')} sentetik profil parametresiyle
            ({r.model.guncelleme?.toLocaleString('tr-TR')} güncelleme, {r.model.sure_dk} dk) eğitildi. Sentetik profiller müşteri ya da persona değildir;</>}
        yalnızca politikanın profil parametrelerine göre genelleşmesi içindir. Aksiyon {Object.keys(r.model.kategori_rd ?? {}).length || 10} varlık kategorisine ağırlıktır; her kategori kısa listedeki
        (yoksa uygun listedeki) en yüksek skorlu ürüne eşlenir. Piyasa durumu: {r.tarih}. Eğitim {r.model.donem.egitim.join(' → ')},
        doğrulama {r.model.donem.dogrulama.join(' → ')}, test {r.model.donem.test.join(' → ')}.
      </p>
      {r.notlar.map((n, i) => <div key={i} className="banner warnbox">{n}</div>)}

      <h3>Başlangıç dağılımı</h3>
      <GateBox g={r.guvenli_iyilestirme} />
      {r.mevcut_dagilim && Object.keys(r.mevcut_dagilim).length > 0 && (
        <p className="small">Mevcut portföy: {Object.entries(r.mevcut_dagilim).map(([k, v]) => `${CAT_LABEL[k] ?? k} ${pct(v, 0)}`).join(', ')}
          {r.devir !== undefined && <> · geçiş için portföyün <strong>{pct(r.devir, 0)}</strong> kadarı el değiştirir</>}</p>
      )}
      <table>
        <thead><tr><th>Kategori</th><th>Kategori payı</th><th>Ürün payı</th><th>Eşlenen ürün</th><th>Kısa listede</th><th>Takip farkı (yıllık)</th></tr></thead>
        <tbody>
          {r.urun_eslemesi.map((m, i) => (
            <tr key={`${m.kategori}-${i}`} className={m.takip_uyarisi ? 'warnrow' : ''}>
              <td>{CAT_LABEL[m.kategori] ?? m.kategori}</td>
              <td>{pct(m.kategori_agirligi, 0)}</td>
              <td><div className="bar wide"><div style={{ width: `${m.agirlik * 100}%` }} /></div> {pct(m.agirlik, 0)}</td>
              <td className="small">{m.ad ?? <span className="muted">uygun ürün yok</span>}</td>
              <td>{m.kisa_listede ? 'evet' : 'hayır'}</td>
              <td className="small" title={m.takip_uyarisi ?? ''}>{m.takip_farki === null ? '—' : pct(m.takip_farki)}{m.takip_uyarisi ? ' ⚠' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Takip farkı: fonun son 1 yılda kategori endeksinden (modelin eğitimde gördüğü medyan fon) yıllık sapması.
        Yüzde 30 ve üzeri kategori payı en iyi 2 fona bölünür.</p>

      {r.dislanan_kategoriler.length > 0 && (
        <>
          <h3>Profil maskesiyle dışlanan kategoriler</h3>
          <ul className="flags">{r.dislanan_kategoriler.map((d) => <li key={d}>{d}</li>)}</ul>
        </>
      )}

      {r.persona_testi && (
        <>
          <h3>{ppo ? 'Bu persona için performans tahmini (aynı ayarlarla, test dönemini görmeden eğitilmiş modeller)' : 'Bu persona için test dönemi sonuçları (eğitimde kullanılmadı)'}</h3>
          <MetricsTable data={r.persona_testi} highlight={DQN} />
        </>
      )}
      {r.genel_test && (
        <>
          <h3>Görülmemiş sentetik profillerde test sonuçları (genelleme)</h3>
          <MetricsTable data={r.genel_test.gorulmemis_profiller} highlight={DQN} />
        </>
      )}

      <h3>Bugünkü piyasa göstergeleri</h3>
      <table>
        <thead><tr><th>Gösterge</th><th>Bugün</th><th>Eğitim ort.</th><th>Sapma</th></tr></thead>
        <tbody>
          {r.gostergeler.map((g) => (
            <tr key={g.ad} className={Math.abs(g.z) > 3 ? 'warnrow' : ''}>
              <td>{g.ad}</td><td>{pct(g.bugun)}</td><td>{pct(g.egitim_ort)}</td><td>{`${g.z >= 0 ? '+' : ''}${g.z.toFixed(1)}σ`}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <DecisionExplanation a={r.aciklama} />
      <BeliefTable rows={r.beklenen_getiri} />

      <h3>Duyarlılık (dağılımı değiştiren ±1σ gösterge hareketleri)</h3>
      {r.duyarlilik.length === 0 ? <p className="muted small">Hiçbir göstergenin ±1σ değişimi başlangıç dağılımını değiştirmiyor.</p> : (
        <table>
          <tbody>
            {r.duyarlilik.map((d, i) => (
              <tr key={i}><td className="small">{d.gosterge}</td>
                <td className="small">{Object.entries(d.dagilim).map(([k, v]) => `${CAT_LABEL[k] ?? k} ${pct(v, 0)}`).join(', ')}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
