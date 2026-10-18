from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import evidence, guard, pipeline, universe
from .config import BASE_DIR, constraint_rules, decision_config
from .models import RMDecision, RunRequest
from .db import store
from .sources import evds, manual, tefas_import

app = FastAPI(title="RM Yatırım Karar Destek")
from .rl_api import router as rl_router  # noqa: E402
app.include_router(rl_router)
from .rl_tools_api import router as rl_tools_router  # noqa: E402
app.include_router(rl_tools_router)
from .agent_api import router as agent_router  # noqa: E402
app.include_router(agent_router)
from .portal_api import router as portal_router  # noqa: E402
app.include_router(portal_router)
from .degerlendirme_api import router as degerlendirme_router  # noqa: E402
app.include_router(degerlendirme_router)
from .veri_api import router as veri_router  # noqa: E402
app.include_router(veri_router)
from .musteri_api import router as musteri_router  # noqa: E402
app.include_router(musteri_router)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _warm_up():
    """Ağır önbellekleri (TEFAS metrikleri, RL veri seti) arka planda ısıtır: ilk RM isteği beklemesin."""
    import threading

    def work():
        try:
            tefas_import.load()
            from .rl_api import _dataset
            _dataset()
        except Exception as exc:  # ısıtma başarısız olursa ilk istek yine kurar
            print(f"Önbellek ısıtma atlandı: {type(exc).__name__}: {exc}")
    threading.Thread(target=work, daemon=True).start()


@app.get("/api/personas")
def personas():
    return pipeline.list_personas()


@app.post("/api/runs")
def create_run(req: RunRequest):
    if not req.persona and not req.persona_id:
        raise HTTPException(400, "persona_id veya persona gerekli")
    try:
        return pipeline.run(req)
    except KeyError as e:
        raise HTTPException(404, f"Persona bulunamadı: {e}")


@app.get("/api/runs")
def runs():
    return evidence.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    r = evidence.load(run_id)
    if r is None:
        raise HTTPException(404, "Kayıt bulunamadı")
    return r


@app.post("/api/runs/{run_id}/decision")
def decide(run_id: str, d: RMDecision):
    r = evidence.load(run_id)
    if r is None:
        raise HTTPException(404, "Kayıt bulunamadı")
    if r["status"] not in ("rm_onayi_bekliyor", "oneri_yok"):
        raise HTTPException(409, f"Bu kayıt karar için uygun değil (durum: {r['status']})")
    if d.decision != "red":
        text = d.edited_text if d.decision == "duzenleyerek_onay" else r["draft_text"]
        if not text:
            raise HTTPException(400, "Düzenleyerek onay için düzenlenmiş metin gerekli")
        excluded_codes = [e["product_id"].split(":", 1)[1] for e in r["excluded"] if e["product_id"].startswith("FON:")]
        # RM'in yazdığı oranlar RM sorumluluğundadır; burada yalnız yasaklı ifade ve dışlanan ürün kontrol edilir
        names = [s["product"]["name"] for s in r.get("shortlist", [])]
        violations = [v for v in guard.check(text, excluded_codes, allowed_numbers=set(), allowed_names=names)
                      if not v.startswith("Kaynağı olmayan oran")]
        if violations:
            raise HTTPException(422, {"message": "Metin onaylanamaz", "violations": violations})
    return evidence.add_decision(run_id, d.model_dump())


@app.get("/api/data/status")
def data_status():
    metrics, meta = tefas_import.load()
    comparison, _ = tefas_import.load_comparison()
    raw = manual.load_raw()
    ind = universe.indicators(raw, set())
    return {
        "tefas": {"fon_sayisi": len(metrics), "imports": meta.get("imports", []), "conflicts": meta.get("conflicts", []),
                  "karsilastirma_fon_sayisi": len(comparison), "karsilastirma_imports": meta.get("karsilastirma_imports", [])},
        "veritabani": {"mevduat": len(raw.get("mevduat", [])), "stopaj": len(raw.get("stopaj", [])),
                       "kap_fon": len((store.kap_funds() or {}).get("fonlar", {})), "benchmark": len(raw.get("benchmarklar", []))},
        "evds_anahtari": evds.has_key(),
        "gostergeler": {k: {"status": v.status, "source": v.source, "as_of": v.as_of, "note": v.note} for k, v in ind.items()},
    }


@app.post("/api/data/tefas")
async def upload_tefas(file: UploadFile = File(...), as_of: str | None = Form(None)):
    """Tarihsel Veriler veya Fon Karşılaştırma dışa aktarımı; tür sütunlardan otomatik tanınır.
    Karşılaştırma dosyasında tarih sütunu olmadığından as_of (dönem sonu tarihi) ayrıca verilmelidir."""
    try:
        return tefas_import.import_any(await file.read(), file.filename or "yukleme.csv", as_of)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/config")
def get_config():
    return {"decision_config": decision_config(), "constraint_rules": constraint_rules()}


# --- Derlenmiş frontend (tek port: API + arayüz aynı sunucudan; cloudflared vb. tünel için) -----------------
# `npm run build` ile üretilen frontend/dist sunulur. API rotaları yukarıda tanımlı olduğundan önceliklidir.
DIST = (BASE_DIR.parent / "frontend" / "dist").resolve()


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "API ucu bulunamadı")
    if not (DIST / "index.html").exists():
        raise HTTPException(404, "Frontend derlenmemiş: frontend klasöründe 'npm run build' çalıştırın.")
    target = (DIST / path).resolve()
    if path and target.is_file() and target.is_relative_to(DIST) and target.name != "index.html":
        return FileResponse(target)
    # SPA: bilinmeyen yollar index.html'e düşer. index.html önbelleklenmez; yoksa yeni derleme tarayıcıya ulaşmaz
    # (asset dosyaları içerik hash'li olduğu için onlar önbelleklenebilir).
    return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
