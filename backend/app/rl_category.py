"""Profile koşullu, varlık kategorisi düzeyinde DQN: çevrimdışı uzun eğitim, çevrimiçi anında çıkarım.

Neden kategori düzeyi: her persona/profilin kısa listesi farklıdır; tek bir modelin binlerce profil için öğrenebilmesi
ortak bir aksiyon uzayı gerektirir. Aksiyon = varlık kategorilerine ağırlık; çıkarımda her kategorinin ağırlığı o
personanın kısa listesindeki (yoksa uygun listesindeki) en yüksek skorlu ürüne eşlenir.

Veri
  - Kategori endeksleri: TEFAS fiyat geçmişinden fon günlük getirilerinin kategori bazında medyanı (aykırı değere
    dayanıklı; >%20 günlük sıçrama ve 0 fiyat eksik sayılır). Mevduat: TCMB 3 aya kadar vadeli tasarruf mevduatı faizi
    (EVDS, tarihsel) ile günlük faiz birikimi.
  - Stopaj: fon kategorilerinde dönemine göre (ING tablosu tarihçesi), mevduatta güncel oran.
  - Reel getiri: dönemin gerçek TÜFE'si (EVDS).
  - Göstergeler (hepsi karar anında bilinen, gecikmeli): kategori 3 ay getiri ve oynaklıkları, USD/TRY 3 ay değişimi,
    TÜFE 3 ay eğilimi ve yıllık, mevduat faizi seviye ve 3 ay değişimi, politika faizi, reel faiz.
Profiller
  - Eğitim için ~10.000 SENTETİK PROFİL PARAMETRESİ örneklenir (risk, ufuk, anapara koruması, likidite, döviz tercihi,
    açık kısıt bayrakları). Bunlar müşteri ya da persona değildir; yalnızca politikanın profil parametrelerine göre
    genelleşmesi için kullanılır. Öneri yalnızca verilen 5 persona için üretilir.
"""
from __future__ import annotations

import json
import os
from datetime import date

import numpy as np
import pandas as pd

from .config import CACHE_DIR, SNAPSHOT_DIR, decision_config
from .sources import evds, tefas_import

CATS_V1 = ["mevduat_tl", "para_piyasasi", "borclanma", "katilim", "kiymetli_maden", "karma_degisken", "fon_sepeti",
           "hisse", "yabanci_hisse", "eurobond_doviz"]
CATS_V2 = CATS_V1 + ["bist30_hisse"]      # doğrudan BIST 30 hisseleri (fon değil; eşit ağırlıklı sepet endeksi)
CATS = CATS_V1                           # eski (v1) modelin kategori listesi; yeni kod ds["cats"] / meta["kategoriler"] kullanır
FX_CATS = {"kiymetli_maden", "yabanci_hisse", "eurobond_doviz"}
EQUITY_CATS = {"hisse", "bist30_hisse"}  # yüksek riskli ürün yasağında yapısı gereği dışlanan kategoriler
STOCK_CAT = "bist30_hisse"


def cats_for(feature_set: str | None) -> list[str]:
    """Gösterge setine göre kategori listesi: v1 (eski model) 10 kategori; v2 ve v3 BIST 30 hisseleriyle 11 kategori."""
    return CATS_V2 if feature_set in ("v2", "v3") else CATS_V1


def _cats_of(actions: np.ndarray) -> list[str]:
    return CATS_V2 if actions.shape[1] == len(CATS_V2) else CATS_V1


def cfg() -> dict:
    return decision_config()["rl_kategori"]


def _fund_categories(codes: list[str]) -> dict[str, str]:
    from .universe import _kap_snapshot, detect_category
    names = tefas_import.price_names() if hasattr(tefas_import, "price_names") else {}
    kap = _kap_snapshot()
    out = {}
    for c in codes:
        cat = detect_category(names.get(c, "")) or (detect_category(kap.get(c, {}).get("semsiye_turu", "")) if c in kap else None)
        if cat in CATS_V2:
            out[c] = cat
    return out


GECMIS_DIR = SNAPSHOT_DIR / "gecmis"


def hist(name: str) -> pd.Series:
    """snapshots/gecmis/<ad>.csv (tarih, deger) tarihçe serisi. Kaynak bilgisi gecmis/meta.json'da."""
    path = GECMIS_DIR / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Tarihçe serisi yok: {path}")
    df = pd.read_csv(path)
    s = pd.Series(df["deger"].to_numpy(dtype=float), index=pd.to_datetime(df["tarih"]))
    return s[~s.index.duplicated(keep="last")].sort_index()


def hist_meta() -> dict:
    path = GECMIS_DIR / "meta.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


BIST_DIR = GECMIS_DIR / "bist"
BIST_INDEXES = ("XU030", "XU100")


def bist_meta() -> dict:
    path = BIST_DIR / "meta.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def bist_series(code: str, column: str = "duzeltilmis_kapanis") -> pd.Series:
    """snapshots/gecmis/bist/<KOD>.csv: tarih, kapanis, duzeltilmis_kapanis (temettü/bölünme düzeltmeli), hacim."""
    path = BIST_DIR / f"{code}.csv"
    if not path.exists():
        raise FileNotFoundError(f"BIST serisi yok: {path}")
    df = pd.read_csv(path)
    s = pd.Series(df[column].to_numpy(dtype=float), index=pd.to_datetime(df["tarih"]))
    return s[~s.index.duplicated(keep="last")].sort_index().replace(0, np.nan)


def bist_stock_codes() -> list[str]:
    codes = bist_meta().get("bilesim", {}).get("kodlar")
    if codes:
        return [c for c in codes if (BIST_DIR / f"{c}.csv").exists()]
    return sorted(f.stem for f in BIST_DIR.glob("*.csv") if f.stem not in BIST_INDEXES)


