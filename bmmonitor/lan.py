"""Selainkäyttö lähiverkossa: salasana, istunnot, oikeudet ja lähiverkko-osoitteet.

Paikallinen käyttöliittymä (127.0.0.1) toimii ennallaan. Kun lähiverkkokäyttö on päällä, sama sovellus kuuntelee
lisäksi osoitteessa 0.0.0.0:<portti>. Kaikki tähän porttiin tulevat pyynnöt vaativat kirjautumisen, ja osa
toiminnoista on aina kielletty etälaitteelta (ohjelman sulkeminen ja päivitys, avainten ja salasanojen muutos,
lähiverkkoasetukset, palautteen lokipaketti). Tila "Vain katselu" estää lisäksi kaikki muuttavat pyynnöt.
Salasanasta tallennetaan vain PBKDF2-tiiviste käyttöjärjestelmän salasanavarastoon.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import socket
import time

from .i18n import tr

DEFAULT_LAN = {"enabled": False, "port": 8766, "mode": "full", "pw": ""}
MODES = ("full", "view")
COOKIE = "bmm_lan"
SESSION_DAYS = 30
PBKDF2_ITERS = 200_000
MIN_PW = 8

# aina kielletyt etälaitteelta (polku, metodi)
REMOTE_DENY = {("/api/update/install", "POST"), ("/api/shutdown", "POST"), ("/api/ui/show", "POST"),
               ("/api/feedback/bundle", "GET"), ("/api/feedback/send", "POST"), ("/api/lan", "POST"),
               ("/api/lan/logout_all", "POST")}
REMOTE_PUBLIC = {"/login", "/api/lan/login"}
CONFIG_DENY = ("lan", "ui")          # asetusavaimet, joita ei voi muuttaa etänä
SECRET_FIELDS = (("bm_api_key",), ("messaging", "passcode"), ("ais", "key"))


def normalize_lan(new: dict, old: dict) -> dict:
    out = {**DEFAULT_LAN, **(old or {})}
    if "enabled" in new:
        out["enabled"] = bool(new["enabled"])
    if "port" in new:
        try:
            p = int(new["port"])
        except (TypeError, ValueError):
            raise ValueError(tr("Virheellinen portti: {x}", x=new["port"])) from None
        if not 1024 <= p <= 65535:
            raise ValueError(tr("Portin pitää olla välillä 1024–65535"))
        out["port"] = p
    if "mode" in new:
        if new["mode"] not in MODES:
            raise ValueError(tr("Virheellinen valinta"))
        out["mode"] = new["mode"]
    return out


def hash_password(pw: str, iters: int = PBKDF2_ITERS) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters)
    return f"pbkdf2_sha256${iters}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt, h = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, AttributeError):
        return False


def has_secret_change(body: dict, qso_fields: list[str]) -> bool:
    """Yrittääkö asetusmuutos asettaa avaimen tai salasanan (muu kuin peitetty "********")."""
    def changed(v):
        return v is not None and str(v) != "********"
    for path in SECRET_FIELDS:
        d = body
        for k in path[:-1]:
            d = d.get(k) if isinstance(d, dict) else None
        if isinstance(d, dict) and path[-1] in d and changed(d[path[-1]]):
            return True
    lg = body.get("log")
    if isinstance(lg, dict) and any(f in lg and changed(lg[f]) for f in qso_fields):
        return True
    return False


def host_allowed(host_header: str | None) -> bool:
    """DNS rebinding -suoja: hyväksytään vain IP-osoite, localhost ja koneen oma nimi (myös .local).
    Muuten haitallinen verkkosivu voisi ohjata oman nimensä osoitteeseen 127.0.0.1 ja lukea ohjelman tietoja."""
    import ipaddress
    h = (host_header or "").strip().lower()
    if not h:
        return True                                     # HTTP/1.0 ilman Host-otsaketta (ei selaimilta)
    if h.startswith("["):                               # [IPv6]:portti
        h = h[1:h.find("]")] if "]" in h else h[1:]
    elif h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    own = socket.gethostname().lower()
    return h.rstrip(".") in {"localhost", own, own.split(".")[0], own.split(".")[0] + ".local", own + ".local"}


def lan_addresses() -> list[str]:
    """Koneen IPv4-osoitteet lähiverkossa (ei 127.x)."""
    out: list[str] = []
    try:                                       # ensisijainen osoite: reitti ulos (ei lähetä mitään)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            out.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    # (ei koneen nimen nimipalveluhakua: se voi kestää macOS:ssä kymmeniä sekunteja)
    return [ip for ip in out if not ip.startswith(("127.", "169.254.", "0."))]


def qr_svg(text: str) -> str | None:
    try:
        import segno
    except ImportError:
        return None
    import io
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=4, dark="#000", light="#fff", border=2, xmldecl=False)
    return buf.getvalue().decode()


class LanAccess:
    """Istunnot ja kirjautumisyritykset (vain muistissa: uudelleenkäynnistys kirjaa etälaitteet ulos)."""

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.fails: dict[str, list] = {}       # ip → [epäonnistumiset, estetty asti]

    def blocked_for(self, ip: str) -> float:
        f = self.fails.get(ip)
        return max(0.0, f[1] - time.time()) if f else 0.0

    def login(self, ip: str, pw: str, stored_hash: str) -> str:
        wait = self.blocked_for(ip)
        if wait > 0:
            raise PermissionError(tr("Liian monta väärää yritystä – odota {n} s", n=int(wait) + 1))
        if not stored_hash or not verify_password(pw or "", stored_hash):
            f = self.fails.setdefault(ip, [0, 0.0])
            f[0] += 1
            if f[0] >= 5:                       # 5 virhettä → tauko 30 s, joka kaksinkertaistuu (max 15 min)
                f[1] = time.time() + min(900, 30 * 2 ** (f[0] - 5))
            raise PermissionError(tr("Väärä salasana"))
        self.fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"t": time.time(), "last": time.time(), "ip": ip}
        return token

    def check(self, token: str | None) -> bool:
        s = self.sessions.get(token or "")
        if not s:
            return False
        if time.time() - s["last"] > SESSION_DAYS * 86400:
            self.sessions.pop(token, None)
            return False
        s["last"] = time.time()
        return True

    def logout(self, token: str | None):
        self.sessions.pop(token or "", None)

    def logout_all(self):
        self.sessions.clear()


LOGIN_HTML = """<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><link rel="icon" href="/static/favicon.png">
<style>
 body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: #0f1720; color: #e6edf3; margin: 0;
        display: flex; min-height: 100vh; align-items: center; justify-content: center; }}
 form {{ background: #16202b; border: 1px solid #2a3846; border-radius: 10px; padding: 26px 24px; width: min(340px, 90vw); }}
 h1 {{ font-size: 18px; margin: 0 0 4px; }} p {{ color: #9fb0c0; font-size: 13px; margin: 0 0 18px; }}
 label {{ font-size: 13px; color: #9fb0c0; }}
 input {{ width: 100%; box-sizing: border-box; padding: 10px; margin: 6px 0 14px; border-radius: 6px; border: 1px solid #2a3846;
         background: #0f1720; color: #e6edf3; font-size: 16px; }}
 button {{ width: 100%; padding: 10px; border: 0; border-radius: 6px; background: #4dabf7; color: #04111d; font-weight: 600;
          font-size: 15px; cursor: pointer; }}
 .err {{ color: #ff8787; font-size: 13px; min-height: 18px; margin-top: 10px; }}
</style></head><body>
<form id="f"><h1>{title}</h1><p>{intro}</p>
<label for="pw">{pwlabel}</label><input type="password" id="pw" autocomplete="current-password" autofocus required>
<button type="submit">{button}</button><div class="err" id="err"></div></form>
<script>
document.getElementById("f").onsubmit = async e => {{
  e.preventDefault();
  const r = await fetch("/api/lan/login", {{method: "POST", headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{password: document.getElementById("pw").value}})}});
  const j = await r.json().catch(() => ({{}}));
  if (j.ok) location.href = "/"; else document.getElementById("err").textContent = j.error || "?";
}};
</script></body></html>"""


def login_page(lang: str, app_name: str) -> str:
    import html
    return LOGIN_HTML.format(lang=html.escape(lang), title=html.escape(app_name),
                             intro=html.escape(tr("Selainkäyttö lähiverkossa. Kirjaudu ohjelman lähiverkkosalasanalla.")),
                             pwlabel=html.escape(tr("Salasana")), button=html.escape(tr("Kirjaudu")))
