"""Loki: yhteyksien (QSO) tunnistus BrandMeisterin puheluista, paikallinen loki ja QRZ Logbook -synkronointi.

Tunnistus: kun oma kutsu ja toinen asema vuorottelevat samassa puheryhmässä niin, että oman vuoron jälkeen
toinen vastaa ja sinä puhut vielä uudelleen (oma–toinen–oma, siis myös toinen–oma–toinen–oma), ja vuorojen väli
on enintään QSO_GAP sekuntia, syntyy yhteysehdokas. Pelkkä toinen–oma–toinen ei riitä: check-in-netissä
nettijohtaja kutsuu, asema kuittautuu ja nettijohtaja kuittaa – se ei ole yhteys. Ehdokas päättyy,
kun QSO_QUIET sekuntia on hiljaista. Yksityispuhelu omaan kutsuun tai omasta kutsusta on aina ehdokas.
Käyttäjä lisää ehdokkaan lokiin (tai ohittaa sen); lokiin lisätty yhteys lähetetään valittuihin palveluihin
(QRZ Logbook, eQSL, HamQTH). BrandMeisterin kautta pidetyt yhteydet ovat internet-avusteisia (ADIF PROP_MODE=INTERNET);
kunkin palvelun kohdalla voi valita, ettei niitä lähetetä.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import sqlite3
import time
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs

from . import __version__, secret_store
from .i18n import tr

log = logging.getLogger("bmmonitor.qso")

QSO_GAP = 180          # s: vuorojen suurin väli
QSO_QUIET = 600        # s: tämän jälkeen ehdokas on päättynyt
TURN_KEEP = 1800       # s: puheryhmän vuorot muistissa
CAND_KEEP = 7 * 86400  # s: käsittelemätön ehdokas säilyy (myös uudelleenkäynnistyksen yli) viikon
QRZ_URL = "https://logbook.qrz.com/api"
QRZ_RETRY = 300        # s: verkkovirheen jälkeen uusi yritys
EQSL_URL = "https://www.eqsl.cc/qslcard/ImportADIF.cfm"
HAMQTH_API = "https://www.hamqth.com/api/v2"
EQSL_PASS_MAX = 14     # eQSL:n rajapinta ei hyväksy pidempää salasanaa (käyttäjien raportit)
SERVICES = ("qrz", "eqsl", "hamqth")
SECRETS = {"qrz": ("qrz_key", "qrz-logbook-key"), "eqsl": ("eqsl_pass", "eqsl-password"),
           "hamqth": ("hamqth_key", "hamqth-api-key")}
# ADIF PROP_MODE: etenemistapa. Internet-avusteiset (BrandMeister, EchoLink, toistimet) eivät kelpaa useimpiin diplomeihin.
PROP_MODES = ("", "INTERNET", "RPT", "SAT", "ECH")
ASSISTED = {"INTERNET", "RPT", "ECH"}
NET_MODES = {"DMR", "DSTAR", "C4FM", "M17"}          # näillä tiloilla oletuksena INTERNET

DEFAULT_LOG = {
    "my_call": "",          # tyhjä = Laitteet-osion oma kutsu tai APRS-IS-tunnus
    "my_grid": "",
    "rst": "59",
    "band": "70cm",         # käytetään, jos taajuus ei ole tiedossa
    "detect": True,         # tunnista yhteysehdokkaat BrandMeisterin puheluista
    "mode": "DMR",          # käsin lisättävien yhteyksien oletustila
    "qrz": False,           # lähetä lokiin lisätyt QRZ Logbookiin
    "qrz_inet": True,       # lähetä myös internet-avusteiset (PROP_MODE INTERNET/RPT/ECH)
    # "qrz_key": salasanavarastossa (qrz-logbook-key)
    "eqsl": False, "eqsl_user": "", "eqsl_qth": "", "eqsl_inet": True,
    # "eqsl_pass": salasanavarastossa (eqsl-password)
    "hamqth": False, "hamqth_inet": True,
    # "hamqth_key": salasanavarastossa (hamqth-api-key)
}


def default_prop(label: str) -> str:
    return "INTERNET" if label in NET_MODES else ""

# Tila käyttöliittymän nimellä → ADIF MODE / SUBMODE (ADIF 3.1). Pikapainikkeet ensin.
MODES = {
    "SSB": ("SSB", ""), "CW": ("CW", ""), "AM": ("AM", ""), "FM": ("FM", ""), "RTTY": ("RTTY", ""),
    "PSK31": ("PSK", "PSK31"), "DSTAR": ("DIGITALVOICE", "DSTAR"), "DMR": ("DIGITALVOICE", "DMR"),
    "C4FM": ("DIGITALVOICE", "C4FM"),
    "LSB": ("SSB", "LSB"), "USB": ("SSB", "USB"), "FT8": ("FT8", ""), "FT4": ("MFSK", "FT4"), "JS8": ("MFSK", "JS8"),
    "Q65": ("MFSK", "Q65"), "JT65": ("JT65", ""), "JT9": ("JT9", ""), "MSK144": ("MSK144", ""),
    "PSK63": ("PSK", "PSK63"), "PSK125": ("PSK", "PSK125"), "OLIVIA": ("OLIVIA", ""), "CONTESTI": ("CONTESTI", ""),
    "THOR": ("THOR", ""), "DOMINO": ("DOMINO", ""), "HELL": ("HELL", ""), "MFSK": ("MFSK", ""), "SSTV": ("SSTV", ""),
    "FAX": ("FAX", ""), "ATV": ("ATV", ""), "PKT": ("PKT", ""), "M17": ("DIGITALVOICE", "M17"),
    "FREEDV": ("DIGITALVOICE", "FREEDV"), "DIGITALVOICE": ("DIGITALVOICE", ""),
}
PHONE = {"SSB", "AM", "FM", "DIGITALVOICE"}      # puhetilat: raportti RS (59), muut RST (599)


def mode_label(mode: str | None, submode: str | None) -> str:
    return (submode or mode or "").upper()


def default_rst(label: str) -> str:
    return "59" if MODES.get(label, ("", ""))[0] in PHONE else "599"


BANDS = [(1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.06, 5.45, "60m"), (7.0, 7.3, "40m"), (10.1, 10.15, "30m"),
         (14.0, 14.35, "20m"), (18.068, 18.168, "17m"), (21.0, 21.45, "15m"), (24.89, 24.99, "12m"),
         (28.0, 29.7, "10m"), (50.0, 54.0, "6m"), (70.0, 71.0, "4m"), (144.0, 148.0, "2m"), (222.0, 225.0, "1.25m"),
         (420.0, 450.0, "70cm"), (902.0, 928.0, "33cm"), (1240.0, 1300.0, "23cm")]
BAND_NAMES = [b for _, _, b in BANDS]
CALL_RE = re.compile(r"^[A-Z0-9]{1,4}[0-9][A-Z0-9]{0,4}(/[A-Z0-9]{1,4})?$")
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?$", re.I)


def base_call(c: str) -> str:
    c = (c or "").strip().upper()
    return re.split(r"[-/ ]", c)[0] if c else ""


def band_for(freq_mhz: float | None) -> str:
    if not freq_mhz:
        return ""
    for lo, hi, name in BANDS:
        if lo <= freq_mhz <= hi:
            return name
    return ""


def parse_freq(v) -> float | None:
    """BrandMeisterin laitteen taajuus ('434.5000', '434500000', 434.5) MHz:inä."""
    try:
        f = float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    while f > 100000:          # Hz / kHz → MHz
        f /= 1000.0
    return round(f, 6)


def _utc(ts: float, fmt: str) -> str:
    return time.strftime(fmt, time.gmtime(float(ts)))


def _adif_field(name: str, value) -> str:
    if value is None or value == "":
        return ""
    v = str(value)
    return f"<{name}:{len(v.encode('utf-8'))}>{v}"


def qso_adif(q: dict) -> str:
    """Yksi yhteys ADIF-tietueena (<eor> lopussa)."""
    parts = [
        _adif_field("CALL", q["call"]),
        _adif_field("QSO_DATE", _utc(q["start"], "%Y%m%d")),
        _adif_field("TIME_ON", _utc(q["start"], "%H%M%S")),
        _adif_field("QSO_DATE_OFF", _utc(q["end"], "%Y%m%d") if q.get("end") else ""),
        _adif_field("TIME_OFF", _utc(q["end"], "%H%M%S") if q.get("end") else ""),
        _adif_field("BAND", q.get("band")),
        _adif_field("FREQ", f"{q['freq']:.6f}".rstrip("0").rstrip(".") if q.get("freq") else ""),
        _adif_field("FREQ_RX", f"{q['freq_rx']:.6f}".rstrip("0").rstrip(".") if q.get("freq_rx") else ""),
        _adif_field("MODE", q.get("mode") or "DIGITALVOICE"),
        _adif_field("SUBMODE", q.get("submode") or "DMR"),
        _adif_field("RST_SENT", q.get("rst_sent")),
        _adif_field("RST_RCVD", q.get("rst_rcvd")),
        _adif_field("NAME", q.get("name")),
        _adif_field("QTH", q.get("qth")),
        _adif_field("GRIDSQUARE", (q.get("grid") or "").upper()),
        _adif_field("COMMENT", q.get("comment")),
        _adif_field("STATION_CALLSIGN", q.get("my_call")),
        _adif_field("MY_GRIDSQUARE", (q.get("my_grid") or "").upper()),
        _adif_field("PROP_MODE", q.get("prop_mode")),
    ]
    return "".join(parts) + "<eor>"


def adif_file(rows: list[dict]) -> str:
    head = (f"Monitor for Amateur Radio {__version__} ADIF export\n"
            + _adif_field("ADIF_VER", "3.1.4") + _adif_field("PROGRAMID", "MonitorForAmateurRadio")
            + _adif_field("PROGRAMVERSION", __version__) + _adif_field("CREATED_TIMESTAMP", _utc(time.time(), "%Y%m%d %H%M%S"))
            + "<eoh>\n")
    return head + "\n".join(qso_adif(r) for r in rows) + "\n"


def csv_file(rows: list[dict]) -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["date_utc", "time_on", "time_off", "call", "name", "band", "freq", "mode", "prop_mode", "rst_sent",
                "rst_rcvd", "qth", "grid", "tg", "via", "comment", "qrz", "eqsl", "hamqth"])
    for r in rows:
        w.writerow([_utc(r["start"], "%Y-%m-%d"), _utc(r["start"], "%H:%M"), _utc(r["end"], "%H:%M") if r.get("end") else "",
                    r["call"], r.get("name") or "", r.get("band") or "", r.get("freq") or "",
                    mode_label(r.get("mode"), r.get("submode")) or "DMR", r.get("prop_mode") or "",
                    r.get("rst_sent") or "", r.get("rst_rcvd") or "", r.get("qth") or "", r.get("grid") or "",
                    r.get("tg") or "", r.get("via") or "", r.get("comment") or "", r.get("qrz_status") or "",
                    r.get("eqsl_status") or "", r.get("hamqth_status") or ""])
    return out.getvalue()


def normalize_log(new: dict, old: dict) -> dict:
    out = dict(old)
    if "my_call" in new:
        c = str(new["my_call"] or "").strip().upper()
        if c and not CALL_RE.match(c):
            raise ValueError(tr("Virheellinen kutsu: {x}", x=c))
        out["my_call"] = c
    if "my_grid" in new:
        g = str(new["my_grid"] or "").strip()
        if g and not GRID_RE.match(g):
            raise ValueError(tr("Virheellinen lokaattori: {x}", x=g))
        out["my_grid"] = g[:2].upper() + g[2:4] + g[4:6].lower() + g[6:]
    if "rst" in new:
        out["rst"] = str(new["rst"] or "59").strip()[:3] or "59"
    if "band" in new:
        if new["band"] not in BAND_NAMES:
            raise ValueError(tr("Virheellinen arvo: {x}", x=new["band"]))
        out["band"] = new["band"]
    if "mode" in new:
        m = str(new["mode"] or "").upper()
        if m not in MODES:
            raise ValueError(tr("Virheellinen arvo: {x}", x=m))
        out["mode"] = m
    for k in ("detect", "qrz", "eqsl", "hamqth", "qrz_inet", "eqsl_inet", "hamqth_inet"):
        if k in new:
            out[k] = bool(new[k])
    if "eqsl_user" in new:
        u = str(new["eqsl_user"] or "").strip().upper()
        if u and not re.match(r"^[A-Z0-9/]{3,20}$", u):
            raise ValueError(tr("Virheellinen kutsu: {x}", x=u))
        out["eqsl_user"] = u
    if "eqsl_qth" in new:
        out["eqsl_qth"] = str(new["eqsl_qth"] or "").strip()[:40]
    return out


class QsoDetector:
    """Yhteysehdokkaat puheluista. Kutsutaan jokaisesta päättyneestä lähetyksestä (Stop > 0)."""

    def __init__(self, mine):
        self.mine = mine                        # funktio: kutsu → onko oma
        self.turns: dict[str, deque] = {}       # puheryhmä → viimeisimmät vuorot
        self.cands: dict[str, dict] = {}        # ehdokas-id → ehdokas
        self.closed: dict[str, float] = {}      # lokiin lisätyt / ohitetut ehdokas-id:t
        self.seen: dict[tuple, float] = {}
        self.private_open: dict[str, tuple] = {}  # asema → (yksityispuhelu-ehdokkaan id, viimeisin vuoro)
        self.rev = 0
        self.db = None                          # QsoLog asettaa: ehdokkaat säilyvät uudelleenkäynnistyksen yli

    # ---------------- tallennus (qso_log.sqlite)
    def attach(self, db):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, end REAL, data TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS candidates_closed (id TEXT PRIMARY KEY, t REAL)")
        now = time.time()
        db.execute("DELETE FROM candidates WHERE end < ?", (now - CAND_KEEP,))
        db.execute("DELETE FROM candidates_closed WHERE t < ?", (now - CAND_KEEP * 2,))
        db.commit()
        for cid, t in db.execute("SELECT id, t FROM candidates_closed"):
            self.closed[cid] = t
        for cid, data in db.execute("SELECT id, data FROM candidates"):
            try:
                c = json.loads(data)
            except ValueError:
                continue
            if cid not in self.closed:
                self.cands[cid] = c
        if self.cands:
            self.rev += 1

    def _save(self, cid: str):
        if self.db is None:
            return
        c = self.cands.get(cid)
        try:
            if c:
                self.db.execute("INSERT OR REPLACE INTO candidates (id, end, data) VALUES (?, ?, ?)",
                                (cid, c["end"], json.dumps(c, ensure_ascii=False)))
            else:
                self.db.execute("DELETE FROM candidates WHERE id = ?", (cid,))
            if cid in self.closed:
                self.db.execute("INSERT OR REPLACE INTO candidates_closed (id, t) VALUES (?, ?)", (cid, self.closed[cid]))
            self.db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("Ehdokkaan tallennus epäonnistui: %s", e)

    def on_event(self, p: dict, info: dict | None = None):
        stop = p.get("Stop") or 0
        start = p.get("Start") or 0
        if not stop or not start or stop < start:
            return
        call = base_call(p.get("SourceCall") or "")
        if not call:
            return
        key = (p.get("SourceID"), p.get("DestinationID"), int(float(start) // 3))
        if key in self.seen:
            return
        self.seen[key] = time.time()
        group = "private" not in [str(x).lower() for x in (p.get("CallTypes") or [])]
        own = self.mine(call)
        turn = {"start": float(start), "stop": float(stop), "call": call, "own": own,
                "name": p.get("SourceName") or "", "id": p.get("SourceID"), "ctx": p.get("ContextID"),
                "via": p.get("LinkCall") or p.get("LinkName") or "", "tg": p.get("DestinationID"),
                "tg_name": p.get("DestinationName") or ""}
        if not group:
            other = base_call(p.get("DestinationCall") or "")
            if own and other and not self.mine(other):
                self._private(other, turn, {})
            elif not own and other and self.mine(other):
                self._private(call, turn, turn)
            return
        k = f"TG:{p.get('DestinationID')}"
        q = self.turns.setdefault(k, deque(maxlen=200))
        q.append(turn)
        while q and turn["stop"] - q[0]["stop"] > TURN_KEEP:
            q.popleft()
        others = {t["call"] for t in q if not t["own"]} if own else {call}
        if not own and not any(t["own"] for t in q):
            return
        for other in others:
            self._chain(k, other, q)

    def _chain(self, k: str, other: str, q):
        seq = sorted([t for t in q if t["own"] or t["call"] == other], key=lambda t: t["start"])
        chain = [seq[-1]]
        for t in reversed(seq[:-1]):
            if chain[0]["start"] - t["stop"] > QSO_GAP:
                break
            chain.insert(0, t)
        # vuorot lohkoiksi (peräkkäiset saman puolen lähetykset yhdeksi); vaaditaan oma–toinen–oma
        blocks = [chain[0]["own"]] + [b["own"] for a, b in zip(chain, chain[1:]) if a["own"] != b["own"]]
        if not any(blocks[i] and not blocks[i + 1] and blocks[i + 2] for i in range(len(blocks) - 2)):
            return
        info = next((t for t in reversed(chain) if not t["own"]), {})
        self._upsert(f"{k}:{other}:{int(chain[0]['start'])}", other, chain, private=False, other_info=info)

    def _private(self, other: str, turn: dict, info: dict):
        """Yksityispuhelu: yksi ehdokas per asema niin kauan kuin puhutaan (hiljaisuus katkaisee)."""
        prev_cid, last = self.private_open.get(other, (None, 0.0))
        same = bool(prev_cid) and turn["start"] - last <= QSO_QUIET
        cid = prev_cid if same else f"P:{other}:{int(turn['start'])}"
        self.private_open[other] = (cid, max(last, turn["stop"]) if same else turn["stop"])
        c = self.cands.get(cid)
        prev = c["_turns"] if c else []
        self._upsert(cid, other, prev + [turn], private=True, other_info=info)

    def _upsert(self, cid: str, other: str, turns: list[dict], private: bool, other_info: dict):
        if cid in self.closed:
            return
        own_t = [t for t in turns if t["own"]]
        oth_t = [t for t in turns if not t["own"]]
        prev = self.cands.get(cid, {})
        self.cands[cid] = {
            "id": cid, "call": other, "name": other_info.get("name") or prev.get("name") or "",
            "start": min(t["start"] for t in turns), "end": max(t["stop"] for t in turns),
            "turns": len(turns), "own_turns": len(own_t), "other_turns": len(oth_t),
            "tg": None if private else turns[0].get("tg"), "tg_name": "" if private else turns[0].get("tg_name", ""),
            "private": private, "via": (oth_t[-1]["via"] if oth_t else ""),
            "my_via": own_t[-1]["via"] if own_t else "", "my_ctx": own_t[-1]["ctx"] if own_t else None,
            "_turns": turns[-50:],
        }
        self.rev += 1
        self._save(cid)

    def close(self, cid: str):
        self.cands.pop(cid, None)
        self.closed[cid] = time.time()
        self.rev += 1
        self._save(cid)

    def housekeeping(self):
        now = time.time()
        for cid, c in list(self.cands.items()):
            if now - c["end"] > CAND_KEEP:
                self.cands.pop(cid, None)
                self.rev += 1
                self._save(cid)
        for k, t in list(self.closed.items()):
            if now - t > CAND_KEEP * 2:
                self.closed.pop(k, None)
        for k, t in list(self.seen.items()):
            if now - t > TURN_KEEP * 2:
                self.seen.pop(k, None)
        if self.db is not None:
            try:
                self.db.execute("DELETE FROM candidates_closed WHERE t < ?", (now - CAND_KEEP * 2,))
                self.db.commit()
            except Exception:  # noqa: BLE001
                pass

    def snapshot(self) -> list[dict]:
        now = time.time()
        out = []
        for c in sorted(self.cands.values(), key=lambda c: -c["end"]):
            d = {k: v for k, v in c.items() if not k.startswith("_")}
            d["active"] = now - c["end"] < QSO_QUIET
            out.append(d)
        return out


class QsoLog:
    """Paikallinen loki (SQLite) ja synkronointi palveluihin (QRZ Logbook)."""

    FIELDS = ("call", "name", "start", "end", "band", "freq", "freq_rx", "mode", "submode", "rst_sent", "rst_rcvd",
              "qth", "grid", "comment", "tg", "via", "my_call", "my_grid", "prop_mode")

    def __init__(self, mon, path: Path):
        self.mon = mon
        self.path = path
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS qsos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, call TEXT NOT NULL, name TEXT, start REAL NOT NULL, end REAL,
            band TEXT, freq REAL, freq_rx REAL, mode TEXT, submode TEXT, rst_sent TEXT, rst_rcvd TEXT, qth TEXT,
            grid TEXT, comment TEXT, tg INTEGER, via TEXT, my_call TEXT, my_grid TEXT, source TEXT,
            created REAL, updated REAL,
            qrz_status TEXT, qrz_logid TEXT, qrz_error TEXT, qrz_time REAL)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS qsos_start ON qsos(start)")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(qsos)")}
        if "prop_mode" not in cols:           # 0.17: etenemistapa; BrandMeisterista tunnistetut = INTERNET
            self.db.execute("ALTER TABLE qsos ADD COLUMN prop_mode TEXT")
            self.db.execute("UPDATE qsos SET prop_mode='INTERNET' WHERE source='candidate' OR submode IN ('DMR','DSTAR','C4FM','M17')")
        for svc in ("eqsl", "hamqth"):
            for c, t in (("status", "TEXT"), ("logid", "TEXT"), ("error", "TEXT"), ("time", "REAL")):
                if f"{svc}_{c}" not in cols:
                    self.db.execute(f"ALTER TABLE qsos ADD COLUMN {svc}_{c} {t}")
        self.db.commit()
        self.rev = 0
        self.detector = QsoDetector(self.is_mine)
        self.detector.attach(self.db)
        self.secrets: dict[str, str] = {}
        moved_any = False
        for svc, (field, name) in SECRETS.items():
            self.secrets[svc], moved = secret_store.load_field(self.cfg, field, name)
            moved_any = moved_any or moved
        if moved_any:
            mon.save_config()
        self.qrz_state = {"ok": None, "error": None, "callsign": None, "count": None, "checked": None}
        self.svc_state = {s: {"ok": None, "error": None, "checked": None} for s in ("eqsl", "hamqth")}
        self._pending_deletes: list[str] = []
        self._pending_hq_deletes: list[dict] = []
        self._wake = asyncio.Event()

    # ---------------- asetukset ----------------
    @property
    def cfg(self) -> dict:
        return self.mon.cfg["log"]

    def my_call(self) -> str:
        c = self.cfg.get("my_call") or (self.mon.cfg.get("devices") or {}).get("my_call") or \
            (self.mon.cfg.get("messaging") or {}).get("login") or ""
        return base_call(c)

    def is_mine(self, call: str) -> bool:
        mc = self.my_call()
        return bool(mc) and base_call(call) == mc

    @property
    def qrz_key(self) -> str:
        return self.secrets.get("qrz", "")

    @qrz_key.setter
    def qrz_key(self, v: str):
        self.secrets["qrz"] = v

    def set_secret(self, svc: str, value: str):
        value = (value or "").strip()
        field, name = SECRETS[svc]
        self.secrets[svc] = value
        secret_store.save_field(self.cfg, field, name, value)
        if svc == "qrz":
            self.qrz_state = {"ok": None, "error": None, "callsign": None, "count": None, "checked": None}
        else:
            self.svc_state[svc] = {"ok": None, "error": None, "checked": None}
        # aiemmin tunnusten takia hylätyt yritetään uudelleen
        self.db.execute(f"UPDATE qsos SET {svc}_status='queued' WHERE {svc}_status='auth'")
        self.db.commit()
        self._changed()

    def set_qrz_key(self, key: str):
        self.set_secret("qrz", key)

    def _initial_status(self, svc: str, prop_mode: str | None) -> str | None:
        """Uuden yhteyden tila palvelussa: None = ei käytössä, skip = internet-avusteinen ohitettu, queued = jonoon."""
        if not self.cfg.get(svc):
            return None
        if (prop_mode or "") in ASSISTED and not self.cfg.get(f"{svc}_inet", True):
            return "skip"
        return "queued"

    # ---------------- tunnistus ----------------
    def on_event(self, p: dict):
        if not self.cfg.get("detect", True) or not self.my_call():
            return
        before = self.detector.rev
        self.detector.on_event(p)
        if self.detector.rev != before:
            self.mon.dirty = True

    def candidate_defaults(self, cid: str) -> dict | None:
        c = self.detector.cands.get(cid)
        if not c:
            return None
        dev = {}
        ctx = c.get("my_ctx")
        try:
            dev = self.mon.devmon.state.get(int(ctx), {}) if ctx is not None else {}
        except (TypeError, ValueError):
            dev = {}
        freq = parse_freq(dev.get("rx"))         # radio lähettää toistimen/hotspotin vastaanottotaajuudella
        freq_rx = parse_freq(dev.get("tx"))
        via = c.get("my_via") or c.get("via") or ""
        where = tr("yksityispuhelu") if c["private"] else f"TG {c['tg']}" + (f" {c['tg_name']}" if c.get("tg_name") else "")
        return {"call": c["call"], "name": c.get("name") or "", "start": c["start"], "end": c["end"], "mode_label": "DMR",
                "band": band_for(freq) or self.cfg.get("band") or "70cm", "freq": freq, "freq_rx": freq_rx,
                "rst_sent": self.cfg.get("rst") or "59", "rst_rcvd": self.cfg.get("rst") or "59",   # DMR = puhe
                "comment": f"BrandMeister {where}" + (f" via {via}" if via else ""),
                "tg": c.get("tg"), "via": via, "candidate": cid, "prop_mode": "INTERNET"}

    # ---------------- loki ----------------
    def _changed(self):
        self.rev += 1
        self.mon.dirty = True
        self._wake.set()

    def _clean(self, d: dict) -> dict:
        out = {}
        call = str(d.get("call") or "").strip().upper()
        if not CALL_RE.match(call):
            raise ValueError(tr("Virheellinen kutsu: {x}", x=call or "–"))
        out["call"] = call
        try:
            out["start"] = float(d.get("start"))
            out["end"] = float(d["end"]) if d.get("end") not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError(tr("Virheellinen aika")) from None
        if out["end"] is not None and out["end"] < out["start"]:
            out["end"] = out["start"]
        band = str(d.get("band") or "").strip()
        out["freq"] = parse_freq(d.get("freq")) if d.get("freq") not in (None, "") else None
        out["freq_rx"] = parse_freq(d.get("freq_rx")) if d.get("freq_rx") not in (None, "") else None
        # taajuus ratkaisee kaistan, jos ne ovat ristiriidassa
        out["band"] = band_for(out["freq"]) or (band if band in BAND_NAMES else self.cfg.get("band") or "70cm")
        grid = str(d.get("grid") or "").strip()
        if grid and not GRID_RE.match(grid):
            raise ValueError(tr("Virheellinen lokaattori: {x}", x=grid))
        out["grid"] = grid
        for k, n in (("name", 60), ("qth", 60), ("comment", 200), ("rst_sent", 3), ("rst_rcvd", 3), ("via", 30)):
            out[k] = str(d.get(k) or "").strip()[:n]
        label = str(d.get("mode_label") or "").upper() or mode_label(d.get("mode"), d.get("submode")) or \
            (self.cfg.get("mode") or "DMR")
        if label not in MODES:
            raise ValueError(tr("Virheellinen arvo: {x}", x=label))
        out["mode"], out["submode"] = MODES[label]
        if "prop_mode" in d and d.get("prop_mode") is not None:
            pm = str(d.get("prop_mode") or "").upper()
            if pm not in PROP_MODES:
                raise ValueError(tr("Virheellinen arvo: {x}", x=pm))
            out["prop_mode"] = pm
        else:
            out["prop_mode"] = default_prop(label)
        try:
            out["tg"] = int(d["tg"]) if d.get("tg") not in (None, "") else None
        except (TypeError, ValueError):
            out["tg"] = None
        out["my_call"] = self.my_call() or None
        out["my_grid"] = self.cfg.get("my_grid") or ""
        return out

    def add(self, d: dict) -> int:
        q = self._clean(d)
        now = time.time()
        st = [self._initial_status(s, q.get("prop_mode")) for s in SERVICES]
        cur = self.db.execute(
            f"INSERT INTO qsos ({', '.join(self.FIELDS)}, source, created, updated, qrz_status, eqsl_status, hamqth_status) "
            f"VALUES ({', '.join('?' * len(self.FIELDS))}, ?, ?, ?, ?, ?, ?)",
            [q.get(f) for f in self.FIELDS] + ["candidate" if d.get("candidate") else "manual", now, now] + st)
        self.db.commit()
        if d.get("candidate"):
            self.detector.close(str(d["candidate"]))
        self._changed()
        return cur.lastrowid

    def update(self, qid: int, d: dict):
        old = self.get(qid)
        if not old:
            raise KeyError(qid)
        merged = {**old, **d}
        if "prop_mode" not in d and "mode_label" in d:       # tila vaihtui eikä etenemistapaa annettu: oletus tilan mukaan
            merged.pop("prop_mode", None)
        q = self._clean(merged)
        st = {}
        for s in SERVICES:
            prev = old.get(f"{s}_status")
            if prev == "ok":
                st[s] = "ok" if s == "eqsl" else "queued"    # eQSL ei tue muokkausta; QRZ: REPLACE, HamQTH: PUT
            elif prev in ("queued", "error", "failed", "auth"):
                st[s] = "queued"
            else:
                st[s] = self._initial_status(s, q.get("prop_mode"))
        self.db.execute(f"UPDATE qsos SET {', '.join(f + '=?' for f in self.FIELDS)}, updated=?, "
                        "qrz_status=?, qrz_error=NULL, eqsl_status=?, eqsl_error=NULL, hamqth_status=?, hamqth_error=NULL "
                        "WHERE id=?", [q.get(f) for f in self.FIELDS] + [time.time(), st["qrz"], st["eqsl"], st["hamqth"], qid])
        self.db.commit()
        self._changed()

    def delete(self, qid: int, remote: bool = False):
        old = self.get(qid)
        if not old:
            return
        self.db.execute("DELETE FROM qsos WHERE id=?", (qid,))
        self.db.commit()
        if remote and old.get("qrz_logid"):
            self._pending_deletes.append(old["qrz_logid"])
        if remote and old.get("hamqth_logid"):
            try:
                self._pending_hq_deletes.append(json.loads(old["hamqth_logid"]))
            except ValueError:
                pass
        self._changed()

    def get(self, qid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM qsos WHERE id=?", (qid,)).fetchone()
        return {**dict(r), "mode_label": mode_label(r["mode"], r["submode"])} if r else None

    def list(self, limit: int = 200, q: str = "", mode: str = "", prop: str = "") -> list[dict]:
        sql, args, where = "SELECT * FROM qsos", [], []
        if prop == "inet":
            where.append("prop_mode IN ('INTERNET','RPT','ECH')")
        elif prop == "direct":
            where.append("(prop_mode IS NULL OR prop_mode NOT IN ('INTERNET','RPT','ECH'))")
        if q:
            where.append("(call LIKE ? OR name LIKE ? OR comment LIKE ?)")
            args += [f"%{q.upper()}%", f"%{q}%", f"%{q}%"]
        if mode:
            m, sm = MODES.get(mode.upper(), (mode.upper(), ""))
            if sm:
                where.append("submode = ?")
                args.append(sm)
            else:
                where.append("mode = ? AND (submode IS NULL OR submode = '')")
                args.append(m)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY start DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [{**dict(r), "mode_label": mode_label(r["mode"], r["submode"])} for r in self.db.execute(sql, args)]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM qsos").fetchone()[0]

    def snapshot(self) -> dict:
        def pend(s):
            return self.db.execute(f"SELECT COUNT(*) FROM qsos WHERE {s}_status IN ('queued','error')").fetchone()[0]
        return {"rev": self.rev, "count": self.count(), "my_call": self.my_call(),
                "modes": [[k, default_rst(k), default_prop(k)] for k in MODES],
                "candidates": self.detector.snapshot() if self.cfg.get("detect", True) else [],
                "qrz": {**self.qrz_state, "enabled": bool(self.cfg.get("qrz")), "key": bool(self.qrz_key), "pending": pend("qrz")},
                "eqsl": {**self.svc_state["eqsl"], "enabled": bool(self.cfg.get("eqsl")), "key": bool(self.secrets.get("eqsl")),
                         "long_pass": len(self.secrets.get("eqsl") or "") > EQSL_PASS_MAX, "pending": pend("eqsl")},
                "hamqth": {**self.svc_state["hamqth"], "enabled": bool(self.cfg.get("hamqth")),
                           "key": bool(self.secrets.get("hamqth")), "pending": pend("hamqth")}}

    def housekeeping(self):
        before = self.detector.rev
        self.detector.housekeeping()
        if before != self.detector.rev:
            self.mon.dirty = True

    def close(self):
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------------- QRZ Logbook ----------------
    def _ua(self) -> str:
        return f"MonitorForAmateurRadio/{__version__} (+https://github.com/hannulma/monitor-for-amateur-radio)"

    async def _qrz(self, data: dict) -> dict:
        from .messaging import http_session
        async with http_session(20) as s:
            async with s.post(QRZ_URL, data={"KEY": self.qrz_key, **data}, headers={"User-Agent": self._ua()}) as r:
                text = await r.text()
                if r.status != 200:
                    raise ConnectionError(f"HTTP {r.status}")
        res = {k: v[0] for k, v in parse_qs(text.strip(), keep_blank_values=True).items()}
        if "DATA" in res:      # STATUS: sisäkkäiset nimi=arvo-parit
            res["_data"] = {k: v[0] for k, v in parse_qs(res["DATA"], keep_blank_values=True).items()}
        return res

    async def qrz_status(self) -> dict:
        if not self.qrz_key:
            raise ValueError(tr("Aseta QRZ Logbookin API-avain"))
        res = await self._qrz({"ACTION": "STATUS"})
        if res.get("RESULT") == "OK":
            d = res.get("_data") or {}
            self.qrz_state = {"ok": True, "error": None, "callsign": d.get("CALLSIGN") or res.get("CALLSIGN"),
                              "count": d.get("COUNT") or res.get("COUNT"), "checked": time.time()}
        else:
            self.qrz_state = {"ok": False, "error": res.get("REASON") or res.get("RESULT") or "?", "callsign": None,
                              "count": None, "checked": time.time()}
        self.mon.dirty = True
        return self.qrz_state

    async def _qrz_send(self, row: dict):
        replace = bool(row.get("qrz_logid"))          # muokattu yhteys korvaa aiemmin lähetetyn
        try:
            res = await self._qrz({"ACTION": "INSERT", "ADIF": qso_adif(row), **({"OPTION": "REPLACE"} if replace else {})})
        except Exception as e:  # noqa: BLE001
            self._set_qrz(row["id"], "error", None, str(e) or e.__class__.__name__)
            return False
        result, reason = res.get("RESULT", ""), res.get("REASON", "")
        if result in ("OK", "REPLACE"):
            self._set_qrz(row["id"], "ok", res.get("LOGID") or row.get("qrz_logid"), None)
        elif result == "AUTH" or "invalid api key" in reason.lower() or "access" in reason.lower():
            self._set_qrz(row["id"], "auth", None, reason or tr("API-avain ei kelpaa"))
            self.qrz_state.update(ok=False, error=reason or tr("API-avain ei kelpaa"))
        elif "duplicate" in reason.lower():
            self._set_qrz(row["id"], "ok", row.get("qrz_logid"), tr("oli jo QRZ:ssa"))
        else:
            self._set_qrz(row["id"], "failed", None, reason or result or "?")
        return True

    def _set_qrz(self, qid: int, status: str, logid, err):
        self._set(qid, "qrz", status, logid, err)

    def _set(self, qid: int, svc: str, status: str, logid, err):
        self.db.execute(f"UPDATE qsos SET {svc}_status=?, {svc}_logid=COALESCE(?, {svc}_logid), {svc}_error=?, {svc}_time=? "
                        "WHERE id=?", (status, logid, err, time.time(), qid))
        self.db.commit()
        self.rev += 1
        self.mon.dirty = True

    def retry(self, svc: str, qid: int | None = None):
        if svc not in SERVICES:
            raise ValueError(svc)
        if qid is None:
            self.db.execute(f"UPDATE qsos SET {svc}_status='queued' WHERE {svc}_status IN ('error','failed','auth')")
        else:      # myös ohitettu (internet-avusteinen) yhteys voidaan lähettää käsin
            self.db.execute(f"UPDATE qsos SET {svc}_status='queued' WHERE id=?", (qid,))
        self.db.commit()
        self._changed()

    def retry_qrz(self, qid: int | None = None):
        self.retry("qrz", qid)

    # ---------------- eQSL ----------------
    async def _eqsl_post(self, adif: str) -> tuple[int, str]:
        import aiohttp
        from .messaging import http_session
        form = aiohttp.FormData()
        form.add_field("EQSL_USER", self.cfg.get("eqsl_user") or self.my_call())
        form.add_field("EQSL_PSWD", self.secrets.get("eqsl") or "")
        form.add_field("Filename", adif.encode("utf-8"), filename="upload.adi", content_type="text/plain")
        async with http_session(30) as sess:
            async with sess.post(EQSL_URL, data=form, headers={"User-Agent": self._ua()}) as r:
                return r.status, await r.text()

    def _eqsl_adif(self, row: dict) -> str:
        rec = qso_adif(row)
        if self.cfg.get("eqsl_qth"):
            rec = rec[:-5] + _adif_field("APP_EQSL_QTH_NICKNAME", self.cfg["eqsl_qth"]) + "<eor>"
        return (_adif_field("ADIF_VER", "3.1.4") + _adif_field("PROGRAMID", "MonitorForAmateurRadio")
                + _adif_field("PROGRAMVERSION", __version__) + "<eoh>\n" + rec + "\n")

    @staticmethod
    def eqsl_result(status: int, text: str) -> tuple[str, str | None]:
        """eQSL:n vastaus → (tila, virhe). ok / auth / error (uusi yritys) / failed."""
        if status != 200:
            return "error", f"HTTP {status}"
        plain = re.sub(r"<[^>]*>", " ", text)
        m = re.search(r"Result:\s*(\d+)\s+out of\s+(\d+)\s+records added", plain, re.I)
        err = re.search(r"Error:\s*([^\r\n<]+)", plain)
        if err:
            msg = err.group(1).strip()
            if "no match" in msg.lower() or "missing eqsl" in msg.lower() or "multiple accounts" in msg.lower():
                return "auth", "Error: " + msg
            if "down" in msg.lower():
                return "error", "Error: " + msg
            return "failed", "Error: " + msg
        if re.search(r"Bad record:\s*Duplicate", plain, re.I):
            return "ok", tr("oli jo eQSL:ssä")
        if m and m.group(1) == m.group(2) and m.group(2) != "0":
            return "ok", None
        warn = re.search(r"Warning:\s*([^\r\n<]+)", plain)
        return "failed", ("Warning: " + warn.group(1).strip()) if warn else (m.group(0) if m else "?")

    async def _eqsl_send(self, row: dict) -> bool:
        try:
            status, text = await self._eqsl_post(self._eqsl_adif(row))
        except Exception as e:  # noqa: BLE001
            self._set(row["id"], "eqsl", "error", None, str(e) or e.__class__.__name__)
            return False
        st, err = self.eqsl_result(status, text)
        self._set(row["id"], "eqsl", st, None, err)
        if st == "auth":
            self.svc_state["eqsl"] = {"ok": False, "error": err, "checked": time.time()}
        elif st == "ok":
            self.svc_state["eqsl"] = {"ok": True, "error": None, "checked": time.time()}
        return st != "error"

    # ---------------- HamQTH ----------------
    @staticmethod
    def hamqth_qso(row: dict) -> dict:
        """Yhteys HamQTH:n API v2 -muodossa (ADIF-kenttänimet pienellä)."""
        d = {"call": row["call"], "qso_date": _utc(row["start"], "%Y%m%d"), "time_on": _utc(row["start"], "%H%M%S"),
             "band": (row.get("band") or "").upper(), "mode": row.get("mode") or "DIGITALVOICE"}
        if row.get("submode"):
            d["submode"] = row["submode"]
        if row.get("freq"):
            d["freq"] = f"{row['freq']:.6f}".rstrip("0").rstrip(".")
        if row.get("end"):
            d["time_off"] = _utc(row["end"], "%H%M%S")
            d["qso_date_off"] = _utc(row["end"], "%Y%m%d")
        for k, f in (("rst_sent", "rst_sent"), ("rst_rcvd", "rst_rcvd"), ("name", "name"), ("qth", "qth"),
                     ("gridsquare", "grid"), ("comment", "comment"), ("my_gridsquare", "my_grid"),
                     ("station_callsign", "my_call"), ("prop_mode", "prop_mode")):
            v = row.get(f)
            if v:
                d[k] = v.upper() if k in ("gridsquare", "my_gridsquare") else v
        return d

    @staticmethod
    def _hamqth_key_fields(q: dict) -> dict:
        return {k: q[k] for k in ("call", "qso_date", "time_on", "band", "mode") if q.get(k)}

    async def _hamqth(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> tuple[int, str]:
        from .messaging import http_session
        h = {"Authorization": f"Bearer {self.secrets.get('hamqth') or ''}", "User-Agent": self._ua(),
             "Accept": "application/json"}
        async with http_session(20) as sess:
            async with sess.request(method, HAMQTH_API + path, json=body, params=params, headers=h) as r:
                return r.status, await r.text()

    async def hamqth_test(self) -> dict:
        if not self.secrets.get("hamqth"):
            raise ValueError(tr("Aseta HamQTH:n API-avain"))
        status, _ = await self._hamqth("GET", f"/callbook/{self.my_call() or 'OH2BEA'}")
        if status == 401:
            self.svc_state["hamqth"] = {"ok": False, "error": tr("API-avain ei kelpaa"), "checked": time.time()}
        elif status in (200, 404):
            self.svc_state["hamqth"] = {"ok": True, "error": None, "checked": time.time()}
        else:
            self.svc_state["hamqth"] = {"ok": False, "error": f"HTTP {status}", "checked": time.time()}
        self.mon.dirty = True
        return self.svc_state["hamqth"]

    async def _hamqth_send(self, row: dict) -> bool:
        qso = self.hamqth_qso(row)
        orig = None
        if row.get("hamqth_logid"):
            try:
                orig = json.loads(row["hamqth_logid"])
            except ValueError:
                orig = None
        try:
            if orig:
                status, text = await self._hamqth("PUT", "/log/qso", {"original": orig, "qso": qso})
                if status == 404:          # poistettu HamQTH:sta: lisätään uudelleen
                    status, text = await self._hamqth("POST", "/log/qso", {"qso": qso})
            else:
                status, text = await self._hamqth("POST", "/log/qso", {"qso": qso})
        except Exception as e:  # noqa: BLE001
            self._set(row["id"], "hamqth", "error", None, str(e) or e.__class__.__name__)
            return False
        ident = json.dumps(self._hamqth_key_fields(qso))
        if status in (200, 201, 204):
            self._set(row["id"], "hamqth", "ok", ident, None)
            self.svc_state["hamqth"].update(ok=True, error=None)
        elif status == 409:
            self._set(row["id"], "hamqth", "ok", ident, tr("oli jo HamQTH:ssa"))
        elif status in (401, 403):
            err = tr("API-avain ei kelpaa") if status == 401 else _api_error(text) or f"HTTP {status}"
            self._set(row["id"], "hamqth", "auth", None, err)
            self.svc_state["hamqth"] = {"ok": False, "error": err, "checked": time.time()}
        elif status in (422, 400, 404):
            self._set(row["id"], "hamqth", "failed", None, _api_error(text) or f"HTTP {status}")
        else:
            self._set(row["id"], "hamqth", "error", None, f"HTTP {status}")
            return False
        return True

    def _due(self, svc: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM qsos WHERE {svc}_status='queued' OR ({svc}_status='error' AND {svc}_time < ?) "
            "ORDER BY start LIMIT 20", (time.time() - QRZ_RETRY,))]

    async def sync_once(self):
        """Yksi synkronointikierros kaikkiin käytössä oleviin palveluihin."""
        if self.qrz_key:
            while self._pending_deletes:
                logid = self._pending_deletes.pop(0)
                try:
                    await self._qrz({"ACTION": "DELETE", "LOGIDS": logid})
                except Exception as e:  # noqa: BLE001
                    log.info("QRZ-poisto epäonnistui: %s", e)
        if self.secrets.get("hamqth"):
            while self._pending_hq_deletes:
                ident = self._pending_hq_deletes.pop(0)
                try:
                    await self._hamqth("DELETE", "/log/qso", params=ident)
                except Exception as e:  # noqa: BLE001
                    log.info("HamQTH-poisto epäonnistui: %s", e)
        senders = {"qrz": self._qrz_send, "eqsl": self._eqsl_send, "hamqth": self._hamqth_send}
        for svc, send in senders.items():
            if not self.cfg.get(svc) or not self.secrets.get(svc):
                continue
            for row in self._due(svc):
                if not await send(row):
                    break          # verkkovirhe: yritetään myöhemmin
                if self.db.execute(
                        f"SELECT {svc}_status FROM qsos WHERE id=?", (row["id"],)).fetchone()[0] == "auth":
                    break          # väärät tunnukset: ei toisteta pyyntöjä (palvelu voi estää osoitteen)

    async def run(self):
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), 60)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.sync_once()
            except Exception:  # noqa: BLE001
                log.exception("Lokin synkronointi")


def _api_error(text: str) -> str:
    """JSON-virhevastauksen viesti (HamQTH: {"error": …} tai {"message": …})."""
    try:
        d = json.loads(text)
    except ValueError:
        return ""
    if isinstance(d, dict):
        for k in ("error", "message", "detail", "title"):
            v = d.get(k)
            if isinstance(v, str) and v:
                return v[:200]
            if isinstance(v, dict) and v.get("message"):
                return str(v["message"])[:200]
    return ""
