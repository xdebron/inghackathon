export const CATS: [string, string][] = [
  ['mevduat_tl', 'Mevduat'], ['para_piyasasi', 'Para piyasası'], ['borclanma', 'Borçlanma'], ['katilim', 'Katılım'],
  ['kiymetli_maden', 'Kıymetli maden'], ['karma_degisken', 'Karma/değişken'], ['fon_sepeti', 'Fon sepeti'], ['hisse', 'Hisse'],
  ['yabanci_hisse', 'Yabancı hisse'], ['eurobond_doviz', 'Eurobond/döviz'], ['bist30_hisse', 'BIST 30 hisse'],
]

/** RM'in girdiği mevcut portföy (kategori → yüzde). Boş bırakılırsa model nakitten başlangıç varsayar.
 *  cats verilirse yalnızca modelin kategori listesindeki alanlar gösterilir (eski model BIST 30 kategorisini bilmez). */
export function CurrentAllocInput({ value, onChange, cats }: { value: Record<string, number>; onChange: (v: Record<string, number>) => void; cats?: string[] }) {
  const total = Object.values(value).reduce((a, b) => a + (b || 0), 0)
  return (
    <div className="curalloc">
      <div className="row wrap">
        <span className="muted small">Mevcut portföy (RM girer, %):</span>
        {total > 0 && <span className={`small ${Math.abs(total - 100) > 1 ? 'warntext' : 'muted'}`}>toplam %{total}{Math.abs(total - 100) > 1 ? ' (otomatik %100\'e ölçeklenir)' : ''}</span>}
        {total > 0 && <button className="ghost small" onClick={() => onChange({})}>Temizle</button>}
      </div>
      <div className="curgrid">
        {CATS.filter(([k]) => !cats || cats.includes(k)).map(([k, label]) => (
          <label key={k} className="field small"><span>{label}</span>
            <input type="number" min={0} max={100} step={5} value={value[k] ?? ''} placeholder="0"
              onChange={(e) => { const n = Number(e.target.value); const v = { ...value }; if (!e.target.value || n <= 0) delete v[k]; else v[k] = n; onChange(v) }} />
          </label>
        ))}
      </div>
    </div>
  )
}

export const toFractions = (v: Record<string, number>) =>
  Object.keys(v).length ? Object.fromEntries(Object.entries(v).map(([k, n]) => [k, n / 100])) : undefined