def bist_stock_returns(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Hisselerin düzeltilmiş kapanışından, verilen (TEFAS) takvimine hizalanmış günlük toplam getiriler.
    Borsa kapalı / işlem yok günlerinde son fiyat taşınır (en fazla 5 gün), yani getiri 0 sayılır."""
    c = cfg()
    cols = {}
    for code in bist_stock_codes():
        s = bist_series(code)
        lv = s.reindex(s.index.union(dates)).ffill(limit=5).reindex(dates)
        r = lv.pct_change(fill_method=None)
        r[r.abs() > c["max_gunluk_sicrama"]] = np.nan
        cols[code] = r
    return pd.DataFrame(cols, index=dates)


def bist30_basket_returns(dates: pd.DatetimeIndex) -> tuple[pd.Series, pd.DataFrame]:
    """BIST 30 hisse kategorisi endeksi: hisselerin günlük getirilerinin eşit ağırlıklı ortalaması (günlük yeniden
    dengelenen sepet). Geçerli hisse sayısı `bist30.min_hisse` altındaysa gün eksik sayılır."""
    rets = bist_stock_returns(dates)
    n = rets.notna().sum(axis=1)
    return rets.mean(axis=1).where(n >= cfg()["bist30"]["min_hisse"]), rets


def _deposit_rate_series(source: str) -> tuple[pd.Series, dict]:
    if source == "gecmis":
        m = hist_meta().get("mevduat_tl_3a", {})
        return hist("mevduat_tl_3a"), {"source": m.get("kaynak"), "status": m.get("status"), "fetched_at": m.get("alinma")}
    dep = evds.fetch_history("TP.TRYTAS.MT02")
    ws = pd.Series({pd.to_datetime(p[0], format="%d-%m-%Y"): p[1] / 100 for p in dep["points"]}).sort_index()
    return ws[~ws.index.duplicated()], {k: dep.get(k) for k in ("source", "status", "fetched_at")}


def category_indices(deposit_source: str = "evds", start: str | None = None, cats: list[str] | None = None) -> dict:
    """Kategori endeks seviyeleri (tarih × kategori), üye fon sayıları, kategori risk değeri ve gün geçerliliği.

    Veri boşluğu koruması: ardışık iki fiyat tarihi arasında `max_bosluk_gun` takvim gününden fazla fark varsa boşluğun
    ardındaki gün geçersiz sayılır (boşluk boyunca biriken getiri tek günlük getiri gibi hesaplanmaz) ve bu günü içeren
    pencereler eğitim/değerlendirme havuzlarından çıkarılır. Kategori endeksi hesaplanamayan günler de geçersizdir."""
    c = cfg()
    cats_list = cats or CATS_V1
    prices = tefas_import.price_matrix(list(tefas_import.price_names().keys()))
    if start:
        prices = prices.loc[pd.Timestamp(start):]
    prices = prices.replace(0, np.nan)
    rets = prices.pct_change(fill_method=None)
    rets[rets.abs() > c["max_gunluk_sicrama"]] = np.nan
    gap_days = pd.Series(prices.index, index=prices.index).diff().dt.days
    gap = (gap_days > c["max_bosluk_gun"]).to_numpy()
    rets.iloc[gap, :] = np.nan
    gaps = [{"once": str(prices.index[i - 1].date()), "sonra": str(prices.index[i].date()), "gun": int(gap_days.iloc[i])}
            for i in np.where(gap)[0]]
    fund_cat = _fund_categories(list(prices.columns))
    levels, counts, members = {}, {}, {}
    for cat in cats_list[1:]:
        if cat == STOCK_CAT:
            basket, srets = bist30_basket_returns(prices.index)
            members[cat] = list(srets.columns)
            counts[cat] = srets.notna().sum(axis=1)
            levels[cat] = basket.where(~gap)
            continue
        cols = [k for k, v in fund_cat.items() if v == cat]
        members[cat] = cols
        if not cols:
            continue
        r = rets[cols]
        n = r.notna().sum(axis=1)
        med = r.median(axis=1).where(n >= c["min_fon_sayisi"])
        counts[cat] = n
        levels[cat] = med
    dates = prices.index
    ws, dep_info = _deposit_rate_series(deposit_source)
    rate = ws.reindex(ws.index.union(dates)).ffill().reindex(dates)
    days = pd.Series(dates, index=dates).diff().dt.days.fillna(1)
    levels["mevduat_tl"] = (rate * days / 365).where(~gap)
    R = pd.DataFrame(levels).reindex(columns=cats_list)
    valid = R.notna().all(axis=1)
    start = valid.idxmax() if valid.any() else None
    gecerli = valid.loc[start:].to_numpy()
    R = R.loc[start:].fillna(0.0)          # geçersiz günler seviyeyi taşımaz; pencere filtresi onları dışlar
    level = (1 + R).cumprod()
    # Kategori risk değeri: üye fonların resmi RD medyanı (KAP); yoksa endeks oynaklığından SPK bandı
    from .universe import _kap_snapshot, estimate_rd
    kap = _kap_snapshot()
    rd = {"mevduat_tl": 1}
    for cat in cats_list[1:]:
        offs = [kap[f]["resmi_rd"] for f in members.get(cat, []) if f in kap and kap[f].get("resmi_rd") is not None]
        vol = float(R[cat].std() * np.sqrt(252))
        rd[cat] = int(np.median(offs)) if offs else estimate_rd(vol)
    return {"level": level, "deposit_rate": rate.loc[start:], "start": str(start.date()) if start is not None else None,
            "members": members, "rd": rd, "gecerli": gecerli, "bosluklar": gaps,
            "gecersiz_gun": int((~gecerli).sum()),
            "fund_counts": {k: int(v.loc[start:].median()) for k, v in counts.items()},
            "deposit_source": dep_info}


# --- Çevrimiçi çıkarım (eğitilmiş model) ---------------------------------------------------------

# Canlı model dizini: RL_MODEL ortam değişkeniyle seçilir (örn. rl_kategori = v1, rl_kategori_v4 = v4).
# Varsayılan: profilin tamamı + kısıt kataloğuyla, tüm veride eğitilen NN-PPO (16.10).
MODEL_DIR = CACHE_DIR.parent.parent / "models" / os.getenv("RL_MODEL", "rl_ppo_v14_tum6m")   # canlı: NN-PPO v14 (what-if tutarlılık düzenleyicili), tüm veri, 6M adım
_MODEL_CACHE: dict = {}

# Ürün kategorisi → model kategorisi (katılım hesabı mevduat gibi değerlendirilir)
PRODUCT_TO_CAT = {"katilim_hesabi": "mevduat_tl"}
# Modelin eğitimde görmediği doğrudan ürünler en yakın kategori endeksiyle temsil edilir (vekil). Getiri davranışları
# kategori endeksinden farklıdır (örn. tek tahvilin vade/faiz riski, mevduatın sabit faizi); eşlemede RM'e not düşülür.
# Ayrı kategori olarak öğrenilmeleri için kendi endeksleriyle yeniden eğitim gerekir.
PROXY_CAT = {"dibs_tl": "borclanma", "dibs_tufe": "borclanma", "maden_hesabi": "kiymetli_maden",
             "mevduat_doviz": "eurobond_doviz"}
PRODUCT_TO_CAT.update(PROXY_CAT)


def model_status() -> dict:
    s = MODEL_DIR / "durum.json"
    return json.loads(s.read_text(encoding="utf-8")) if s.exists() else {}


class PPOPolicy:
    """Stable-Baselines3 MaskablePPO (MlpPolicy, tanh) aktör ağının numpy çıkarımı. forward(x) = aksiyon logitleri; maskeli
    argmax deterministik karardır. DQN ağıyla aynı arayüz (forward) — çıkarım kodu ikisini aynı biçimde kullanır.
    Ağırlıklar tools/export_ppo_model.py ile aktarılır (W: girdi × çıktı)."""
    kind = "ppo"
    belief_W = None

    def __init__(self, w, activation: str = "tanh"):
        self.w = {k: np.asarray(w[k], dtype=np.float64) for k in w}
        self.act = np.tanh if activation == "tanh" else (lambda z: np.maximum(z, 0.0))
        # katmanlar: genel biçim L{i}W / V{i}W; eski (v8–v10) biçim P0W,P1W / V0W,V1W
        gen = "L0W" in self.w
        n = sum(1 for k in self.w if k[0] == "L" and k.endswith("W")) if gen else 2
        self.pi = [(self.w[f"L{i}W"], self.w[f"L{i}b"]) if gen else (self.w[f"P{i}W"], self.w[f"P{i}b"]) for i in range(n)]
        nv = sum(1 for k in self.w if k[0] == "V" and k[1:-1].isdigit() and k.endswith("W"))
        self.vf = [(self.w[f"V{i}W"], self.w[f"V{i}b"]) for i in range(nv)]

    def forward(self, x, keep: bool = False):
        w, h, acts = self.w, x, [x]
        for W, b in self.pi:
            h = self.act(h @ W + b)
            acts.append(h)
        if "G" in w:     # aksiyon gömmeli çıkış: (W·h + b) · g(A)ᵀ + c
            out = (h @ w["PW"] + w["Pb"]) @ w["G"].T + w["Ab"]
        else:
            out = h @ w["AW"] + w["Ab"]
        acts.append(out)
        return (out, acts) if keep else out

    def value(self, x):
        h = x
        for W, b in self.vf:
            h = self.act(h @ W + b)
        return (h @ self.w["VW"] + self.w["Vb"])[..., 0]

    def probs(self, x, mask):
        z = np.where(mask, self.forward(x), -np.inf)
        z = z - z.max(axis=-1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=-1, keepdims=True)


def _load_model():
    from .rl import MLP
    mp, mt = MODEL_DIR / "model.npz", MODEL_DIR / "meta.json"
    if not (mp.exists() and mt.exists()):
        return None, None
    key = (mp.stat().st_mtime, mt.stat().st_mtime)
    if key not in _MODEL_CACHE:
        meta = json.loads(mt.read_text(encoding="utf-8"))
        w = np.load(mp)
        if meta.get("model_turu") == "ppo":
            _MODEL_CACHE.clear()
            _MODEL_CACHE[key] = (PPOPolicy(w, meta.get("aktivasyon", "tanh")), meta)
            return _MODEL_CACHE[key]
        d_in = w["W0"].shape[0]
        n_layers = sum(1 for k in w.files if k[0] == "W" and k[1:].isdigit())
        net = MLP(d_in, w["W0"].shape[1], w[f"W{n_layers - 1}"].shape[1], np.random.default_rng(0), 0.0)
        net.load_state_dict(w)
        net.belief_W = (w["Wb"], w["bb"]) if "Wb" in w.files else None     # beklenen getiri inanç başlığı
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = (net, meta)
    return _MODEL_CACHE[key]


_CI_CACHE: dict = {}


def _category_levels() -> pd.DataFrame:
    key = tefas_import._mtime(tefas_import.PRICES)
    if key not in _CI_CACHE:
        _CI_CACHE.clear()
        _CI_CACHE[key] = category_indices()["level"]
    return _CI_CACHE[key]


def tracking_errors(codes: list[str], cat: str, window: int) -> dict[str, float | None]:
    """Fonların son `window` iş gününde kategori endeksine göre yıllık takip farkı (günlük getiri farkının std'si × √252).
    Model eğitimde kategori endeksini (medyan fon) gördüğü için, seçilen fonun endeksten ne kadar saptığını ölçer."""
    if not codes or cat == "mevduat_tl":
        return {c: None for c in codes}
    lv = _category_levels()
    pm = tefas_import.price_matrix(codes)
    if pm is None:
        return {c: None for c in codes}
    cat_r = lv[cat].pct_change()
    out = {}
    for c in codes:
        if c not in pm.columns:
            out[c] = None
            continue
        fr = pm[c].replace(0, np.nan).pct_change(fill_method=None)
        fr[fr.abs() > cfg()["max_gunluk_sicrama"]] = np.nan
        d = (fr - cat_r.reindex(fr.index)).dropna().iloc[-window:]
        out[c] = float(d.std() * np.sqrt(252)) if len(d) >= 60 else None
    return out


def stock_basket_tracking_error(codes: list[str], window: int) -> float | None:
    """Seçilen hisselerin eşit ağırlıklı sepetinin BIST 30 eşit ağırlıklı sepetinden yıllık takip farkı."""
    if not codes:
        return None
    lv = _category_levels()
    try:
        basket, rets = bist30_basket_returns(lv.index)
    except FileNotFoundError:
        return None
    sub = rets[[c for c in codes if c in rets.columns]]
    if sub.shape[1] == 0:
        return None
    d = (sub.mean(axis=1) - basket).dropna().iloc[-window:]
    return float(d.std() * np.sqrt(252)) if len(d) >= 60 else None


def _map_stocks(cat: str, wgt: float, cands: list, ec: dict) -> tuple[list[dict], list[str]]:
    """BIST 30 hisse kategorisi: kategori payı en yüksek skorlu `sepet_hisse_sayisi` hisseye eşit bölünür (tek hisseye
    yığılma yok). Takip farkı tek hisse için değil, seçilen sepet için ölçülür."""
    b = cfg()["bist30"]
    pool = cands[:b["sepet_hisse_sayisi"]]
    n = len(pool)
    te = stock_basket_tracking_error([s.product.product_id.split(":", 1)[1] for s in pool], ec["pencere_gun"])
    limit = b["sepet_takip_farki_esik"]
    warn = None
    if te is None or te > limit:
        warn = (f"{n} hisselik sepetin BIST 30 eşit ağırlıklı sepetinden yıllık takip farkı "
                + ("ölçülemiyor" if te is None else f"%{te*100:.0f}") + f" (eşik %{limit*100:.0f}); model kategori sepetini varsaydı")
    notes = [f"'{cat}' için {warn}."] if warn else []
    if n < b["sepet_hisse_sayisi"]:
        notes.append(f"'{cat}' kategorisinde yalnızca {n} uygun hisse var; pay {n} hisseye bölündü (hisse başına %{wgt/n*100:.0f}).")
    rows = [{"kategori": cat, "kategori_agirligi": wgt, "agirlik": wgt / n, "product_id": s.product.product_id,
             "ad": s.product.name, "kisa_listede": bool(s.selected), "skor": round(s.score, 4),
             "takip_farki": None if te is None else round(te, 4), "takip_uyarisi": warn} for s in pool]
    return rows, notes


def map_to_funds(weights: dict[str, float], scored: list) -> tuple[list[dict], list[str]]:
    """Kategori ağırlıklarını ürünlere aktarır.
    - Adaylar: önce kısa listedekiler, sonra uygun listedekiler; her grup kendi içinde deterministik skora göre.
    - Takip farkı eşiğini (düşük oynaklıklı kategoride sıkı, oynak kategoride gevşek) aşan fonlar ancak başka aday yoksa seçilir
      ve RM'e uyarı düşer.
    - `bolme_esigi` ve üzeri kategori payı en iyi `bolme_fon_sayisi` fona eşit bölünür (tek fona yığılmayı önler)."""
    ec = cfg()["eslesme"]
    by_cat: dict[str, list] = {}
    for s in scored:
        by_cat.setdefault(PRODUCT_TO_CAT.get(s.product.category, s.product.category), []).append(s)
    mapping, notes = [], []
    for cat, wgt in sorted(weights.items(), key=lambda kv: -kv[1]):
        cands = sorted(by_cat.get(cat, []), key=lambda s: (not s.selected, -s.score))
        if not cands:
            notes.append(f"'{cat}' kategorisine %{wgt*100:.0f} ayrıldı ancak bu persona için uygun ürün yok; RM başka kategoriye aktarmalı.")
            mapping.append({"kategori": cat, "kategori_agirligi": wgt, "agirlik": wgt, "product_id": None, "ad": None,
                            "kisa_listede": False, "skor": None, "takip_farki": None, "takip_uyarisi": None})
            continue
        if cat == STOCK_CAT:
            rows, n_ = _map_stocks(cat, wgt, cands, ec)
            mapping += rows
            notes += n_
            continue
        codes = [s.product.product_id[4:] for s in cands if s.product.product_id.startswith("FON:")]
        te = tracking_errors(codes, cat, ec["pencere_gun"])
        limit = ec["takip_farki_esik"]["dusuk" if cat in ec["dusuk_oynaklik_kategorileri"] else "yuksek"]
        te_of = lambda s: te.get(s.product.product_id[4:]) if s.product.product_id.startswith("FON:") else None
        is_fund = lambda s: s.product.product_id.startswith("FON:")
        good = [s for s in cands if not is_fund(s) or (te_of(s) is not None and te_of(s) <= limit)]
        pool, warn = (good, None) if good else (cands, f"kategori endeksinden yıllık takip farkı eşiği (%{limit*100:.0f}) aşılıyor "
                                                       "veya ölçülemiyor; modelin varsaydığı kategori davranışından sapabilir")
        if warn:
            notes.append(f"'{cat}' için uygun ürünlerin hiçbiri kategoriyi yakın temsil etmiyor: {warn}.")
        n = ec["bolme_fon_sayisi"] if wgt >= ec["bolme_esigi"] - 1e-9 and len(pool) >= 2 else 1
        fund_max = decision_config()["rl"]["fon_basina_max_agirlik"]
        if any(is_fund(x) for x in pool[:1]):        # fon başına üst sınır: pay en az ceil(pay / sınır) fona bölünür
            need = int(np.ceil(wgt / fund_max - 1e-9))
            if need > len(pool):
                notes.append(f"'{cat}' payı %{wgt*100:.0f}; fon başına %{fund_max*100:.0f} sınırı için {need} fon gerekir, "
                             f"uygun {len(pool)} fon var. RM payı başka kategoriye aktarmalı.")
            n = max(n, min(need, len(pool)))
        for s in pool[:n]:
            t = te_of(s)
            proxy = s.product.category in PROXY_CAT
            w_note = warn
            if proxy:
                w_note = (f"model bu ürünü '{cat}' kategori endeksiyle temsil etti (vekil); ürünün kendi getiri davranışı "
                          "eğitimde görülmedi")
                notes.append(f"{s.product.name}: {w_note}.")
            mapping.append({"kategori": cat, "kategori_agirligi": wgt, "agirlik": wgt / n, "product_id": s.product.product_id,
                            "ad": s.product.name, "kisa_listede": bool(s.selected), "skor": round(s.score, 4),
                            "takip_farki": None if t is None else round(t, 4), "takip_uyarisi": w_note,
                            "vekil_kategori": proxy})
    return mapping, notes


def current_vector(current: dict[str, float] | None, cats: list[str] | None = None) -> tuple[np.ndarray, list[str]]:
    """RM'in girdiği mevcut dağılımı (kategori → oran) model durumuna çevirir. Boşsa nakit (sıfır vektör).
    Modelin kategori listesinde olmayan bir kategoriye pozitif pay girilirse hata (sıfır paylar yok sayılır)."""
    cats = cats or CATS_V1
    notes = []
    if not current:
        return np.zeros(len(cats)), ["Mevcut portföy dağılımı girilmedi; model nakitten başlangıç varsaydı (geçiş maliyeti hesaba katılmadı)."]
    unknown = [k for k, v in current.items() if k not in cats and float(v or 0) > 0]
    if unknown:
        raise ValueError(f"Modelin kategori listesinde olmayan kategori: {unknown} (model kategorileri: {', '.join(cats)})")
    v = np.array([max(0.0, float(current.get(c, 0.0))) for c in cats])
    tot = v.sum()
    if tot <= 0:
        return np.zeros(len(cats)), notes
    if abs(tot - 1) > 0.01:
        notes.append(f"Mevcut dağılımın toplamı %{tot*100:.0f}; %100'e ölçeklendi.")
    return v / tot, notes


def infer(persona, view, scored: list, current: dict[str, float] | None = None) -> dict:
    """Eğitilmiş profile koşullu modelle bugünkü piyasa koşullarında kategori dağılımı ve ürün eşlemesi.
    scored: uygun ürünlerin ScoredProduct listesi (skor sırasında; kısa listedekiler selected=True).
    current: RM'in girdiği mevcut kategori dağılımı (geçiş maliyeti ve mevcut pozisyon durumu için)."""
    net, meta = _load_model()
    if net is None:
        st = model_status()
        why = (f"Model eğitimi sürüyor ({st.get('guncelleme')}/{st.get('toplam')} güncelleme)." if st and not st.get("bitti")
               else "Eğitilmiş model yok (backend/tools/train_rl.py ile eğitilmeli).")
        return {"durum": "model_yok", "neden": why}
    ds = build_dataset(meta.get("gosterge_seti", "v1"))
    if ds["feature_names"] != meta["gosterge_adlari"]:
        return {"durum": "hata", "neden": "Modelin gösterge listesi güncel veriyle uyuşmuyor; model yeniden eğitilmeli."}
    mu, sd = np.array(meta["gosterge_ort"]), np.array(meta["gosterge_std"])
    # Karar, göstergelerinin tamamı hesaplanabilen en son tarihte verilir (eksik gösterge NaN → Q değerleri NaN olurdu)
    full = np.where(np.isfinite(ds["features_raw"]).all(axis=1))[0]
    if not len(full):
        return {"durum": "hata", "neden": "Göstergeleri tam hesaplanabilen tarih yok."}
    today = int(full[-1])
    raw = ds["features_raw"][today]
    z = (raw - mu) / sd
    date_note = ([f"Son veri tarihi {ds['today']}, ancak bazı göstergeler eksik; karar göstergeleri tam olan en son tarihe "
                  f"({ds['dates'][today].date()}) göre verildi."] if today != ds["today_index"] else [])
    A = np.array(meta["aksiyonlar"])
    CATS = meta.get("kategoriler") or CATS_V1                    # modelin kendi kategori listesi
    prof = profile_from_persona(persona, view)
    mask, why = profile_mask(prof, A, meta["kategori_rd"], CATS)
    if not mask.any():
        return {"durum": "hata", "neden": "Açık kısıtlar birlikte sağlanamıyor: izin verilen hiçbir dağılım yok. "
                                          f"Kapanan kategoriler: {'; '.join(why) or '-'}. RM kısıtları gözden geçirmeli."}
    t_cap = meta["rl"]["durum_max_adim"]

    clip = meta.get("ozellik_kirp")
    try:
        prev, cur_notes = current_vector(current, CATS)
    except ValueError as e:
        return {"durum": "hata", "neden": f"Mevcut dağılım bu modelle kullanılamıyor: {e}"}

    def state(zvec, prev_vec):
        zin = model_input(zvec, meta)                                  # eğitimdeki dönüşümün aynısı
        return np.concatenate([zin, profile_vector(prof, meta.get("profil_surumu", 1)), np.zeros(state_dims(meta)), prev_vec])[None, :]

    def decide(zvec):
        q = np.where(mask, net.forward(state(zvec, prev))[0], -np.inf)
        return int(np.argmax(q))

    a = decide(z)
    a_model = a
    model_weights = {CATS[j]: float(A[a][j]) for j in range(len(CATS)) if A[a][j] > 0}
    gate = apply_gate(meta, net.forward(state(z, prev))[0], a, A, mask, CATS)
    a = gate["aksiyon"] if gate else a
    weights = {CATS[j]: float(A[a][j]) for j in range(len(CATS)) if A[a][j] > 0}
    mapping, notes = map_to_funds(weights, scored)
    notes = date_note + cur_notes + notes
    turnover = float(0.5 * np.abs(A[a] - prev).sum() + 0.5 * (1 - prev.sum()))
    if current and not meta.get("mevcut_portfoy_egitimi"):
        notes.append("Bu model eğitimde başlangıçta hep nakit gördü; mevcut dağılıma verdiği tepki henüz eğitilmedi (yeniden eğitim bekleniyor).")
    ood = [{"ad": meta["gosterge_adlari"][k], "z": round(float(z[k]), 1)} for k in range(len(z))
           if np.isfinite(z[k]) and abs(z[k]) > decision_config()["rl"]["ood_esik_sigma"]]
    if ood:
        notes.append("Bugünkü göstergelerden bazıları eğitim döneminin dışında: "
                     + ", ".join(f"{o['ad']} ({o['z']:+.1f}σ)" for o in ood) + "; model bu bölgede ekstrapolasyon yapıyor.")
    sens = []
    for k, name in enumerate(meta["gosterge_adlari"]):
        for sgn in (-1, 1):
            zz = z.copy()
            zz[k] += sgn
            b = decide(zz)
            if b != a:
                sens.append({"gosterge": f"{name} {'−' if sgn < 0 else '+'}1σ",
                             "dagilim": {CATS[j]: float(A[b][j]) for j in range(len(CATS)) if A[b][j] > 0}})
    beliefs = belief_table(net, state(z, prev), CATS, mask, A)
    explanation = explain_decision(meta, raw, prof, model_weights, prev)        # EBM, modelin kendi tercihini açıklar
    test = meta.get("test", {}).get("personalar", {}).get(persona.persona_id)
    return {"durum": "tamam", "tarih": str(ds["dates"][today].date()), "kategori_dagilimi": weights, "urun_eslemesi": mapping,
            "beklenen_getiri": beliefs, "aciklama": explanation,
            "guvenli_iyilestirme": gate and {k: v for k, v in gate.items() if k != "aksiyon"},
            "mevcut_dagilim": {CATS[j]: float(prev[j]) for j in range(len(CATS)) if prev[j] > 0}, "devir": round(turnover, 3),
            "dislanan_kategoriler": why, "dagilim_disi": ood, "duyarlilik": sens, "notlar": notes,
            "gostergeler": [{"ad": n, "bugun": float(raw[k]), "egitim_ort": float(mu[k]), "egitim_std": float(sd[k]), "z": float(z[k])}
                            for k, n in enumerate(meta["gosterge_adlari"])],
            "persona_testi": test, "model": {k: meta.get(k) for k in ("olusturma", "sure_dk", "guncelleme", "en_iyi_guncelleme",
                                                                       "donem", "profil_sayisi", "kategori_rd", "model_turu",
                                                                       "model_adi", "egitim_verisi")},
            "genel_test": ({k: meta["test"][k] for k in ("egitim_profilleri", "gorulmemis_profiller") if k in meta["test"]}
                           if "gorulmemis_profiller" in (meta.get("test") or {}) else None)}


def load_gate(meta: dict) -> dict | None:
    """<model>/kapi.json (tools/calibrate_gate.py): doğrulamada seçilen güvenli iyileştirme eşiği. Başka model içinse yok sayılır."""
    path = MODEL_DIR / "kapi.json"
    if not path.exists():
        return None
    g = json.loads(path.read_text(encoding="utf-8"))
    return g if g.get("model_olusturma") == meta.get("olusturma") else None


def apply_gate(meta: dict, q: np.ndarray, a_model: int, A: np.ndarray, mask: np.ndarray, cats: list[str]) -> dict | None:
    """Güvenli iyileştirme: modelin önerisi, kendi Q tahminine göre taban kuraldan doğrulamada seçilen eşik kadar iyi
    değilse taban kural uygulanır. Q farkı modelin kendi değer tahminidir (ölçek: ödül × 10)."""
    g = load_gate(meta)
    if g is None:
        return None
    a_base = baseline_action(A, mask, cats)
    diff = float(q[a_model] - q[a_base])
    use_model = a_model != a_base and diff > g["delta"]
    w = lambda a: {cats[j]: float(A[a][j]) for j in range(len(cats)) if A[a][j] > 0}
    if a_model == a_base:
        why = "Modelin önerisi taban kuralla aynı."
    elif use_model:
        why = (f"Modelin önerisi kendi değer tahminine göre taban kuraldan {diff:.2f} puan iyi; doğrulamada seçilen eşik "
               f"{g['delta']:.2f}. Model önerisi kullanıldı.")
    else:
        why = (f"Modelin önerisi taban kuraldan yalnızca {diff:.2f} puan iyi (eşik {g['delta']:.2f}); fark güvenilir sayılmadı, "
               "taban kural (%50 para piyasası + %50 mevduat/borçlanma) uygulandı.")
    return {"aksiyon": a_model if use_model or a_model == a_base else a_base,
            "kaynak": "model" if use_model or a_model == a_base else "taban_kural",
            "model_tercihi": w(a_model), "taban": w(a_base), "q_farki": round(diff, 3), "esik": g["delta"],
            "gerekce": why, "dogrulama": g.get("ozet")}


def belief_table(net, x: np.ndarray, cats: list[str], mask: np.ndarray, A: np.ndarray) -> list[dict] | None:
    """İnanç başlığının bugünkü beklentisi, kategori başına (yüzde, 3 aylık vergi sonrası reel). Model inanç başlığı
    olmadan eğitildiyse None."""
    b = belief(net, x)
    if b is None:
        return None
    m, s = b[0][0], b[1][0]
    allowed = (A[mask] > 0).any(axis=0)
    return [{"kategori": c, "ortalama": round(float(m[j]) * 100, 2), "std": round(float(s[j]) * 100, 2),
             "izinli": bool(allowed[j])} for j, c in enumerate(cats)]


_EXPL_CACHE: dict = {}


def load_explainer(meta: dict) -> dict | None:
    """<model>/aciklama.json (tools/explain_rl.py). Başka bir model için üretilmişse kullanılmaz."""
    from . import rl_explain as rx
    path = MODEL_DIR / "aciklama.json"
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime)
    if key not in _EXPL_CACHE:
        _EXPL_CACHE.clear()
        _EXPL_CACHE[key] = rx.load(path)
    e = _EXPL_CACHE[key]
    return e if e and e.get("model_olusturma") == meta.get("olusturma") else None


def explain_decision(meta: dict, raw: np.ndarray, prof: dict, weights: dict, prev: np.ndarray) -> dict | None:
    from . import rl_explain as rx
    expl = load_explainer(meta)
    if expl is None:
        return None
    out = rx.explain(expl, rx.design_row(raw, prof), weights)
    if prev.sum() > 0:
        out["not"] = ("Vekil model nakitten başlangıç kararlarına damıtıldı; mevcut portföyün etkisi (geçiş maliyeti) "
                      "açıklamada yer almaz.")
    return out


def belief(net, x: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """İnanç başlığı: kategori başına sonraki 3 aylık adımın vergi sonrası reel getiri beklentisi (ortalama, std).
    rl_gpu.Net.belief'in numpy karşılığı (BSCALE=10, log std [-5, 2] kırpılır)."""
    if getattr(net, "belief_W", None) is None:
        return None
    _, acts = net.forward(x, keep=True)
    Wb, bb = net.belief_W
    o = acts[-2] @ Wb + bb
    C = o.shape[-1] // 2
    return o[..., :C] / 10.0, np.exp(np.clip(o[..., C:], -5.0, 2.0)) / 10.0


# --- Veri seti: adım getirileri, enflasyon, stopaj, göstergeler ----------------------------------

def _stopaj_matrix(start_dates: pd.DatetimeIndex, cats: list[str] | None = None) -> tuple[np.ndarray, list[str]]:
    """Blok başlangıç tarihine göre kategori stopajı (fon: ING tablosu tarihçesi; mevduat ve tabloda olmayanlar: varsayım;
    doğrudan BIST hisseleri: config `bist30.stopaj` varsayımı)."""
    CATS = cats or CATS_V1
    notes = []
    path = SNAPSHOT_DIR / "stopaj_tarihce.json"
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    from .sources import manual
    current = {s["kategori"]: s["oran"] for s in manual.load_raw().get("stopaj", []) if s.get("oran") is not None}
    T = np.zeros((len(start_dates), len(CATS)))
    for j, cat in enumerate(CATS):
        if cat == STOCK_CAT:
            b = cfg()["bist30"]
            T[:, j] = b["stopaj"]
            notes.append(f"{cat}: %{b['stopaj']*100:.0f} stopaj varsayıldı ({b['stopaj_notu']}).")
            continue
        if hist and cat in hist["kategoriler"]:
            rates = hist["kategoriler"][cat]
            col = np.full(len(start_dates), rates[-1])
            for k, per in enumerate(hist["donemler"]):
                lo = pd.Timestamp(per["baslangic"]) if per["baslangic"] else pd.Timestamp.min
                hi = pd.Timestamp(per["bitis"]) if per["bitis"] else pd.Timestamp.max
                col[(start_dates >= lo) & (start_dates <= hi)] = rates[k]
            T[:, j] = col
        else:
            fallback = current.get(cat, current.get("borclanma", 0.175))
            T[:, j] = fallback
            notes.append(f"{cat}: tarihsel stopaj yok, güncel/varsayılan %{fallback*100:.1f} tüm döneme uygulandı.")
    return T, notes


def _lagged(series: pd.Series, dates: pd.DatetimeIndex, lag_days: int) -> np.ndarray:
    s = series[~series.index.duplicated()].sort_index()
    q = dates - pd.Timedelta(days=lag_days)
    return s.reindex(s.index.union(q)).ffill().reindex(q).to_numpy()


def _cpi_points_from_hist() -> list:
    s = hist("tufe_endeks")
    return [[f"{d.year}-{d.month}", float(v)] for d, v in s.items()]


def _window_ok(gecerli: np.ndarray, step: int, n: int) -> np.ndarray:
    """Başlangıç i için: ileri pencere (i+1..i+step] ve geri gösterge penceresi (i-step+1..i] tamamen geçerli günlerden mi."""
    bad = np.concatenate([[0], np.cumsum(~gecerli)])          # bad[k] = ilk k gündeki geçersiz gün sayısı
    i = np.arange(n)
    fwd = bad[np.minimum(i + step + 1, len(gecerli))] - bad[i + 1]
    lo = np.maximum(i - step + 1, 0)
    back = bad[i + 1] - bad[lo]
    return (fwd == 0) & (back == 0) & (i - step + 1 >= 0)


def model_input(z: np.ndarray, meta: dict) -> np.ndarray:
    """Normalize gösterge → ağ girdisi, modelin eğitimindeki dönüşümle. sert (eski): ±c'de kesme; tanh: c·tanh(z/c) —
    monoton yumuşak sınır, uç değerler (ör. what-if şokları) birbirinden ayırt edilebilir kalır."""
    z = np.nan_to_num(z)
    c = meta.get("ozellik_kirp")
    if not c:
        return z
    return c * np.tanh(z / c) if meta.get("kirpma_turu") == "tanh" else np.clip(z, -c, c)


def build_dataset(feature_set: str | None = None, start: str | None = None, test_start: str | None = None,
                  test_by_end: bool = False) -> dict:
    """start: eğitim/doğrulama/test pencerelerinin en erken başlangıcı (veri kesilmez).
test_by_end: test havuzu = sonucu test başlangıcından sonraya uzanan tüm pencereler (eğitim/doğrulama pencereleri test
başlangıcından önce biter). Kısa bir son dönemi (ör. son 3 ay = tek adım) dışarıda tutmak için.
Adım getirileri, enflasyon, stopaj ve durum göstergeleri.

    feature_set:
      v1  önceki set (EVDS'den canlı çekim; 2023 sonrası). Mevcut modelin çıkarımı için korunur.
      v2  snapshots/gecmis/ tarihçe serileri (TCMB EVDS, 2021 sonrası): politika faizi karar tarihli adım serisi,
          TLREF, BIST 100 toplam getiri, mevduat faizi, TÜFE, USD/TRY. Başlangıç `veri_baslangic`.
    Veri boşluğu içeren pencereler havuzlardan çıkarılır ve raporlanır."""
    from .rl import _monthly_to_daily
    rc, rl_cfg = cfg(), decision_config()["rl"]
    fs = feature_set or rc.get("gosterge_seti", "v1")
    CATS = cats_for(fs)
    step, cal = rl_cfg["adim_is_gunu"], rl_cfg["adim_takvim_gunu"]
    ci = category_indices(deposit_source="gecmis" if fs != "v1" else "evds",
                          start=rc.get("veri_baslangic") if fs != "v1" else None, cats=CATS)
    level, dates = ci["level"], ci["level"].index
    L = level.to_numpy()
    n = len(dates) - step
    nominal = L[step:] / L[:-step] - 1
    dep_rate = ci["deposit_rate"].reindex(dates).ffill().to_numpy()
    nominal[:, 0] = dep_rate[:n] * cal / 365                     # 3 aylık mevduat: başlangıçta kilitlenen oran, basit faiz
    start_dates, end_dates = dates[:-step], dates[step:]
    tax, notes = _stopaj_matrix(start_dates, CATS)

    cpi_pts = _cpi_points_from_hist() if fs != "v1" else evds.fetch_history("TP.GENENDEKS.T1")["points"]
    cpi = _monthly_to_daily(cpi_pts, dates)
    c_on = cpi.reindex(dates).to_numpy()
    inflation = c_on[step:] / c_on[:-step] - 1

    feats, names = [], []
    daily = level.pct_change().to_numpy()
    for j, cat in enumerate(CATS):
        if cat == "mevduat_tl":
            continue
        tr = np.full(len(dates), np.nan)
        tr[step:] = L[step:, j] / L[:-step, j] - 1
        vol = pd.Series(daily[:, j]).rolling(step).std().to_numpy() * np.sqrt(252)
        feats += [tr, vol]
        names += [f"{cat} 3 ay getirisi", f"{cat} 3 ay oynaklığı"]
    if fs != "v1":
        usd_s = hist("usdtry")
    else:
        usd = evds.fetch_history("TP.DK.USD.A.YTL")["points"]
        usd_s = pd.Series({pd.to_datetime(p[0], format="%d-%m-%Y"): p[1] for p in usd})
    u_now, u_prev = _lagged(usd_s, dates, 1), _lagged(usd_s, dates - pd.Timedelta(days=cal), 1)
    feats.append(u_now / u_prev - 1)
    names.append("USD/TRY 3 ay değişimi")
    lag = rl_cfg["tufe_gecikme_gun"]
    k_now = cpi.reindex(dates - pd.Timedelta(days=lag)).to_numpy()
    k_3m = cpi.reindex(dates - pd.Timedelta(days=lag + cal)).to_numpy()
    k_1y = cpi.reindex(dates - pd.Timedelta(days=lag + 365)).to_numpy()
    infl_3m, infl_yoy = (k_now / k_3m) ** (365 / cal) - 1, k_now / k_1y - 1
    feats += [infl_3m, infl_yoy]
    names += ["TÜFE 3 ay eğilimi (yıllık)", "TÜFE yıllık"]
    if fs != "v1":
        d1_s = hist("mevduat_tl_1a")
    else:
        d1 = evds.fetch_history("TP.TRYTAS.MT01")["points"]
        d1_s = pd.Series({pd.to_datetime(p[0], format="%d-%m-%Y"): p[1] / 100 for p in d1})
    dr_now, dr_prev = _lagged(d1_s, dates, 7), _lagged(d1_s, dates - pd.Timedelta(days=cal), 7)
    feats += [dr_now, dr_now - dr_prev]
    names += ["Mevduat faizi 1 ay", "Mevduat faizi 3 ay değişimi"]
    if fs != "v1":
        pol_s = hist("politika_faizi_karar")                     # karar tarihinde açıklanır: 1 gün gecikme yeterli
        feats.append(_lagged(pol_s, dates, 1))
        names.append("Politika faizi (karar)")
        tl = hist("tlref")
        feats.append(_lagged(tl, dates, 1))
        names.append("TLREF")
        b = hist("bist100_getiri")
        b_now, b_prev = _lagged(b, dates, 1), _lagged(b, dates - pd.Timedelta(days=cal), 1)
        feats.append(b_now / b_prev - 1)
        names.append("BIST 100 toplam getiri 3 ay")
        x30 = bist_series("XU030", "kapanis")                    # fiyat endeksi (temettü hariç); Yahoo, resmi değil
        x_now, x_prev = _lagged(x30, dates, 1), _lagged(x30, dates - pd.Timedelta(days=cal), 1)
        feats.append(x_now / x_prev - 1)
        names.append("BIST 30 endeksi 3 ay")
    else:
        pol = evds.fetch_history("TP.BISPOLFAIZ.TUR")["points"]
        pol_s = pd.Series({pd.Timestamp(int(p[0].split("-")[0]), int(p[0].split("-")[1]), 1) + pd.offsets.MonthEnd(0): p[1] / 100
                           for p in pol})
        feats.append(_lagged(pol_s, dates, rc["politika_gecikme_gun"]))
        names.append("Politika faizi")
    feats.append(dr_now - infl_yoy)
    names.append("Reel mevduat faizi")
    if fs == "v3":
        # çok ufuklu zamansal göstergeler (hepsi karar anında bilinen, geriye bakan değerler)
        for j, cat in enumerate(CATS):
            if cat == "mevduat_tl":
                continue
            for lab, h in (("1 ay", 21), ("6 ay", 126), ("12 ay", 252)):
                tr = np.full(len(dates), np.nan)
                tr[h:] = L[h:, j] / L[:-h, j] - 1
                feats.append(tr)
                names.append(f"{cat} {lab} getirisi")
        for lab, dd in (("1 ay", 30), ("6 ay", 182), ("12 ay", 365)):
            feats.append(_lagged(usd_s, dates, 1) / _lagged(usd_s, dates - pd.Timedelta(days=dd), 1) - 1)
            names.append(f"USD/TRY {lab} değişimi")
            feats.append(_lagged(b, dates, 1) / _lagged(b, dates - pd.Timedelta(days=dd), 1) - 1)
            names.append(f"BIST 100 toplam getiri {lab}")
        for lab, dd in (("3 ay", cal), ("6 ay", 182), ("12 ay", 365)):
            feats.append(_lagged(pol_s, dates, 1) - _lagged(pol_s, dates - pd.Timedelta(days=dd), 1))
            names.append(f"Politika faizi {lab} değişimi")
        feats.append(_lagged(tl, dates, 1) - _lagged(tl, dates - pd.Timedelta(days=cal), 1))
        names.append("TLREF 3 ay değişimi")
        for lab, dd in (("6 ay", 182), ("12 ay", 365)):
            feats.append(dr_now - _lagged(d1_s, dates - pd.Timedelta(days=dd), 7))
            names.append(f"Mevduat faizi {lab} değişimi")
        k_6m = cpi.reindex(dates - pd.Timedelta(days=lag + 182)).to_numpy()
        feats.append((k_now / k_6m) ** (365 / 182) - 1)
        names.append("TÜFE 6 ay eğilimi (yıllık)")
        k_1y_prev = cpi.reindex(dates - pd.Timedelta(days=lag + cal + 365)).to_numpy()
        real_prev = dr_prev - (k_3m / k_1y_prev - 1)
        feats.append((dr_now - infl_yoy) - real_prev)
        names.append("Reel mevduat faizi 3 ay değişimi")
    F = np.column_stack(feats)

    ok_win = _window_ok(ci["gecerli"], step, n)
    has_feat = ~np.isnan(F[:n]).any(axis=1)
    valid = np.where(has_feat & ok_win)[0]
    if start:   # pencere başlangıç alt sınırı: göstergeler tüm geçmişten hesaplanır, yalnızca karar anları sınırlanır
        valid = valid[start_dates[valid] >= pd.Timestamp(start)]
    dropped = int((has_feat & ~ok_win).sum())
    if ci["bosluklar"]:
        notes.append(f"Fiyat verisinde {len(ci['bosluklar'])} boşluk var (en uzunu {max(g['gun'] for g in ci['bosluklar'])} gün); "
                     f"boşluk içeren {dropped} başlangıç penceresi dışlandı.")
    test_start = pd.Timestamp(test_start or rl_cfg["test_baslangic"])
    train_all = valid[end_dates[valid] < test_start]
    test = valid[end_dates[valid] >= test_start] if test_by_end else valid[start_dates[valid] >= test_start]
    cut = int(len(train_all) * (1 - rl_cfg["dogrulama_orani"]))
    train, val = train_all[:cut], train_all[cut:]
    if len(train) < 20 or len(val) < 5 or len(test) < 5:
        notes.append(f"Havuzlar küçük (eğitim {len(train)}, doğrulama {len(val)}, test {len(test)} başlangıç); sonuçlar güvenilir değil.")
    mu = F[train].mean(axis=0) if len(train) else np.nanmean(F, axis=0)
    sd = F[train].std(axis=0) if len(train) else np.nanstd(F, axis=0)
    sd[~np.isfinite(sd) | (sd < 1e-9)] = 1.0
    if STOCK_CAT in CATS:
        bm = bist_meta()
        notes.append(f"{STOCK_CAT}: {len(ci['members'].get(STOCK_CAT, []))} hissenin eşit ağırlıklı sepeti; kaynak "
                     f"{bm.get('kaynak', 'Yahoo Finance')}. Bileşim {bm.get('bilesim', {}).get('durum', '?')}; güncel bileşim "
                     "geçmişe uygulandığı için hayatta kalma yanlılığı var (geçmiş getiri olduğundan iyi görünebilir).")
    nominal_net = np.where(nominal > 0, nominal * (1 - tax), nominal)        # vergi sonrası nominal
    real = (1 + nominal_net) / (1 + inflation)[:, None] - 1
    span = lambda idx: [str(start_dates[idx[0]].date()), str(end_dates[idx[-1]].date())] if len(idx) else None
    return {"cats": CATS, "dates": dates, "step": step, "nominal": nominal, "nominal_net": nominal_net, "tax": tax,
            "inflation": inflation,
            "real": real, "features": (F - mu) / sd, "features_raw": F, "feature_names": names, "mu": mu, "sd": sd,
            "train": train, "val": val, "test": test, "today_index": len(dates) - 1, "today": str(dates[-1].date()),
            "rd": ci["rd"], "members": {k: len(v) for k, v in ci["members"].items()}, "fund_counts": ci["fund_counts"],
            "gosterge_seti": fs, "bosluklar": ci["bosluklar"], "dislanan_pencere": dropped,
            "period": {"baslangic": str(dates[0].date()), "bitis": str(dates[-1].date()),
                       "egitim": span(train), "dogrulama": span(val), "test": span(test)},
            "notes": notes}


# --- Sentetik profil parametreleri ve maskeler ---------------------------------------------------

RISKS = ["Muhafazakâr", "Temkinli", "Dengeli", "Agresif"]
LEVELS = ["Düşük", "Orta", "Yüksek"]
FX_LEVELS = [0.2, 0.4, 0.6, 1.0]
HORIZONS = [0.5, 1.0, 3.0, 5.0]
FLAGS = ["yuksek_risk_yasagi", "kilit", "doviz_artirmama", "yogunlasma"]


def sample_profiles(n: int, seed: int) -> list[dict]:
    """Çelişkisiz profil parametresi örnekleri (profile.py'nin çelişki kurallarına uyar). Müşteri/persona değildir."""
    rng = np.random.default_rng(seed)
    risk_cfg = decision_config()["risk_profilleri"]
    ana_p = {"Muhafazakâr": [0, .3, .7], "Temkinli": [.1, .4, .5], "Dengeli": [.2, .6, .2], "Agresif": [.6, .4, 0]}
    out = []
    while len(out) < n:
        risk = RISKS[rng.integers(4)]
        ana = LEVELS[rng.choice(3, p=ana_p[risk])]
        lik = LEVELS[rng.integers(3)]
        p = {"risk_profili": risk, "max_rd": risk_cfg[risk]["max_rd"], "seviye": risk_cfg[risk]["seviye"],
             "ufuk_yil": float(rng.choice(HORIZONS, p=[.15, .4, .25, .2])), "anapara": ana, "likidite": lik,
             "fx": float(rng.choice(FX_LEVELS)),
             "yuksek_risk_yasagi": bool(risk in ("Muhafazakâr", "Temkinli") and rng.random() < .3),
             "kilit": bool(lik != "Düşük" and rng.random() < .2),
             "doviz_artirmama": bool(rng.random() < .15), "yogunlasma": bool(rng.random() < .2)}
        if p["ufuk_yil"] < 1 and risk == "Agresif":
            continue
        p["id"] = f"S{len(out):05d}"
        out.append(p)
    return out


def enumerate_profiles(full: bool = False) -> tuple[list[dict], dict]:
    """Brief'te paylaşılan 5 personanın alan değerleri ve açık kısıtlarından çıkan TÜM kombinasyonlar.

    Her kombinasyon sistemin persona yorumlayıcısından (profile.interpret) geçirilir; çelişkili olanlar atılır.
    RL'i etkilemeyen alanlar (gelir ihtiyacı, 'kısa vadeli nakit' kısıtı yalnızca skorlamayı etkiler) birleştirilerek
    RL açısından farklı profiller tekilleştirilir. Yeni değer uydurulmaz; yalnızca verilen değerler birleştirilir."""
    import glob
    import itertools

    from .config import PERSONA_DIR
    from .models import Persona
    from .profile import interpret
    P = [json.loads(open(f, encoding="utf-8").read()) for f in sorted(glob.glob(str(PERSONA_DIR / "*.json")))]
    vals = {k: sorted({p[k] for p in P}) for k in ("risk_profili", "yatirim_ufku", "likidite_ihtiyaci",
                                                   "gelir_nakit_akisi_ihtiyaci", "anapara_koruma_tercihi", "doviz_maruz_kalma_tercihi")}
    kis = sorted({k for p in P for k in p["acik_kisitlar"]})
    subsets = [s for n in range(len(kis) + 1) for s in itertools.combinations(kis, n)]
    # tam sürüm: öncelikler ve yatırım amacı da profile girer; brief'teki 5 personanın (öncelikler, amaç) demetlerinden biri
    bundles = [(tuple(p["oncelikler"]), p["yatirim_amaci"]) for p in P] if full else [(("-",), "-")]
    seen, out, raw, ok = set(), [], 0, 0
    for r, u, l, g, a, d in itertools.product(*vals.values()):
        for ks in subsets:
            for bi, (prios, goal) in enumerate(bundles):
                raw += 1
                persona = Persona(persona_id="K", risk_profili=r, yatirim_ufku=u, likidite_ihtiyaci=l, gelir_nakit_akisi_ihtiyaci=g,
                                  anapara_koruma_tercihi=a, doviz_maruz_kalma_tercihi=d, oncelikler=list(prios), yatirim_amaci=goal,
                                  acik_kisitlar=list(ks))
                view, _ = interpret(persona, {})
                if view is None:
                    continue
                ok += 1
                prof = profile_from_persona(persona, view)
                key = (prof["risk_profili"], prof["ufuk_yil"], prof["anapara"], prof["likidite"], prof["fx"]) + tuple(prof[f] for f in FLAGS)
                if full:
                    key += (prof["gelir"], prof["nakit_kisiti"], bi)
                if key in seen:
                    continue
                seen.add(key)
                prof["id"] = f"K{len(out):05d}" if full else f"K{len(out):04d}"
                prof["kaynak"] = {"ufuk": u, "doviz": d, "kisitlar": list(ks)} | ({"oncelik_demeti": bi} if full else {})
                out.append(prof)
    stats = {"alan_degerleri": {k: v for k, v in vals.items()}, "kisitlar": kis, "ham": raw, "celiskisiz": ok, "rl_farkli": len(out)}
    return out, stats


LEGACY_RULES = {"K_YUKSEK_RISK", "K_LIKIDITE_KILIDI", "K_DOVIZ_ARTIRMAMA", "K_YOGUNLASMA", "K_KISA_VADE_NAKIT_YOK"}


def catalog_rules() -> list[dict]:
    """Brief'teki 5 kısıta ek katalog kısıtları (config/constraint_rules.json, 'rl' alanı olan kurallar), sabit sırada."""
    from .config import constraint_rules
    return [r for r in constraint_rules()["kurallar"] if r["id"] not in LEGACY_RULES and "rl" in r]


def catalog_param_value(rule: dict, prm: dict) -> float:
    """Profil vektörü için kısıtın sayısal parametresi (0–1 ölçeği)."""
    if "oran" in prm:
        return float(prm["oran"])
    if "min_kategori" in prm:
        return prm["min_kategori"] / 5
    if "max_mevduat_vade_gun" in prm:
        return min(1.0, prm["max_mevduat_vade_gun"] / 92)
    return 0.0


RL_CRITERIA = ["getiri", "dusuk_oynaklik", "likidite", "maliyet", "gelir", "anapara", "doviz_uyumu", "sadelik"]


def profile_weights(persona, view) -> dict[str, float]:
    """Profilin tamamından kriter ağırlıkları: deterministik skorlamanın ağırlıkları (scoring.weights; risk, ufuk, likidite,
    gelir ihtiyacı, anapara, öncelikler, ağırlık kısıtları) — yatırım amacı metni de aynı öncelik kalıplarından geçirilir.
    Ek olarak RL'e özel 'cesitlendirme' (brief önceliği; skorlamada karşılığı yok): öncelik/amaçta 'çeşitlendir' başına 0,5."""
    from .config import norm_text
    from .scoring import weights
    prios = list(persona.oncelikler or []) + ([persona.yatirim_amaci] if persona.yatirim_amaci else [])
    W = weights(persona.model_copy(update={"oncelikler": prios}), view)
    out = {k: float(W[k]["weight"]) for k in RL_CRITERIA}
    out["cesitlendirme"] = float(min(1.0, 0.5 * sum("cesitlendir" in norm_text(x).replace("ç", "c").replace("ş", "s") or
                                                     "çeşitlendir" in norm_text(x) for x in prios)))
    return out


def profile_from_persona(persona, view) -> dict:
    ids = {r["id"] for r in view.rules}
    return {"id": persona.persona_id, "risk_profili": persona.risk_profili, "max_rd": view.max_rd, "seviye": view.risk_level,
            "ufuk_yil": view.horizon_years, "anapara": persona.anapara_koruma_tercihi or "Orta",
            "likidite": persona.likidite_ihtiyaci or "Orta", "fx": view.fx_pref,
            "yuksek_risk_yasagi": "K_YUKSEK_RISK" in ids, "kilit": "K_LIKIDITE_KILIDI" in ids,
            "doviz_artirmama": "K_DOVIZ_ARTIRMAMA" in ids, "yogunlasma": "K_YOGUNLASMA" in ids,
            # profil sürüm 2 alanları (state ve ödül için profilin tamamı)
            "gelir": persona.gelir_nakit_akisi_ihtiyaci or "Orta",
            "nakit_kisiti": any(r["tip"] == "agirlik" for r in view.rules),
            "agirlik": profile_weights(persona, view),
            # katalog kısıtları (sürüm 3): kural kimliği, RL karşılığı ve metinden çıkarılan parametre
            "ek_kisit": [{"id": r["id"], "rl": r["rl"], "parametre": r.get("parametre", {})} for r in view.rules
                         if r["id"] not in LEGACY_RULES and "rl" in r]}


def profile_vector(p: dict, version: int = 1) -> np.ndarray:
    """Sürüm 1 (eski modeller): risk, RD sınırı, ufuk, anapara, likidite, döviz, 4 kısıt bayrağı (10 boyut).
    Sürüm 2: + gelir ihtiyacı, 'kısa vadeli nakit' kısıtı, 8 kriter ağırlığı, çeşitlendirme (21 boyut) — profilin tamamı."""
    lv = {"Düşük": 0.2, "Orta": 0.5, "Yüksek": 1.0}
    v1 = [p["seviye"], p["max_rd"] / 7, np.log1p(p["ufuk_yil"]) / np.log1p(5), lv[p["anapara"]],
          lv[p["likidite"]], p["fx"]] + [float(p[f]) for f in FLAGS]
    if version == 1:
        return np.array(v1)
    w = p["agirlik"]
    v2 = v1 + [lv[p["gelir"]], float(p["nakit_kisiti"])] + [w[k] for k in RL_CRITERIA] + [w["cesitlendirme"]]
    if version == 2:
        return np.array(v2)
    # sürüm 3: katalog kısıtları — kural başına bayrak + parametre (yoksa 0)
    have = {e["id"]: e for e in p.get("ek_kisit", [])}
    flags, params = [], []
    for r in catalog_rules():
        e = have.get(r["id"])
        flags.append(1.0 if e else 0.0)
        params.append(catalog_param_value(r, e["parametre"]) if e else 0.0)
    return np.array(v2 + flags + params)


def _compositions(units: int, caps: list[int], max_nonzero: int) -> list[tuple[int, ...]]:
    """Toplamı `units` olan, i. bileşeni caps[i]'yi aşmayan ve en fazla `max_nonzero` sıfırdan farklı bileşeni olan
    tüm tamsayı vektörleri (özyinelemeli; tam çarpımı dolaşmaz)."""
    out, cur = [], [0] * len(caps)

    def rec(i, rem, nz):
        if i == len(caps):
            if rem == 0:
                out.append(tuple(cur))
            return
        for u in range(0, min(caps[i], rem) + 1):
            if u > 0 and nz == max_nonzero:
                break
            cur[i] = u
            rec(i + 1, rem - u, nz + (u > 0))
        cur[i] = 0

    rec(0, units, 0)
    return out


def category_caps(cats: list[str]) -> np.ndarray:
    """Kategori başına üst ağırlık. Mevduat sınırsız; düşük oynaklıklı fon kategorileri (para piyasası, borçlanma, katılım)
    `dusuk_oynaklik_max_agirlik` (fon başına %50 sınırı eşlemede en az iki fona bölünerek sağlanır); oynak kategoriler (kıymetli maden, hisse, karma, döviz, ...) `kategori_max_agirlik` (%40).
    Oynak kategoride tek bahsin rejim dönünce yarattığı kaybı sınırlar (v5/NN-PPO teşhisi: %50 altın); kısıtlı profillerde
    (örn. kilitli muhafazakâr) dağılımın yine %100'e tamamlanabilmesi için düşük oynaklıklılar %50'de kalır."""
    rl_cfg, c = decision_config()["rl"], cfg()
    low = set(c["eslesme"]["dusuk_oynaklik_kategorileri"])
    fund_max = rl_cfg["fon_basina_max_agirlik"]
    low_max = c.get("dusuk_oynaklik_max_agirlik", fund_max)
    risky_max = c.get("kategori_max_agirlik", fund_max)
    return np.array([1.0 if k == "mevduat_tl" else low_max if k in low else risky_max for k in cats])


def action_grid(cats: list[str] | None = None) -> np.ndarray:
    """Aksiyon kümesi: ince ızgara (örn. %10 adım, en fazla 3 kategori) ∪ kaba ızgara (%25 adım, kategori sayısı serbest).
    Kaba ızgara korunur; böylece ince ızgaraya geçişte daha önce seçilebilen hiçbir dağılım kaybolmaz.
    Kategori üst sınırları category_caps ile (kaba ızgaranın sınırı aşan satırları atılır)."""
    from .rl import action_grid as grid
    rl_cfg, g = decision_config()["rl"], cfg()["izgara"]
    CATS = cats or CATS_V1
    fund = np.array([c != "mevduat_tl" for c in CATS])
    cap_w = category_caps(CATS)
    units = round(1 / g["adim"])
    caps = [int(round(w * units + 1e-9)) for w in cap_w]
    fine = np.array(_compositions(units, caps, g["max_kategori"]), dtype=float) / units
    coarse = grid(len(CATS), rl_cfg["izgara"], None, fund, rl_cfg["fon_basina_max_agirlik"]) if g.get("kaba_izgarayi_koru", True)         else np.zeros((0, len(CATS)))
    coarse = coarse[(coarse <= cap_w + 1e-9).all(axis=1)]
    allA = np.vstack([fine, coarse])
    _, idx = np.unique(np.round(allA, 6), axis=0, return_index=True)
    return allA[np.sort(idx)]


def profile_mask(p: dict, actions: np.ndarray, rd: dict, cats: list[str] | None = None) -> tuple[np.ndarray, list[str]]:
    """Profile göre izinli aksiyonlar ve kategori dışlama gerekçeleri."""
    CATS = cats or _cats_of(actions)
    kat = decision_config()["kategoriler"]
    allowed, why = np.ones(len(CATS), bool), []
    for j, cat in enumerate(CATS):
        reason = None
        if rd[cat] > p["max_rd"]:
            reason = f"RD {rd[cat]} > profil sınırı {p['max_rd']}"
        elif p["yuksek_risk_yasagi"] and (rd[cat] >= 5 or cat in EQUITY_CATS):
            reason = "yüksek riskli ürün yasağı"
        elif p["kilit"] and (cat == "mevduat_tl" or (kat.get(cat, {}).get("valor_gun") or 0) > 2):
            reason = "kilitli/düşük likidite yasağı"
        elif p["doviz_artirmama"] and cat in FX_CATS:
            reason = "döviz maruziyetini artırmama"
        if reason:
            allowed[j] = False
            why.append(f"{cat}: {reason}")
    # katalog kısıtları: kategori düzeyi
    for e in p.get("ek_kisit", []):
        rl, prm = e["rl"], e["parametre"]
        for j, cat in enumerate(CATS):
            hit = (cat in rl.get("haric", [])) or ("yalniz" in rl and cat not in rl["yalniz"]) or \
                  (cat in rl.get("haric_vade_asarsa", {}) and prm.get("max_mevduat_vade_gun", 1e9) < rl["haric_vade_asarsa"][cat])
            if hit and allowed[j]:
                allowed[j] = False
                why.append(f"{cat}: katalog kısıtı {e['id']}")
    fxi = np.array([c in FX_CATS for c in CATS])
    fx_cap = cfg()["doviz_tavan"][str(float(p["fx"]))]
    m = (actions[:, ~allowed].sum(axis=1) < 1e-9) & (actions[:, fxi].sum(axis=1) <= fx_cap + 1e-9)
    if p["yogunlasma"]:
        m &= actions.max(axis=1) <= decision_config()["rl"]["yogunlasma_max_agirlik"] + 1e-9
    # katalog kısıtları: portföy düzeyi (grup payı, kategori payı, kategori sayısı)
    for e in p.get("ek_kisit", []):
        rl, prm = e["rl"], e["parametre"]
        if "max_toplam" in rl:
            g = rl["max_toplam"]
            grp = np.array([(c in g.get("kategoriler", [])) or ("min_rd" in g and rd[c] >= g["min_rd"]) for c in CATS])
            m &= actions[:, grp].sum(axis=1) <= prm.get("oran", 1.0) + 1e-9
        if "min_toplam" in rl:
            grp = np.array([c in rl["min_toplam"]["kategoriler"] for c in CATS])
            m &= actions[:, grp].sum(axis=1) >= prm.get("oran", 0.0) - 1e-9
        if rl.get("max_kategori_payi"):
            m &= actions.max(axis=1) <= prm.get("oran", 1.0) + 1e-9
        if rl.get("min_kategori_sayisi"):
            m &= (actions > 1e-9).sum(axis=1) >= prm.get("min_kategori", 1)
    return m, why


# --- Profile koşullu DQN -------------------------------------------------------------------------

def nominal_mode() -> bool:
    """Anapara cezası nominal servete mi bağlı (config rl_kategori.anapara_tabani)."""
    return cfg().get("anapara_tabani", "reel") == "nominal"


def state_dims(meta: dict | None = None) -> int:
    """Gösterge ve profil dışındaki skaler durum boyutu: süre, reel servet (+ nominal modda nominal servet).
    meta verilirse modelin eğitildiği değer (eski modeller: 2)."""
    if meta is not None:
        return meta.get("durum_boyutu", 2)
    return 3 if nominal_mode() else 2


class ProfileDQN:
    SCALE = 10.0  # ödül ölçeği (sayısal kararlılık)

    def __init__(self, ds: dict, actions: np.ndarray, seed: int):
        from .rl import MLP
        c = cfg()
        self.ds, self.A = ds, actions
        self.nominal = nominal_mode()
        self.d_in = ds["features"].shape[1] + len(profile_vector(sample_profiles(1, 0)[0])) + state_dims() + len(ds["cats"])
        rng = np.random.default_rng(seed)
        self.net = MLP(self.d_in, c["gizli"], len(actions), rng, c["ogrenme_orani"])
        self.target = MLP(self.d_in, c["gizli"], len(actions), rng, c["ogrenme_orani"])
        self.target.copy_from(self.net)

    def x(self, pvec, t, w, idx, prev, feats=None) -> np.ndarray:
        """w: (B, 2) servet = [reel, nominal]; nominal sütun yalnızca nominal modda girdidir."""
        F = self.ds["features"][idx] if feats is None else feats
        t_cap = decision_config()["rl"]["durum_max_adim"]
        wealth = [(w[:, 0] - 1) * 5] + ([(w[:, 1] - 1) * 5] if self.nominal else [])
        return np.column_stack([F, pvec, np.minimum(t, t_cap) / t_cap, *wealth, prev])

    def q(self, X, mask) -> np.ndarray:
        return np.where(mask, self.net.forward(X), -np.inf)


def _horizon(p: dict) -> tuple[int, int]:
    uc = decision_config()["rl"]["ufuk_ceyrek"]
    key = min((k for k in uc if not k.startswith("_")), key=lambda k: abs(float(k) - p["ufuk_yil"]))
    return tuple(uc[key])


def _penalty(p: dict) -> tuple[float, float]:
    c = decision_config()["rl"]["ceza"][p["anapara"]]
    return c["lambda"], c["sabit"]


class Sim:
    """Profil kümesi üzerinde vektörel ortam (çok sayıda bölüm aynı anda)."""

    def __init__(self, ds, actions, profiles, masks, nominal: bool | None = None):
        rl_cfg = decision_config()["rl"]
        self.ds, self.A, self.P, self.M = ds, actions, profiles, masks
        self.pvec = np.array([profile_vector(p) for p in profiles])
        hz = np.array([_horizon(p) for p in profiles])
        self.hmin, self.hmax = hz[:, 0], hz[:, 1]
        pen = np.array([_penalty(p) for p in profiles])
        self.lam, self.fixed = pen[:, 0], pen[:, 1]
        self.cost = rl_cfg["degistirme_maliyeti"]
        self.step = ds["step"]
        self.L = rl_cfg["blok_ceyrek"]
        self.nominal = nominal_mode() if nominal is None else nominal     # model/deney bazında geçersiz kılınabilir
        self.pen_cap = cfg().get("ceza_tavan") or np.inf

    def p_continue(self, pid, t):
        lo, hi = self.hmin[pid], self.hmax[pid]
        alive = lambda k: np.where(k < lo, hi - lo + 1, np.maximum(0, hi - np.maximum(k, lo) + 1))
        a_now, a_next = alive(t + 1), alive(t + 2)
        return np.where(a_now > 0, a_next / np.maximum(a_now, 1), 0.0)

    def next_idx(self, idx, pool_member, pool, rng):
        nxt = idx + self.step
        inside = nxt < len(pool_member)
        ok = inside & pool_member[np.minimum(nxt, len(pool_member) - 1)] & (rng.random(len(idx)) >= 1 / self.L)
        return np.where(ok, nxt, rng.choice(pool, size=len(idx)))

    def outcome(self, pid, w, prev, idx, acts):
        """acts: (B,) veya (B,K) aksiyon indeksleri → yeni servet [reel, nominal], ödül, sürüklenmiş dağılım.

        Ödül = reel servet değişimi − anapara cezası. Ceza tabanı config'e göre reel ya da nominal servettir
        (nominal: müşterinin "anaparam korunsun" ifadesinin olağan anlamı; reel getiri yine amaçtır). Adım başına
        ceza `ceza_tavan` ile sınırlanır: uzun negatif reel faiz dönemlerinde birikip Q ölçeğini şişirmesin."""
        r, rn = self.ds["real"][idx], self.ds["nominal_net"][idx]
        Wt = self.A[acts]
        if Wt.ndim == 3:
            r_, rn_, w_, prev_ = r[:, None, :], rn[:, None, :], w[:, None, :], prev[:, None, :]
            lam, fixed = self.lam[pid][:, None], self.fixed[pid][:, None]
        else:
            r_, rn_, w_, prev_, lam, fixed = r, rn, w, prev, self.lam[pid], self.fixed[pid]
        cash = 1 - prev_.sum(axis=-1)
        turnover = 0.5 * np.abs(Wt - prev_).sum(axis=-1) + 0.5 * cash
        wr, wn = w_[..., 0], w_[..., 1]
        wr_new = wr * (1 + (Wt * r_).sum(axis=-1)) - wr * self.cost * turnover
        wn_new = wn * (1 + (Wt * rn_).sum(axis=-1)) - wn * self.cost * turnover
        base = wn_new if self.nominal else wr_new
        pen = np.where(base < 1, np.minimum(lam * (1 - base) + fixed, self.pen_cap), 0.0)
        reward = (wr_new - wr) - pen
        d = Wt * (1 + r_)
        d = d / np.clip(d.sum(axis=-1, keepdims=True), 1e-9, None)
        return np.stack([wr_new, wn_new], axis=-1), reward, d


def baseline_action(A: np.ndarray, mask_row: np.ndarray, cats: list[str]) -> int:
    """Sabit taban kural: %50 para piyasası + %50 mevduat (mevduat izinli değilse borçlanma); profil maskesindeki en yakın
    izinli aksiyon. Güvenli iyileştirme kapısı ve değerlendirmedeki 'pp_agirlikli' kıyası bunu kullanır."""
    ok = np.where(mask_row)[0]
    goal = np.zeros(len(cats))
    goal[cats.index("para_piyasasi")] = 0.5
    second = "mevduat_tl" if (A[ok][:, cats.index("mevduat_tl")] > 0).any() else "borclanma"
    goal[cats.index(second)] = 0.5
    return int(ok[np.argmin(np.abs(A[ok] - goal).sum(axis=1))])


class LoadedPolicy:
    """Kaydedilmiş (numpy) ağı rl_category.evaluate arayüzüne uyarlar. Göstergeler modelin kendi normalizasyonu ve
    kırpmasıyla verilir (ds['features'] üzerine yazılır); durum boyutu modelin meta'sından."""
    SCALE = 10.0

    def __init__(self, net, meta: dict, ds: dict):
        mu, sd = np.array(meta["gosterge_ort"]), np.array(meta["gosterge_std"])
        ds["features"] = model_input((ds["features_raw"] - mu) / sd, meta)
        self.net, self.ds, self.nominal = net, ds, state_dims(meta) == 3

    def x(self, pvec, t, w, idx, prev):
        t_cap = decision_config()["rl"]["durum_max_adim"]
        wealth = [(w[:, 0] - 1) * 5] + ([(w[:, 1] - 1) * 5] if self.nominal else [])
        return np.column_stack([self.ds["features"][idx], pvec, np.minimum(t, t_cap) / t_cap, *wealth, prev])

    def q(self, X, mask):
        return np.where(mask, self.net.forward(X), -np.inf)


def eval_horizon(T):
    """Değerlendirmede bölüm uzunluğu tavanı (config `degerlendirme_max_ceyrek`). Kısa bir test/doğrulama penceresinde uzun
    bölümler aynı çeyrekleri tekrar tekrar örnekleyip tek bir rejimi onlarca kez bileşikler; sonuçlar (iyi ya da kötü)
    abartılır. Rastgele çekiliş sırası değişmez: aynı tohum aynı senaryoları verir. Eğitim bölümleri tam ufuktadır."""
    cap = cfg().get("degerlendirme_max_ceyrek")
    if not cap:
        return T
    try:
        import torch
        if isinstance(T, torch.Tensor):
            return torch.clamp(T, max=cap)
    except ImportError:
        pass
    return np.minimum(T, cap)


def _random_valid(mask_rows: np.ndarray, k: int, rng) -> np.ndarray:
    s = rng.random(mask_rows.shape)
    s[~mask_rows] = -1
    k = min(k, s.shape[1])
    return np.argpartition(-s, kth=k - 1, axis=1)[:, :k]


def train(model: ProfileDQN, sim: Sim, train_ids: np.ndarray, pool: np.ndarray, updates: int, rng,
          on_eval=None, eval_every: int = 5000, log=print, start_update: int = 0) -> list[dict]:
    """Paralel bölümlerle DQN eğitimi. Her güncellemede: tüm paralel bölümler bir adım ilerler (ε-greedy, maskeli),
    tampondan B örnek alınır, her örnek için alınan aksiyon + K−1 rastgele izinli aksiyonun hedefi hesaplanır."""
    c = cfg()
    E, B, K, sync = c["paralel_bolum"], c["batch"], c["hedef_aksiyon"], c["hedef_senkron"]
    member = np.zeros(len(sim.ds["real"]) + sim.step + 1, bool)
    member[pool] = True
    cap = c["tampon"]
    buf = {"pid": np.zeros(cap, int), "t": np.zeros(cap, int), "w": np.zeros((cap, 2)), "idx": np.zeros(cap, int),
           "prev": np.zeros((cap, len(sim.ds["cats"]))), "a": np.zeros(cap, int)}
    n_buf, head = 0, 0
    pid = rng.choice(train_ids, size=E)
    T = rng.integers(sim.hmin[pid], sim.hmax[pid] + 1)
    p_cur = c.get("mevcut_portfoy_olasiligi", 0.0)

    def start_prev(n):  # GPU sürümüyle aynı: bölümlerin bir kısmı rastgele mevcut portföyle başlar
        rnd = sim.A[rng.integers(len(sim.A), size=n)]
        return np.where((rng.random(n) < p_cur)[:, None], rnd, 0.0)

    t, w, idx, prev = np.zeros(E, int), np.ones((E, 2)), rng.choice(pool, size=E), start_prev(E)
    ep_rew, done_rewards, losses, curve = np.zeros(E), [], [], []
    total_updates = start_update + updates
    for u in range(start_update, total_updates):
        eps = max(c["eps_son"], 1.0 - u / (c["eps_azalma_orani"] * total_updates))
        X = model.x(sim.pvec[pid], t, w, idx, prev)
        greedy = np.argmax(model.q(X, sim.M[pid]), axis=1)
        rnd = _random_valid(sim.M[pid], 1, rng)[:, 0]
        a = np.where(rng.random(E) < eps, rnd, greedy)
        sl = (np.arange(E) + head) % cap
        for k_, v in (("pid", pid), ("t", t), ("w", w), ("idx", idx), ("prev", prev), ("a", a)):
            buf[k_][sl] = v
        head, n_buf = (head + E) % cap, min(cap, n_buf + E)
        w2, r, d = sim.outcome(pid, w, prev, idx, a)
        ep_rew += r
        t, w, prev = t + 1, w2, d
        idx = sim.next_idx(idx, member, pool, rng)
        done = t >= T
        if done.any():
            done_rewards += ep_rew[done].tolist()
            nd = int(done.sum())
            pid[done] = rng.choice(train_ids, size=nd)
            T[done] = rng.integers(sim.hmin[pid[done]], sim.hmax[pid[done]] + 1)
            t[done], w[done], ep_rew[done] = 0, 1.0, 0.0
            prev[done] = start_prev(nd)
            idx[done] = rng.choice(pool, size=nd)
        if n_buf >= B:
            j = rng.integers(n_buf, size=B)
            bp, bt, bw, bi, bprev, ba = (buf[k_][j] for k_ in ("pid", "t", "w", "idx", "prev", "a"))
            acts = np.column_stack([ba, _random_valid(sim.M[bp], K - 1, rng)])
            wn, rw, dn = sim.outcome(bp, bw, bprev, bi, acts)
            ni = sim.next_idx(bi, member, pool, rng)
            Xn = model.x(np.repeat(sim.pvec[bp], K, axis=0), np.repeat(bt + 1, K), wn.reshape(-1, 2),
                         np.repeat(ni, K), dn.reshape(-1, len(sim.ds["cats"])))
            qn = np.where(np.repeat(sim.M[bp], K, axis=0), model.target.forward(Xn), -np.inf).max(axis=1)
            qn = qn.reshape(B, K) / model.SCALE
            y = (rw + sim.p_continue(bp, bt)[:, None] * qn) * model.SCALE
            y_full, msk = np.zeros((B, len(model.A))), np.zeros((B, len(model.A)), bool)
            rows = np.repeat(np.arange(B), K)
            y_full[rows, acts.ravel()] = y.ravel()
            msk[rows, acts.ravel()] = True
            losses.append(model.net.step_masked(model.x(sim.pvec[bp], bt, bw, bi, bprev), y_full, msk))
            if (u + 1) % sync == 0:
                model.target.copy_from(model.net)
        if (u + 1) % eval_every == 0:
            row = {"guncelleme": u + 1, "eps": round(eps, 3),
                   "ortalama_bolum_odulu": round(float(np.mean(done_rewards[-2000:])) if done_rewards else 0.0, 4),
                   "kayip": round(float(np.mean(losses[-eval_every:])) if losses else 0.0, 5)}
            if on_eval:
                row.update(on_eval(u + 1))
            curve.append(row)
            log(row)
    return curve


def evaluate(model: ProfileDQN | None, sim: Sim, ids: np.ndarray, pool: np.ndarray, n_per: int, rng, policy: str = "dqn",
             delta: float | None = None) -> dict:
    """Aynı senaryolarda politika değerlendirmesi. policy: dqn | esit | en_guvenli | pp_agirlikli | kapili.
    kapili: güvenli iyileştirme — DQN'in seçtiği aksiyonun Q değeri taban kuralınkini `delta`dan fazla aşmıyorsa taban kural."""
    member = np.zeros(len(sim.ds["real"]) + sim.step + 1, bool)
    member[pool] = True
    pid = np.repeat(ids, n_per)
    E = len(pid)
    T = eval_horizon(rng.integers(sim.hmin[pid], sim.hmax[pid] + 1))
    idx = rng.choice(pool, size=E)
    CATS = sim.ds["cats"]
    t, w, prev = np.zeros(E, int), np.ones((E, 2)), np.zeros((E, len(CATS)))
    total, below, active = np.zeros(E), np.zeros(E, bool), np.ones(E, bool)
    static = None
    dqn_used = np.zeros(E)
    if policy != "dqn":
        A = sim.A
        vol = sim.ds["real"][pool].std(axis=0)
        static = np.zeros(E, int)
        for p_ in np.unique(pid):
            ok = np.where(sim.M[p_])[0]
            if policy == "esit":
                allowed_c = (A[ok] > 0).any(axis=0)
                goal = allowed_c / max(1, allowed_c.sum())
                choice = ok[np.argmin(np.abs(A[ok] - goal).sum(axis=1))]
            elif policy == "en_guvenli":
                choice = ok[np.argmin(A[ok] @ vol)]
            else:  # pp_agirlikli ve kapili: taban kural
                choice = baseline_action(A, sim.M[p_], CATS)
            static[pid == p_] = choice
    for _ in range(int(T.max())):
        if policy == "dqn":
            a = np.argmax(model.q(model.x(sim.pvec[pid], t, w, idx, prev), sim.M[pid]), axis=1)
        elif policy == "kapili":
            q = model.q(model.x(sim.pvec[pid], t, w, idx, prev), sim.M[pid])
            a_d = np.argmax(q, axis=1)
            rows = np.arange(E)
            use = q[rows, a_d] - q[rows, static] > delta
            a = np.where(use, a_d, static)
            dqn_used += active & use
        else:
            a = static
        w2, r, d = sim.outcome(pid, w, prev, idx, a)
        total = np.where(active, total + r, total)
        w = np.where(active[:, None], w2, w)
        prev = np.where(active[:, None], d, prev)
        below |= active & (w[:, 0] < 1)
        t = t + 1
        active &= t < T
        idx = sim.next_idx(idx, member, pool, rng)
    g = w[:, 0] - 1
    gn = w[:, 1] - 1
    return {"ortalama_reel_kazanc": float(g.mean()), "medyan_reel_kazanc": float(np.median(g)),
            "kotu_yuzde5": float(np.percentile(g, 5)), "anapara_alti_bitis_olasiligi": float((g < 0).mean()),
            "nominal_anapara_alti_bitis_olasiligi": float((gn < 0).mean()),
            "herhangi_an_anapara_alti_olasiligi": float(below.mean()), "ortalama_odul": float(total.mean()),
            **({"model_karar_orani": float(dqn_used.sum() / max(1, (T).sum()))} if policy == "kapili" else {})}
