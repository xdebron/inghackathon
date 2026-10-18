// Kategori dağılımı çubuğu (yüzde puan): RL kararı, what-if önce/sonra karşılaştırması.

export const CAT_LABEL: Record<string, string> = {
  mevduat_tl: 'TL mevduat', para_piyasasi: 'Para piyasası', borclanma: 'Borçlanma araçları', katilim: 'Katılım',
  kiymetli_maden: 'Kıymetli maden', karma_degisken: 'Karma / değişken', fon_sepeti: 'Fon sepeti', hisse: 'Hisse fonu',
  yabanci_hisse: 'Yabancı hisse', eurobond_doviz: 'Eurobond / döviz', bist30_hisse: 'BIST 30 hisseleri',
}

const COLORS: Record<string, string> = {
  mevduat_tl: '#ffcfa8', para_piyasasi: '#bcd5fb', borclanma: '#c8ecd5', katilim: '#e3d3f7', kiymetli_maden: '#f8e3a3',
  karma_degisken: '#f6c6cf', fon_sepeti: '#cdeef0', hisse: '#f3b5a5', yabanci_hisse: '#d9d0c4', eurobond_doviz: '#c9d3e6',
  bist30_hisse: '#f0a8a8',
}

export function AllocBar({ d, compact }: { d: Record<string, number>; compact?: boolean }) {
  const entries = Object.entries(d).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
  return (
    <div className={`alloc ${compact ? 'compact' : ''}`}>
      {entries.map(([c, v]) => (
        <div key={c} className="alloc-seg" style={{ flexGrow: v, background: COLORS[c] ?? '#eef0f4' }} title={`${CAT_LABEL[c] ?? c} %${v}`}>
          <span>{CAT_LABEL[c] ?? c} %{v}</span>
        </div>
      ))}
    </div>
  )
}
