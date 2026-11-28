"""Monitor for Amateur Radio (sisäinen nimi Monitor for BrandMeister) – BrandMeister-, APRS-, loki- ja palveluseuranta.

Kuuntelee Brandmeisterin julkista Last Heard -virtaa (socket.io,
https://api.brandmeister.network/lh/socket.io) ja näyttää valittujen
puheryhmien tiedot paikallisessa selainkäyttöliittymässä.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import fnmatch
import hashlib
import json
import logging
import os
import random
import re
import secrets
import socket
import sys
import time
import webbrowser
from collections import deque
from pathlib import Path

import aiohttp
import socketio
from aiohttp import web

from .i18n import LANGS, get_lang, set_lang, set_units, tr
from .updater import Updater
from .checkin import CheckIn
from .sheetsync import SheetError
from . import APP_ID, APP_NAME, __date__, __version__, desktop, feedback, secret_store
from .nets import DEFAULT_NETS, NetCalendar, normalize_nets
from .qso import DEFAULT_LOG, SECRETS as QSO_SECRETS, QsoLog, adif_file, csv_file, normalize_log
from .ais import DEFAULT_AIS, AisFeed, normalize_ais
from .propagation import DEFAULT_PROP, WINDOWS as PROP_WINDOWS, PropFeed, normalize_prop
from .tropo import TropoFeed
from . import lan as lanmod
from .lan import DEFAULT_LAN, LanAccess, normalize_lan
from .aprsrx import DEFAULT_APRSRX, DEFAULT_TILES, DEFAULT_TILES_ATTR, AprsRx, normalize_aprsrx
from .messaging import (DEFAULT_MESSAGING, MessagingHub, friendly_error, http_session, normalize_messaging, split_message,
                        validate_passcode, ws_options)
from .bmapi import BMApi, BMApiError
from .services import DEFAULT_SERVICES, MA_COUNTRIES, MA_FEED, ServiceHub, fetch_text, ma_areas, normalize_services, parse_meteoalarm
from .features import (CALL_OR_ID, DEFAULT_DEVICES, DEFAULT_WATCH, Commands, DeviceMonitor, Heard, Stats,
                       Watcher, bm_user_history)

BM_URL = "https://api.brandmeister.network"
LH_PATH = "/lh"
HISTORY_ROWS = 500   # BM palauttaa enintään n. 500 viimeisintä tapahtumaa
TG_NAMES_URL = "https://api.brandmeister.network/v2/talkgroup"

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Windowsissa mimetypes lukee tyypit rekisteristä, jossa .js voi olla esim. text/plain → varmistetaan oikeat tyypit
import mimetypes  # noqa: E402
for _ext, _type in ((".js", "text/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"), (".png", "image/png")):
    mimetypes.add_type(_type, _ext)
MAX_CALLS = 500
MAX_DATA = 500
STALE_ACTIVE_SEC = 300          # aktiivinen sessio ilman Stopia -> unohdetaan
RAW_LOG_MAX_BYTES = 5_000_000

log = logging.getLogger("bmmonitor")


# Sivun pyyntöjen raskas työ omissa säikeissään ja aikakatkaisulla: jumiutunut laskenta tai ikkunakutsu
# ei saa pitää selaimen (rajallisia) yhteyksiä paikalliseen palvelimeen varattuina.
from concurrent.futures import ThreadPoolExecutor as _TPE
_PAGE_POOL = _TPE(max_workers=6, thread_name_prefix="bmm-page")


async def in_thread(fn, *args, timeout: float = 30):
    fut = asyncio.get_running_loop().run_in_executor(_PAGE_POOL, fn, *args)
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout)
    except asyncio.TimeoutError:
        log.warning("Pyyntö %s kesti yli %d s – keskeytetään", getattr(fn, "__qualname__", fn), timeout)
        raise web.HTTPGatewayTimeout(text=tr("Toiminto kesti liian kauan – yritä uudelleen"))

DEFAULT_CONFIG = {
    "talkgroups": ["244*", "91"],
    "data_filter": "all",       # "all" = koko verkon dataliikenne, "matched" = vain seuratut TG:t
    "min_duration": 0,          # sekuntia; lyhyemmät avaukset ohitetaan
    "port": 8765,
    "log_csv": True,
    "calls_limit": 10,          # Viimeisimmät puhelut -listan pituus (10/25/50/100)
    "bm_api_key": "",           # BM SelfCare API-avain (vain laitteen hallintaan)
    "language": "",             # "fi" / "en" / "de" / "es" / "fr"; tyhjä = valitaan selaimen kielen mukaan
    "units": "metric",          # "metric" (km) / "imperial" (mailit); asetukset tallennetaan aina kilometreinä
    "update_check": True,       # tarkista uudet versiot GitHubista kerran päivässä
    # "update_beta": asennetaanko myös testiversiot; puuttuessa päätetään käynnistyksessä (beta-asennus → True)
    "theme": "dark",            # "dark" / "light" / "auto" (käyttöjärjestelmän mukaan)
    "ui": "window",             # "window" = oma ikkuna (Windows/macOS), "browser" = oletusselain
    "hidden_tabs": [],          # piilotetut välilehdet (data-tab); Ohjelma-välilehteä ei voi piilottaa
    "map_tiles": DEFAULT_TILES,           # karttatiilten osoite (haetaan paikallisen palvelimen kautta)
    "map_tiles_attr": DEFAULT_TILES_ATTR,
}
CALLS_LIMITS = (10, 25, 50, 100)      # 0.17: 150 poistettu (tallennettu 150 → 100)
# välilehdet, jotka käyttäjä voi piilottaa (Ohjelma = "asetukset" on aina näkyvissä)
HIDEABLE_TABS = ("seuranta", "tilastot", "kaverit", "laitteet", "checkin", "aprs", "viestit", "lokit", "palvelut", "satelliitit",
                 "radiosaa")


def nearest_calls_limit(n) -> int:
    """Vanhat arvot lähimpään sallittuun: 15 -> 10, 20 -> 25, 150 -> 100."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return CALLS_LIMITS[0]
    return min(CALLS_LIMITS, key=lambda v: (abs(v - n), v))


# --------------------------------------------------------------------------
# Apufunktiot
# --------------------------------------------------------------------------
def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "BMMonitor"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_patterns(items) -> list[str]:
    """Hyväksyy listan tai merkkijonon. Sallitut: 244, 244*, 24?1 ."""
    if isinstance(items, str):
        items = items.replace(",", " ").replace(";", " ").split()
    out: list[str] = []
    for raw in items:
        p = str(raw).strip()
        if not p:
            continue
        if not all(c.isdigit() or c in "*?" for c in p):
            raise ValueError(tr("Virheellinen puheryhmä: {p} (sallitut: numerot, * ja ?)", p=repr(p)))
        if p not in out:
            out.append(p)
    return out


class Matcher:
    def __init__(self, patterns: list[str]):
        self.patterns = patterns
        self.exact = {int(p) for p in patterns if p.isdigit()}
        self.wild = [p for p in patterns if not p.isdigit()]

    def match(self, tg) -> str | None:
        if tg is None:
            return None
        try:
            tg = int(tg)
        except (TypeError, ValueError):
            return None
        if tg in self.exact:
            return str(tg)
        s = str(tg)
        for p in self.wild:
            if fnmatch.fnmatchcase(s, p):
                return p
        return None


def _lower_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).lower() for x in v]
    if isinstance(v, str):
        return [v.lower()]
    return []


def is_data_session(p: dict) -> bool:
    ct = _lower_list(p.get("CallTypes"))
    if any(k in t for t in ct for k in ("data", "text", "sms", "message")):
        return True
    if "voice" not in ct and (p.get("DataCount") or 0) > 0:
        return True
    return False


def is_group_call(p: dict) -> bool:
    ct = _lower_list(p.get("CallTypes"))
    if not ct:
        return True
    return "group" in ct


TEXT_FIELDS = ("Text", "Message", "TextMessage", "SMS", "Payload", "Data")


