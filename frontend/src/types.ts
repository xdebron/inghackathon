import type { Belief, Explanation, Gate } from './components/RLExplain'
export type DataStatus = 'ok' | 'missing' | 'stale' | 'conflict' | 'assumption'

export interface DataPoint {
  key: string
  value: unknown
  unit?: string | null
  source: string
  url?: string | null
  as_of?: string | null
  fetched_at?: string | null
  status: DataStatus
  note?: string | null
}

export interface Persona {
  persona_id: string
  profil: string
  yas: number
  risk_profili: string
  durum: string
  yatirim_amaci: string
  yatirim_ufku: string
  likidite_ihtiyaci: string
  doviz_maruz_kalma_tercihi: string
  gelir_nakit_akisi_ihtiyaci: string
  anapara_koruma_tercihi: string
  oncelikler: string[]
  acik_kisitlar: string[]
  rm_notu?: string
}

export interface Question {
  field: string
  issue: 'eksik' | 'celiski' | 'taninmayan_deger' | 'eslesmeyen_kisit'
  question: string
  detail: string
}

export interface Product {
  product_id: string
  name: string
  category: string
  currency: string
  fields: Record<string, DataPoint>
}

export interface Criterion {
  criterion: string
  raw: unknown
  normalized: number | null
  weight: number
  contribution: number
  explanation: string
}

export interface Scored {
  product: Product
  score: number
  confidence: number
  breakdown: Criterion[]
  rank: number
}

export interface Other {
  product_id: string
  name: string
  category: string
  score: number
  confidence: number
  rank: number
  reason: string
  breakdown: Criterion[]
}

export interface Exclusion {
  product_id: string
  name: string
  category: string
  rule_id: string
  reason: string
}

export interface Flag {
  product_id?: string | null
  level: 'bilgi' | 'uyari' | 'teyit'
  message: string
}

export interface TraceStep {
  step: string
  actor: 'deterministik' | 'rl' | 'llm' | 'rm'
  started_at: string
  duration_ms: number
  summary: string
}

export interface RunRecord {
  run_id: string
  created_at: string
  status: 'rm_teyidi_bekliyor' | 'rm_onayi_bekliyor' | 'oneri_yok' | 'rm_onayladi' | 'rm_reddetti'
  config_hash: string
  persona: Persona
  rm_answers: Record<string, unknown>
  disabled_sources: string[]
  questions?: Question[]
  profile_view?: { risk_level: number; max_rd: number; horizon_years: number; fx_pref: number; rules: { id: string; aciklama: string; kaynak_kisit: string }[]; acknowledged: string[]; ignored_constraints: string[] }
  weights?: Record<string, { weight: number; explanation: string }>
  market?: DataPoint[]
  flags?: Flag[]
  shortlist?: Scored[]
  others?: Other[]
  excluded?: Exclusion[]
  universe_size?: number
  hidden_flag_count?: number
  llm?: string
  draft_text?: string
  guard?: { violations: string[]; fallback_used: boolean }
  trace: TraceStep[]
  rm_decision?: { decision: string; rm_name: string; comment?: string; edited_text?: string; decided_at: string }
  rl?: RLResult
  rl_kategori?: RLCategoryResult
}

