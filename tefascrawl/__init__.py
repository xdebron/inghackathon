"""tefascrawl: docs/tefas.md'de tanımlanan TEFAS veri ihtiyaçlarını otomatik çeken araç.

Kapsam (docs/tefas.md ile birebir):
  1. Fon fiyat geçmişi (13 ay, YAT)      -> tefascrawl.tarihsel
  2. Fon getirileri (tüm fonlar, YAT)    -> tefascrawl.getiriler
  3. Portföy dağılımı (opsiyonel)        -> tefascrawl.portfoy
  4. Fon analiz/detay sayfası            -> kapsam dışı, bkz. README.md ("Kapsam dışı" bölümü)

ÖNEMLİ: docs/veri_kaynaklari.md ekibin "TEFAS API'sini script ile çekmeyiz" kararını
içeriyordu. Bu paket, kullanıcının bu kararın aksine verdiği açık talimatla, TEFAS'ın
F5 BIG-IP ASM korumasını gerçek bir tarayıcı (Playwright) ile geçip sitenin kendi iç
JSON API'lerine doğrudan istek atar. Ayrıntılar için README.md'ye bakın.
"""

__version__ = "0.1.0"