# --------------------------------------------------------------------------
# Sovelluksen tila
# --------------------------------------------------------------------------
class Monitor:
    def __init__(self, cfg_path: Path, demo: bool = False):
        self.cfg_path = cfg_path
        self.demo = demo
        self.ui = None                  # desktop.Desktop, kun käyttöliittymä on omassa ikkunassa
        self.cfg = dict(DEFAULT_CONFIG)
        if cfg_path.exists():
            try:
                self.cfg.update(json.loads(cfg_path.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001
                log.warning("Asetustiedoston luku epäonnistui: %s", e)
        self.cfg["talkgroups"] = parse_patterns(self.cfg.get("talkgroups", []))
        self.cfg["messaging"] = {**DEFAULT_MESSAGING, **(self.cfg.get("messaging") or {})}
        self.cfg["watch"] = {**DEFAULT_WATCH, **(self.cfg.get("watch") or {})}
        self.cfg["devices"] = {**DEFAULT_DEVICES, **(self.cfg.get("devices") or {})}
        svc = self.cfg.get("services") or {}
        self.cfg["services"] = {k: {**v, **(svc.get(k) or {})} for k, v in DEFAULT_SERVICES.items()}
        self.cfg["calls_limit"] = nearest_calls_limit(self.cfg.get("calls_limit"))
        self.cfg["aprsrx"] = normalize_aprsrx({}, {**DEFAULT_APRSRX, **(self.cfg.get("aprsrx") or {})})   # myös radiolista
        self.cfg["nets"] = {**DEFAULT_NETS, **(self.cfg.get("nets") or {})}
        self.cfg["log"] = {**DEFAULT_LOG, **(self.cfg.get("log") or {})}
        self.cfg["ais"] = {**DEFAULT_AIS, **(self.cfg.get("ais") or {})}
        self.cfg["propagation"] = {**DEFAULT_PROP, **(self.cfg.get("propagation") or {})}
        if "update_beta" not in self.cfg:
            # beta-asennuksen käyttäjä pysyy testiversioissa myös vakaan version jälkeen; uusi vakaa asennus ei
            from .updater import is_prerelease
            self.cfg["update_beta"] = is_prerelease(__version__)
        self.matcher = Matcher(self.cfg["talkgroups"])
        set_lang(self.cfg.get("language") or "fi")
        set_units(self.cfg.get("units"))

        self.tg_names: dict[int, str] = {}
        self.tg_stats: dict[int, dict] = {}
        self.sessions: dict[str, dict] = {}        # aktiiviset puhesessiot
        self.calls: deque[dict] = deque(maxlen=MAX_CALLS)
        self.data: deque[dict] = deque(maxlen=MAX_DATA)
        self.data_index: dict[str, dict] = {}
        self.dedup: dict[tuple, float] = {}

        self.status = {"connected": False, "since": None, "events": 0,
                       "last_event": None, "error": None, "demo": demo}
        self.started = time.time()
        self.shutdown_event = asyncio.Event()
        self.clients: set[web.WebSocketResponse] = set()
        self.dirty = True

        d = config_dir()
        self.calls_csv = d / "calls.csv"
        self.data_csv = d / "sms_data.csv"
        self.raw_log = d / "raw_data_events.jsonl"
        self.names_cache = d / "tg_names.json"
        self.hub = MessagingHub(self.cfg["messaging"], d, self.touch)
        self.heard = Heard()
        self.watcher = Watcher(self)
        # Salaisuudet käyttöjärjestelmän salasanavarastoon (vanhat asetustiedoston arvot siirretään sinne)
        self.bm_key, moved_key = secret_store.load_field(self.cfg, "bm_api_key", "bm-api-key")
        self.hub.passcode_secret, moved_pass = secret_store.load_field(self.cfg["messaging"], "passcode", "aprs-passcode")
        self.cfg["lan"] = {**DEFAULT_LAN, **(self.cfg.get("lan") or {})}
        self.lan_hash, moved_lan = secret_store.load_field(self.cfg["lan"], "pw", "lan-password")
        new_gen = not self.cfg["lan"].get("gen")
        if new_gen:                                  # istuntoevästeiden sukupolvi (ks. lan.LanAccess)
            self.cfg["lan"]["gen"] = secrets.token_hex(8)
        self.lan = LanAccess(lambda: f'{self.lan_hash}|{self.cfg["lan"].get("gen", "")}' if self.lan_hash else "")
        self.lan_site = None
        self.lan_state = {"running": False, "error": None, "port": None}
        self.runner = None
        self.local_port = None
        if moved_key or moved_pass or moved_lan or new_gen:
            self.save_config()
        self.api = BMApi(lambda: self.bm_key)
        self.devmon = DeviceMonitor(self, self.api)
        self.stats = Stats(self)
        self.stats.load_csv(self.calls_csv)
        self.commands = Commands(self)
        self.services = ServiceHub(self)
        self.hub.command_handler = self.commands.handle
        self.updater = Updater(self)
        self.checkin = CheckIn(self, self.api)
        self.aprsrx = AprsRx(self, cfg_path.parent / "aprs_rx.sqlite")
        self.hub.rf = self.aprsrx              # APRS-viestit myös radiolla (jos sallittu)
        self.nets = NetCalendar(self, cfg_path.parent / "nets_cache")
        self.qso = QsoLog(self, cfg_path.parent / "qso_log.sqlite")
        self.ais = AisFeed(self, demo=demo)
        self.prop = PropFeed(self, demo=demo)
        self.tropo = TropoFeed(self, demo=demo)
        self.tile_sem = asyncio.Semaphore(4)
        self.tile_fail, self.tile_down_until = 0, 0.0

    def touch(self):
        self.dirty = True

    # ---------------- asetukset ----------------
    def public_config(self) -> dict:
        """Asetukset sivulle: salaisuudet vain peitettyinä."""
        return {**self.cfg, "bm_api_key": "********" if self.bm_key else "",
                "messaging": {**self.cfg["messaging"], "passcode": "********" if self.hub.passcode_secret else ""},
                "log": {**self.cfg["log"], **{f: "********" if self.qso.secrets.get(svc) else ""
                                               for svc, (f, _) in QSO_SECRETS.items()}},
                "ais": {**self.cfg["ais"], "key": "********" if self.ais.key else ""},
                "lan": {**{k: v for k, v in self.cfg["lan"].items() if k != "gen"}, "pw": "********" if self.lan_hash else ""},
                "checkin": {**(self.cfg.get("checkin") or {}),
                            "sheet": {**((self.cfg.get("checkin") or {}).get("sheet") or {}), "key": "********"}}}

    async def lan_apply(self):
        """Käynnistää / pysäyttää / vaihtaa lähiverkkokuuntelijan asetusten mukaan."""
        c = self.cfg["lan"]
        want = c.get("enabled") and self.lan_hash and self.runner is not None
        if self.lan_site is not None and (not want or self.lan_state.get("port") != c["port"]):
            try:
                await self.lan_site.stop()
            except Exception:  # noqa: BLE001
                pass
            self.lan_site = None
            self.lan_state = {"running": False, "error": None, "port": None}
        if want and self.lan_site is None:
            try:
                site = web.TCPSite(self.runner, "0.0.0.0", c["port"])
                await site.start()
                self.lan_site = site
                self.lan_state = {"running": True, "error": None, "port": c["port"]}
                log.info("Lähiverkkokäyttö: portti %s", c["port"])
            except OSError as e:
                self.lan_state = {"running": False, "error": tr("Porttia {p} ei voi avata ({e})", p=c["port"], e=e.strerror or e),
                                  "port": None}
                log.warning("Lähiverkkokäyttö: %s", e)
        elif c.get("enabled") and not self.lan_hash:
            self.lan_state = {"running": False, "error": tr("Aseta salasana"), "port": None}
        self.dirty = True

    def lan_logout_all(self, save: bool = True):
        """Uusi evästesukupolvi: kaikki etälaitteiden kirjautumiset mitätöityvät (myös uudelleenkäynnistyksen yli)."""
        self.cfg["lan"]["gen"] = secrets.token_hex(8)
        self.lan.logout_all()
        if save:
            self.save_config()

    async def lan_public(self) -> dict:
        port = self.cfg["lan"]["port"]
        links = []
        if self.lan_state.get("running"):
            ifs = await asyncio.to_thread(lanmod.interfaces)
            links = [{"url": f"http://{i['ip']}:{port}/", "name": i["name"], "virtual": i["virtual"],
                      "primary": i["primary"]} for i in ifs]
            host = await asyncio.to_thread(lanmod.local_hostname)
            if host:
                links.append({"url": f"http://{host}:{port}/", "name": "mDNS", "virtual": False, "primary": False,
                              "mdns": True})
        return {**self.lan_state, "addresses": [x["url"] for x in links], "links": links,
                "has_password": bool(self.lan_hash), "sessions": len(self.lan.sessions), "mode": self.cfg["lan"]["mode"],
                "platform": sys.platform}

    def save_config(self):
        self.cfg_path.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    def update_config(self, new: dict):
        if "talkgroups" in new:
            pats = parse_patterns(new["talkgroups"])
            self.cfg["talkgroups"] = pats
            self.matcher = Matcher(pats)
            # poista tilastot, jotka eivät enää kuulu seurattaviin
            self.tg_stats = {tg: s for tg, s in self.tg_stats.items() if self.matcher.match(tg)}
            for s in self.tg_stats.values():
                s["pattern"] = self.matcher.match(s["tg"])
        if "data_filter" in new and new["data_filter"] in ("all", "matched"):
            self.cfg["data_filter"] = new["data_filter"]
        if "min_duration" in new:
            self.cfg["min_duration"] = max(0, float(new["min_duration"] or 0))
        if "log_csv" in new:
            self.cfg["log_csv"] = bool(new["log_csv"])
        if "calls_limit" in new:
            n = int(new["calls_limit"])
            if n not in CALLS_LIMITS:
                raise ValueError(tr("Sallitut arvot: {v}", v=", ".join(map(str, CALLS_LIMITS))))
            self.cfg["calls_limit"] = n
        if "watch" in new:
            w = new["watch"]
            out = dict(self.cfg["watch"])
            if "list" in w:
                items = w["list"].replace(",", " ").split() if isinstance(w["list"], str) else w["list"]
                lst = []
                for x in items:
                    x = str(x).strip().upper().split("-")[0]
                    if not x:
                        continue
                    if not CALL_OR_ID.fullmatch(x):
                        raise ValueError(tr("Virheellinen kutsu tai DMR-ID: {x}", x=x))
                    if x not in lst:
                        lst.append(x)
                out["list"] = lst
            if "quiet_min" in w:
                out["quiet_min"] = max(0, min(1440, float(w["quiet_min"] or 0)))
            if w.get("scope") in ("all", "matched"):
                out["scope"] = w["scope"]
            for k in ("desktop", "sound", "sms"):
                if k in w:
                    out[k] = bool(w[k])
            self.cfg["watch"] = out
        if "devices" in new:
            dv = new["devices"]
            out = dict(self.cfg["devices"])
            if "list" in dv:
                lst, seen = [], set()
                for d in dv["list"]:
                    did = str(d.get("id") if isinstance(d, dict) else d).strip()
                    if not did:
                        continue
                    if not did.isdigit() or not 4 <= len(did) <= 10:
                        raise ValueError(tr("Virheellinen laitteen ID: {x}", x=did))
                    if int(did) in seen:
                        continue
                    seen.add(int(did))
                    own = d.get("own") if isinstance(d, dict) else None
                    lst.append({"id": int(did), "note": str(d.get("note", "") if isinstance(d, dict) else "")[:40],
                                "own": own if own in (True, False) else None})
                out["list"] = lst
            if "offline_min" in dv:
                out["offline_min"] = max(2, min(1440, float(dv["offline_min"] or 15)))
            if "sms" in dv:
                out["sms"] = bool(dv["sms"])
            if "my_call" in dv:
                mc = str(dv["my_call"] or "").strip().upper().split("-")[0]
                if mc and not re.fullmatch(r"[A-Z0-9]{3,8}", mc):
                    raise ValueError(tr("Virheellinen kutsu: {x}", x=mc))
                out["my_call"] = mc
            self.cfg["devices"] = out
            self.devmon._ids_cache = self.devmon.ids()
            self.devmon.wake.set()
        if "services" in new:
            out, changed = normalize_services(new["services"], self.cfg["services"])
            self.cfg["services"] = out
            self.services.reconfigure(changed)
        if "language" in new:
            lang = str(new["language"] or "")
            if lang not in LANGS:
                raise ValueError(tr("Virheellinen kieli"))
            self.cfg["language"] = lang
            set_lang(lang)
        if "units" in new:
            if new["units"] not in ("metric", "imperial"):
                raise ValueError(tr("Virheellinen yksikkö"))
            self.cfg["units"] = new["units"]
            set_units(new["units"])
        if "log" in new:
            lg = dict(new["log"] or {})
            keys = {svc: lg.pop(f, None) for svc, (f, _) in QSO_SECRETS.items()}
            self.cfg["log"] = normalize_log(lg, self.cfg["log"])
            for svc, key in keys.items():
                if key is not None and key != "********":
                    self.qso.set_secret(svc, str(key))
            self.qso._changed()
        if "nets" in new:
            self.cfg["nets"] = normalize_nets(new["nets"] or {}, self.cfg["nets"])
            self.nets.reconfigure()
        if "ais" in new:
            a = dict(new["ais"] or {})
            key = a.pop("key", None)
            old = {k: self.cfg["ais"].get(k) for k in ("enabled", "source", "km")}
            self.cfg["ais"] = normalize_ais(a, self.cfg["ais"])
            if key is not None and key != "********":
                self.ais.set_key(str(key))
            elif old != {k: self.cfg["ais"].get(k) for k in ("enabled", "source", "km")}:
                self.ais.reconfigure()
            self.dirty = True
        if "propagation" in new:
            self.cfg["propagation"] = normalize_prop(new["propagation"] or {}, self.cfg["propagation"])
            self.prop.reconfigure()
            self.tropo.reconfigure()
            self.dirty = True
        elif any(k in new for k in ("services", "log", "devices", "messaging")) and self.cfg["propagation"].get("enabled"):
            self.prop.reconfigure()        # oma kutsu tai sijainti voi vaihtua → uudet tilaukset
            self.tropo.reconfigure()
        if "aprsrx" in new:
            old_km = self.cfg["aprsrx"].get("is_km")
            self.cfg["aprsrx"] = normalize_aprsrx(new["aprsrx"] or {}, self.cfg["aprsrx"])
            self.aprsrx.reconfigure()
            if self.cfg["aprsrx"].get("is_km") != old_km:
                self.hub.reconfigure(self.cfg["messaging"])      # APRS-IS-suodatin muuttuu → uusi yhteys
        if "map_tiles" in new:
            t = str(new["map_tiles"] or "").strip() or DEFAULT_TILES
            if not re.match(r"^https?://[^\s]+$", t) or not all(k in t for k in ("{z}", "{x}", "{y}")):
                raise ValueError(tr("Karttatiilten osoitteessa pitää olla {z}, {x} ja {y}"))
            self.cfg["map_tiles"] = t
            self.cfg["map_tiles_attr"] = str(new.get("map_tiles_attr") or "").strip()[:200] or (
                DEFAULT_TILES_ATTR if t == DEFAULT_TILES else "")
        if "theme" in new:
            if new["theme"] not in ("dark", "light", "auto"):
                raise ValueError(tr("Virheellinen valinta"))
            self.cfg["theme"] = new["theme"]
        if "ui" in new:
            if new["ui"] not in ("window", "browser"):
                raise ValueError(tr("Virheellinen valinta"))
            self.cfg["ui"] = new["ui"]
        if "hidden_tabs" in new:
            ht = new["hidden_tabs"] or []
            if not isinstance(ht, list) or any(t not in HIDEABLE_TABS for t in ht):
                raise ValueError(tr("Virheellinen valinta"))
            self.cfg["hidden_tabs"] = [t for t in HIDEABLE_TABS if t in ht]
        if "update_check" in new:
            self.cfg["update_check"] = bool(new["update_check"])
        if "update_beta" in new:
            self.cfg["update_beta"] = bool(new["update_beta"])
            self.updater.wake.set()
        if "bm_api_key" in new:
            key = str(new["bm_api_key"] or "").strip()
            if key != "********":
                secret_store.save_field(self.cfg, "bm_api_key", "bm-api-key", key)
                self.bm_key = key
        if "messaging" in new:
            msg = dict(new["messaging"])
            pc = msg.pop("passcode", "********")
            self.cfg["messaging"] = normalize_messaging(msg, self.cfg["messaging"])
            if pc != "********":
                pc = validate_passcode(pc)
                secret_store.save_field(self.cfg["messaging"], "passcode", "aprs-passcode", pc)
                self.hub.passcode_secret = pc
            self.hub.reconfigure(self.cfg["messaging"])
        self.save_config()
        self.dirty = True

    # ---------------- puheryhmien nimet ----------------
    async def load_tg_names(self):
        if self.names_cache.exists():
            try:
                self.tg_names = {int(k): v for k, v in json.loads(self.names_cache.read_text("utf-8")).items()}
            except Exception:  # noqa: BLE001
                pass
            if time.time() - self.names_cache.stat().st_mtime < 86400 and self.tg_names:
                return
        if self.demo:
            return
        try:
            async with http_session(20) as s:
                async with s.get(TG_NAMES_URL) as r:
                    r.raise_for_status()
                    data = await r.json(content_type=None)
            if isinstance(data, dict):
                self.tg_names = {int(k): str(v) for k, v in data.items() if str(k).isdigit()}
            elif isinstance(data, list):
                self.tg_names = {int(x["ID"]): str(x.get("Name", "")) for x in data if "ID" in x}
            self.names_cache.write_text(json.dumps(self.tg_names, ensure_ascii=False), encoding="utf-8")
            log.info("Ladattu %d puheryhmän nimeä", len(self.tg_names))
            self.dirty = True
        except Exception as e:  # noqa: BLE001
            log.warning("Puheryhmien nimien haku epäonnistui: %s", e)

    def tg_name(self, tg) -> str:
        try:
            return self.tg_names.get(int(tg), "")
        except (TypeError, ValueError):
            return ""

    # ---------------- tapahtumien käsittely ----------------
    def handle_payload(self, p: dict, history: bool = False):
        try:
            self._handle_payload(p, history)
        except Exception:  # noqa: BLE001
            n = self.status.get("handler_errors", 0) + 1
            self.status["handler_errors"] = n
            if n <= 5:
                log.exception("Virhe tapahtuman käsittelyssä: %s", str(p)[:300])

    def _handle_payload(self, p: dict, history: bool = False):
        for k in ("Start", "Stop", "DestinationID", "SourceID"):
            v = p.get(k)
            if isinstance(v, str) and v.strip().lstrip("-").isdigit():
                p[k] = int(v)
        for k in ("SourceCall", "LinkCall", "TalkerAlias", "SourceName", "DestinationName", "DestinationCall"):
            if isinstance(p.get(k), str):
                p[k] = p[k].strip()
        if not p.get("SourceCall") and p.get("TalkerAlias"):
            p["SourceCall"] = p["TalkerAlias"].split()[0]
        if p.get("DestinationName") and p.get("DestinationID") is not None:
            try:
                self.tg_names.setdefault(int(p["DestinationID"]), p["DestinationName"])
            except (TypeError, ValueError):
                pass
        self.status["events"] += 1
        self.status["last_event"] = time.time()
        if is_data_session(p):
            self._handle_data(p)
            return
        self.heard.update(p, self.tg_name)
        self.watcher.on_event(p, history)
        self.devmon.on_event(p)
        if is_group_call(p):
            self.checkin.on_event(p, history)
        self.qso.on_event(p)
        tg = p.get("DestinationID")
        pattern = self.matcher.match(tg)
        if not pattern or not is_group_call(p):
            return
        self._handle_voice(p, int(tg), pattern)

    def _stats(self, tg: int, pattern: str) -> dict:
        s = self.tg_stats.get(tg)
        if s is None:
            s = {"tg": tg, "pattern": pattern, "count": 0, "airtime": 0.0,
                 "last": None, "active": None}
            self.tg_stats[tg] = s
        return s

    def _handle_voice(self, p: dict, tg: int, pattern: str):
        ev = p.get("Event", "")
        sid = p.get("SessionID") or f"{p.get('SourceID')}-{tg}-{p.get('Start')}"
        start = p.get("Start") or time.time()
        stop = p.get("Stop") or 0
        info = {
            "tg": tg,
            "tg_name": self.tg_name(tg),
            "call": p.get("SourceCall") or "",
            "name": p.get("SourceName") or "",
            "id": p.get("SourceID"),
            "alias": p.get("TalkerAlias") or "",
            "via": p.get("LinkCall") or p.get("LinkName") or "",
            "context": p.get("ContextID"),
            "master": p.get("Master"),
            "slot": p.get("Slot"),
            "start": start,
            "stop": stop,
        }
        s = self._stats(tg, pattern)

        if stop and stop > 0 or ev == "Session-Stop":
            self.sessions.pop(sid, None)
            if s["active"] and s["active"].get("sid") == sid:
                s["active"] = None
            if not stop:
                stop = time.time()
            dur = max(0.0, float(stop) - float(start))
            info["stop"] = stop
            info["duration"] = round(dur, 1)
            key = (info["id"], tg, int(float(start) // 3))
            if key in self.dedup:
                return
            self.dedup[key] = time.time()
            try:
                min_dur = float(self.cfg.get("min_duration") or 0)
            except (TypeError, ValueError):
                min_dur = 0.0
            if dur < min_dur:
                self.status["skipped_short"] = self.status.get("skipped_short", 0) + 1
                self.dirty = True
                return
            s["count"] += 1
            s["airtime"] += dur
            s["last"] = info
            keep = True
            if len(self.calls) == self.calls.maxlen:
                oldest = min(self.calls, key=lambda c: float(c.get("start") or 0))
                if float(info["start"]) < float(oldest.get("start") or 0):
                    keep = False          # vanhempi kuin kaikki listassa olevat
                else:
                    self.calls.remove(oldest)
            if keep:
                self.calls.appendleft(info)
            self.stats.add(info)
            self._csv(self.calls_csv,
                      ["aika", "tg", "tg_nimi", "kutsu", "nimi", "dmr_id", "kesto_s", "kautta"],
                      [_iso(start), tg, info["tg_name"], info["call"], info["name"],
                       info["id"], info["duration"], info["via"]])
        else:
            info["sid"] = sid
            self.sessions[sid] = info
            if s["active"] is None or s["active"].get("sid") == sid or \
                    float(start) >= float(s["active"].get("start", 0)):
                s["active"] = info
        self.dirty = True

    def _handle_data(self, p: dict):
        dst = p.get("DestinationID")
        if self.cfg.get("data_filter") == "matched" and not self.matcher.match(dst) \
                and not self.matcher.match(p.get("SourceID")):
            return
        sid = p.get("SessionID") or f"{p.get('SourceID')}-{dst}-{p.get('Start')}"
        text = ""
        for f in TEXT_FIELDS:
            v = p.get(f)
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break
        ct = p.get("CallTypes") or []
        entry = self.data_index.get(sid)
        if entry is None:
            entry = {
                "sid": sid,
                "start": p.get("Start") or time.time(),
                "from_call": p.get("SourceCall") or "",
                "from_name": p.get("SourceName") or "",
                "from_id": p.get("SourceID"),
                "to_call": p.get("DestinationCall") or "",
                "to_id": dst,
                "to_name": self.tg_name(dst) if "Group" in ct else (p.get("DestinationName") or ""),
                "kind": "ryhmä" if "Group" in ct else ("yksityinen" if "Private" in ct else ""),
                "via": p.get("LinkCall") or p.get("LinkName") or "",
                "types": ", ".join(map(str, ct)) if isinstance(ct, list) else str(ct),
                "count": p.get("DataCount") or 0,
                "text": text,
                "done": False,
            }
            self.data_index[sid] = entry
            self.data.appendleft(entry)
            # siivoa indeksi
            if len(self.data_index) > MAX_DATA * 2:
                keep = {e["sid"] for e in self.data}
                self.data_index = {k: v for k, v in self.data_index.items() if k in keep}
        else:
            entry["count"] = p.get("DataCount") or entry["count"]
            if text:
                entry["text"] = text
        if (p.get("Stop") or 0) > 0 or p.get("Event") == "Session-Stop":
            if not entry["done"]:
                entry["done"] = True
                self._csv(self.data_csv,
                          ["aika", "lahettaja", "lahettaja_id", "vastaanottaja", "vastaanottaja_id",
                           "tyyppi", "kautta", "datablokit", "teksti"],
                          [_iso(entry["start"]), entry["from_call"], entry["from_id"], entry["to_call"],
                           entry["to_id"], entry["kind"], entry["via"], entry["count"], entry["text"]])
        self._raw(p)
        self.dirty = True

    def _csv(self, path: Path, header: list, row: list):
        if not self.cfg.get("log_csv", True):
            return
        try:
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(header)
                w.writerow(row)
        except Exception as e:  # noqa: BLE001
            log.warning("CSV-kirjoitus epäonnistui: %s", e)

    def _raw(self, p: dict):
        """Tallentaa dataliikenteen raakatapahtumat, jotta kenttiä voi tutkia."""
        try:
            if self.raw_log.exists() and self.raw_log.stat().st_size > RAW_LOG_MAX_BYTES:
                self.raw_log.replace(self.raw_log.with_suffix(".old.jsonl"))
            with self.raw_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def housekeeping(self):
        self.qso.housekeeping()
        now = time.time()
        for sid, info in list(self.sessions.items()):
            if now - float(info.get("start", now)) > STALE_ACTIVE_SEC:
                self.sessions.pop(sid, None)
                s = self.tg_stats.get(info["tg"])
                if s and s["active"] and s["active"].get("sid") == sid:
                    s["active"] = None
                    self.dirty = True
        self.dedup = {k: t for k, t in self.dedup.items() if now - t < 600}

    def watch_status(self) -> list[dict]:
        out = []
        for k in self.cfg["watch"].get("list", []):
            hits = self.heard.lookup(dmr_id=k) if k.isdigit() else self.heard.lookup(call=k)
            e = hits[0] if hits else {}
            out.append({"key": k, "call": e.get("call") or ("" if k.isdigit() else k), "id": e.get("id"),
                        "name": e.get("name", ""), "tg": e.get("tg"), "tg_name": e.get("tg_name", ""),
                        "to": e.get("to_call"), "via": e.get("via", ""), "start": e.get("start"),
                        "active": bool(e.get("active")) and time.time() - float(e.get("start") or 0) < 300,
                        "last_activity": self.watcher.last_activity.get(k)})
        return out

    # ---------------- tila käyttöliittymälle ----------------
    def snapshot(self) -> dict:
        rows = []
        seen = set()
        for tg, s in self.tg_stats.items():
            seen.add(tg)
            rows.append({**s, "name": self.tg_name(tg)})
        for p in self.cfg["talkgroups"]:
            if p.isdigit() and int(p) not in seen:
                rows.append({"tg": int(p), "pattern": p, "name": self.tg_name(p), "count": 0,
                             "airtime": 0, "last": None, "active": None})
        return {
            "type": "snapshot",
            "now": time.time(),
            "version": __version__, "date": __date__,
            "ui": {"window": self.ui is not None, "available": desktop.available()},
            "aprsrx": self.aprsrx.snapshot(),
            "ais": self.ais.snapshot(),
            "prop": self.prop.snapshot(),
            "log": self.qso.snapshot(),
            "config": self.public_config(),
            "status": self.status,
            "started": self.started,
            "talkgroups": rows,
            "messaging": self.hub.status(),
            "alerts": list(self.watcher.alerts)[:50],
            "watch_status": self.watch_status(),
            "devices": self.devmon.snapshot(),
            "services": self.services.snapshot(),
            "update": self.updater.snapshot(),
            "checkin": self.checkin.snapshot(),
            "calls": sorted(self.calls, key=lambda c: float(c.get("start") or 0), reverse=True)[:200],
            "data": sorted(self.data, key=lambda c: float(c.get("start") or 0), reverse=True)[:200],
            "paths": {"config": str(self.cfg_path), "calls_csv": str(self.calls_csv),
                      "data_csv": str(self.data_csv), "raw": str(self.raw_log)},
        }

    async def broadcast_loop(self):
        while True:
            await asyncio.sleep(0.5)
            self.housekeeping()
            if not self.dirty or not self.clients:
                continue
            self.dirty = False
            msg = json.dumps(self.snapshot(), ensure_ascii=False, default=str)
            for ws in list(self.clients):
                try:
                    await send_ws(ws, msg)
                except Exception:  # noqa: BLE001
                    self.clients.discard(ws)


WS_CHUNK = int(os.environ.get("BMM_WS_CHUNK", 256 * 1024))     # ympäristömuuttuja vain testaukseen


async def send_ws(ws, msg: str):
    """Suuri tila osissa: iOS/iPadOS Safarin WebSocket-toteutus ei välttämättä ota vastaan yli 1 Mt:n viestiä.
    Osaviesti: "\x01<tunniste>/<osa>/<osia>|<data>"; selain kokoaa osat ennen JSONin jäsennystä."""
    if len(msg) <= WS_CHUNK:
        await ws.send_str(msg)
        return
    tag = random.randrange(1 << 30)
    parts = [msg[i:i + WS_CHUNK] for i in range(0, len(msg), WS_CHUNK)]
    for i, part in enumerate(parts):
        await ws.send_str(f"\x01{tag}/{i}/{len(parts)}|{part}")


def _iso(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ""


# --------------------------------------------------------------------------
# Brandmeister-yhteys
# --------------------------------------------------------------------------
def decode_payload(msg):
    """BM lähettää {'topic': ..., 'payload': '<json-merkkijono>'}; hyväksytään myös muut muodot."""
    if isinstance(msg, (list, tuple)) and msg:
        msg = msg[0]
    payload = msg.get("payload", msg) if isinstance(msg, dict) else msg
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload if isinstance(payload, dict) else None


def bm_thread(mon: Monitor, loop: asyncio.AbstractEventLoop):
    """Sama synkroninen socket.io-asiakas kuin Brandmeisterin virallisessa
    Python-esimerkissä. Ajetaan omassa säikeessään."""
    sio = socketio.Client(reconnection=True, reconnection_delay=2,
                          reconnection_delay_max=30, logger=False, engineio_logger=False,
                          websocket_extra_options=ws_options())
    warned = {"parse": False}

    def call(fn, *a):
        if loop.is_closed():          # ohjelma sulkeutuu: myöhässä tulevat viestit ohitetaan
            return
        try:
            loop.call_soon_threadsafe(fn, *a)
        except RuntimeError:
            pass

    def set_status(**kw):
        mon.status.update(**kw)
        mon.dirty = True

    history: list[dict] = []

    @sio.event
    def connect():
        log.info("Yhdistetty Brandmeisteriin")
        call(lambda: set_status(connected=True, since=time.time(), error=None))
        # Ilman join-viestiä palvelin ei lähetä mitään. "everything" = koko verkko,
        # jota tarvitaan *-hakuihin ja koko verkon dataliikenteeseen.
        history.clear()
        sio.emit("join", "everything")
        sio.emit("searchHouse", {"query": None, "amount": HISTORY_ROWS})

    @sio.on("searchHouseComplete")
    def history_done(*_):
        rows = sorted(history, key=lambda p: float(p.get("Start") or 0))
        history.clear()
        log.info("Historiasta ladattu %d tapahtumaa", len(rows))
        for p in rows:
            call(mon.handle_payload, p, True)

    @sio.event
    def connect_error(data):
        log.warning("Yhteysvirhe: %s", data)
        call(lambda: set_status(connected=False, error=str(data)))

    @sio.event
    def disconnect(*_):
        log.warning("Yhteys Brandmeisteriin katkesi")
        call(lambda: set_status(connected=False))

    def on_any(event, data):
        mon.status["raw"] = mon.status.get("raw", 0) + 1
        mon.status["last_raw_event"] = event
        try:
                p = decode_payload(data)
        except Exception as e:  # noqa: BLE001
            if not warned["parse"]:
                warned["parse"] = True
                log.warning("Tuntematon viestimuoto (%s): %s – %r", event, e, str(data)[:300])
            return
        if p is not None:
            call(mon.handle_payload, p)

    @sio.on("mqtt")
    def on_mqtt(data):
        if isinstance(data, dict) and data.get("topic") == "LH-Startup":
            try:
                p = decode_payload(data)
                if p:
                    history.append(p)
            except Exception:  # noqa: BLE001
                pass
            return
        on_any("mqtt", data)

    @sio.on("*")
    def catch_all(event, data=None):
        if mon.status.get("raw", 0) < 5:
            log.info("Tapahtuma '%s': %s", event, str(data)[:200])
        on_any(event, data)

    while True:
        try:
            sio.connect(BM_URL, socketio_path=LH_PATH, transports=["websocket"], wait_timeout=20)
            sio.wait()
        except Exception as e:  # noqa: BLE001
            log.warning("Yhteysvirhe: %s – yritetään uudelleen 10 s päästä", e)
            call(lambda e=e: set_status(connected=False, error=str(e)))
        time.sleep(10)


async def bm_loop(mon: Monitor):
    import threading
    loop = asyncio.get_running_loop()
    threading.Thread(target=bm_thread, args=(mon, loop), daemon=True).start()
    while True:
        await asyncio.sleep(3600)


def diagnose(seconds: int = 30):
    """Tulostaa mitä Brandmeisterin palvelimelta oikeasti tulee."""
    import platform
    import engineio
    print(f"{APP_NAME} {__version__} – vianetsintä")
    print(f"Python {platform.python_version()} / {platform.system()} {platform.release()}")
    print(f"python-socketio {getattr(socketio, '__version__', '?')}, "
          f"python-engineio {getattr(engineio, '__version__', '?')}")
    counts: dict[str, int] = {}
    shown = [0]
    sio = socketio.Client(logger=False, engineio_logger=False, websocket_extra_options=ws_options())

    @sio.event
    def connect():
        print(f"[{time.strftime('%H:%M:%S')}] YHDISTETTY, kuunnellaan {seconds} s…")
        sio.emit("join", "everything")

    @sio.event
    def connect_error(data):
        print("YHTEYSVIRHE:", data)

    @sio.event
    def disconnect(*_):
        print("Yhteys katkesi")

    @sio.on("*")
    def any_event(event, data=None):
        counts[event] = counts.get(event, 0) + 1
        if shown[0] < 5:
            shown[0] += 1
            print(f"\n--- tapahtuma '{event}' ---\n{str(data)[:800]}")

    @sio.on("mqtt")
    def mqtt(data):
        any_event("mqtt", data)

    try:
        sio.connect(BM_URL, socketio_path=LH_PATH, transports=["websocket"], wait_timeout=20)
        time.sleep(seconds)
    except Exception as e:  # noqa: BLE001
        print("VIRHE:", repr(e))
    finally:
        try:
            sio.disconnect()
        except Exception:  # noqa: BLE001
            pass
    print("\nYhteenveto:", counts or "EI YHTÄÄN TAPAHTUMAA")


async def demo_loop(mon: Monitor):
    """Tuottaa keksittyä liikennettä käyttöliittymän testaamiseen."""
    mon.status.update(connected=True, since=time.time())
    mon.tg_names.update({244: "Finland", 2441: "Uusimaa", 2442: "Turku", 2443: "Tampere",
                         2449: "Lappi", 91: "Worldwide", 24400: "Finland Chat"})
    users = [("OH2ABC", "Matti", 2440001), ("OH3XYZ", "Pekka", 2440002), ("OH6QQ", "Liisa", 2440003),
             ("OH1TT", "Juha", 2440004), ("DL1AAA", "Hans", 2620001), ("OH8RR", "Anna", 2440005)]
    tgs = [244, 2441, 2442, 2443, 2449, 91, 24400, 3100]
    me = mon.qso.my_call()
    if me:      # esimerkkiyhteydet lokin kokeilemiseen: vuorottelu TG 244:ssä ja yksityispuhelu
        t = time.time() - 900
        for i, (c, nm, uid) in enumerate([(me, "", 2440999), ("OH2ABC", "Matti", 2440001), (me, "", 2440999),
                                         ("OH2ABC", "Matti", 2440001)]):
            mon.handle_payload({"SessionID": f"dq{i}", "SourceCall": c, "SourceName": nm, "SourceID": uid,
                                "DestinationID": 244, "CallTypes": ["Voice", "Group"], "LinkCall": "OH2RUC",
                                "Start": int(t + i * 60), "Stop": int(t + i * 60 + 40), "Event": "Session-Stop"})
        mon.handle_payload({"SessionID": "dqp", "SourceCall": "OH6QQ", "SourceName": "Liisa", "SourceID": 2440003,
                            "DestinationID": 2440999, "DestinationCall": me, "CallTypes": ["Voice", "Private"],
                            "Start": int(time.time() - 120), "Stop": int(time.time() - 90), "Event": "Session-Stop"})
    n = 0
    while True:
        n += 1
        call, name, uid = random.choice(users)
        start = time.time()
        sid = f"demo-{n}"
        if random.random() < 0.2:
            to = random.choice(users)
            base = {"SessionID": sid, "SourceCall": call, "SourceName": name, "SourceID": uid,
                    "DestinationID": to[2], "DestinationCall": to[0], "CallTypes": ["Data", "Private"],
                    "LinkCall": "OH2RUA", "Start": int(start), "Stop": 0, "DataCount": 3}
            mon.handle_payload({**base, "Event": "Session-Start"})
            await asyncio.sleep(1)
            mon.handle_payload({**base, "Event": "Session-Stop", "Stop": int(time.time())})
            await asyncio.sleep(random.uniform(1, 3))
            continue
        tg = random.choice(tgs)
        base = {"SessionID": sid, "SourceCall": call, "SourceName": name, "SourceID": uid,
                "DestinationID": tg, "CallTypes": ["Voice", "Group"], "LinkCall": "OH2RUC",
                "Slot": 2, "Start": int(start), "Stop": 0}
        mon.handle_payload({**base, "Event": "Session-Start"})
        dur = random.uniform(1, 12)
        await asyncio.sleep(dur)
        mon.handle_payload({**base, "Event": "Session-Stop", "Stop": int(start + dur)})
        await asyncio.sleep(random.uniform(0.5, 4))


# --------------------------------------------------------------------------
# Web-palvelin
# --------------------------------------------------------------------------
REMOTE_KEY = web.RequestKey("remote", bool) if hasattr(web, "RequestKey") else "remote"


def make_app(mon: Monitor) -> web.Application:
    @web.middleware
    async def lan_guard(request, handler):
        """Lähiverkkoportin pyynnöt: kirjautuminen, aina kielletyt toiminnot, Vain katselu -tila, Origin-tarkistus."""
        sock = request.transport.get_extra_info("sockname") if request.transport else None
        remote = bool(mon.lan_site is not None and sock and mon.local_port and sock[1] != mon.local_port)
        request[REMOTE_KEY] = remote
        if not lanmod.host_allowed(request.headers.get("Host")):
            log.warning("Pyyntö hylätty: tuntematon Host-nimi %r", request.headers.get("Host", "")[:80])
            return web.Response(status=421, text="Misdirected request")
        if not remote:
            return await handler(request)
        path, method = request.path, request.method
        if path.startswith("/static/") or path in lanmod.REMOTE_PUBLIC:
            return await handler(request)
        token = request.cookies.get(lanmod.COOKIE)
        if not mon.lan.check(token):
            if path == "/" and method == "GET":
                raise web.HTTPFound("/login")
            return web.json_response({"ok": False, "error": tr("Kirjaudu sisään")}, status=401)
        if path == "/" and method == "GET":         # sivun latauksessa eväste uusitaan (liukuva voimassaolo)
            fresh = mon.lan.renew(token)
            if fresh:
                resp = await handler(request)
                resp.set_cookie(lanmod.COOKIE, fresh, max_age=lanmod.SESSION_DAYS * 86400, httponly=True,
                                samesite="Strict", path="/")
                return resp
        if method not in ("GET", "HEAD"):
            origin = request.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] != request.host:
                return web.json_response({"ok": False, "error": tr("Kielletty")}, status=403)
        deny = (path, method) in lanmod.REMOTE_DENY or \
            (mon.cfg["lan"]["mode"] == "view" and method not in ("GET", "HEAD") and path != "/api/lan/logout")
        if deny:
            return web.json_response({"ok": False, "error": tr("Tämä toiminto on käytettävissä vain ohjelman omalla koneella.")},
                                     status=403)
        return await handler(request)

    async def no_stale_page(request, response):
        """Sivu ja sen skriptit aina tuoreina: päivityksen jälkeen selain (esim. iPadin Safari) ei saa yhdistellä
        uutta sivua ja vanhoja välimuistissa olevia skriptejä. Karttatiilet saavat jäädä välimuistiin."""
        if request.path == "/" or request.path == "/login" or request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"

    app = web.Application(middlewares=[lan_guard])
    app.on_response_prepare.append(no_stale_page)

    index_cache: dict = {}

    async def index(_):
        """Sivu muistista; skripti- ja tyylitiedostoihin versio (?v=…), jotta selain ei käytä vanhaa
        (mahdollisesti rikki tullutta) kopiota välimuistista päivityksen jälkeen."""
        f = STATIC_DIR / "index.html"
        mt = f.stat().st_mtime
        if index_cache.get("mt") != mt:
            html = f.read_text(encoding="utf-8")
            html = re.sub(r'((?:src|href)="/static/[^"?#]+\.(?:js|css))"', rf'\1?v={__version__}"', html)
            index_cache.update(mt=mt, body=html.encode("utf-8"))
        return web.Response(body=index_cache["body"], content_type="text/html", charset="utf-8")

    async def ws_handler(request):
        remote = request.get(REMOTE_KEY)
        if remote:
            log.info("Lähiverkko: WebSocket %s (%s)", request.remote, request.headers.get("User-Agent", "")[:80])
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        mon.clients.add(ws)
        try:
            await send_ws(ws, json.dumps(mon.snapshot(), ensure_ascii=False, default=str))
            async for _ in ws:
                pass
        finally:
            mon.clients.discard(ws)
            if remote:
                log.info("Lähiverkko: WebSocket suljettu %s", request.remote)
        return ws

    async def snapshot_http(_):
        """Sama tila kuin WebSocketissa – varatie selaimille, joissa WebSocket ei aukea."""
        return web.Response(text=json.dumps(mon.snapshot(), ensure_ascii=False, default=str), content_type="application/json")

    client_log_t: list = []

    async def client_log(request):
        """Selaimen virheet ja yhteystapahtumat ohjelman lokiin (vianetsintä etälaitteilla)."""
        now = time.time()
        client_log_t[:] = [t for t in client_log_t if now - t < 60]
        if len(client_log_t) < 30:
            client_log_t.append(now)
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
            log.info("Selain %s%s: %s", request.remote, " (lähiverkko)" if request.get(REMOTE_KEY) else "",
                     str(body.get("msg", ""))[:500])
        return web.json_response({"ok": True})

    async def get_config(_):
        return web.json_response(mon.public_config())

    async def post_config(request):
        try:
            body = await request.json()
            if request.get(REMOTE_KEY) and (any(k in body for k in lanmod.CONFIG_DENY) or
                                          lanmod.has_secret_change(body, [f for f, _ in QSO_SECRETS.values()])):
                return web.json_response({"ok": False, "error": tr("Avaimia ja salasanoja voi muuttaa vain ohjelman omalla koneella.")},
                                         status=403)
            if request.get(REMOTE_KEY) and lanmod.has_address_change(body, mon.cfg):
                return web.json_response({"ok": False, "error": tr("Palvelin- ja syöteosoitteita voi muuttaa vain ohjelman omalla koneella.")},
                                         status=403)
            body.pop("lan", None)               # lähiverkkoasetukset vain /api/lan-kautta
            mon.update_config(body)
            return web.json_response({"ok": True, "config": mon.public_config()})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def clear(_):
        mon.calls.clear()
        mon.data.clear()
        mon.data_index.clear()
        for s in mon.tg_stats.values():
            s.update(count=0, airtime=0.0, last=None)
        mon.dirty = True
        return web.json_response({"ok": True})

    async def test_message(request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        text = (body.get("text") or "").strip() or tr("Monitor for Amateur Radio: testiviesti {t}", t=time.strftime("%H:%M"))
        m = mon.cfg["messaging"]
        if not m.get("enabled"):
            return web.json_response({"ok": False, "error": tr("Lähetys ei ole päällä")}, status=400)
        if not m.get("recipients"):
            return web.json_response({"ok": False, "error": tr("Vastaanottajia ei ole määritetty")}, status=400)
        mon.hub.announce("", text, source="testi")
        return web.json_response({"ok": True})

    async def check_feeds(_):
        mon.hub.feeds.wake.set()
        return web.json_response({"ok": True})

    async def preview_feed(request):
        url = request.query.get("url", "")
        try:
            title, items = await mon.hub.feeds.fetch(url)
            return web.json_response({"ok": True, "title": title, "items": items[:5]})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)}, status=400)

    async def fb_bundle(request):
        """Palautepaketti ladattavaksi (käyttäjä voi tarkistaa sisällön)."""
        data, _names = await in_thread(feedback.build_bundle, mon, request.query.get("message", ""), request.query.get("logs") == "1")
        name = feedback.bundle_name(request.query.get("call", ""))
        return web.Response(body=data, content_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def fb_info(request):
        _data, names = await in_thread(feedback.build_bundle, mon, "", request.query.get("logs") == "1")
        call = mon.cfg["devices"].get("my_call") or (mon.cfg["messaging"].get("login") or "").split("-")[0]
        return web.json_response({"files": names, "size": len(_data), "call": call,
                                  "enabled": bool(mon.cfg.get("feedback_url") or feedback.FEEDBACK_URL)})

    async def fb_send(request):
        b = await request.json()
        msg = str(b.get("message") or "").strip()
        if not msg:
            return web.json_response({"ok": False, "error": tr("Kirjoita viesti")}, status=400)
        url = mon.cfg.get("feedback_url") or feedback.FEEDBACK_URL
        if not url:
            return web.json_response({"ok": False, "error": tr("Palautteen lähetys ei ole vielä käytössä – tallenna paketti ja lähetä se itse.")}, status=400)
        call = str(b.get("call") or "").strip().upper()
        data, names = await in_thread(feedback.build_bundle, mon, msg, bool(b.get("logs")))
        name = feedback.bundle_name(call)
        try:
            await feedback.send(url, feedback.payload(msg, call, str(b.get("email") or "").strip(), data, name))
        except RuntimeError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=502)
        log.info("Palaute lähetetty (%s, %d tavua, %s)", name, len(data), ", ".join(names))
        return web.json_response({"ok": True, "name": name})

    async def sat_map(request):
        """Satelliitit-välilehden kartta: lasketaan pyydettäessä (sivu kysyy muutaman sekunnin välein)."""
        svc = mon.services.services["satellites"]
        data = await in_thread(svc.map_data)
        return web.json_response(data)

    async def ma_area_list(request):
        """MeteoAlarm-maan alueet (nimi ja EMMA_ID) syötteestä alueen valintaa varten."""
        country = request.query.get("country", "")
        if country not in MA_COUNTRIES:
            return web.json_response({"ok": False, "error": tr("Tuntematon maa: {c}", c=country)}, status=400)
        try:
            items = parse_meteoalarm(await fetch_text(MA_FEED.format(country), 60))
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)}, status=502)
        areas = ma_areas(items)
        return web.json_response({"ok": True, "areas": areas, "polygons": any(a["poly"] for a in areas),
                                  "warnings": len(items)})

    async def station(request):
        q = (request.query.get("q") or "").strip().upper().split("-")[0]
        if not q:
            return web.json_response({"ok": False, "error": tr("Anna kutsu tai DMR-ID")}, status=400)
        is_id = q.isdigit()
        out = {"ok": True, "q": q, "registry": [], "devices": [], "heard": [], "errors": []}
        call = q
        try:
            if is_id:
                r = await mon.api.registry(int(q))
                if r:
                    out["registry"] = [r] if isinstance(r, dict) else list(r)
                    call = (out["registry"][0].get("Call") or "").upper() or q
            if not is_id or call != q:
                out["registry"] = out["registry"] or await mon.api.registry_by_call(call)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(tr("Rekisteri: {e}", e=friendly_error(e)))
        try:
            if not call.isdigit():
                out["devices"] = await mon.api.devices_by_call(call)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(tr("Laitteet: {e}", e=friendly_error(e)))
        ids = {int(r.get("ID")) for r in out["registry"] if str(r.get("ID", "")).isdigit()}
        if is_id:
            ids.add(int(q))
        heard = []
        for i in ids:
            heard += mon.heard.lookup(dmr_id=i)
        if not call.isdigit():
            heard += [e for e in mon.heard.lookup(call=call) if e not in heard]
        out["heard"] = sorted(heard, key=lambda e: -float(e.get("start") or 0))[:10]
        out["call"] = call
        out["local_calls"] = [c for c in list(mon.stats.calls)[-5000:] if (c["call"] or "").upper() == call][-10:][::-1]
        out["watched"] = call in mon.cfg["watch"]["list"] or q in mon.cfg["watch"]["list"]
        return web.json_response(out, dumps=lambda o: json.dumps(o, default=str))

    async def stats(request):
        try:
            hours = int(request.query.get("hours") or 24)
            tg = request.query.get("tg")
            tg = int(tg) if tg else None
        except ValueError:
            return web.json_response({"ok": False, "error": tr("Virheellinen parametri")}, status=400)
        hours = max(1, min(24 * 14, hours))
        return web.json_response({"ok": True, **mon.stats.compute(hours, tg)}, dumps=lambda o: json.dumps(o, default=str))

    async def stats_bm(request):
        try:
            tg = int(request.query.get("tg") or 0)
        except ValueError:
            tg = 0
        if tg <= 0:
            return web.json_response({"ok": False, "error": tr("Anna puheryhmä")}, status=400)
        hit = mon.stats.bm_cache.get(str(tg))
        if hit and time.time() - hit[0] < 300:
            users = hit[1]
        else:
            try:
                users = await in_thread(bm_user_history, [tg], timeout=45)
            except Exception as e:  # noqa: BLE001
                return web.json_response({"ok": False, "error": friendly_error(e)}, status=502)
            mon.stats.bm_cache[str(tg)] = (time.time(), users)
        users = sorted(users, key=lambda u: -u["count"])
        return web.json_response({"ok": True, "tg": tg, "name": mon.tg_name(tg), "total_users": len(users),
                                  "total_calls": sum(u["count"] for u in users),
                                  "oldest": min((u["last"] for u in users), default=None), "users": users[:25]})

    async def device_action(request):
        try:
            b = await request.json()
            dev = int(b["id"])
            if dev not in mon.devmon.ids():
                raise ValueError(tr("Laite ei ole seurattavien listalla"))
            act = b.get("action")
            slot = int(b.get("slot", 0))
            if act == "add":
                r = await mon.api.add_static(dev, slot, int(b["tg"]))
            elif act == "delete":
                r = await mon.api.delete_static(dev, slot, int(b["tg"]))
            elif act == "drop_dynamic":
                r = await mon.api.drop_dynamic(dev, slot)
            elif act == "drop_call":
                r = await mon.api.drop_call(dev, slot)
            elif act == "reset":
                r = await mon.api.reset_connection(dev)
            else:
                raise ValueError(tr("Tuntematon toiminto"))
            await asyncio.sleep(1)
            mon.devmon.wake.set()
            return web.json_response({"ok": True, "result": r}, dumps=lambda o: json.dumps(o, default=str))
        except BMApiError as e:
            if e.status == 403:   # 401 = avain hylätty kokonaan, 403 = laite ei kuulu tilille
                # Brandmeister ei salli: ei oma laite -> hallintatyökalut piiloon tästä laitteesta
                for d in mon.cfg["devices"]["list"]:
                    if d["id"] == dev:
                        d["own"] = False
                mon.save_config()
                return web.json_response({"ok": False, "not_own": True, "error": tr(
                    "Brandmeister ei salli tätä toimintoa tälle laitteelle, joten se ei ole tämän API-avaimen tilin laite. "
                    "Hallintatyökalut piilotettiin; ne saa takaisin valinnalla Oma laite.")}, status=403)
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)}, status=400)

    async def devices_refresh(_):
        mon.api.invalidate("/device/")
        mon.devmon.wake.set()
        return web.json_response({"ok": True})

    async def device_lookup(request):
        call = (request.query.get("call") or "").strip().upper()
        try:
            return web.json_response({"ok": True, "devices": await mon.api.devices_by_call(call)},
                                     dumps=lambda o: json.dumps(o, default=str))
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)}, status=400)

    async def test_alert(_):
        mon.watcher.seq += 1
        mon.watcher.alerts.appendleft({"seq": mon.watcher.seq, "time": time.time(), "kind": "testi", "call": "TESTI",
                                       "name": tr("Testihälytys"), "id": 0, "tg": 91, "tg_name": mon.tg_name(91), "via": ""})
        mon.touch()
        return web.json_response({"ok": True})

    async def checkin_action(req):
        try:
            body = await req.json()
            ci, action = mon.checkin, body.get("action")
            if req.get(REMOTE_KEY) and action in ("sheet_connect", "sheet_disconnect"):
                return web.json_response({"ok": False, "error": tr("Tämä toiminto on käytettävissä vain ohjelman omalla koneella.")},
                                         status=403)
            if action == "start":
                await ci.a_start(body.get("tg"), body.get("round"), body.get("date", ""), body.get("operator", ""))
            elif action == "stop":
                ci.stop()
            elif action == "log":
                await ci.a_log(body.get("call", ""))
            elif action == "unlog":
                await ci.a_unlog(body.get("call", ""))
            elif action == "edit":
                await ci.a_edit(body.get("call", ""), body.get("field", ""), body.get("value", ""))
            elif action == "sheet_connect":
                await ci.connect_sheet(body.get("url", ""), body.get("code", ""), allow_test=bool(os.environ.get("BMM_SHEET_TEST")))
            elif action == "sheet_disconnect":
                ci.disconnect_sheet()
            elif action == "sheet_sync":
                if ci.sheet and ci.s["active"]:
                    await ci._remote("list", {})
            elif action == "copied":
                ci.mark_copied(body.get("upto", 0))
            else:
                raise ValueError(tr("Tuntematon toiminto"))
            return web.json_response({"ok": True, "checkin": ci.snapshot()})
        except (ValueError, SheetError) as e:
            return web.json_response({"ok": False, "error": str(e), "checkin": mon.checkin.snapshot()}, status=400)

    async def checkin_export(req):
        fmt = req.query.get("fmt", "tsv")
        since = int(req.query.get("since", "0") or 0)
        text = mon.checkin.export("csv" if fmt == "csv" else "tsv", since=since, header=req.query.get("header") == "1")
        if fmt == "csv":
            s = mon.checkin.s
            name = f"checkin_TG{s['tg']}_kierros{s['round']}.csv"
            return web.Response(body=("\ufeff" + text).encode("utf-8"), content_type="text/csv", charset="utf-8",
                                headers={"Content-Disposition": f'attachment; filename="{name}"'})
        return web.Response(text=text, content_type="text/plain", charset="utf-8")

    async def update_check(_):
        return web.json_response({"ok": True, "update": await mon.updater.check()})

    async def update_install(_):
        try:
            await mon.updater.install()
            return web.json_response({"ok": True})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)}, status=400)

    async def ping(_):
        return web.json_response({"app": APP_ID, "version": __version__})

    def home():
        loc = mon.cfg["services"]["location"]
        return (loc["lat"], loc["lon"]) if loc.get("lat") is not None else None

    async def aprsrx_extra(request):
        q = request.query
        mon.aprsrx._commit()
        return web.json_response(await in_thread(mon.aprsrx.extra, q.get("layer", ""), q.get("period", "24h"), home()))

    async def ais_ships(_):
        return web.json_response({"ok": True, "ships": mon.ais.ship_list(), "state": mon.ais.snapshot()})

    async def prop_data(request):
        q = request.query
        try:
            mins = int(q.get("mins", 60))
        except ValueError:
            mins = 60
        mins = mins if mins in PROP_WINDOWS else 60
        bands = [b for b in q.get("bands", "").split(",") if b]
        modes = [m for m in q.get("modes", "").split(",") if m]
        return web.json_response({"ok": True, **(await in_thread(mon.prop.query, mins, bands, modes)),
                                  "state": mon.prop.snapshot()})

    async def tropo_data(request):
        try:
            h = max(0, min(71, int(request.query.get("h", 0))))
        except ValueError:
            h = 0
        out = mon.tropo.query(h)
        out["aprs"] = await in_thread(mon.aprsrx.tropo_meter, home())
        return web.json_response({"ok": True, **out})

    async def aprsrx_serialports(_):
        from .radios import serial_ports
        return web.json_response({"ports": await in_thread(serial_ports)})

    async def aprsrx_summary(request):
        period = request.query.get("period", "24h")
        mon.aprsrx._commit()
        return web.json_response(await in_thread(mon.aprsrx.summary, period, home()))

    async def aprsrx_cells(request):
        q = request.query
        try:
            res = min(100.0, max(0.5, float(q.get("res", 5))))
        except ValueError:
            res = 5.0
        mode = q.get("mode", "all")
        via = (q.get("via") or "").upper() or None
        mon.aprsrx._commit()
        return web.json_response(await in_thread(mon.aprsrx.cells, q.get("period", "24h"), mode, via, res, home()))

    # ---------------- loki ----------------
    async def log_list(request):
        q = request.query
        try:
            limit = max(0, min(5000, int(q.get("limit", 200))))
        except ValueError:
            limit = 200
        return web.json_response({"ok": True, "rows": mon.qso.list(limit, q.get("q", "").strip(), q.get("mode", "").strip(),
                                                                   q.get("prop", "").strip()),
                                  "count": mon.qso.count()})

    async def log_save(request):
        try:
            body = await request.json()
            if body.get("id"):
                mon.qso.update(int(body["id"]), body)
                qid = int(body["id"])
            else:
                qid = mon.qso.add(body)
            return web.json_response({"ok": True, "id": qid})
        except (ValueError, KeyError) as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def log_delete(request):
        mon.qso.delete(int(request.match_info["id"]), remote=request.query.get("remote") == "1")
        return web.json_response({"ok": True})

    async def log_export(request):
        rows = list(reversed(mon.qso.list(0)))
        call = (mon.qso.my_call() or "loki").lower()
        if request.match_info["fmt"] == "adi":
            body, ctype, ext = adif_file(rows), "text/plain", "adi"
        else:
            body, ctype, ext = "\ufeff" + csv_file(rows), "text/csv", "csv"
        return web.Response(text=body, content_type=ctype, charset="utf-8", headers={
            "Content-Disposition": f'attachment; filename="{call}-{time.strftime("%Y%m%d")}.{ext}"'})

    async def log_candidate(request):
        d = mon.qso.candidate_defaults(request.match_info["cid"])
        return web.json_response({"ok": bool(d), "qso": d, "error": None if d else tr("Ehdokasta ei löydy")})

    async def log_dismiss(request):
        body = await request.json()
        mon.qso.detector.close(str(body.get("id") or ""))
        mon.dirty = True
        return web.json_response({"ok": True})

    async def log_qrz_test(request):
        try:
            st = await mon.qso.qrz_status()
            return web.json_response({"ok": bool(st.get("ok")), "state": st, "error": st.get("error")})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)})

    async def log_qrz_retry(request):
        body = await request.json() if request.can_read_body else {}
        mon.qso.retry_qrz(int(body["id"]) if body.get("id") else None)
        return web.json_response({"ok": True})

    async def log_svc_retry(request):
        body = await request.json() if request.can_read_body else {}
        try:
            mon.qso.retry(request.match_info["svc"], int(body["id"]) if body.get("id") else None)
        except ValueError:
            return web.json_response({"ok": False, "error": tr("Tuntematon toiminto")})
        return web.json_response({"ok": True})

    async def log_hamqth_test(request):
        try:
            st = await mon.qso.hamqth_test()
            return web.json_response({"ok": bool(st.get("ok")), "state": st, "error": st.get("error")})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": friendly_error(e)})

    async def aprsrx_stations(request):
        q = request.query
        via = (q.get("via") or "").upper() or None
        mon.aprsrx._commit()
        return web.json_response(await in_thread(mon.aprsrx.stations, q.get("period", "24h"), q.get("mode", "all"),
                                                 via, home()))

    async def tile(request):
        try:
            z, x, y = (int(request.match_info[k]) for k in ("z", "x", "y"))
        except ValueError:
            raise web.HTTPNotFound()
        if not (0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise web.HTTPNotFound()
        tmpl = mon.cfg.get("map_tiles") or DEFAULT_TILES
        key = hashlib.sha1(tmpl.encode()).hexdigest()[:10]
        path = mon.cfg_path.parent / "tiles" / key / str(z) / str(x) / f"{y}.png"
        fresh = path.exists() and time.time() - path.stat().st_mtime < 7 * 86400
        down = mon.tile_down_until > time.time()      # tiilipalvelin ei vastaa: ei jonoteta pyyntöjä turhaan
        if not fresh and not down:
            url = tmpl.replace("{s}", "abc"[(x + y) % 3]).replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            # OpenStreetMapin käyttöehdot: sovellus tunnistaa itsensä omalla User-Agentilla.
            ua = f"MonitorForAmateurRadio/{__version__} (+https://github.com/hannulma/monitor-for-amateur-radio)"
            try:
                # Selain avaa samaan osoitteeseen vain ~6 yhteyttä kerrallaan: tiilipyyntö ei saa jäädä jonoon
                # pitkäksi aikaa, muuten muut sivun pyynnöt (tilastot, satelliitit) odottavat sen takana.
                await asyncio.wait_for(mon.tile_sem.acquire(), 3)
            except asyncio.TimeoutError:
                if path.exists():
                    return web.FileResponse(path, headers={"Cache-Control": "max-age=300"})
                raise web.HTTPServiceUnavailable()
            try:
                async with http_session(8) as s, s.get(url, headers={"User-Agent": ua}) as r:
                    if r.status == 200:
                        data = await r.read()
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                        fresh = True
                        mon.tile_fail = 0
                    else:
                        log.debug("Karttatiili %s: HTTP %s", url, r.status)
            except Exception as e:  # noqa: BLE001
                log.debug("Karttatiili %s: %s", url, e)
                mon.tile_fail += 1
                if mon.tile_fail >= 3:
                    mon.tile_down_until = time.time() + 60
                    log.info("Karttatiilipalvelin ei vastaa – tauko 60 s")
            finally:
                mon.tile_sem.release()
        if not path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "max-age=86400"})

    async def nets_list(request):
        if request.query.get("refresh"):
            await mon.nets.refresh(force=True)
        days = 2 if request.query.get("days") != "7" else 7
        return web.json_response(await in_thread(mon.nets.snapshot, days))

    async def ui_show(_):
        if mon.ui is None:
            return web.json_response({"ok": False})
        await in_thread(mon.ui.show, timeout=5)
        return web.json_response({"ok": True})

    async def shutdown(_):
        log.info("Sammutus käyttöliittymästä")
        asyncio.get_running_loop().call_later(0.3, mon.shutdown_event.set)
        return web.json_response({"ok": True})

    app.router.add_get("/api/ping", ping)
    app.router.add_post("/api/update/check", update_check)
    app.router.add_post("/api/checkin", checkin_action)
    app.router.add_get("/api/checkin/export", checkin_export)
    app.router.add_post("/api/update/install", update_install)
    app.router.add_post("/api/shutdown", shutdown)
    app.router.add_post("/api/ui/show", ui_show)
    app.router.add_get("/api/aprsrx/summary", aprsrx_summary)
    app.router.add_get("/api/nets", nets_list)
    app.router.add_get("/api/aprsrx/cells", aprsrx_cells)
    app.router.add_get("/api/aprsrx/stations", aprsrx_stations)
    app.router.add_get("/api/aprsrx/serialports", aprsrx_serialports)
    app.router.add_get("/api/aprsrx/extra", aprsrx_extra)
    app.router.add_get("/api/ais", ais_ships)
    app.router.add_get("/api/prop", prop_data)
    app.router.add_get("/api/tropo", tropo_data)
    app.router.add_get("/api/log", log_list)
    app.router.add_post("/api/log", log_save)
    app.router.add_delete("/api/log/{id:\\d+}", log_delete)
    app.router.add_get("/api/log/export.{fmt:(adi|csv)}", log_export)
    app.router.add_get("/api/log/candidate/{cid}", log_candidate)
    app.router.add_post("/api/log/dismiss", log_dismiss)
    app.router.add_post("/api/log/qrz/test", log_qrz_test)
    app.router.add_post("/api/log/qrz/retry", log_qrz_retry)
    app.router.add_post("/api/log/hamqth/test", log_hamqth_test)
    app.router.add_post("/api/log/{svc:(eqsl|hamqth)}/retry", log_svc_retry)
    app.router.add_get("/tiles/{z}/{x}/{y}.png", tile)
    app.router.add_get("/api/station", station)
    app.router.add_get("/api/stats", stats)
    app.router.add_get("/api/stats/bm", stats_bm)
    app.router.add_post("/api/device/action", device_action)
    app.router.add_post("/api/devices/refresh", devices_refresh)
    app.router.add_get("/api/devices/lookup", device_lookup)
    app.router.add_post("/api/alerts/test", test_alert)
    async def aprs_send(request):
        from .messaging import clean_text, split_message, validate_call
        try:
            body = await request.json()
            m = mon.cfg["messaging"]
            if not m.get("login"):
                raise ValueError(tr("Aseta oma APRS-IS-tunnus (APRS-välilehti)"))
            a = mon.hub.aprs
            to = validate_call(str(body.get("to", "")))
            route = mon.hub.pick_route(to, body.get("route"))
            if route == "rf":
                if not mon.hub.rf_ready():
                    raise ValueError(tr("Ei yhteyttä Graywolfiin – viestiä ei voi lähettää radiolla"))
            else:
                if not a.connected:
                    raise ValueError(tr("APRS-IS-yhteys ei ole auki"))
                if a.verified is False:
                    raise ValueError(tr("Passcode ei kelpaa – viestejä ei välitetä"))
            frm = validate_call(str(body.get("from") or m["login"]))
            if frm.split("-")[0] not in mon.hub.watch_bases():
                raise ValueError(tr("Lähettäjän pitää olla oma kutsusi tai seurattava kutsu"))
            text = clean_text(str(body.get("text", "")), bool(m.get("ascii")))
            if not text:
                raise ValueError(tr("Kirjoita viesti"))
            parts = split_message(text, 3)
            for part in parts:
                a.send(to, part, source="vastaus", src=frm, route=route)
            return web.json_response({"ok": True, "parts": len(parts), "route": route})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    app.router.add_post("/api/aprs/send", aprs_send)
    app.router.add_post("/api/test_message", test_message)
    app.router.add_post("/api/feeds/check", check_feeds)
    app.router.add_get("/api/feeds/preview", preview_feed)
    app.router.add_get("/api/services/meteoalarm_areas", ma_area_list)
    app.router.add_get("/api/satellites/map", sat_map)
    app.router.add_get("/api/feedback/bundle", fb_bundle)
    app.router.add_get("/api/feedback/info", fb_info)
    app.router.add_post("/api/feedback/send", fb_send)
    async def lan_login_page(_):
        return web.Response(text=lanmod.login_page(get_lang(), APP_NAME), content_type="text/html")

    async def lan_login(request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        ip = request.remote or "?"
        try:
            token = mon.lan.login(ip, str(body.get("password") or ""), mon.lan_hash)
        except PermissionError as e:
            log.info("Lähiverkko: kirjautuminen epäonnistui (%s)", ip)
            return web.json_response({"ok": False, "error": str(e)}, status=401)
        resp = web.json_response({"ok": True})
        resp.set_cookie(lanmod.COOKIE, token, max_age=lanmod.SESSION_DAYS * 86400, httponly=True, samesite="Strict", path="/")
        log.info("Lähiverkko: kirjautuminen %s", ip)
        return resp

    async def lan_logout(request):
        mon.lan.logout(request.cookies.get(lanmod.COOKIE))
        resp = web.json_response({"ok": True})
        resp.del_cookie(lanmod.COOKIE, path="/")
        return resp

    async def lan_me(request):
        return web.json_response({"remote": bool(request.get(REMOTE_KEY)), "mode": mon.cfg["lan"]["mode"]})

    async def lan_get(_):
        return web.json_response({"ok": True, **(await mon.lan_public())})

    async def lan_post(request):
        try:
            body = await request.json()
            pw = body.pop("password", None)
            body.pop("gen", None)
            new = normalize_lan(body, mon.cfg["lan"])
            rotate = False
            if pw is not None and pw != "********":
                pw = str(pw)
                if len(pw) < lanmod.MIN_PW:
                    raise ValueError(tr("Salasanan pitää olla vähintään {n} merkkiä", n=lanmod.MIN_PW))
                h = lanmod.hash_password(pw)
                secret_store.save_field(new, "pw", "lan-password", h)
                mon.lan_hash = h
                rotate = True                           # salasanan vaihto kirjaa etälaitteet ulos
            if new["enabled"] and not mon.lan_hash:
                raise ValueError(tr("Aseta salasana"))
            if new["mode"] != mon.cfg["lan"]["mode"]:
                rotate = True
            mon.cfg["lan"] = new
            if rotate:
                mon.lan_logout_all(save=False)
            mon.save_config()
            await mon.lan_apply()
            return web.json_response({"ok": True, **(await mon.lan_public()), "config": mon.public_config()})
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def lan_logout_all(_):
        mon.lan_logout_all()
        return web.json_response({"ok": True, **(await mon.lan_public())})

    def _fw_exe() -> str:
        return sys.executable if getattr(sys, "frozen", False) else ""

    async def lan_fw_get(_):
        st = await asyncio.to_thread(lanmod.firewall_status, mon.cfg["lan"]["port"], _fw_exe())
        if st.get("error"):
            log.warning("Lähiverkko: palomuurin tila: %s", st["error"])
        return web.json_response({"ok": True, **st})

    async def lan_fw_post(_):
        ok, msg = await asyncio.to_thread(lanmod.firewall_allow, mon.cfg["lan"]["port"], _fw_exe())
        st = await asyncio.to_thread(lanmod.firewall_status, mon.cfg["lan"]["port"], _fw_exe())
        log.info("Lähiverkko: palomuurisääntö: %s; tila nyt %s (ohjelma %s)", msg, st, _fw_exe() or "-")
        if ok and not st.get("rule"):
            msg = tr("Sääntö lisättiin, mutta sitä ei löydy tarkistuksessa") + f" ({msg}; {st.get('error') or ''})"
        return web.json_response({**st, "ok": ok, **({} if ok and st.get("rule") else {"error": msg})})

    async def lan_qr(request):
        url = request.query.get("u", "")
        ok = any(url == a for a in (await mon.lan_public())["addresses"])
        svg = lanmod.qr_svg(url) if ok else None
        if not svg:
            raise web.HTTPNotFound()
        return web.Response(text=svg, content_type="image/svg+xml")

    app.router.add_get("/login", lan_login_page)
    app.router.add_post("/api/lan/login", lan_login)
    app.router.add_post("/api/lan/logout", lan_logout)
    app.router.add_get("/api/lan/me", lan_me)
    app.router.add_get("/api/lan", lan_get)
    app.router.add_post("/api/lan", lan_post)
    app.router.add_post("/api/lan/logout_all", lan_logout_all)
    app.router.add_get("/api/lan/qr.svg", lan_qr)
    app.router.add_get("/api/lan/firewall", lan_fw_get)
    app.router.add_post("/api/lan/firewall", lan_fw_post)
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/snapshot", snapshot_http)
    app.router.add_post("/api/clientlog", client_log)
    app.router.add_get("/api/config", get_config)
    app.router.add_post("/api/config", post_config)
    app.router.add_post("/api/clear", clear)
    app.router.add_static("/static", STATIC_DIR)
    return app


def _can_bind(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform != "win32":
            # kuten aiohttp: suljetun yhteyden TIME_WAIT-tila ei estä samaa porttia
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def free_port(preferred: int, wait: float = 0) -> int:
    """Ensisijaisesti sama portti (selainvälilehti yhdistää siihen uudelleen); tarvittaessa odotetaan."""
    deadline = time.time() + wait
    while not _can_bind(preferred) and time.time() < deadline:
        time.sleep(0.5)
    for port in range(preferred, preferred + 50):
        if _can_bind(port):
            return port
    raise RuntimeError(tr("Vapaata porttia ei löytynyt"))


def running_instance(port: int) -> bool:
    """Onko ohjelma jo käynnissä tässä portissa?"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read().decode()).get("app") == APP_ID
    except Exception:  # noqa: BLE001
        return False


async def run(args, ui=None):
    cfg_path = Path(args.config) if args.config else config_dir() / "config.json"
    mon = Monitor(cfg_path, demo=args.demo)
    try:
        await _run(args, mon, cfg_path, ui)
    finally:
        if ui is not None:
            ui.cancel()
            ui.server_stopped()


def start_loop_watchdog(loop, limit: float = 10.0) -> None:
    """Vianetsintä: jos tapahtumasilmukka on jumissa yli `limit` s, kaikkien säikeiden pinot lokiin."""
    import faulthandler
    import threading
    state = {"beat": time.monotonic(), "dumped": 0.0}

    def beat():
        state["beat"] = time.monotonic()
        loop.call_later(1.0, beat)
    loop.call_soon(beat)

    def watch():
        while not loop.is_closed():
            time.sleep(2)
            lag = time.monotonic() - state["beat"]
            if lag > limit and time.monotonic() - state["dumped"] > 300:
                state["dumped"] = time.monotonic()
                log.warning("Ohjelman pääsilmukka jumissa %.0f s – säikeiden tila:", lag)
                try:
                    for h in logging.getLogger().handlers:
                        h.flush()
                    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
                except Exception:  # noqa: BLE001
                    pass
    threading.Thread(target=watch, name="loop-watchdog", daemon=True).start()


def show_running(port: int) -> bool:
    """Pyydä jo käynnissä olevaa ohjelmaa näyttämään oma ikkunansa."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ui/show", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return bool(json.loads(r.read().decode()).get("ok"))
    except Exception:  # noqa: BLE001
        return False


async def _run(args, mon, cfg_path, ui):
    if not cfg_path.exists():
        mon.save_config()
    preferred = args.port or int(mon.cfg.get("port", 8765))
    if args.wait_for_exit:   # päivityksen jälkeen: odota, että vanha versio on sulkeutunut
        for _ in range(60):
            if not running_instance(preferred):
                break
            await asyncio.sleep(0.5)
    if not args.demo and running_instance(preferred):
        url = f"http://127.0.0.1:{preferred}/"
        print(f"{APP_NAME} on jo käynnissä: {url}")
        if not args.no_browser and not show_running(preferred):
            webbrowser.open(url)
        return
    port = free_port(preferred, wait=30 if args.wait_for_exit else 0)

    runner = web.AppRunner(make_app(mon))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    mon.runner, mon.local_port = runner, port
    await mon.lan_apply()
    url = f"http://127.0.0.1:{port}/"
    print(f"{APP_NAME} {__version__} käynnissä: {url}")
    print(f"Asetukset: {cfg_path}")
    print("Lopeta painamalla Ctrl+C tai Ohjelma → Sulje ohjelma.")
    log.info("%s %s käynnissä: %s", APP_NAME, __version__, url)
    if ui is not None:
        mon.ui = ui
        loop = asyncio.get_running_loop()
        ui.server_started(url, lambda: loop.call_soon_threadsafe(mon.shutdown_event.set))
    elif not args.no_browser:
        webbrowser.open(url)

    tasks = [asyncio.create_task(mon.broadcast_loop()), asyncio.create_task(mon.load_tg_names()),
             asyncio.create_task(mon.hub.run()), asyncio.create_task(mon.devmon.run()),
             asyncio.create_task(mon.services.run())]
    tasks.append(asyncio.create_task(mon.checkin.run()))
    tasks.append(asyncio.create_task(mon.aprsrx.run()))
    tasks.append(asyncio.create_task(mon.nets.run()))
    tasks.append(asyncio.create_task(mon.qso.run()))
    tasks.append(asyncio.create_task(mon.ais.run()))
    tasks.append(asyncio.create_task(mon.prop.run()))
    tasks.append(asyncio.create_task(mon.prop.run_rbn()))
    tasks.append(asyncio.create_task(mon.tropo.run()))
    start_loop_watchdog(asyncio.get_running_loop())
    if not args.demo:
        tasks.append(asyncio.create_task(mon.updater.run()))
    tasks.append(asyncio.create_task(demo_loop(mon) if args.demo else bm_loop(mon)))
    try:
        await mon.shutdown_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        # avoimet selainyhteydet kiinni, muuten aiohttp odottaa niitä sulkiessaan jopa minuutin
        for ws in list(mon.clients):
            try:
                await asyncio.wait_for(ws.close(code=1001, message=b"shutdown"), 2)
            except Exception:  # noqa: BLE001
                pass
        await runner.cleanup()
        mon.aprsrx.close()
        mon.qso.close()


def main():
    ap = argparse.ArgumentParser(prog="bmmonitor", description=f"{APP_NAME} – BrandMeister, APRS, loki, check-in, satelliitit ja hälytykset")
    ap.add_argument("--port", type=int, default=0, help="paikallinen portti (oletus 8765)")
    ap.add_argument("--no-browser", action="store_true",
                    help="älä avaa selainta automaattisesti (omassa ikkunassa: käynnisty taustalle)")
    ap.add_argument("--browser", action="store_true", help="käytä selainta, vaikka oma ikkuna on valittu")
    ap.add_argument("--config", help="asetustiedoston polku")
    ap.add_argument("--demo", action="store_true", help="keksitty testiliikenne, ei yhteyttä BM:ään")
    ap.add_argument("--wait-for-exit", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--check-secrets", metavar="FILE", help=argparse.SUPPRESS)
    ap.add_argument("--diag", action="store_true", help="vianetsintä: näytä mitä BM lähettää (30 s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.diag:
        diagnose()
        return
    if args.check_secrets:          # julkaisun itsetesti: toimiiko salasanavarasto paketissa
        from . import secret_store
        ok = secret_store.put("selftest", "x1") and secret_store.get("selftest") == "x1"
        secret_store.delete("selftest")
        backend = secret_store._backend.get_keyring() if secret_store._backend else None
        Path(args.check_secrets).write_text(f"secrets {'OK' if ok else 'UNAVAILABLE'} {backend}\n", encoding="utf-8")
        return
    # Ilman komentoikkunaa (Windows .exe / macOS .app) tulosteet ja loki menevät tiedostoon.
    if sys.stdout is None or sys.stderr is None:
        logf = config_dir() / "monitor.log"
        try:
            if logf.exists() and logf.stat().st_size > 2_000_000:
                logf.replace(logf.with_suffix(".old.log"))
        except OSError:
            pass
        stream = open(logf, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
                        stream=sys.stderr)
    feedback.install_memory_log()
    try:
        import faulthandler
        faulthandler.enable(file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if use_window(args):
        run_windowed(args)
        return
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


def use_window(args) -> bool:
    """Oma ikkuna, jos se on valittu asetuksissa ja mahdollinen tällä koneella."""
    if args.browser or args.diag:
        return False
    cfg_path = Path(args.config) if args.config else config_dir() / "config.json"
    try:
        ui = json.loads(cfg_path.read_text(encoding="utf-8")).get("ui", "window")
    except Exception:  # noqa: BLE001
        ui = "window"
    return ui != "browser" and desktop.available()


def run_windowed(args) -> None:
    """Palvelin omassa säikeessään, ikkuna pääsäikeessä (macOS vaatii käyttöliittymän pääsäikeeseen)."""
    import threading
    # --no-browser ilman --wait-for-exit = käynnistys Windowsin mukana: ikkuna piiloon, kuvake näkyviin.
    # Päivityksen jälkeen (--wait-for-exit) ikkuna avataan, koska vanhan version ikkuna sulkeutui.
    cfg_path = Path(args.config) if args.config else config_dir() / "config.json"
    try:
        light = json.loads(cfg_path.read_text(encoding="utf-8")).get("theme") == "light"
    except Exception:  # noqa: BLE001
        light = False
    ui = desktop.Desktop(start_hidden=args.no_browser and not args.wait_for_exit, light=light)
    errors = []

    def server():
        try:
            asyncio.run(run(args, ui))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            log.exception("Palvelin pysähtyi virheeseen")
    t = threading.Thread(target=server, name="server")
    t.start()
    ui.ready.wait()
    if ui.url is None:            # ohjelma oli jo käynnissä (ikkuna näytettiin) tai käynnistys epäonnistui
        t.join()
        return
    log.info("Käyttöliittymä omassa ikkunassa%s", " (piilossa, kuvake ilmaisinalueella)" if ui.start_hidden else "")
    try:
        ui.run(config_dir() / "webview")
    except Exception:  # noqa: BLE001
        log.exception("Omaa ikkunaa ei voitu avata – käytetään selainta")
        if not ui.start_hidden:
            webbrowser.open(ui.url)
        ui.window = None
        t.join()
        return
    # ikkuna suljettu: sammutetaan palvelin, jos se on vielä käynnissä
    if t.is_alive() and ui.on_quit:
        ui.on_quit()
    t.join(30)


if __name__ == "__main__":
    main()