export interface RLCategoryResult {
  durum: 'tamam' | 'model_yok' | 'hata'
  neden?: string
  tarih: string
  kategori_dagilimi: Record<string, number>
  urun_eslemesi: { kategori: string; kategori_agirligi: number; agirlik: number; product_id: string | null; ad: string | null; kisa_listede: boolean; skor: number | null; takip_farki: number | null; takip_uyarisi: string | null; vekil_kategori?: string | null }[]
  urun_ayrintilari?: Record<string, Scored>
  kategori_alternatifleri?: Record<string, { product_id: string; ad: string; skor: number; kisa_listede: boolean }[]>
  ozellik_shap?: { model: string; yontem: string; deger: string; taban: string; karar_tarihi: string; kategoriler: Record<string, import('./components/ShapChart').ShapCat> } | null
  politika_belirsizligi?: { secilen_olasilik?: number | null; beklenen_dagilim?: Record<string, number> | null; model?: string } | null
  mevcut_dagilim?: Record<string, number>
  devir?: number
  dislanan_kategoriler: string[]
  dagilim_disi: { ad: string; z: number }[]
  duyarlilik: { gosterge: string; dagilim: Record<string, number> }[]
  notlar: string[]
  beklenen_getiri?: Belief[] | null
  aciklama?: Explanation | null
  guvenli_iyilestirme?: Gate | null
  gostergeler: { ad: string; bugun: number; egitim_ort: number; egitim_std: number; z: number }[]
  persona_testi?: Record<string, RLMetrics>
  genel_test?: { egitim_profilleri: Record<string, RLMetrics>; gorulmemis_profiller: Record<string, RLMetrics> }
  model: { olusturma?: string; sure_dk?: number; guncelleme?: number; en_iyi_guncelleme?: number; donem: { egitim: string[]; dogrulama: string[]; test: string[] }; profil_sayisi?: { egitim: number; gorulmemis: number }; kategori_rd?: Record<string, number>; model_turu?: string; model_adi?: string; egitim_verisi?: string }
}

export interface RLMetrics {
  ortalama_reel_kazanc: number
  medyan_reel_kazanc: number
  kotu_yuzde5: number
  anapara_alti_bitis_olasiligi: number
  herhangi_an_anapara_alti_olasiligi: number
  ortalama_devir: number
}

export interface RLResult {
  durum: 'tamam' | 'calismadi' | 'hata'
  neden?: string
  secilen_ajan: string
  baslangic_ajani: string
  baslangic_tarihi: string
  dagilim_disi: { ad: string; z: number }[]
  secim_dogrulama: Record<string, RLMetrics>
  duyarlilik: { gosterge: string; deger: number | null; dagilim: Record<string, number>; degisti?: boolean }[]
  gostergeler: { ad: string; bugun: number | null; egitim_ort: number; egitim_std: number }[]
  dqn_egrisi: { guncelleme: number; ortalama_odul: number; kayip: number }[]
  urunler: string[]
  urun_adlari: Record<string, string>
  dislanan: { product_id: string; neden: string }[]
  baslangic_dagilimi: Record<string, number>
  politika: { adim: number; ay: number; servet_bandi: string; ziyaret: number; agirliklar: Record<string, number>; guvenilir: boolean }[]
  test: Record<string, RLMetrics>
  egitim: Record<string, RLMetrics>
  ogrenme_egrisi: { bolum: number; ortalama_odul: number }[]
  parametreler: { aksiyon_sayisi: number; ufuk_ceyrek: number[]; ceza_lambda: number; ceza_sabit: number; degistirme_maliyeti: number; izgara: number; tohum: number; yogunlasma_siniri: boolean; fon_basina_max: number; adim_is_gunu: number; blok_ceyrek: number; dqn: { gizli: number; guncelleme: number; batch: number } }
  donem: { baslangic: string; bitis: string; egitim: string[]; dogrulama: string[] | null; test: string[] }
  enflasyon_kaynagi: { source?: string; status?: string; son_ay?: string; note?: string }
  notlar: string[]
}

export interface DataStatusInfo {
  tefas: {
    fon_sayisi: number
    imports: { dosya: string; iceri_alma_zamani: string; satir: number; fon_sayisi: number; tarih_araligi: string[] }[]
    conflicts: unknown[]
    karsilastirma_fon_sayisi: number
    karsilastirma_imports: { dosya: string; iceri_alma_zamani: string; fon_sayisi: number; as_of: string | null }[]
  }
  veritabani: { mevduat: number; stopaj: number; kap_fon: number; benchmark: number }
  evds_anahtari: boolean
  gostergeler: Record<string, { status: DataStatus; source: string; as_of: string | null; note: string | null }>
}
