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
import re
import secrets
import socket
import sys
import time

from .i18n import tr

DEFAULT_LAN = {"enabled": False, "port": 8766, "mode": "full", "pw": "", "gen": ""}
MODES = ("full", "view")
COOKIE = "bmm_lan"
SESSION_DAYS = 30
PBKDF2_ITERS = 200_000
MIN_PW = 8

# aina kielletyt etälaitteelta (polku, metodi)
REMOTE_DENY = {("/api/update/install", "POST"), ("/api/shutdown", "POST"), ("/api/ui/show", "POST"),
               ("/api/feedback/bundle", "GET"), ("/api/feedback/send", "POST"), ("/api/lan", "POST"),
               ("/api/lan/logout_all", "POST"), ("/api/feeds/preview", "GET"),
               ("/api/lan/firewall", "GET"), ("/api/lan/firewall", "POST")}
REMOTE_PUBLIC = {"/login", "/api/lan/login"}
CONFIG_DENY = ("lan", "ui")          # asetusavaimet, joita ei voi muuttaa etänä
SECRET_FIELDS = (("bm_api_key",), ("messaging", "passcode"), ("ais", "key"))
# Osoitteet, joihin ohjelma itse ottaa yhteyttä. Etälaite ei saa vaihtaa niitä, jottei lähiverkon
# laitteella voi ohjata ohjelmaa hakemaan tietoa mielivaltaisista osoitteista (esim. reitittimen hallinta).
ADDRESS_FIELDS = (("map_tiles",), ("feedback_url",), ("messaging", "server"), ("messaging", "port"),
                  ("aprsrx", "host"), ("aprsrx", "port"))


def _url(i: dict) -> str:
    u = str(i.get("url") or "").strip()
    return "https://" + u[len("webcal://"):] if u.startswith("webcal://") else u


def _tcp(i: dict) -> str:
    if (i.get("type") or "kiss_tcp") not in ("kiss_tcp", "agwpe"):
        return ""
    return f'{str(i.get("host") or "").strip() or "127.0.0.1"}:{str(i.get("port") or "").strip()}'


# listat: polku ja funktio, joka antaa alkion osoitteen ("" = ei osoitetta)
ADDRESS_LISTS = ((("messaging", "feeds"), _url), (("nets", "sources"), _url), (("aprsrx", "radios"), _tcp))


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


def has_address_change(body: dict, cfg: dict) -> bool:
    """Yrittääkö asetusmuutos vaihtaa osoitetta, johon ohjelma ottaa yhteyttä?
    Listoissa (syötteet, kalenterit) sallitaan päälle/pois-kytkentä ja poisto, ei uusia osoitteita."""
    def get(d, path):
        for k in path:
            if not isinstance(d, dict) or k not in d:
                return None, False
            d = d[k]
        return d, True
    for path in ADDRESS_FIELDS:
        v, ok = get(body, path)
        if ok and str(v if v is not None else "").strip() != str(get(cfg, path)[0] or "").strip():
            if path == ("map_tiles",) and not str(v or "").strip() and not get(cfg, path)[0]:
                continue
            return True
    for path, addr in ADDRESS_LISTS:
        v, ok = get(body, path)
        if not ok:
            continue
        old = {addr(i) for i in (get(cfg, path)[0] or []) if isinstance(i, dict)}
        for i in v or []:
            u = addr(i) if isinstance(i, dict) else ""
            if u and u not in old:
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
    """Koneen ensisijainen IPv4-osoite lähiverkossa (reitti ulos; ei lähetä mitään)."""
    out: list[str] = []
    try:
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


VIRTUAL_RE = re.compile(r"vethernet|virtualbox|vmware|vmnet|hyper-v|wsl|docker|^veth|^br-|^virbr|utun|^tun|^tap|^wg|"
                        r"tailscale|zerotier|^zt|vboxnet|bridge|awdl|llw|nordlynx|openvpn|tap-windows|wireguard|vpn|"
                        r"loopback|^anpi|^ap\d", re.I)
_IF_CACHE: list = [0.0, []]


def _run(cmd: list[str], timeout: float = 6) -> str:
    import subprocess
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000                 # CREATE_NO_WINDOW: ei välähtävää konsoli-ikkunaa
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _parse_ifconfig(text: str) -> list[tuple[str, str]]:
    """macOS/BSD ifconfig → [(sovitin, ipv4)]."""
    out, cur = [], ""
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+): flags=", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"^\s+inet (\d+\.\d+\.\d+\.\d+)", line)
        if m and cur:
            out.append((cur, m.group(1)))
    return out


