import { useEffect, useState } from 'react'
import { MiniMarkdown } from '../components/MiniMarkdown'

// Müşteri portalı: müşteri yalnızca danışmanının (RM) onayladığı içeriği görür. Ajan müşteriye doğrudan yanıt vermez;
// sorular danışmanın kuyruğuna düşer, ajan taslak hazırlar, RM onaylayınca burada görünür (brief kural 4).

interface Profile { persona_id: string; profil: string; yas: number; risk_profili: string; yatirim_amaci: string; yatirim_ufku: string;
  likidite_ihtiyaci: string; anapara_koruma_tercihi: string; oncelikler: string[] }
interface View {
  profil: Profile
  durum: 'hazir' | 'hazirlaniyor'
  ozet: { metin: string; onay_zamani?: string; danisman?: string } | null
  sorular: { id: string; soru: string; zaman: string; durum: 'yanitlandi' | 'danismaninizda'; cevap: string | null; yanit_zamani: string | null }[]
}

const IDS = ['P01', 'P02', 'P03', 'P04', 'P05']
const dt = (s?: string | null) => (s ? new Date(s).toLocaleDateString('tr-TR', { day: 'numeric', month: 'long', year: 'numeric' }) : '')

export function CustomerPortal() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [pid, setPid] = useState<string | null>(null)
  const [view, setView] = useState<View | null>(null)
  const [q, setQ] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    Promise.all(IDS.map((id) => fetch(`/api/musteri/${id}`).then((r) => r.json()).then((v: View) => v.profil).catch(() => null)))
      .then((ps) => setProfiles(ps.filter(Boolean) as Profile[]))
  }, [])

  const load = async (id: string) => {
    setPid(id); setMsg(null)
    setView(await (await fetch(`/api/musteri/${id}`)).json())
  }

  const ask = async () => {
    if (!pid || !q.trim()) return
    const r = await fetch(`/api/musteri/${pid}/soru`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ soru: q }) })
    const j = await r.json().catch(() => ({}))
    if (!r.ok) { setMsg(typeof j.detail === 'string' ? j.detail : 'Soru gönderilemedi'); return }
    setQ(''); setMsg('Sorunuz danışmanınıza iletildi. Yanıtı burada görebileceksiniz.')
    load(pid)
  }

  if (!pid || !view) {
    return (
      <div className="portal">
        <div className="portal-hero">
          <h1>Yatırım görüşmenize hazırlık</h1>
          <p>Danışmanınızın sizin için hazırladığı ve onayladığı özeti görüntüleyin, sorularınızı iletin.</p>
          <p className="muted small">Demo: gerçek kullanımda müşteri kendi hesabıyla giriş yapar. Aşağıdan bir müşteri profili seçin.</p>
        </div>
        <div className="profile-grid">
          {profiles.map((p) => (
            <button key={p.persona_id} className="profile-card" onClick={() => load(p.persona_id)}>
              <strong>{p.profil}</strong>
              <span className="muted">{p.yas} yaş · {p.risk_profili}</span>
              <span className="small">{p.yatirim_amaci}</span>
            </button>
          ))}
        </div>
      </div>
    )
  }

  const p = view.profil
  return (
    <div className="portal">
      <button className="ghost back" onClick={() => { setPid(null); setView(null) }}>← Profil seçimine dön</button>
      <div className="portal-head">
        <h1>Merhaba</h1>
        <p className="muted">{p.profil} · {p.yas} yaş</p>
        <div className="chips">
          <span className="chip-lg">Risk tercihi: {p.risk_profili}</span>
          <span className="chip-lg">Yatırım süresi: {p.yatirim_ufku}</span>
          <span className="chip-lg">Nakde erişim ihtiyacı: {p.likidite_ihtiyaci}</span>
          <span className="chip-lg">Anaparayı koruma: {p.anapara_koruma_tercihi}</span>
        </div>
      </div>

      <section className="portal-card">
        <h2>Danışmanınızın hazırladığı özet</h2>
        {view.ozet ? (
          <>
            <MiniMarkdown text={view.ozet.metin} />
            <div className="muted small signed">Danışmanınız {view.ozet.danisman} tarafından {dt(view.ozet.onay_zamani)} tarihinde onaylandı.</div>
          </>
        ) : (
          <div className="pending">
            <p><strong>Danışmanınız sizin için hazırlık yapıyor.</strong></p>
            <p className="muted">Özet, danışmanınız tarafından incelenip onaylandıktan sonra burada görünecek.</p>
          </div>
        )}
      </section>

      <section className="portal-card">
        <h2>Danışmanınıza sorun</h2>
        <div className="ask-row">
          <textarea rows={3} placeholder="Örn. Param ihtiyacım olduğunda ne kadar sürede hesabıma geçer?" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="primary" disabled={!q.trim()} onClick={ask}>Gönder</button>
        </div>
        {msg && <div className="banner ok small">{msg}</div>}
        {view.sorular.slice().reverse().map((s) => (
          <div key={s.id} className="portal-q">
            <div className="q-text">{s.soru}</div>
            <div className="muted small">{dt(s.zaman)}</div>
            {s.cevap ? (
              <div className="portal-a"><div className="small a-label">Danışmanınızın yanıtı · {dt(s.yanit_zamani)}</div><MiniMarkdown text={s.cevap} /></div>
            ) : <div className="small muted waiting">Danışmanınız yanıtlıyor…</div>}
          </div>
        ))}
      </section>
      <p className="muted small disclaimer">
        Bu sayfadaki bilgiler danışmanınız tarafından onaylanmıştır ve yatırım tavsiyesi değildir. Kararlarınızı danışmanınızla birlikte verin.
      </p>
    </div>
  )
}
