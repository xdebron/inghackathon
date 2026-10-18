"""TEFAS'ın F5 BIG-IP ASM WAF korumasını gerçek bir tarayıcı (Playwright) ile geçip,
sitenin kendi Next.js API uçlarına (fonGetiriBazliBilgiGetir, fonGnlBlgSiraliGetir,
dagilimSiraliGetirT, ...) doğrudan istek atmaya yarayan oturum.

Çalışma şekli:
  1. Headless Chromium ile https://www.tefas.gov.tr/tr/fon-verileri açılır; WAF'ın
     JS doğrulaması geçilir ve `wid...`/`xid..._session_` çerezleri tarayıcı
     bağlamına (context) otomatik olarak yerleşir.
  2. Sayfanın kendi çıkan isteklerinden `Authorization: Bearer ST-...` başlığı
     yakalanır (bu token derlenmiş JS'e gömülü, herkese açık bir frontend token'ı;
     değişme ihtimaline karşı sabit yazmak yerine çalışma anında yakalanır).
  3. Sonraki tüm `/api/funds/...` POST istekleri **aynı sayfa içinden `fetch()` ile**
     atılır (`page.evaluate`) — Playwright'ın ayrı `APIRequestContext`'i denendi ama
     WAF bunu gerçek tarayıcı trafiğinden ayırt edip ECONNRESET ile engelliyordu
     (muhtemelen farklı bir TLS/HTTP2 parmak izi kullanıyor). Sayfa içi `fetch()`,
     gerçek tarayıcının ağ yığınını kullandığı için çerezler + TLS parmak izi
     tutarlı kalıyor ve WAF'ı geçiyor.

WAF, art arda hızlı isteklerde bağlantıyı bazen sıfırlıyor (ERR_CONNECTION_RESET);
bu yüzden her istek arasında küçük bir bekleme ve üstel geri çekilmeli yeniden
deneme uygulanır.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from playwright.sync_api import sync_playwright

BASE_URL = "https://www.tefas.gov.tr"
WARMUP_URL = f"{BASE_URL}/tr/fon-verileri?fundType=YAT"
# 15.10.2026'da tarayıcıda yakalanan, derlenmiş JS'e gömülü genel frontend token'ı.
# Oturum açılışında gerçek istekten yakalanamazsa yedek olarak kullanılır.
FALLBACK_BEARER = "ST-tefaswebwse3irfmSBj4iRAzGPbAlS94Se"
# Playwright'ın varsayılan Chromium'u navigator.webdriver=true bırakıyor; bu UA + flag
# kombinasyonu WAF'ın warm-up isteğini ERR_CONNECTION_RESET ile engelleme ihtimalini azaltıyor.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
WARMUP_MAX_ATTEMPTS = 4

_FETCH_JS = """
async ({ url, body, bearer, timeoutMs }) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + bearer },
            body: JSON.stringify(body),
            signal: controller.signal,
        });
        const text = await res.text();
        return { status: res.status, text, retryAfter: res.headers.get('retry-after') };
    } catch (e) {
        return { status: 0, text: '', error: String(e) };
    } finally {
        clearTimeout(timer);
    }
}
"""


class TefasApiError(RuntimeError):
    """TEFAS API'si errorMessage döndürdüğünde veya istek kalıcı olarak başarısız olduğunda."""


@dataclass
class TefasSession:
    headless: bool = True
    request_delay: float = 2.5
    max_retries: int = 8
    request_timeout_ms: int = 20000
    _playwright: Any = field(default=None, repr=False, init=False)
    _browser: Any = field(default=None, repr=False, init=False)
    _context: Any = field(default=None, repr=False, init=False)
    _page: Any = field(default=None, repr=False, init=False)
    _bearer: str = field(default=FALLBACK_BEARER, repr=False, init=False)
    _last_request_at: float = field(default=0.0, repr=False, init=False)

    def __enter__(self) -> "TefasSession":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(locale="tr-TR", user_agent=DEFAULT_USER_AGENT)

        captured: dict[str, str] = {}

        def on_request(req: Any) -> None:
            if "/api/funds/" in req.url:
                auth = req.headers.get("authorization")
                if auth:
                    captured["bearer"] = auth.replace("Bearer ", "").strip()

        self._page = self._context.new_page()
        self._page.on("request", on_request)

        last_err: Optional[BaseException] = None
        for attempt in range(1, WARMUP_MAX_ATTEMPTS + 1):
            try:
                self._page.goto(WARMUP_URL, wait_until="networkidle", timeout=45000)
                last_err = None
                break
            except Exception as exc:  # WAF bazen ilk denemede ERR_CONNECTION_RESET döner
                last_err = exc
                time.sleep(2 * attempt)
        if last_err is not None:
            self.close()
            raise TefasApiError(f"Warm-up başarısız ({WARMUP_MAX_ATTEMPTS} deneme): {last_err}")

        self._page.wait_for_timeout(1500)
        if captured.get("bearer"):
            self._bearer = captured["bearer"]

    def reconnect(self) -> None:
        """Uzun süren çekimlerde WAF çerezleri/oturumu bayatlayabiliyor (ardı ardına
        zaman aşımı/abort hataları gözlemlendi); warm-up sayfasını yeniden ziyaret
        ederek çerezleri tazeler."""
        if self._page is None:
            return
        try:
            self._page.goto(WARMUP_URL, wait_until="networkidle", timeout=45000)
            self._page.wait_for_timeout(1000)
        except Exception:
            pass  # post_json zaten yeniden deneyecek; burada sessizce geçiyoruz

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self.request_delay - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.2))
        self._last_request_at = time.monotonic()

    def post_json(self, endpoint: str, body: dict) -> dict:
        if self._page is None:
            raise RuntimeError("TefasSession başlatılmadı; `with TefasSession() as s:` kullanın")
        url = f"{BASE_URL}/api/funds/{endpoint}"
        last_err: Optional[BaseException] = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                result = self._page.evaluate(
                    _FETCH_JS,
                    {"url": url, "body": body, "bearer": self._bearer, "timeoutMs": self.request_timeout_ms},
                )
            except Exception as exc:  # sayfa/tarayıcı seviyesinde hata
                last_err = exc
                time.sleep(min(2 ** attempt, 30))
                if attempt >= 3 and attempt % 2 == 1:
                    self.reconnect()
                continue
            if result.get("error") or result.get("status") != 200:
                status = result.get("status")
                last_err = TefasApiError(f"{endpoint}: {result.get('error') or ('HTTP ' + str(status))}")
                if status == 429:
                    # Oran sınırlandı: sunucunun Retry-After'ına uy, yoksa uzunca bekle.
                    retry_after = result.get("retryAfter")
                    wait = float(retry_after) if retry_after and str(retry_after).isdigit() else 15.0
                    time.sleep(wait)
                else:
                    # Zaman aşımı/abort gibi sessiz WAF engellemeleri de burada düşer.
                    time.sleep(min(2 ** attempt, 30))
                if attempt >= 3 and attempt % 2 == 1:
                    self.reconnect()
                continue
            data = json.loads(result["text"])
            if data.get("errorMessage"):
                raise TefasApiError(f"{endpoint}: {data['errorMessage']}")
            return data
        raise TefasApiError(f"{endpoint}: {self.max_retries} denemeden sonra başarısız: {last_err}")