def _parse_ip_addr(text: str) -> list[tuple[str, str]]:
    """Linuxin `ip -o -4 addr show` → [(sovitin, ipv4)]."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^\d+:\s+([^\s:]+)\s+inet (\d+\.\d+\.\d+\.\d+)", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _parse_win_json(text: str) -> list[tuple[str, str]]:
    import json
    try:
        j = json.loads(text or "[]")
    except ValueError:
        return []
    if isinstance(j, dict):
        j = [j]
    return [(str(i.get("InterfaceAlias") or ""), str(i.get("IPAddress") or "")) for i in j if isinstance(i, dict)]


def interfaces(max_age: float = 60) -> list[dict]:
    """Kaikki koneen lähiverkko-osoitteet sovittimen nimen kanssa: [{ip, name, virtual, primary}].
    Ensisijainen (reitti ulos) ensin, virtuaali- ja VPN-sovittimet viimeisinä. Hidas (ulkoinen komento):
    kutsu säikeessä; tulos pidetään muistissa minuutin."""
    if time.time() - _IF_CACHE[0] < max_age and _IF_CACHE[1]:
        return _IF_CACHE[1]
    if sys.platform == "win32":
        pairs = _parse_win_json(_run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                      "Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,InterfaceAlias | ConvertTo-Json -Compress"]))
    elif sys.platform == "darwin":
        pairs = _parse_ifconfig(_run(["/sbin/ifconfig"]))
    else:
        pairs = _parse_ip_addr(_run(["ip", "-o", "-4", "addr", "show"])) or _parse_ifconfig(_run(["ifconfig"]))
    prim = set(lan_addresses())
    seen, out = set(), []
    for name, ip in pairs:
        if not ip or ip in seen or ip.startswith(("127.", "169.254.", "0.")):
            continue
        seen.add(ip)
        cgnat = ip.startswith("100.") and 64 <= int(ip.split(".")[1]) <= 127      # esim. Tailscale
        out.append({"ip": ip, "name": name, "virtual": bool(VIRTUAL_RE.search(name)) or cgnat, "primary": ip in prim})
    for ip in prim - seen:
        out.append({"ip": ip, "name": "", "virtual": False, "primary": True})
    out.sort(key=lambda i: (not i["primary"], i["virtual"]))
    _IF_CACHE[:] = [time.time(), out]
    return out


def local_hostname() -> str:
    """Koneen nimi mDNS-muodossa (nimi.local), jos nimi kelpaa osoitteeksi; muuten ""."""
    h = ""
    if sys.platform == "darwin":
        h = _run(["scutil", "--get", "LocalHostName"], 3).strip()
    h = h or socket.gethostname().split(".")[0]
    return f"{h.lower()}.local" if re.fullmatch(r"[A-Za-z0-9-]{1,63}", h or "") else ""


# ---- Windowsin palomuuri ----
FW_RULE = "Monitor for Amateur Radio (LAN {port})"


def firewall_status(port: int, exe: str = "") -> dict:
    """Windows: onko portille sallintasääntö, onko ohjelmalle estosääntöjä (syntyy, jos Windowsin kysymykseen
    vastasi Peruuta) ja onko jokin verkkoyhteys Julkinen-profiilissa. Muilla järjestelmillä {"supported": False}."""
    if sys.platform != "win32":
        return {"supported": False}
    import json
    name = FW_RULE.format(port=int(port))
    ps = ("$r = @(Get-NetFirewallRule -DisplayName '" + name + "' -ErrorAction SilentlyContinue | "
          "Where-Object { $_.Enabled -eq 'True' }).Count; $b = 0; ")
    if exe and "'" not in exe:
        ps += ("$b = @(Get-NetFirewallApplicationFilter -Program '" + exe + "' -ErrorAction SilentlyContinue | "
               "Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Action -eq 'Block' "
               "-and $_.Direction -eq 'Inbound' }).Count; ")
    ps += ("$p = @(Get-NetConnectionProfile | ForEach-Object { @{n=$_.Name; c=[string]$_.NetworkCategory} }); "
           "@{rule=$r; block=$b; profiles=$p} | ConvertTo-Json -Compress -Depth 3")
    raw = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], 15)
    try:
        j = json.loads(raw or "{}")
    except ValueError:
        return {"supported": True, "rule": False, "blocked": 0, "public": [], "error": "status: " + (raw or "")[:300]}
    profs = j.get("profiles") or []
    if isinstance(profs, dict):
        profs = [profs]
    if not raw.strip():
        return {"supported": True, "rule": False, "blocked": 0, "public": [], "error": "status: powershell no output"}
    return {"supported": True, "rule": bool(j.get("rule")), "blocked": int(j.get("block") or 0),
            "public": [p.get("n") for p in profs if isinstance(p, dict) and str(p.get("c")) == "Public"]}


def firewall_allow(port: int, exe: str) -> tuple[bool, str]:
    """Windows: lisää sallintasääntö yksityisiin verkkoihin (pyytää järjestelmänvalvojan oikeudet UAC-ikkunalla).
    → (onnistui, viesti). Korotetun PowerShellin tulos kirjoitetaan tiedostoon, jotta virhe näkyy lokissa."""
    if sys.platform != "win32":
        return False, "not windows"
    import base64
    import os
    import tempfile
    port = int(port)
    name = FW_RULE.format(port=port)
    out = os.path.join(tempfile.gettempdir(), f"bmm-firewall-{os.getpid()}.txt")
    try:
        os.remove(out)
    except OSError:
        pass
    body = (f"Remove-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue; "
            f"$null = New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow -Protocol TCP "
            f"-LocalPort {port} -Profile Private -ErrorAction Stop")
    if exe and "'" not in exe:
        # Windowsin oma kysymys (Peruuta) tekee estosäännön, joka voittaa sallintasäännön: poistetaan se käytöstä
        body += (f"; $n = @(Get-NetFirewallApplicationFilter -Program '{exe}' -ErrorAction SilentlyContinue | Get-NetFirewallRule | "
                 "Where-Object { $_.Action -eq 'Block' -and $_.Direction -eq 'Inbound' -and $_.Enabled -eq 'True' }); "
                 "$n | Disable-NetFirewallRule; $msg = 'OK blocks-disabled=' + $n.Count")
    else:
        body += "; $msg = 'OK'"
    q = out.replace("'", "''")
    inner = (f"try {{ {body}; $msg | Out-File -Encoding utf8 '{q}' }} "
             f"catch {{ ('ERROR ' + $_.Exception.Message) | Out-File -Encoding utf8 '{q}' }}")
    enc = base64.b64encode(inner.encode("utf-16-le")).decode()
    outer = ("try { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
             f"-ArgumentList '-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand','{enc}' -ErrorAction Stop; 'started' }} "
             "catch { 'cancel ' + $_.Exception.Message }")
    _IF_CACHE[0] = 0
    started = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", outer], 120).strip()
    try:
        with open(out, encoding="utf-8-sig") as f:
            res = f.read().strip()
        os.remove(out)
    except OSError:
        res = ""
    if not started.endswith("started") and not res:
        return False, started or "powershell: no output"
    if not res:
        return False, "elevated command wrote no result (" + started + ")"
    return res.startswith("OK"), res


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
    """Kirjautumiset. Istuntoeväste on allekirjoitettu (voimassaolo.satunnaisosa.HMAC), joten se säilyy
    ohjelman uudelleenkäynnistyksen ja päivityksen yli. Avain johdetaan salasanan tiivisteestä ja
    sukupolviarvosta: salasanan vaihto tai "kirjaa kaikki ulos" (uusi sukupolvi) mitätöi kaikki evästeet."""

    def __init__(self, secret=lambda: ""):
        self.secret = secret                   # → merkkijono (salasanan tiiviste + sukupolvi); "" = ei kirjautumista
        self.active: dict[str, float] = {}     # satunnaisosa → viimeksi nähty (vain muistissa, laskuria varten)
        self.revoked: set[str] = set()         # uloskirjatut evästeet tämän käynnistyksen aikana
        self.fails: dict[str, list] = {}       # ip → [epäonnistumiset, estetty asti]

    @property
    def sessions(self) -> dict:
        now = time.time()
        return {n: t for n, t in self.active.items() if now - t < SESSION_DAYS * 86400}

    def _mac(self, body: str) -> str | None:
        sec = self.secret()
        if not sec:
            return None
        key = hashlib.sha256(("bmm-lan-session|" + sec).encode()).digest()
        return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()[:43]

    def _make(self, nonce: str) -> str:
        body = f"{int(time.time()) + SESSION_DAYS * 86400}.{nonce}"
        return f"{body}.{self._mac(body)}"

    def _parse(self, token: str | None):
        """→ (voimassa asti, satunnaisosa) tai None."""
        try:
            exp, nonce, mac = (token or "").split(".")
            want = self._mac(f"{exp}.{nonce}")
            if not want or not hmac.compare_digest(mac, want) or int(exp) < time.time() or nonce in self.revoked:
                return None
            return int(exp), nonce
        except (ValueError, TypeError):
            return None

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
        if not self.secret():
            raise PermissionError(tr("Aseta salasana"))
        self.fails.pop(ip, None)
        nonce = secrets.token_urlsafe(18).replace(".", "_")
        self.active[nonce] = time.time()
        return self._make(nonce)

    def check(self, token: str | None) -> bool:
        p = self._parse(token)
        if not p:
            return False
        self.active[p[1]] = time.time()
        return True

    def renew(self, token: str | None) -> str | None:
        """Uusi eväste samalle laitteelle, kun puolet voimassaoloajasta on kulunut (liukuva 30 päivää)."""
        p = self._parse(token)
        if p and p[0] - time.time() < SESSION_DAYS * 86400 / 2:
            return self._make(p[1])
        return None

    def logout(self, token: str | None):
        p = self._parse(token)
        if p:
            self.revoked.add(p[1])
            self.active.pop(p[1], None)

    def logout_all(self):
        """Kutsujan pitää myös vaihtaa sukupolvi (Monitor.lan_logout_all), jotta vanhat evästeet mitätöityvät."""
        self.active.clear()
        self.revoked.clear()


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
