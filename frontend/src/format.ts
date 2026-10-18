import type { DataPoint } from './types'

export const pct = (x: unknown, digits = 1) =>
  typeof x === 'number' ? `%${(x * 100).toFixed(digits).replace('.', ',')}` : '—'

export const CRITERIA: Record<string, string> = {
  getiri: 'Getiri',
  dusuk_oynaklik: 'Düşük oynaklık',
  likidite: 'Likidite',
  maliyet: 'Düşük maliyet',
  gelir: 'Gelir üretimi',
  anapara: 'Anapara koruma',
  doviz_uyumu: 'Döviz uyumu',
  sadelik: 'Sadelik',
}

export const STATUS_LABEL: Record<string, string> = {
  ok: 'Kaynaklı',
  missing: 'Eksik',
  stale: 'Bayat',
  conflict: 'Çelişkili',
  assumption: 'Varsayım',
}

export const RUN_STATUS: Record<string, string> = {
  rm_teyidi_bekliyor: 'RM teyidi bekliyor',
  rm_onayi_bekliyor: 'RM onayı bekliyor',
  oneri_yok: 'Uygun ürün yok',
  rm_onayladi: 'RM onayladı',
  rm_reddetti: 'RM reddetti',
}

const PCT_FIELDS = new Set([
  'getiri_1y', 'getiri_3a', 'getiri_1a', 'volatilite', 'max_dusus', 'yonetim_ucreti', 'stopaj',
  'reel_getiri_1y', 'benchmark_farki_1y', 'getiri_1y_karsilastirma', 'sektor_farki', 'giris_komisyonu', 'cikis_komisyonu',
])

export const FIELD_LABEL: Record<string, string> = {
  getiri_1y: '1Y getiri',
  reel_getiri_1y: '1Y reel getiri',
  getiri_1y_karsilastirma: '1Y getiri (TEFAS karş.)',
  benchmark_farki_1y: 'Benchmark farkı',
  volatilite: 'Volatilite',
  max_dusus: 'Max düşüş',
  rd: 'Risk değeri',
  valor_gun: 'Satış valörü',
  vade_gun: 'Vade (gün)',
  yonetim_ucreti: 'Yönetim ücreti',
  buyukluk: 'Fon büyüklüğü',
  stopaj: 'Stopaj',
  sektor_farki: 'Sektör ort. farkı',
  tmsf_limiti: 'TMSF limiti (TL)',
  uyari_notu: 'Uyarı notu',
  giris_komisyonu: 'Giriş komisyonu',
  cikis_komisyonu: 'Çıkış komisyonu',
  api_capraz_kontrol: 'TEFAS çapraz kontrol',
  ing_platformunda: 'ING platformunda',
  semsiye_turu: 'Şemsiye türü',
}

export const INDICATOR_LABEL: Record<string, string> = {
  tufe_yillik: 'TÜFE (yıllık)',
  politika_faizi: 'Politika faizi',
  mevduat_sektor_ortalamasi_1a: 'Sektör mevduat 1A',
  mevduat_sektor_ortalamasi_3a: 'Sektör mevduat 3A',
  mevduat_sektor_ortalamasi_6a: 'Sektör mevduat 6A',
  mevduat_sektor_ortalamasi_1y: 'Sektör mevduat 1Y',
  altin_gram_tl: 'Gram altın',
  dibs_gosterge_2y: 'DİBS 2Y',
  tmsf_sigorta_limiti_tl: 'TMSF limiti',
}

export function fmtField(name: string, dp: DataPoint): string {
  if (dp.value === null || dp.value === undefined) return '—'
  if (PCT_FIELDS.has(name) && typeof dp.value === 'number') return pct(dp.value, 2)
  if (name === 'buyukluk' && typeof dp.value === 'number') return `${(dp.value / 1e6).toLocaleString('tr-TR', { maximumFractionDigits: 0 })} mn TL`
  if (typeof dp.value === 'boolean') return dp.value ? 'Evet' : 'Hayır'
  if (typeof dp.value === 'number') return dp.value.toLocaleString('tr-TR', { maximumFractionDigits: 4 })
  return String(dp.value)
}

export const fmtTime = (iso?: string | null) => (iso ? new Date(iso).toLocaleString('tr-TR') : '—')
