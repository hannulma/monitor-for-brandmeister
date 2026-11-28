"""Perustestit: ajetaan GitHub Actionsissa Windowsilla, macOS:llä ja Linuxilla (ei verkkoyhteyksiä)."""
import asyncio
import os
import calendar
import json
import re
import socket
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from bmmonitor import APP_ID, __version__
from bmmonitor.app import Matcher, parse_patterns
from bmmonitor.features import Commands
from bmmonitor.messaging import aprs_passcode, clean_text, parse_feed, split_message
from bmmonitor.services import (distance_km, latlon_to_locator, locator_to_latlon, parse_location,
                                point_in_polygon, short_traffic, short_warning, sun_elevation)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Asetukset ja lokit väliaikaiseen kansioon kaikilla alustoilla."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


def test_patterns_and_matcher():
    pats = parse_patterns("244*, 91 2441?")
    assert pats == ["244*", "91", "2441?"]
    m = Matcher(pats)
    assert m.match(24420) == "244*"
    assert m.match(91) == "91"
    assert m.match(24415) == "244*"
    assert m.match(3100) is None
    with pytest.raises(ValueError):
        parse_patterns("abc")


def test_aprs_helpers():
    assert aprs_passcode("N0CALL") == 13023
    assert aprs_passcode("n0call-13") == 13023
    parts = split_message("x" * 150, 2)
    assert len(parts) == 2 and all(len(p) <= 67 for p in parts)
    assert split_message("lyhyt", 2) == ["lyhyt"]
    assert clean_text("<b>A</b> | B ~ {C}", False) == "A / B - (C}"
    assert clean_text("äö", True) == "ao"


def test_locator_and_geo():
    lat, lon = locator_to_latlon("KP20LE")
    assert abs(lat - 60.19) < 0.05 and abs(lon - 24.96) < 0.05
    assert latlon_to_locator(60.1699, 24.9384) == "KP20LE"
    assert parse_location("60,17 24,94") == (60.17, 24.94)
    assert 160 < distance_km(60.17, 24.94, 61.5, 23.76) < 170
    sq = [(0, 0), (0, 1), (1, 1), (1, 0)]
    assert point_in_polygon(0.5, 0.5, sq) and not point_in_polygon(1.5, 0.5, sq)
    # keskikesän aurinkokeskipäivä Helsingissä (~10.20 UTC): aurinko n. 53° korkeudella.
    # UTC-aika, jotta testi ei riipu koneen aikavyöhykkeestä.
    noon = calendar.timegm((2026, 6, 21, 10, 20, 0))
    assert 50 < sun_elevation(60.17, 24.94, noon) < 56
    midnight = calendar.timegm((2026, 12, 21, 22, 20, 0))
    assert sun_elevation(60.17, 24.94, midnight) < -40


def test_rss_parse():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Testi</title>
      <item><title>Uusin</title><guid>2</guid></item><item><title>Vanha</title><guid>1</guid></item></channel></rss>"""
    title, items = parse_feed(rss)
    assert title == "Testi" and [i["id"] for i in items] == ["2", "1"]


def test_short_traffic():
    props = {"situationType": "TRAFFIC_ANNOUNCEMENT", "trafficAnnouncementType": "GENERAL"}
    ann = {
        "title": "Tie 101, eli Kehä I, Espoo. Liikennetiedote. ",
        "location": {"description": "Tie 101, eli Kehä I, Espoo.\nTarkempi paikka: Kalevalantie."},
        "locationDetails": {"roadAddressLocation": {"primaryPoint": {
            "municipality": "Espoo", "roadAddress": {"road": 101}, "roadName": "Kehä I",
            "alertCLocation": {"name": "Kalevalantie"}}}},
        "features": [{"name": "Liikennevalot"}, {"name": "Epäkunnossa"}],
    }
    assert short_traffic(props, ann) == "Tie 101 Espoo, Kalevalantie: Liikennevalot, epäkunnossa"


def test_short_warning():
    w = {"event": "Liikennesää", "color": "yellow", "msgType": "Update",
         "params": {"eventCause": "snowOrSleet", "probability": "30"},
         "onset": time.time() - 60, "expires": None}
    s = short_warning(w)
    assert s.startswith("Keltainen liikennesää: lumi/räntä, nyt") and s.endswith("(30 %)")
    assert short_warning({**w, "msgType": "Cancel"}) == "POISTETTU: Keltainen liikennesää"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_ping_and_commands():
    """Käynnistää web-palvelimen ilman BM-yhteyttä ja tarkistaa rajapinnan sekä radiokomennot."""
    from aiohttp import web

    from bmmonitor.app import Monitor, config_dir, make_app

    async def run():
        mon = Monitor(config_dir() / "config.json")
        mon.update_config({"messaging": {"login": "OH2ABC-13", "recipients": "OH2ABC-7"}})
        port = _free_port()
        runner = web.AppRunner(make_app(mon))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        loop = asyncio.get_running_loop()
        body = await loop.run_in_executor(None, lambda: urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/ping", timeout=5).read())
        await runner.cleanup()
        info = json.loads(body)
        assert info == {"app": APP_ID, "version": __version__}
        c = Commands(mon)
        assert c.handle("OH2ABC-7", "ohje").startswith("Komennot:")
        assert c.handle("OH2ABC-7", "apua").startswith("Tuntematon")
        assert "ei liikennettä" in c.handle("OH2ABC-7", "TG244")
        assert c.handle("OH9EVIL", "TG244") is None   # ei sallittu lähettäjä

    asyncio.run(run())


def test_language_switch():
    from bmmonitor import i18n
    from bmmonitor.features import Commands  # noqa: F401  (tuonti varmistaa, ettei kierrosta ole)
    try:
        i18n.set_lang("en")
        assert i18n.tr("Anna puheryhmä") == "Enter a talkgroup"
        assert i18n.tr("Virheellinen kutsu tai DMR-ID: {x}", x="X") == "Invalid callsign or DMR ID: X"
        assert i18n.num(1.25, 2) == "1.25" and i18n.bearing(1) == "NE"
        i18n.set_lang("fi")
        assert i18n.tr("Anna puheryhmä") == "Anna puheryhmä" and i18n.num(1.25, 2) == "1,25"
        # jokaisen käännöksen paikkamerkit täsmäävät suomenkielisen avaimen kanssa
        ph = re.compile(r"\{(\w+)\}")
        for fi, en in i18n.EN.items():
            assert set(ph.findall(fi)) == set(ph.findall(en)), fi
    finally:
        i18n.set_lang("fi")


def test_ui_dictionary_placeholders():
    """static/i18n.js: käännösten paikkamerkit ({n} jne.) vastaavat suomenkielisiä avaimia."""
    js = (Path(__file__).resolve().parents[1] / "bmmonitor" / "static" / "i18n.js").read_text("utf-8")
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)":\s*\n?\s*"((?:[^"\\]|\\.)*)"', js)
    assert len(pairs) > 300
    ph = re.compile(r"\{(\w+)\}")
    for fi, en in pairs:
        assert set(ph.findall(fi)) == set(ph.findall(en)), fi


def test_update_versions():
    from bmmonitor import updater as U
    pv = U.parse_version
    assert pv("0.9.0b1") < pv("0.9.0rc1") < pv("0.9.0") < pv("0.9.1a1") < pv("1.0")
    assert pv("v0.8.0b3") == (0, 8, 0, 1, 3) and pv("latest") is None
    rels = [{"tag_name": "v1.0.0b1"}, {"tag_name": "v0.9.0"}, {"tag_name": "v2.0.0", "draft": True}, {"tag_name": "nightly"}]
    assert U.pick_latest(rels, "0.8.0")["tag_name"] == "v0.9.0"          # vakaa käyttäjä: ei betoja
    assert U.pick_latest(rels, "0.8.0b3")["tag_name"] == "v1.0.0b1"      # beta-käyttäjä: myös betat
    assert U.pick_latest(rels, "0.9.0", beta=True)["tag_name"] == "v1.0.0b1"   # vakaa + testiversiot-asetus
    assert U.pick_latest(rels, "0.8.0b3", beta=False)["tag_name"] == "v0.9.0"
    sums = U.parse_sums("a" * 64 + "  file.dmg\n" + "B" * 64 + " *other.exe\nroska\n")
    assert sums == {"file.dmg": "a" * 64, "other.exe": "b" * 64}
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><updated>2026-11-19T17:00:00Z</updated>
        <link rel="alternate" href="https://github.com/hannulma/monitor-for-amateur-radio/releases/tag/v0.9.0b1"/></entry>
      <entry><link rel="alternate" href="https://github.com/hannulma/monitor-for-amateur-radio/releases/tag/nightly"/></entry>
    </feed>"""
    rel = U.releases_from_atom(atom)
    assert [r["tag_name"] for r in rel] == ["v0.9.0b1"]
    a = U.find_asset(rel[0], "macos")
    assert a["name"] == "MonitorForBrandMeister-0.9.0b1-macos-universal.dmg"
    assert a["url"].endswith("/releases/download/v0.9.0b1/MonitorForBrandMeister-0.9.0b1-macos-universal.dmg")
    assert U.find_asset(rel[0], "windows-setup")["name"].endswith("-windows-setup.exe")


def test_checkin_round(tmp_path):
    """Check-in: saapumisjärjestys pysyy, päällekkäisyys, kirjaus, numerointi ja vienti."""
    from bmmonitor.checkin import CheckIn, clean_city, country_prefix

    class Mon:
        cfg_path = tmp_path / "config.json"
        cfg = {}
        def touch(self): pass
        def save_config(self): pass
        def tg_name(self, tg): return "Finland"

    assert clean_city("Jarvenpaa, KP20NK") == "Jarvenpaa" and clean_city("Oulu") == "Oulu"
    assert country_prefix(244) == "244" and country_prefix(91) is None
    ci = CheckIn(Mon(), api=None)
    ci.start(244, "177", "20.11.2026", "oh2abc")
    t = time.time()

    def ev(call, sid, src, start, stop=0, ctx=244207):
        ci.on_event({"SourceCall": call, "SourceName": "X", "SourceID": src, "DestinationID": 244,
                     "SessionID": sid, "Start": start, "Stop": stop, "ContextID": ctx,
                     "Event": "Session-Stop" if stop else "Session-Start"})

    ev("OH2AAA", "a", 2440001, t)
    ev("ES1BBB", "b", 2480001, t + 1)                 # avaa toisen päälle -> päällekkäin
    ev("OH2AAA", "a", 2440001, t, t + 3)
    ev("OH3CCC", "c", 2440002, t + 5, ctx=2440002001)
    ev("OH2AAA", "d", 2440001, t + 9)                 # uusi avaus: sama rivi, ei siirry
    ev("OH9ZZZ", "z", 2440009, t - 600)               # ennen kierrosta -> ei mukaan
    ev("OH4XXX", "x", 2440004, t, 0)
    ci.on_event({"SourceCall": "OH5TG", "DestinationID": 2441, "Start": t, "SessionID": "o"})   # eri TG
    # avauksen alku pelkällä DMR ID:llä, kutsu myöhemmin samassa avauksessa -> yksi rivi, yksi avaus
    ci.on_event({"SourceCall": "", "SourceID": 3228648, "DestinationID": 244, "SessionID": "k", "Start": t + 20})
    assert "3228648" not in [s["call"] for s in ci.snapshot()["stations"]] and ci.snapshot()["id_only"] == 1
    ci.on_event({"SourceCall": "KE7UYB", "SourceName": "David", "SourceID": 3228648, "DestinationID": 244,
                 "SessionID": "k", "Start": t + 20, "Stop": t + 24, "Event": "Session-Stop"})
    ci.on_event({"SourceCall": "", "SourceID": 2449999, "DestinationID": 244, "SessionID": "q", "Start": t + 30})
    snap = ci.snapshot()
    ke = [s for s in snap["stations"] if s["call"] == "KE7UYB"]
    assert len(ke) == 1 and ke[0]["keys"] == 1 and ke[0]["idx"] == 5 and snap["id_only"] == 1
    calls = [s["call"] for s in ci.snapshot()["stations"]]
    assert calls == ["OH2AAA", "ES1BBB", "OH3CCC", "OH4XXX", "KE7UYB"]
    st = {s["call"]: s for s in ci.s["stations"]}
    assert st["OH2AAA"]["keys"] == 2 and st["ES1BBB"]["overlap"] == 1
    assert st["ES1BBB"]["via"] == "Ulkomaat" and st["OH3CCC"]["via"] == "Hotspot"

    ci.devices[244207] = {"call": "OH2RUP", "city": "Jarvenpaa, KP20NK"}
    ci._fill_device(st["OH2AAA"])
    assert st["OH2AAA"]["via"] == "OH2RUP Jarvenpaa" and st["OH2AAA"]["dev"] == "OH2RUP, Jarvenpaa"

    for c in ("OH3CCC", "OH2AAA", "ES1BBB"):
        ci.log_station(c)
    assert [(e["nro"], e["call"], e["oper"]) for e in ci.s["log"]] == [(1, "OH3CCC", "OH2ABC"), (2, "OH2AAA", "OH2ABC"), (3, "ES1BBB", "OH2ABC")]
    ci.log_station("OH2AAA")                           # kahdesti -> ei uutta riviä
    assert len(ci.s["log"]) == 3
    with pytest.raises(ValueError):
        ci.log_station("OH9NOPE")
    ci.edit("OH2AAA", "info", "mobiili")
    ci.edit("OH2AAA", "kunta", "Tuusula")
    ci.mark_copied(3)
    ci.unlog("OH3CCC")                                 # numerot tiivistyvät, kopioidut merkitään uudelleen
    assert [(e["nro"], e["call"]) for e in ci.s["log"]] == [(1, "OH2AAA"), (2, "ES1BBB")] and ci.s["copied_upto"] == 0
    ci.devices[244207] = {"call": "OH2RUP", "city": "Kerava"}
    ci._fill_device(st["OH2AAA"])                      # käsin muutettu kunta säilyy
    tsv = ci.export("tsv")
    assert tsv.splitlines()[0] == "1\tOH2AAA\tOH2RUP Kerava\tTuusula\tmobiili\tOH2ABC"
    assert ci.export("tsv", since=1).count("\n") == 0

    ci.stop()
    files = list((tmp_path / "checkin").glob("*.csv"))
    assert len(files) == 1 and "OH2AAA" in files[0].read_text("utf-8-sig")
    ci2 = CheckIn(Mon(), api=None)                     # tila säilyy uudelleenkäynnistyksessä
    assert [e["call"] for e in ci2.s["log"]] == ["OH2AAA", "ES1BBB"] and not ci2.s["active"]
    ci2.start(244)
    assert ci2.s["round"] == "178" and ci2.s["log"] == []


def test_sheet_signature_matches_apps_script():
    """Allekirjoitus vastaa Apps Scriptin laskemaa (vektori laskettu Node.js:n HMAC-SHA256:lla, UTF-8)."""
    from bmmonitor.sheetsync import sign, valid_url
    assert sign("k" * 64, "OH2ABC", 1795000000, "abcdef0123456789", "edit", '{"value":"Järvenpää"}') == "c3cba856808a6fead5bdc2d84c3c0e4f4a6757dac07e3998c93a08abfa5e8131"
    assert valid_url("https://script.google.com/macros/s/AKfycbx_abc-123/exec")
    assert not valid_url("https://evil.example.com/macros/s/x/exec") and not valid_url("http://127.0.0.1:8840/exec")
    assert valid_url("http://127.0.0.1:8840/exec", allow_test=True)


def test_secrets_not_in_config_when_keyring(tmp_path, monkeypatch):
    """API-avain ja passcode siirtyvät salasanavarastoon; asetustiedostoon ja käyttöliittymään ei päädy arvoa."""
    from bmmonitor import secret_store
    store = {}
    monkeypatch.setattr(secret_store, "put", lambda n, v: store.__setitem__(n, v) or True)
    monkeypatch.setattr(secret_store, "get", lambda n: store.get(n))
    monkeypatch.setattr(secret_store, "delete", lambda n: store.pop(n, None))
    cfg = {"bm_api_key": "SECRETKEY", "messaging": {"passcode": "12345"}}
    v, moved = secret_store.load_field(cfg, "bm_api_key", "bm-api-key")
    assert v == "SECRETKEY" and moved and cfg["bm_api_key"] == "" and cfg["bm_api_key_store"] == "keyring"
    assert secret_store.load_field(cfg, "bm_api_key", "bm-api-key") == ("SECRETKEY", False)
    secret_store.save_field(cfg["messaging"], "passcode", "aprs-passcode", "23456")
    assert cfg["messaging"] == {"passcode": "", "passcode_store": "keyring"} and store["aprs-passcode"] == "23456"
    secret_store.save_field(cfg["messaging"], "passcode", "aprs-passcode", "")
    assert "aprs-passcode" not in store and cfg["messaging"] == {"passcode": ""}
    # ilman varastoa arvo jää asetustiedostoon kuten ennenkin
    monkeypatch.setattr(secret_store, "put", lambda n, v: False)
    c2 = {}
    secret_store.save_field(c2, "bm_api_key", "bm-api-key", "K2")
    assert c2 == {"bm_api_key": "K2"}


def test_clean_text_linear_on_many_brackets():
    from bmmonitor.messaging import clean_text
    t0 = time.time()
    assert clean_text("<b>Hei</b> " + "<" * 50000, False).startswith("Hei")
    assert time.time() - t0 < 1


def test_checkin_link_kinds(tmp_path):
    """RPT/HOTSPOT: yhteystyyppi (TetraPack, POC, Fusion, Open DMR) ja paikkakunta vain kerran."""
    from bmmonitor.checkin import CheckIn, device_label, link_kind

    class Mon:
        cfg_path = tmp_path / "config.json"
        cfg = {}
        def touch(self): pass
        def save_config(self): pass
        def tg_name(self, tg): return "Finland"

    assert device_label("OH8DMRO Oulu N", "Oulu") == "OH8DMRO Oulu N"
    assert device_label("OH2RUP", "Jarvenpaa") == "OH2RUP Jarvenpaa"
    assert device_label("OH1X Ylöjärvi", "Ylojarvi") == "OH1X Ylöjärvi"
    assert device_label("OH3ROU", "Oulu") == "OH3ROU Oulu"
    assert link_kind({"LinkName": "MMDVM Host", "LinkCall": "OH2RUP"}) == ""
    ci = CheckIn(Mon(), api=None)
    ci.start(244, "1", "22.11.2026", "oh2abc")
    t = time.time()
    rows = [("OH1TET", 2440101, "Detroit Bridge", "TetraPack Core 20261011", 2005, "TetraPack"),
            ("OH1POC", 2440102, "Hytera HyTalk/RoIP", "", 2440102, "POC"),
            ("OH1YSF", 2440103, "YSF Client", "", "", "Fusion"),
            ("OH1ODT", 2440104, "Open DMR Terminal", "", 2440104, "Open DMR"),
            ("OH1HOT", 2440105, "MMDVM Host", "OH1HOT", 244010501, "Hotspot"),
            ("DL1TET", 2620001, "Detroit Bridge", "TetraPack Core", 2005, "Ulkomaat")]
    for i, (call, src, name, lc, ctx, _) in enumerate(rows):
        ci.on_event({"SourceCall": call, "SourceID": src, "DestinationID": 244, "SessionID": f"s{i}",
                     "Start": t + i, "Stop": 0, "ContextID": ctx, "LinkName": name, "LinkCall": lc,
                     "Event": "Session-Start"})
    got = {st["call"]: (st["via"], st["kunta"]) for st in ci.s["stations"]}
    for call, *_, want in rows:
        assert got[call][0] == want, (call, got[call])
    assert got["OH1TET"][1] == ""


def test_calls_limit_migration():
    from bmmonitor.app import CALLS_LIMITS, nearest_calls_limit
    assert CALLS_LIMITS == (10, 25, 50, 100)
    assert [nearest_calls_limit(x) for x in (15, 20, 25, None, 999, 150)] == [10, 25, 25, 10, 100, 100]


MA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
 <entry><cap:polygon>59.0,24.0 59.0,26.0 60.0,26.0 60.0,24.0 59.0,24.0</cap:polygon>
  <cap:areaDesc>Harju county</cap:areaDesc><cap:event>Wind Level 1</cap:event><cap:severity>Moderate</cap:severity>
  <cap:onset>2030-01-01T04:00:00+00:00</cap:onset><cap:expires>2030-01-01T22:00:00+00:00</cap:expires>
  <cap:message_type>Alert</cap:message_type><cap:identifier>X1</cap:identifier>
  <title>Orange Wind Warning issued for Estonia - Harju county</title></entry>
 <entry><cap:geocode><valueName>EMMA_ID</valueName><value>DE202</value></cap:geocode>
  <cap:areaDesc>Berlin</cap:areaDesc><cap:event>fog</cap:event><cap:severity>Minor</cap:severity>
  <cap:message_type>Alert</cap:message_type><cap:identifier>X2</cap:identifier>
  <title>Yellow Fog Warning issued for Germany - Berlin</title></entry>
</feed>"""


def test_meteoalarm_and_ovation():
    from bmmonitor.services import (Warnings, ma_areas, normalize_services, ovation_prob, parse_meteoalarm,
                                    DEFAULT_SERVICES)
    items = parse_meteoalarm(MA_XML)
    assert [(w["id"], w["color"]) for w in items] == [("X1", "orange"), ("X2", "yellow")]
    assert ma_areas(items)[0] == {"id": "DE202", "name": "Berlin", "poly": False}

    class Hub:
        cfg = {"warnings": {"areas": ["de202"]}, "location": {"lat": 59.4, "lon": 24.7}}
    w = Warnings(Hub())
    assert w._match_areas(items[0], 59.4, 24.7) == ["Harju county"]       # sijainti alueen sisällä
    assert w._match_areas(items[1], 59.4, 24.7) == ["Berlin"]             # valittu EMMA_ID:llä
    assert w._match_areas(items[1], None, None) == ["Berlin"]
    grid = {"coordinates": [[25, 60, 3], [25, 62, 20], [205, 61, 50]]}
    assert ovation_prob(grid, 60.2, 24.9, 300) == (3, 20)
    assert ovation_prob(grid, 61.2, -155.0, 300)[1] == 50                 # pituuspiiri 0–359
    old = json.loads(json.dumps(DEFAULT_SERVICES))
    out, ch = normalize_services({"warnings": {"enabled": True, "source": "meteoalarm", "country": "germany",
                                               "areas": "Berlin; DE074"}}, old)
    assert out["warnings"]["areas"] == ["Berlin", "DE074"] and "warnings" in ch
    out, _ = normalize_services({"aurora": {"source": "noaa", "min_prob": 250}}, old)
    assert out["aurora"]["source"] == "noaa" and out["aurora"]["min_prob"] == 100


ISS_TLE = """ISS (ZARYA)
1 25544U 98067A   26326.55861648  .00009004  00000+0  16808-3 0  9990
2 25544  51.6321 236.3579 0007404  53.9088 306.2585 15.49552037596306
"""


def test_satellite_passes_match_reference():
    """Ylilennot Helsingissä verrattuna Skyfieldin laskemiin arvoihin (sama TLE): ±3 s ja ±0,5°."""
    from bmmonitor.satellites import ISS, Orbit, find_passes, freq_text, parse_tle, pick_transmitters
    tle = parse_tle(ISS_TLE)
    assert set(tle) == {ISS}
    start = int(tle[ISS]["epoch"])
    ps = find_passes(Orbit(ISS, tle[ISS]), 60.17, 24.94, start, 24, 30, visible=True)
    ref = [(1795354743.7, 1795355001.4, 10.3, 1795355259.9), (1795360459.5, 1795360752.5, 18.4, 1795361046.2)]
    for p, (aos, tca, el, los) in zip(ps, ref):
        assert abs(p["aos"] - aos) < 3 and abs(p["los"] - los) < 3 and abs(p["tca"] - tca) < 15
        assert abs(p["max_el"] - el) <= 1
    tx = pick_transmitters([
        {"norad_cat_id": ISS, "alive": True, "status": "active", "type": "Transceiver", "description": "FM rpt",
         "mode": "FM", "uplink_low": 145990000, "downlink_low": 437800000},
        {"norad_cat_id": ISS, "alive": True, "status": "active", "type": "Transmitter", "description": "TLM",
         "mode": "FSK", "uplink_low": None, "downlink_low": 437800000}], {ISS})
    assert [freq_text(t) for t in tx[ISS]] == ["FM 145,990↑ 437,800↓"]


def test_satellite_subpoint_and_map():
    """Alipiste verrattuna Skyfieldiin (sama TLE) ja kartan tiedot."""
    from bmmonitor.satellites import ISS, Orbit, parse_tle, short_name, sky_path, track, teme_to_geodetic
    tle = parse_tle(ISS_TLE)
    orb = Orbit(ISS, tle[ISS])
    la, lo, h = teme_to_geodetic(orb.pos(1795354864), 1795354864)
    assert abs(la - 45.315) < 0.02 and abs(lo - 22.224) < 0.02 and abs(h - 417.7) < 1
    t = track(orb, 1795354864, 60.17, 24.94)
    assert len(t["track"]) == 2 * 291 and 15 < t["footprint"] < 25 and 7.5 < t["speed"] < 7.8
    path = sky_path(orb, 1795354743.7, 1795355259.9, 60.17, 24.94)
    assert path[0][2] < 1 and max(p[2] for p in path) > 9
    assert short_name("RS-44 & BREEZE-KM R/B") == "RS-44" and short_name("SAUDISAT 1C (SO-50)") == "SO-50"


def test_feedback_scrub():
    from bmmonitor.feedback import locator4, scrub_config, scrub_text
    cfg = {"bm_api_key": "eyJ0eXAiOiJKV1QiLCJhbGciOi.eyJzdWIiOiIxMjM0NTY3ODkw.abcdefgh", "language": "fi",
           "messaging": {"login": "OH2BEA-13", "passcode": "19369", "feeds": [{"url": "https://x/?key=abc"}]},
           "checkin": {"sheet": {"key": "deadbeef" * 8, "url": "https://script.google.com/macros/s/A/exec"}},
           "services": {"location": {"lat": 60.17, "lon": 24.94, "label": "Kotikatu 1, Helsinki"}}}
    out = scrub_config(cfg)
    flat = json.dumps(out)
    for secret in ("eyJ0eXAi", "19369", "deadbeef", "abc", "Kotikatu", "60.17", "24.94"):
        assert secret not in flat, secret
    assert out["services"]["location"]["locator"] == "KP20" == locator4(60.17, 24.94)
    assert out["messaging"]["login"] == "OH2BEA-13"
    line = "2026-11-24 INFO login OH2BEA-13 pass 19369 vers x api_key=SECRET token: eyJabcdefghijkl.eyJabcdefghijkl.abcdefg"
    s = scrub_text(line)
    assert "19369" not in s and "SECRET" not in s and "eyJabc" not in s and "OH2BEA-13" in s


def test_ui_setting_and_desktop(tmp_path):
    from bmmonitor import desktop
    from bmmonitor.app import Monitor
    if sys.platform.startswith("linux"):
        assert desktop.available() is False      # Linuxissa aina selain
    mon = Monitor(tmp_path / "c.json", demo=True)
    assert mon.cfg["ui"] == "window"
    mon.update_config({"ui": "browser"})
    assert mon.cfg["ui"] == "browser"
    with pytest.raises(ValueError):
        mon.update_config({"ui": "kiosk"})
    assert mon.snapshot()["ui"] == {"window": False, "available": desktop.available()}


def test_aprs_positions():
    from bmmonitor.aprsrx import parse_position
    lat, lon = parse_position("APRS", b"!6012.34N/02456.78E-test")
    assert abs(lat - (60 + 12.34 / 60)) < 1e-4 and abs(lon - (24 + 56.78 / 60)) < 1e-4
    lat, lon = parse_position("APRS", b"@092345z4903.50S/07201.75W>")
    assert lat < 0 and lon < 0 and abs(lat + 49.0583) < 1e-3
    lat, lon = parse_position("APRS", b"!/5L!!<*e7>7P[")          # APRS 1.0.1 -spesifikaation esimerkki
    assert abs(lat - 49.5) < 1e-3 and abs(lon + 72.75) < 1e-3
    lat, lon = parse_position("S32U6T", bytes([0x60, 28 + 12, 28 + 7, 28 + 74]) + b"_fOj/")   # Mic-E
    assert abs(lat - (33 + 25.64 / 60)) < 1e-4 and abs(lon + (12 + 7.74 / 60)) < 1e-4
    assert parse_position("APRS", b";OBJEKTI  *092345z6012.34N/02456.78E-") is None
    assert parse_position("APRS", b">status") is None


def test_aprs_kiss_and_via(tmp_path):
    from collections import deque
    from types import SimpleNamespace
    from bmmonitor.aprsrx import (DEFAULT_APRSRX, AprsRx, KissDecoder, encode_ax25, heard_via, is_alias,
                                  kiss_frame, parse_ax25)
    fr = encode_ax25("OH2ABC-9", "APRS", ["OH2RCH-1*", "WIDE2-1"], b"!6012.34N/02456.78E>\xc0\xdb")
    dec = KissDecoder()
    data = kiss_frame(fr) + kiss_frame(encode_ax25("OH2XYZ", "APRS", [], b">hei"))
    got = dec.feed(data[:7]) + dec.feed(data[7:])
    assert len(got) == 2 and got[0] == fr
    p = parse_ax25(got[0])
    assert p["src"] == "OH2ABC-9" and p["path"] == [("OH2RCH-1", True), ("WIDE2-1", False)]
    assert heard_via(p["src"], p["path"]) == ("OH2RCH-1", False)
    assert heard_via("OH2XYZ", []) == ("OH2XYZ", True)
    assert is_alias("WIDE2-1") and is_alias("RELAY") and not is_alias("OH2RCH-1")

    w = SimpleNamespace(seq=0, alerts=deque())
    mon = SimpleNamespace(cfg={"aprsrx": {**DEFAULT_APRSRX, "enabled": True, "silence_min": 10, "min_heard": 2}},
                          watcher=w, touch=lambda: None, hub=SimpleNamespace(announce=lambda *a, **k: None))
    rx = AprsRx(mon, tmp_path / "rx.sqlite")
    t0 = time.time() - 3600
    for i in range(5):
        rx.handle_frame(fr, now=t0 + i * 60)
    rx.handle_frame(encode_ax25("OH2RCH-1", "APRS", [], b"!6011.00N/02445.00E#digi"), now=t0 + 400)
    rx.handle_frame(encode_ax25("OH3QQ", "APRS", ["OH2RCH-1*", "OH3RAA*"], b"!6130.00N/02345.00E>"), now=t0 + 500)
    rx._commit()
    s = rx.summary("24h", (60.2, 24.9))
    assert s["totals"]["packets"] == 7 and s["totals"]["direct"] == 1
    calls = {d["call"]: d for d in s["digis"]}
    assert calls["OH2RCH-1"]["packets"] == 5 and calls["OH2RCH-1"]["lat"] == 60.18333
    assert calls["OH3RAA"]["packets"] == 1
    c = rx.cells("24h", "digi", "OH2RCH-1", 5, (60.2, 24.9))
    assert sum(x[2] for x in c["cells"]) == 5
    # hiljaisuus: yhteys auki, jotain kuultu äskettäin, digiä ei ole kuultu 10 minuuttiin
    rx.status.update(connected=True, since=time.time() - 3600, last=time.time() - 30)
    assert rx.check_silence() == ["OH2RCH-1"]
    assert w.alerts[0]["level"] == "bad" and rx.check_silence() == []
    rx.handle_frame(fr)
    assert "OH2RCH-1" not in rx.silent and w.alerts[0]["level"] == "ok"
    rx.close()


def test_nets_calendar(tmp_path):
    from collections import deque
    from types import SimpleNamespace
    from bmmonitor.app import Matcher
    from bmmonitor.nets import DEFAULT_NETS, NetCalendar, find_tg, normalize_nets
    assert find_tg("NEPA NET - NORTH EAST PA (31424)") == 31424
    assert find_tg("FinDMR-kierros", "Brandmeister DMR · puheryhmä TG 244") == 244
    assert find_tg("Kahvinetti") is None
    ics = """BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTIMEZONE\r\nTZID:Europe/Helsinki\r\nBEGIN:STANDARD\r\nTZOFFSETFROM:+0300\r\nTZOFFSETTO:+0200\r\nDTSTART:19701025T040000\r\nRRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\nEND:STANDARD\r\nBEGIN:DAYLIGHT\r\nTZOFFSETFROM:+0200\r\nTZOFFSETTO:+0300\r\nDTSTART:19700329T030000\r\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\nEND:DAYLIGHT\r\nEND:VTIMEZONE\r\nBEGIN:VEVENT\r\nUID:k@x\r\nDTSTART;TZID=Europe/Helsinki:20260104T190000\r\nDTEND;TZID=Europe/Helsinki:20260104T203000\r\nRRULE:FREQ=WEEKLY;BYDAY=SU\r\nSUMMARY:FinDMR-kierros\r\nLOCATION:TG 244\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""
    cfg = normalize_nets({"sources": [{"name": "t", "url": "https://example.org/x.ics"}],
                          "own": [{"name": "Oma", "tg": "2442", "day": 7, "time": "18.30", "minutes": 30}]}, DEFAULT_NETS)
    assert cfg["own"][0]["time"] == "18:30"
    w = SimpleNamespace(seq=0, alerts=deque())
    mon = SimpleNamespace(cfg={"nets": cfg}, touch=lambda: None, watcher=w, matcher=Matcher(["244"]))
    nc = NetCalendar(mon, tmp_path)
    nc._parse("https://example.org/x.ics", ics)
    import datetime as dt
    from zoneinfo import ZoneInfo
    hel = ZoneInfo("Europe/Helsinki")
    start = dt.datetime(2026, 11, 29, 0, 0, tzinfo=hel).timestamp()        # sunnuntai, talviaika
    occ = nc.occurrences(start, start + 86400)
    k = [n for n in occ if n["tg"] == 244]
    assert len(k) == 1 and dt.datetime.fromtimestamp(k[0]["start"], hel).hour == 19
    summer = dt.datetime(2026, 7, 5, 0, 0, tzinfo=hel).timestamp()          # kesäaika: silti klo 19 Suomen aikaa
    k = [n for n in nc.occurrences(summer, summer + 86400) if n["tg"] == 244]
    assert dt.datetime.fromtimestamp(k[0]["start"], hel).hour == 19
    assert any(n["name"] == "Oma" and n["tg"] == 2442 for n in occ)
    assert nc.check_reminders(now=k[0]["start"] - 600) == ["FinDMR-kierros"]
    assert nc.check_reminders(now=k[0]["start"] - 500) == []                # ei toista kertaa
    with pytest.raises(ValueError):
        normalize_nets({"own": [{"name": "x", "day": 1, "time": "25:00"}]}, DEFAULT_NETS)


def test_aprs_rf_messages(tmp_path, monkeypatch):
    """Viestit radiolla Graywolfin kautta: reitti, kuittaus, kaksoiskappaleet (RF + iGate→IS), oma kaiku, botti."""
    from types import SimpleNamespace
    from bmmonitor.aprsrx import DEFAULT_APRSRX, AprsRx, KissDecoder, encode_ax25, parse_ax25
    from bmmonitor import messaging
    from bmmonitor.messaging import DEFAULT_MESSAGING, MessagingHub, normalize_messaging
    monkeypatch.setattr(messaging, "RF_MIN_GAP", 0)

    class W:
        def __init__(self):
            self.data = b""
        def write(self, b):
            self.data += b
        def close(self):
            pass

    cfg = {**DEFAULT_MESSAGING, "login": "OH2BEA-3", "rf_tx": True, "route": "auto"}
    hub = MessagingHub(cfg, tmp_path, lambda: None)
    hub.command_handler = lambda src, txt: "PONG" if txt.strip().upper() == "PING" else None
    from bmmonitor.aprsrx import normalize_aprsrx
    from bmmonitor.radios import KissTcpLink
    mon = SimpleNamespace(cfg={"aprsrx": normalize_aprsrx({}, {**DEFAULT_APRSRX, "enabled": True})}, touch=lambda: None,
                          hub=hub, watcher=SimpleNamespace(seq=0, alerts=[]))
    rx = AprsRx(mon, tmp_path / "rx.sqlite")
    hub.rf = rx
    assert [r["id"] for r in mon.cfg["aprsrx"]["radios"]] == ["graywolf"]     # vanha host/port → ensimmäinen radio
    link = KissTcpLink(mon.cfg["aprsrx"]["radios"][0], rx._on_frame, lambda: None)
    link.writer, link.status["connected"] = W(), True
    rx.links[link.id] = link

    # automaattireitti: radiolla äskettäin kuultu → RF, muut → APRS-IS; lähetys pois → aina IS
    rx.handle_frame(encode_ax25("OH2ABC-9", "APRS", ["OH2RCH-1*"], b"!6012.34N/02456.78E>"))
    assert hub.pick_route("OH2ABC-9") == "rf" and hub.pick_route("OH9ZZZ-7") == "rf"   # APRS-IS poikki → radio
    hub.aprs.connected = True
    assert hub.pick_route("OH2ABC-9") == "rf" and hub.pick_route("OH9ZZZ-7") == "is"
    assert hub.pick_route("OH9ZZZ-7", "rf") == "rf"
    cfg["rf_tx"] = False
    assert hub.pick_route("OH2ABC-9", "rf") == "is"
    cfg["rf_tx"] = True

    async def drain():
        task = asyncio.create_task(hub._rf_sender())
        for _ in range(50):
            await asyncio.sleep(0.01)
            if hub.rf_queue.empty():
                break
        await asyncio.sleep(0.02)
        task.cancel()

    async def scenario():
        # radiolta viesti botille kuittauspyynnöllä → kuittaus ja vastaus radioon
        rx.handle_frame(encode_ax25("OH2ABC-9", "APZXXX", ["OH2RCH-1*"], b":OH2BEA-3 :PING{12"))
        assert hub.aprs_msgs[0]["route"] == "rf" and hub.aprs_msgs[0]["text"] == "PONG"
        # sama viesti iGaten kautta APRS-IS:stä: ei toista kuittausta eikä toista vastausta
        hub.handle_message("OH2ABC-9", "OH2BEA-3", "PING{12", "is")
        assert hub.aprs.queue.empty()
        incoming = [m for m in hub.aprs_msgs if m["dir"] == "in"]
        assert len(incoming) == 1 and incoming[0]["route"] == "rf+is"
        await drain()
        frames = [parse_ax25(f) for f in KissDecoder().feed(link.writer.data)]
        assert [f["info"] for f in frames] == [b":OH2ABC-9 :ack12", b":OH2ABC-9 :PONG{" + hub.aprs_msgs[0]["msgid"].encode()]
        assert frames[0]["src"] == "OH2BEA-3" and frames[0]["dest"] == "APZBMM"
        assert [c for c, _ in frames[0]["path"]] == ["WIDE1-1", "WIDE2-1"]
        out = hub.aprs_msgs[0]
        assert out["status"] == "lähetetty" and out["route"] == "rf"
        # digin toistama oma viesti ja oma kuittaus palaavat: ohitetaan
        n = len(hub.aprs_msgs)
        rx.handle_frame(encode_ax25("OH2BEA-3", "APZBMM", ["OH2RCH-1*", "WIDE2*"], f":OH2ABC-9 :PONG{{{out['msgid']}".encode()))
        rx.handle_frame(encode_ax25("OH2BEA-3", "APZBMM", ["OH2RCH-1*"], b":OH2ABC-9 :ack12"))
        assert len(hub.aprs_msgs) == n
        # vastaanottajan kuittaus radiolta
        rx.handle_frame(encode_ax25("OH2ABC-9", "APZXXX", ["OH2RCH-1*"], f":OH2BEA-3 :ack{out['msgid']}".encode()))
        assert out["status"] == "kuitattu" and out["ack_route"] == "rf"
        # radio + APRS-IS: sama viestinumero molempiin, tila ja kuittaus yhteiset
        link.writer.data = b""
        e2 = hub.aprs.send("OH9ZZZ-7", "molemmat", route="both")
        assert e2["route"] == "both"
        kind, item = hub.aprs.queue.get_nowait()
        assert kind == "msg" and item is e2
        await drain()
        f = parse_ax25(KissDecoder().feed(link.writer.data)[0])
        assert f["info"] == f":OH9ZZZ-7 :molemmat{{{e2['msgid']}".encode()
        hub.aprs.connected = False
        assert hub.pick_route("OH9ZZZ-7", "both") == "rf"
        hub.aprs.connected = True
        # ilman radioyhteyttä radioviesti ei lähde
        link.status["connected"] = False
        e = hub.aprs.send("OH2ABC-9", "testi", route="rf")
        hub.rf_sent.clear()
        await drain()
        assert e["status"] == "virhe: ei radioyhteyttä"

    asyncio.run(scenario())
    # asetukset
    n = normalize_messaging({"rf_tx": True, "route": "rf", "rf_path": "wide1-1, wide2-2", "rf_heard_min": 2}, cfg)
    assert n["rf_path"] == "WIDE1-1,WIDE2-2" and n["rf_heard_min"] == 5 and n["route"] == "rf"
    for bad in ({"route": "x"}, {"rf_path": "WIDE1-16"}, {"rf_path": "TOOLONGCALL"}):
        with pytest.raises(ValueError):
            normalize_messaging(bad, cfg)
    with pytest.raises(ValueError):
        encode_ax25("OH2ABC-X", "APZBMM", [], b"x")
    rx.close()


def test_aprs_stations_layer(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from bmmonitor.aprsrx import DEFAULT_APRSRX, AprsRx, encode_ax25, parse_symbol, symbol_kind
    # vanha tietokanta ilman symbolisaraketta päivittyy
    db = sqlite3.connect(str(tmp_path / "rx.sqlite"))
    db.execute("CREATE TABLE packets (ts REAL, src TEXT, via TEXT, direct INTEGER, path TEXT, lat REAL, lon REAL)")
    db.execute("INSERT INTO packets VALUES (?, 'OH2OLD', 'OH2OLD', 1, '', 60.1, 24.1)", (time.time() - 60,))
    db.commit(); db.close()
    mon = SimpleNamespace(cfg={"aprsrx": {**DEFAULT_APRSRX}}, touch=lambda: None, hub=None,
                          watcher=SimpleNamespace(seq=0, alerts=[]))
    rx = AprsRx(mon, tmp_path / "rx.sqlite")
    rx.handle_frame(encode_ax25("OH2CAR-9", "APRS", ["OH2RCH-1*"], b"!6012.34N/02456.78E>auto"))
    rx.handle_frame(encode_ax25("OH2CAR-9", "APRS", [], b">status ilman sijaintia"))
    rx.handle_frame(encode_ax25("OH2IG", "APRS", [], b"!6015.00NI02450.00E&igate"))
    rx._commit()
    st = {s["call"]: s for s in rx.stations("24h", "all", None, (60.2, 24.9))["stations"]}
    assert set(st) == {"OH2CAR-9", "OH2IG", "OH2OLD"}
    car = st["OH2CAR-9"]
    assert car["kind"] == "mobile" and car["packets"] == 2 and car["via"] == "OH2RCH-1" and not car["via_direct"]
    assert st["OH2IG"]["kind"] == "igate" and st["OH2IG"]["via_direct"] and st["OH2OLD"]["kind"] == "fixed"
    assert [s["call"] for s in rx.stations("24h", "all", "OH2RCH-1", None)["stations"]] == ["OH2CAR-9"]
    assert {s["call"] for s in rx.stations("24h", "direct", None, None)["stations"]} == {"OH2IG", "OH2OLD"}
    assert parse_symbol("APRS", b"=6012.34N\\02456.78E_wx") == "\\_" and symbol_kind("\\_") == "wx"
    rx.close()


def test_qso_detector_and_log(tmp_path):
    from types import SimpleNamespace
    from bmmonitor.qso import DEFAULT_LOG, QsoDetector, QsoLog, adif_file, band_for, parse_freq, qso_adif
    det = QsoDetector(lambda c: c == "OH2BEA")
    t0 = 1_700_000_000

    def ev(call, start, dur=20, tg=244, private_to=None, ctx=None):
        p = {"SourceCall": call, "SourceID": hash(call) % 10**7, "DestinationID": tg, "Start": start, "Stop": start + dur,
             "SourceName": call.title(), "LinkCall": "OH2RAC", "ContextID": ctx, "CallTypes": ["Voice", "Group"]}
        if private_to:
            p.update(CallTypes=["Voice", "Private"], DestinationCall=private_to, DestinationID=1234)
        det.on_event(p)

    ev("OH2BEA", t0)                       # oma
    ev("OH2ABC", t0 + 30)                  # toinen
    assert det.snapshot() == []            # vasta yksi vaihto
    ev("OH2BEA", t0 + 60)                  # oma → kaksi vaihtoa = ehdokas
    c = det.snapshot()
    assert len(c) == 1 and c[0]["call"] == "OH2ABC" and c[0]["turns"] == 3 and c[0]["tg"] == 244
    ev("OH2BEA", t0 + 60)                  # sama lähetys uudelleen: ei vaikutusta
    assert det.snapshot()[0]["turns"] == 3
    ev("OH2XYZ", t0 + 90)                  # sivullinen puhuu kerran: ei ehdokasta
    assert {x["call"] for x in det.snapshot()} == {"OH2ABC"}
    ev("OH9AAA", t0 + 1000, tg=91)         # eri puheryhmä, ei omaa vuoroa
    ev("OH2BEA", t0 + 2000, tg=91)         # liian pitkä väli
    ev("OH9AAA", t0 + 2030, tg=91)
    assert {x["call"] for x in det.snapshot()} == {"OH2ABC"}
    ev("OH2BEA", t0 + 2060, tg=91)
    assert {x["call"] for x in det.snapshot()} == {"OH2ABC", "OH9AAA"}
    # check-in-netti: nettijohtaja kutsuu, oma kuittautuminen, nettijohtaja kuittaa → ei yhteyttä
    ev("OH6NET", t0 + 2500, tg=24400)
    ev("OH2BEA", t0 + 2530, tg=24400)
    ev("OH6NET", t0 + 2560, tg=24400)
    ev("OH6NET", t0 + 2600, tg=24400)              # nettijohtaja jatkaa (sama puoli)
    assert "OH6NET" not in {x["call"] for x in det.snapshot()}
    ev("OH2BEA", t0 + 2630, tg=24400)              # toinen–oma–toinen–oma → ehdokas
    assert "OH6NET" in {x["call"] for x in det.snapshot()}
    ev("OH2PRV", t0 + 3000, private_to="OH2BEA")     # yksityispuhelu omaan kutsuun = heti ehdokas
    prv = [x for x in det.snapshot() if x["call"] == "OH2PRV"][0]
    assert prv["private"] and prv["tg"] is None
    det.close(prv["id"])
    ev("OH2BEA", t0 + 3030, private_to="OH2PRV")     # suljettu ehdokas ei palaa
    assert "OH2PRV" not in {x["call"] for x in det.snapshot()}

    assert band_for(parse_freq("434.5000")) == "70cm" and band_for(parse_freq(145600000)) == "2m"
    q = {"call": "OH2ABC", "start": t0, "end": t0 + 80, "band": "70cm", "freq": 434.5, "rst_sent": "59", "rst_rcvd": "57",
         "name": "Matti", "comment": "BM TG 244", "my_call": "OH2BEA", "grid": "KP20lf"}
    a = qso_adif(q)
    assert "<CALL:6>OH2ABC" in a and "<MODE:12>DIGITALVOICE<SUBMODE:3>DMR" in a and "<FREQ:5>434.5" in a
    assert "<QSO_DATE:8>20231114" in a and "<TIME_ON:6>221320" in a and "<GRIDSQUARE:6>KP20LF" in a and a.endswith("<eor>")
    assert "<eoh>" in adif_file([q])

    mon = SimpleNamespace(cfg={"log": dict(DEFAULT_LOG), "devices": {"my_call": "OH2BEA"}, "messaging": {}},
                          dirty=False, save_config=lambda: None,
                          devmon=SimpleNamespace(state={555: {"rx": "433.6250", "tx": "433.6250"}}))
    lg = QsoLog(mon, tmp_path / "log.sqlite")
    lg.qrz_key = ""
    for e in ({"SourceCall": "OH2BEA", "Start": t0, "Stop": t0 + 10, "ContextID": 555},
              {"SourceCall": "OH2ABC", "Start": t0 + 20, "Stop": t0 + 30},
              {"SourceCall": "OH2BEA", "Start": t0 + 40, "Stop": t0 + 50, "ContextID": 555}):
        lg.on_event({"SourceID": hash(e["SourceCall"]), "DestinationID": 244, "CallTypes": ["Voice", "Group"], **e})
    cid = lg.snapshot()["candidates"][0]["id"]
    d = lg.candidate_defaults(cid)
    assert d["call"] == "OH2ABC" and d["band"] == "70cm" and d["freq"] == 433.625 and "TG 244" in d["comment"]
    qid = lg.add({**d, "rst_rcvd": "55"})
    assert lg.snapshot()["candidates"] == [] and lg.count() == 1
    row = lg.get(qid)
    assert row["my_call"] == "OH2BEA" and row["rst_rcvd"] == "55" and row["qrz_status"] is None
    lg.update(qid, {"name": "Matti"})
    assert lg.get(qid)["name"] == "Matti"
    with pytest.raises(ValueError):
        lg.add({"call": "huono kutsu", "start": t0})
    mon.cfg["log"]["qrz"] = True
    qid2 = lg.add({"call": "OH3XYZ", "start": t0 + 500})
    assert lg.get(qid2)["qrz_status"] == "queued" and lg.snapshot()["qrz"]["pending"] == 1
    # tila: oletus DMR, valinta pikanimellä, ADIF-muunnos, suodatus
    assert lg.get(qid)["mode"] == "DIGITALVOICE" and lg.get(qid)["mode_label"] == "DMR"
    q3 = lg.add({"call": "OH1CW", "start": t0 + 900, "mode_label": "CW", "band": "20m"})
    q4 = lg.add({"call": "OH1PSK", "start": t0 + 950, "mode_label": "psk31"})
    q5 = lg.add({"call": "OH1DS", "start": t0 + 990, "mode_label": "DSTAR"})
    assert (lg.get(q3)["mode"], lg.get(q3)["submode"]) == ("CW", "")
    assert (lg.get(q4)["mode"], lg.get(q4)["submode"]) == ("PSK", "PSK31")
    assert lg.get(q5)["submode"] == "DSTAR" and "<SUBMODE:5>DSTAR" in qso_adif(lg.get(q5))
    assert [r["call"] for r in lg.list(0, "", "CW")] == ["OH1CW"]
    assert {r["call"] for r in lg.list(0, "", "DMR")} == {"OH2ABC", "OH3XYZ"}
    lg.update(q3, {"name": "Cw"})
    assert lg.get(q3)["mode"] == "CW"
    with pytest.raises(ValueError):
        lg.add({"call": "OH1XX", "start": t0, "mode_label": "WHATEVER"})
    from bmmonitor.qso import default_rst
    assert default_rst("SSB") == "59" and default_rst("CW") == "599" and default_rst("DMR") == "59"
    lg.delete(qid)
    assert lg.count() == 4
    lg.close()


def test_qrz_send(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from bmmonitor.qso import DEFAULT_LOG, QsoLog
    mon = SimpleNamespace(cfg={"log": {**DEFAULT_LOG, "qrz": True}, "devices": {"my_call": "OH2BEA"}, "messaging": {}},
                          dirty=False, save_config=lambda: None, devmon=SimpleNamespace(state={}))
    lg = QsoLog(mon, tmp_path / "log.sqlite")
    lg.qrz_key = "TEST-KEY"
    sent = []
    replies = iter([{"RESULT": "OK", "LOGID": "777", "COUNT": "1"},
                    {"RESULT": "REPLACE", "LOGID": "777"},
                    {"RESULT": "FAIL", "REASON": "Unable to add QSO to database: duplicate"},
                    {"RESULT": "AUTH", "REASON": "invalid api key"}])

    async def fake(data):
        sent.append(data)
        return next(replies)
    monkeypatch.setattr(lg, "_qrz", fake)

    async def go():
        a = lg.add({"call": "OH2ABC", "start": 1_700_000_000})
        await lg._qrz_send(lg.get(a))
        assert lg.get(a)["qrz_status"] == "ok" and lg.get(a)["qrz_logid"] == "777" and "OPTION" not in sent[0]
        lg.update(a, {"name": "Matti"})
        await lg._qrz_send(lg.get(a))
        assert sent[1]["OPTION"] == "REPLACE" and lg.get(a)["qrz_status"] == "ok"
        b = lg.add({"call": "OH2DEF", "start": 1_700_000_100})
        await lg._qrz_send(lg.get(b))
        assert lg.get(b)["qrz_status"] == "ok"
        c = lg.add({"call": "OH2GHI", "start": 1_700_000_200})
        await lg._qrz_send(lg.get(c))
        assert lg.get(c)["qrz_status"] == "auth" and lg.qrz_state["ok"] is False
    asyncio.run(go())
    lg.close()


def test_bundled_sheet_script_matches_source():
    """Ohjelman mukana kopioitava Apps Script -skripti on sama kuin sheets/-kansiossa."""
    root = Path(__file__).resolve().parent.parent
    for name in ("Code.gs", "appsscript.json"):
        assert (root / "bmmonitor" / "static" / "sheets" / name).read_bytes() == (root / "sheets" / name).read_bytes(), name


def test_i18n_keys_for_new_ui():
    js = (Path(__file__).resolve().parent.parent / "bmmonitor" / "static" / "i18n.js").read_text(encoding="utf-8")
    for k in ("Kirjaa yhteys (QSO) lokiin", "Tila (mode)", "Kaikki tilat", "Google-taulukon käyttöönotto"):
        assert f'"{k}"' in js, k


def test_radio_help_commands():
    from bmmonitor import features
    src = Path(features.__file__).read_text(encoding="utf-8")
    m = re.search(r'if t in \(([^)]*)\):\s+# ei APUA', src)
    words = set(re.findall(r'"([A-Z]*)"', m.group(1)))
    assert {"OHJE", "CMD"} <= words and not {"APUA", "HELP"} & words


def test_hidden_tabs_config():
    from bmmonitor import app
    assert "asetukset" not in app.HIDEABLE_TABS
    html = (Path(app.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    tabs = set(re.findall(r'<button data-tab="([a-z]+)"', html))
    assert tabs - {"asetukset"} == set(app.HIDEABLE_TABS)


def test_html_texts_have_english_translation():
    """Jokaisella suomenkielisellä index.html:n tekstisolmulla ja ohjetekstillä on englanninkielinen käännös."""
    from html.parser import HTMLParser
    root = Path(__file__).resolve().parent.parent / "bmmonitor" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "i18n.js").read_text(encoding="utf-8")
    keys = {json.loads('"' + k + '"') for k in re.findall(r'"((?:[^"\\]|\\.)*)"\s*:', js)}
    void = {"input", "br", "img", "meta", "link", "hr", "source"}

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.out = [], []

        def handle_starttag(self, t, a):
            a = dict(a)
            skip = (t in ("code", "script", "style") or "data-noi18n" in a or (a.get("lang") == "fi" and t != "html")
                    or bool(self.stack and self.stack[-1][1]))
            if not skip:
                self.out += [a[k] for k in ("placeholder", "title", "aria-label") if a.get(k)]
            if t not in void:
                self.stack.append((t, skip))

        def handle_endtag(self, t):
            while self.stack and self.stack.pop()[0] != t:
                pass

        def handle_data(self, d):
            d = re.sub(r"\s+", " ", d).strip()
            if d and not (self.stack and self.stack[-1][1]):
                self.out.append(d)

    p = P()
    p.feed(html)
    fi = re.compile(r"[äöÄÖ]|\b(ja|tai|ei|kun|jos|on|osio|ohje|Tallenna|Poista|Lisää|Avaa|Kutsu|Nimi|Aika)\b")
    allowed = {"KUNTA", "Lisätietoja"}   # taulukon sarakenimet
    missing = sorted({t for t in p.out if fi.search(t) and t not in keys and t not in allowed})
    assert not missing, missing[:10]


def test_radio_config_and_protocols():
    from bmmonitor.radios import (AgwDecoder, KissDecoder, agw_frame, default_radios, kiss_frame, normalize_radios,
                                  SimLink, new_radio)
    from bmmonitor.aprsrx import encode_ax25, parse_ax25, parse_position
    # vanha yksi yhteys → radiolista
    old = default_radios({"host": "10.0.0.5", "port": 8101})
    assert old[0]["type"] == "kiss_tcp" and old[0]["host"] == "10.0.0.5" and old[0]["port"] == 8101
    rs = normalize_radios([{"id": "graywolf", "type": "kiss_tcp", "host": "127.0.0.1", "port": 8001, "name": "GW"},
                           {"type": "agwpe", "host": "localhost"},
                           {"type": "kiss_serial", "serial": "COM5", "baud": 9600, "tx": False},
                           {"type": "sim"}], old)
    assert [r["type"] for r in rs] == ["kiss_tcp", "agwpe", "kiss_serial", "sim"]
    assert rs[1]["port"] == 8000 and rs[2]["tx"] is False and rs[3]["tx"] is False and len({r["id"] for r in rs}) == 4
    for bad in ([{"type": "soundcard"}], [{"type": "kiss_tcp", "host": "a b"}], [{"type": "kiss_serial", "baud": 123}],
                [{"type": "kiss_tcp", "port": 70000}], [{"type": "sim"}] * 9):
        with pytest.raises(ValueError):
            normalize_radios(bad)
    # KISS ja AGWPE: kehys säilyy, myös pätkittynä ja erikoistavuin
    fr = encode_ax25("OH2ABC-9", "APRS", ["WIDE1-1"], b"!6012.34N/02456.78E>\xc0\xdb")
    data = kiss_frame(fr) + kiss_frame(fr)
    dec = KissDecoder()
    assert [x for i in range(0, len(data), 5) for x in dec.feed(data[i:i + 5])] == [fr, fr]
    agw = agw_frame(b"R") + agw_frame(b"K", b"\x00" + fr) + agw_frame(b"K", b"\x00" + fr)
    dec = AgwDecoder()
    assert [x for i in range(0, len(agw), 7) for x in dec.feed(agw[i:i + 7])] == [fr, fr]
    # simuloitu radio tuottaa jäsentyviä sijaintipaketteja kodin ympärille
    sim = SimLink(new_radio("sim"), lambda f, ln: None, lambda: None, home=lambda: (61.0, 25.0))
    pos = [parse_position(p["dest"], p["info"]) for p in (parse_ax25(sim.frame(n)) for n in range(20))]
    assert sum(1 for x in pos if x) >= 14         # loput: objekti ja iGaten välittämä paketti
    assert all(abs(lat - 61.0) < 1 and abs(lon - 25.0) < 2 for lat, lon in filter(None, pos))


def test_multiple_radios_tcp(tmp_path):
    """Kaksi radiota (KISS TCP ja AGWPE) yhtä aikaa: vastaanotto, kaksoiskappaleiden poisto ja lähetys."""
    from types import SimpleNamespace
    from bmmonitor.aprsrx import DEFAULT_APRSRX, AprsRx, encode_ax25, normalize_aprsrx
    from bmmonitor.radios import AgwDecoder, KissDecoder, agw_frame, kiss_frame

    async def scenario():
        got = {"kiss": b"", "agw": b""}
        writers = {}

        async def kiss_srv(r, w):
            writers["kiss"] = w
            while data := await r.read(4096):
                got["kiss"] += data

        async def agw_srv(r, w):
            writers["agw"] = w
            while data := await r.read(4096):
                got["agw"] += data

        s1 = await asyncio.start_server(kiss_srv, "127.0.0.1", 0)
        s2 = await asyncio.start_server(agw_srv, "127.0.0.1", 0)
        p1, p2 = s1.sockets[0].getsockname()[1], s2.sockets[0].getsockname()[1]
        cfg = normalize_aprsrx({"enabled": True, "radios": [
            {"id": "a", "type": "kiss_tcp", "host": "127.0.0.1", "port": p1, "tx": False},
            {"id": "b", "type": "agwpe", "host": "127.0.0.1", "port": p2, "tx": True}]}, DEFAULT_APRSRX)
        mon = SimpleNamespace(cfg={"aprsrx": cfg, "services": {"location": {}}}, touch=lambda: None, hub=None,
                              watcher=SimpleNamespace(seq=0, alerts=[]))
        rx = AprsRx(mon, tmp_path / "rx.sqlite")
        task = asyncio.create_task(rx.run())
        for _ in range(100):
            await asyncio.sleep(0.02)
            if len(writers) == 2 and all(ln.status["connected"] for ln in rx.links.values()):
                break
        assert rx.status["connected"] and rx.can_transmit()
        assert got["agw"][:36] == agw_frame(b"k")              # AGWPE: raakakehykset pyydetty
        fr = encode_ax25("OH2ABC-9", "APRS", ["OH2RCH-1*"], b"!6012.34N/02456.78E>")
        writers["kiss"].write(kiss_frame(fr))
        writers["agw"].write(agw_frame(b"K", b"\x00" + fr))   # sama paketti toiselta radiolta
        await asyncio.sleep(0.2)
        assert rx.status["count"] == 1
        snap = rx.snapshot()
        assert [(r["id"], r["connected"], r["count"]) for r in snap["radios"]] == [("a", True, 1), ("b", True, 1)]
        # lähetys menee radiolle, jolle lähetys on sallittu (b = AGWPE)
        rx.transmit("OH2BEA-3", "APZBMM", ["WIDE1-1"], b":OH2ABC-9 :hei{1")
        await asyncio.sleep(0.2)
        from bmmonitor.aprsrx import parse_ax25
        sent = AgwDecoder().feed(got["agw"][36:])
        assert len(sent) == 1 and parse_ax25(sent[0])["info"] == b":OH2ABC-9 :hei{1"
        assert KissDecoder().feed(got["kiss"]) == []
        # radio pois käytöstä → yhteys suljetaan
        cfg["radios"][1]["enabled"] = False
        rx.reconfigure()
        await asyncio.sleep(0.2)
        assert set(rx.links) == {"a"} and not rx.can_transmit()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        s1.close()
        s2.close()

    asyncio.run(scenario())


def test_aprs_weather_objects_gate_is(tmp_path):
    from types import SimpleNamespace
    from bmmonitor.aprsrx import (DEFAULT_APRSRX, AprsRx, encode_ax25, normalize_aprsrx, parse_nmea, parse_object,
                                  parse_weather, station_weather)
    w = parse_weather("220/004g005t-05r001p002P003h00b10115")
    assert w == {"wind_dir": 220, "wind": 1.8, "gust": 2.2, "temp": -20.6, "rain_1h": 0.3, "rain_24h": 0.5,
                 "rain_mid": 0.8, "humidity": 100, "pressure": 1011.5}
    assert parse_weather("...") is None and parse_weather("c...s...t...") is None
    assert station_weather("APRS", b"_11251200c220s004g005t041h85b10115")["temp"] == 5.0
    assert parse_object(b";REP-HKI  _111111z6012.34NR02456.78E&")["alive"] is False
    assert parse_object(b")AID#2!4903.50N/07201.75WA")["kind"] == "item"
    assert parse_nmea(b"$GPRMC,123519,V,6012.340,N,02456.780,E,0,0,230394,,*00") is None    # ei paikannusta

    cfg = normalize_aprsrx({"enabled": True, "is_km": 50}, DEFAULT_APRSRX)
    mon = SimpleNamespace(cfg={"aprsrx": cfg, "services": {"location": {"lat": 60.2, "lon": 24.9}}},
                          touch=lambda: None, hub=None, watcher=SimpleNamespace(seq=0, alerts=[]))
    rx = AprsRx(mon, tmp_path / "rx.sqlite")
    assert rx.is_filter() == "r/60.200/24.900/50"
    # sääasema radiolla (sijainti + sää), objekti, poistettu objekti, iGaten välittämä asema
    rx.handle_frame(encode_ax25("OH2WX", "APRS", ["OH2RCH-1*"], b"!6012.34N/02456.78E_220/004g005t041h85b10115"))
    rx.handle_frame(encode_ax25("OH2ABC", "APRS", [], b";REP-HKI  *111111z6012.34NR02456.78E&145.725 T103"))
    rx.handle_frame(encode_ax25("OH2ABC", "APRS", [], b";OLD      _111111z6012.34NR02456.78E&"))
    rx.handle_frame(encode_ax25("OH2IGT-10", "APRS", ["WIDE1-1"], b"}OH9XX-9>APRS,TCPIP,OH2IGT-10*:!6112.34N/02556.78E>moi"))
    rx.handle_is_line("OH7ZZ>APRS,TCPIP*,qAC,T2FINLAND:!6212.34N/02656.78E-is-asema")
    rx.handle_is_line("# comment")
    rx._commit()
    wx = rx.extra("wx", "24h", (60.2, 24.9))["items"]
    assert len(wx) == 1 and wx[0]["name"] == "OH2WX" and wx[0]["data"]["temp"] == 5.0 and not wx[0]["direct"]
    objs = rx.extra("obj", "24h", None)["items"]
    assert [o["name"] for o in objs] == ["REP-HKI"] and objs[0]["data"]["comment"].startswith("145.725")
    gate = rx.extra("gate", "24h", None)["items"]
    assert [(g["name"], g["reporter"]) for g in gate] == [("OH9XX-9", "OH2IGT-10")] and abs(gate[0]["lat"] - 61.2057) < 0.01
    isl = rx.extra("is", "24h", None)["items"]
    assert [(i["name"], i["reporter"]) for i in isl] == [("OH7ZZ", "T2FINLAND")]
    # iGaten välittämä asema ei näy radiolla kuultuna asemana eikä RF-tilastoissa
    sta = {s["call"] for s in rx.stations("24h", "all", None, None)["stations"]}
    assert "OH9XX-9" not in sta and "OH7ZZ" not in sta and "OH2WX" in sta


def test_serial_tnc_kiss_init(monkeypatch):
    """KPC-3+ / Kenwood: TNC KISS-tilaan avattaessa ja pois suljettaessa (valesarjaportilla)."""
    import sys
    import types
    from bmmonitor import radios
    written, got = [], []

    class FakeSer:
        def __init__(self, *a, **k):
            self.n = 0

        def write(self, b):
            written.append(b)

        def read(self, n):
            self.n += 1
            if self.n == 2:
                return radios.kiss_frame(b"X" * 20)
            if self.n > 3:
                raise OSError("irti")
            return b""

        def reset_input_buffer(self):
            pass

        def close(self):
            written.append("closed")

    fake = types.ModuleType("serial")
    fake.Serial = FakeSer
    monkeypatch.setitem(sys.modules, "serial", fake)
    monkeypatch.setattr(radios, "INIT_DELAY", 0)
    for kind, first in (("kpc3", b"INTFACE KISS\r"), ("kenwood", b"TC 1\r")):
        written.clear()
        got.clear()
        ln = radios.KissSerialLink(radios.new_radio(kind, serial="COM1"), lambda f, l: got.append(f), lambda: None)
        with pytest.raises(OSError):
            asyncio.run(ln.session())
        assert first in written and written[-2:] == [radios.KISS_EXIT, "closed"] and got == [b"X" * 20]


def test_aprs_unknown_command_counts_as_message(tmp_path):
    """Tavallinen viesti omalle tunnukselle (esim. oman kutsun toiselta SSID:ltä) ei ole botin komento,
    vaikka botti vastaa "Tuntematon komento"; tunnettu komento merkitään (cmd), jottei se näy uutena viestinä."""
    from types import SimpleNamespace
    from bmmonitor.features import Commands
    from bmmonitor.messaging import DEFAULT_MESSAGING, MessagingHub
    cfg = {**DEFAULT_MESSAGING, "login": "OH2BEA-10", "commands_enabled": True}
    hub = MessagingHub(cfg, tmp_path, lambda: None)
    mon = SimpleNamespace(cfg={"messaging": cfg}, hub=hub)
    hub.command_handler = Commands(mon).handle
    hub.handle_message("OH2BEA-7", "OH2BEA-10", "Testiviesti radiosta{1", "is")
    first = [m for m in hub.aprs_msgs if m["dir"] == "in"][0]
    assert first["text"] == "Testiviesti radiosta" and not first.get("cmd")
    hub.handle_message("OH2BEA-7", "OH2BEA-10", "OHJE{2", "is")
    last = [m for m in hub.aprs_msgs if m["dir"] == "in"][0]
    assert last["text"] == "OHJE" and last.get("cmd") is True


def test_german_translation_complete():
    """Saksa: jokaisella englanninkielisellä tekstillä (i18n.js ja i18n.py) on saksankielinen käännös
    samoilla paikkamerkeillä; tr() ja num() toimivat saksaksi; radioon menevät ä/ö/ü/ß ASCII-muodossa."""
    from bmmonitor import i18n
    from bmmonitor.i18n_de import DE
    from bmmonitor.messaging import clean_text
    root = Path(__file__).resolve().parent.parent / "bmmonitor" / "static"
    ph = re.compile(r"\{(\w*)\}")
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)":\s*\n?\s*"((?:[^"\\]|\\.)*)"', (root / "i18n.js").read_text("utf-8"))
    en_ui = {json.loads('"' + en + '"') for en in dict(pairs).values()}   # sama avain myöhemmin = voimassa oleva
    de_pairs = re.findall(r'"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)"', (root / "i18n.de.js").read_text("utf-8"))
    de_ui = {json.loads('"' + k + '"'): json.loads('"' + v + '"') for k, v in de_pairs}
    missing = sorted(en_ui - set(de_ui))
    assert not missing, missing[:10]
    for en, de in de_ui.items():
        assert sorted(ph.findall(en)) == sorted(ph.findall(de)), en
    missing = sorted(set(i18n.EN.values()) - set(DE))
    assert not missing, missing[:10]
    for en, de in DE.items():
        assert sorted(ph.findall(en)) == sorted(ph.findall(de)), en
    try:
        i18n.set_lang("de")
        assert i18n.tr("Anna puheryhmä") == "Gib eine Sprechgruppe ein"
        assert i18n.tr("Virheellinen kutsu tai DMR-ID: {x}", x="X") == "Ungültiges Rufzeichen oder ungültige DMR-ID: X"
        assert i18n.tr("Tuntematon komento. Laheta OHJE") == "Unbekannter Befehl. Sende CMD"
        assert i18n.tr("Tätä tekstiä ei ole sanakirjassa") == "Tätä tekstiä ei ole sanakirjassa"
        assert i18n.num(1.25, 2) == "1,25" and i18n.bearing(1) == "NO"
        assert clean_text("Größe über Öl", True) == "Groesse ueber Oel"
        i18n.set_lang("fi")
        assert clean_text("Pöytä", True) == "Poyta"
    finally:
        i18n.set_lang("fi")


def test_sheet_guide_languages():
    """Check-in-taulukon ohjeikkunassa on oma lohko jokaiselle kielelle (fi, en, de)."""
    html = (Path(__file__).resolve().parent.parent / "bmmonitor" / "static" / "index.html").read_text(encoding="utf-8")
    guide = html[html.index('id="shBg"'):html.index('id="modalBg"')]
    for lang in ("fi", "en", "de", "es", "fr"):
        assert guide.count(f'<div lang="{lang}" data-noi18n>') == 1, lang
        assert f'.guide.{lang} [lang="{lang}"]' in html


def test_no_duplicate_translation_keys():
    """Käännöstiedostoissa ei ole samaa avainta kahdesti (myöhempi kumoaisi aiemman huomaamatta,
    kuten 'Päivitä nyt' → 'Refresh now' kumosi 'Update now')."""
    import ast
    import collections
    root = Path(__file__).resolve().parent.parent / "bmmonitor"
    pat = re.compile(r'"((?:[^"\\]|\\.)*)":\s*\n?\s*"(?:[^"\\]|\\.)*"')
    for f in sorted((root / "static").glob("i18n*.js")):
        keys = pat.findall(f.read_text("utf-8"))
        dup = [k for k, n in collections.Counter(keys).items() if n > 1]
        assert not dup, (f.name, dup)
    for f in sorted(root.glob("i18n*.py")):
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            if isinstance(node, ast.Dict):
                ks = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                dup = [k for k, n in collections.Counter(ks).items() if n > 1]
                assert not dup, (f.name, dup)


def test_log_prop_mode_and_services(tmp_path, monkeypatch):
    """PROP_MODE (BrandMeister = INTERNET), internet-yhteyksien ohitus palvelukohtaisesti, eQSL ja HamQTH."""
    import sqlite3
    from types import SimpleNamespace
    from bmmonitor.qso import DEFAULT_LOG, QsoLog, adif_file, normalize_log, qso_adif
    # vanha tietokanta ilman uusia sarakkeita päivittyy
    db = tmp_path / "log.sqlite"
    c = sqlite3.connect(str(db))
    c.execute("""CREATE TABLE qsos (id INTEGER PRIMARY KEY AUTOINCREMENT, call TEXT NOT NULL, name TEXT, start REAL NOT NULL,
        end REAL, band TEXT, freq REAL, freq_rx REAL, mode TEXT, submode TEXT, rst_sent TEXT, rst_rcvd TEXT, qth TEXT,
        grid TEXT, comment TEXT, tg INTEGER, via TEXT, my_call TEXT, my_grid TEXT, source TEXT, created REAL, updated REAL,
        qrz_status TEXT, qrz_logid TEXT, qrz_error TEXT, qrz_time REAL)""")
    c.execute("INSERT INTO qsos (call, start, mode, submode, source) VALUES ('OH1OLD', 1700000000, 'DIGITALVOICE', 'DMR', 'candidate')")
    c.commit()
    c.close()
    cfg = {**DEFAULT_LOG, "qrz": True, "qrz_inet": False, "eqsl": True, "hamqth": True}
    mon = SimpleNamespace(cfg={"log": cfg, "devices": {"my_call": "OH2BEA"}, "messaging": {}},
                          dirty=False, save_config=lambda: None, devmon=SimpleNamespace(state={}))
    lg = QsoLog(mon, db)
    lg.secrets.update(qrz="K", eqsl="salasana", hamqth="hqk_" + "0" * 64)
    assert lg.get(1)["prop_mode"] == "INTERNET"
    a = lg.add({"call": "OH2ABC", "start": 1_700_000_100, "mode_label": "DMR"})          # DMR → INTERNET
    b = lg.add({"call": "OH2CW", "start": 1_700_000_200, "mode_label": "CW", "band": "20m"})
    s = lg.add({"call": "OH2SAT", "start": 1_700_000_300, "mode_label": "FM", "prop_mode": "SAT"})
    ra, rb = lg.get(a), lg.get(b)
    assert ra["prop_mode"] == "INTERNET" and rb["prop_mode"] == "" and lg.get(s)["prop_mode"] == "SAT"
    assert "<PROP_MODE:8>INTERNET" in qso_adif(ra) and "PROP_MODE" not in qso_adif(rb)
    assert "<PROP_MODE:8>INTERNET" in adif_file([ra])
    # QRZ ei saa internet-yhteyksiä (asetus), eQSL ja HamQTH saavat
    assert (ra["qrz_status"], ra["eqsl_status"], ra["hamqth_status"]) == ("skip", "queued", "queued")
    assert rb["qrz_status"] == "queued"
    assert {r["call"] for r in lg.list(0, prop="inet")} == {"OH1OLD", "OH2ABC"}
    assert {r["call"] for r in lg.list(0, prop="direct")} == {"OH2CW", "OH2SAT"}
    lg.retry("qrz", a)                                     # ohitetun voi lähettää käsin
    assert lg.get(a)["qrz_status"] == "queued"
    with pytest.raises(ValueError):
        lg.add({"call": "OH2X", "start": 1_700_000_000, "prop_mode": "MOON"})
    assert normalize_log({"eqsl_user": "oh2bea", "hamqth": 1}, cfg)["eqsl_user"] == "OH2BEA"

    # eQSL: vastausten tulkinta
    r = QsoLog.eqsl_result
    assert r(200, "<!-- Reply form eQSL.cc ADIF Real-time Interface --> Result: 1 out of 1 records added") == ("ok", None)
    assert r(200, "Warning: Y=2016 M=11 D=13 DK4LI 20M RTTY Bad record: Duplicate Result: 0 out of 1 records added")[0] == "ok"
    assert r(200, "Error: No match on eQSL_User/eQSL_Pswd for date 20231114 22:13")[0] == "auth"
    assert r(200, "Error: The system is down until 0300")[0] == "error"
    assert r(200, "Warning: Y=2023 M=11 D=14 Bad Mode: XYZ Result: 0 out of 1 records added") == \
        ("failed", "Warning: Y=2023 M=11 D=14 Bad Mode: XYZ Result: 0 out of 1 records added")
    assert r(503, "")[0] == "error"
    posted = []

    async def fake_eqsl(adif):
        posted.append(adif)
        return 200, "Result: 1 out of 1 records added"
    monkeypatch.setattr(lg, "_eqsl_post", fake_eqsl)
    cfg["eqsl_qth"] = "Koti"

    # HamQTH: POST, muokkaus PUT, kaksoiskappale 409, väärä avain 401, poisto
    calls = []
    replies = iter([(201, "{}"), (201, "{}"), (201, "{}"), (200, "{}"), (409, "{}"), (401, "{}"), (204, "")])

    async def fake_hq(method, path, body=None, params=None):
        calls.append((method, path, body, params))
        return next(replies)
    monkeypatch.setattr(lg, "_hamqth", fake_hq)
    sent_qrz = []

    async def fake_qrz(data):
        sent_qrz.append(data)
        return {"RESULT": "OK", "LOGID": str(len(sent_qrz))}
    monkeypatch.setattr(lg, "_qrz", fake_qrz)

    async def go():
        await lg.sync_once()
        assert posted and "<EQSL_USER" not in posted[0] and "<APP_EQSL_QTH_NICKNAME:4>Koti" in posted[0]
        assert lg.get(a)["eqsl_status"] == "ok" and lg.get(a)["hamqth_status"] == "ok"
        assert [c[0] for c in calls] == ["POST", "POST", "POST"]
        q = calls[0][2]["qso"]                            # vanhaa (ennen palvelua lisättyä) ei lähetetä
        assert q["call"] == "OH2ABC" and q["prop_mode"] == "INTERNET" and q["submode"] == "DMR" and q["band"] == "70CM"
        assert calls[1][2]["qso"]["call"] == "OH2CW" and "prop_mode" not in calls[1][2]["qso"]
        assert lg.get(a)["qrz_status"] == "ok"            # käsin uudelleen lähetetty
        lg.update(a, {"name": "Matti"})                    # HamQTH: PUT alkuperäisellä tunnisteella; eQSL ei muutu
        assert lg.get(a)["hamqth_status"] == "queued" and lg.get(a)["eqsl_status"] == "ok"
        await lg.sync_once()
        put = [c for c in calls if c[0] == "PUT"][0]
        assert put[2]["original"]["call"] == "OH2ABC" and put[2]["qso"]["name"] == "Matti"
        d = lg.add({"call": "OH2DUP", "start": 1_700_000_400, "mode_label": "SSB", "band": "20m"})
        e = lg.add({"call": "OH2BAD", "start": 1_700_000_500, "mode_label": "SSB", "band": "20m"})
        await lg.sync_once()
        assert lg.get(d)["hamqth_status"] == "ok" and lg.get(e)["hamqth_status"] == "auth"
        assert lg.svc_state["hamqth"]["ok"] is False
        lg.delete(a, remote=True)
        await lg.sync_once()
        dl = [c for c in calls if c[0] == "DELETE"][0]
        assert dl[3]["call"] == "OH2ABC" and dl[3]["qso_date"] == "20231114"
    asyncio.run(go())
    snap = lg.snapshot()
    assert snap["eqsl"]["enabled"] and not snap["eqsl"]["long_pass"] and snap["hamqth"]["key"]
    lg.secrets["eqsl"] = "x" * 15
    assert lg.snapshot()["eqsl"]["long_pass"]
    lg.close()


def test_spanish_translation_complete():
    """Espanja: jokaisella englanninkielisellä tekstillä on käännös samoilla paikkamerkeillä; tr/num/ilmansuunnat/ASCII."""
    from bmmonitor import i18n
    from bmmonitor.i18n_es import ES
    from bmmonitor.messaging import clean_text
    root = Path(__file__).resolve().parent.parent / "bmmonitor" / "static"
    ph = re.compile(r"\{(\w*)\}")
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)":\s*\n?\s*"((?:[^"\\]|\\.)*)"', (root / "i18n.js").read_text("utf-8"))
    en_ui = {json.loads('"' + en + '"') for en in dict(pairs).values()}
    es_pairs = re.findall(r'"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)"', (root / "i18n.es.js").read_text("utf-8"))
    es_ui = {json.loads('"' + k + '"'): json.loads('"' + v + '"') for k, v in es_pairs}
    missing = sorted(en_ui - set(es_ui))
    assert not missing, missing[:10]
    for en, es in es_ui.items():
        assert sorted(ph.findall(en)) == sorted(ph.findall(es)), en
    missing = sorted(set(i18n.EN.values()) - set(ES))
    assert not missing, missing[:10]
    for en, es in ES.items():
        assert sorted(ph.findall(en)) == sorted(ph.findall(es)), en
    html = (root / "index.html").read_text("utf-8")
    assert '<script src="/static/i18n.es.js' in html and '<option value="es">Español</option>' in html
    try:
        i18n.set_lang("es")
        assert i18n.tr("Tuntematon komento. Laheta OHJE") == "Comando desconocido. Envía CMD"
        assert i18n.num(1.25, 2) == "1,25" and i18n.bearing(270) == "O"
        assert clean_text("¿Qué tal, España?", True) == "Que tal, Espana?"
    finally:
        i18n.set_lang("fi")


def test_ais_ships(tmp_path):
    """Laivat (AIS): tyyppiryhmät, asetukset, Digitrafficin ja aisstream.io:n viestien jäsennys, demo."""
    from types import SimpleNamespace
    from bmmonitor.ais import DEFAULT_AIS, AisFeed, normalize_ais, ship_group
    assert [ship_group(t) for t in (70, 84, 60, 41, 52, 31, 30, 37, 0, None, 99)] == \
        ["cargo", "tanker", "passenger", "passenger", "special", "special", "fishing", "pleasure", "other", "other", "other"]
    c = normalize_ais({"km": 200, "types": ["tanker", "cargo"], "source": "aisstream", "enabled": 1}, DEFAULT_AIS)
    assert c["km"] == 200 and c["types"] == ["cargo", "tanker"] and c["enabled"] is True
    for bad in ({"km": 300}, {"types": ["ufo"]}, {"source": "x"}):
        with pytest.raises(ValueError):
            normalize_ais(bad, DEFAULT_AIS)
    mon = SimpleNamespace(cfg={"ais": {**DEFAULT_AIS, "enabled": True, "km": 100},
                               "services": {"location": {"lat": 60.17, "lon": 24.94}}},
                          dirty=False, save_config=lambda: None)
    feed = AisFeed(mon)
    locs = feed.parse_locations({"type": "FeatureCollection", "features": [
        {"mmsi": 230145000, "type": "Feature", "geometry": {"type": "Point", "coordinates": [24.95, 60.15]},
         "properties": {"mmsi": 230145000, "sog": 12.3, "cog": 181.5, "navStat": 0, "rot": 0, "posAcc": True, "raim": False,
                        "heading": 511, "timestamp": 30, "timestampExternal": 1764250000000}},
        {"mmsi": 1, "geometry": {"coordinates": []}, "properties": {}}]})
    assert locs == [{"mmsi": 230145000, "lat": 60.15, "lon": 24.95, "sog": 12.3, "cog": 181.5, "heading": None, "nav": 0,
                     "ts": 1764250000.0}]
    v = feed.parse_vessel({"mmsi": 219598000, "name": "NORD SUPERIOR ", "callSign": "OWPA2", "shipType": 80, "draught": 118,
                           "destination": "NL AMS", "referencePointA": 148, "referencePointB": 35, "referencePointC": 23,
                           "referencePointD": 9, "imo": 9692129, "eta": 416128, "posType": 1, "timestamp": 1591521868371})
    assert v == {"name": "NORD SUPERIOR", "call": "OWPA2", "type": 80, "dest": "NL AMS", "draught": 11.8, "length": 183,
                 "width": 32, "imo": 9692129}
    calls = []

    async def fake_get(sess, path, params=None):
        calls.append((path, params))
        if path == "/vessels":
            return [{"mmsi": 230145000, "name": "FINNLADY", "shipType": 70, "draught": 65}]
        import time as _t
        return {"features": [{"geometry": {"coordinates": [24.95, 60.15]},
                              "properties": {"mmsi": 230145000, "sog": 10, "cog": 90, "navStat": 0, "heading": 88,
                                             "timestampExternal": _t.time() * 1000}},
                             {"geometry": {"coordinates": [25.0, 60.1]},
                              "properties": {"mmsi": 230000001, "sog": 0, "navStat": 5, "timestampExternal": 1000}}]}
    feed._get = fake_get
    asyncio.run(feed._digitraffic_once((60.17, 24.94)))
    assert calls[0][0] == "/vessels" and calls[1] == ("/locations", {"latitude": 60.2, "longitude": 24.9, "radius": 100})
    ships = feed.ship_list()
    assert len(ships) == 1 and ships[0]["name"] == "FINNLADY" and ships[0]["group"] == "cargo" and ships[0]["draught"] == 6.5
    assert feed.snapshot()["ok"] is True and feed.snapshot()["count"] == 1
    # aisstream.io
    feed.ships.clear()
    home = (60.17, 24.94)
    assert feed.handle_stream({"MessageType": "PositionReport", "MetaData": {"MMSI": 266000000, "ShipName": "SVEA  "},
                               "Message": {"PositionReport": {"Latitude": 60.0, "Longitude": 24.9, "Sog": 15.2, "Cog": 270,
                                                              "TrueHeading": 268, "NavigationalStatus": 0}}}, home)
    assert not feed.handle_stream({"MessageType": "PositionReport", "MetaData": {"MMSI": 1},
                                   "Message": {"PositionReport": {"Latitude": 10.0, "Longitude": 10.0}}}, home)   # säteen ulkopuolella
    assert feed.handle_stream({"MessageType": "ShipStaticData", "MetaData": {"MMSI": 266000000},
                               "Message": {"ShipStaticData": {"Name": "SVEA", "CallSign": "SFHQ", "Type": 60, "Destination": "STOCKHOLM",
                                                              "MaximumStaticDraught": 6.2, "Dimension": {"A": 150, "B": 30}}}}, home)
    s = feed.ships[266000000]
    assert (s["name"], s["call"], s["type"], s["length"], s["heading"], s["sog"]) == ("SVEA", "SFHQ", 60, 180, 268, 15.2)
    bb = feed.bbox(home)[0]
    assert bb[0][0] < 60.2 < bb[1][0] and bb[0][1] < 24.9 < bb[1][1]
    # demo
    feed.ships.clear()
    feed._demo_once(home)
    assert len(feed.ship_list()) == 10 and {x["group"] for x in feed.ship_list()} >= {"cargo", "tanker", "passenger", "special"}


def test_french_translation_complete():
    """Ranska: jokaisella englanninkielisellä tekstillä on käännös samoilla paikkamerkeillä; tr/num/ilmansuunnat/ASCII."""
    from bmmonitor import i18n
    from bmmonitor.i18n_fr import FR
    from bmmonitor.messaging import clean_text
    root = Path(__file__).resolve().parent.parent / "bmmonitor" / "static"
    ph = re.compile(r"\{(\w*)\}")
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)":\s*\n?\s*"((?:[^"\\]|\\.)*)"', (root / "i18n.js").read_text("utf-8"))
    en_ui = {json.loads('"' + en + '"') for en in dict(pairs).values()}
    fr_pairs = re.findall(r'"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)"', (root / "i18n.fr.js").read_text("utf-8"))
    fr_ui = {json.loads('"' + k + '"'): json.loads('"' + v + '"') for k, v in fr_pairs}
    missing = sorted(en_ui - set(fr_ui))
    assert not missing, missing[:10]
    for en, fr in fr_ui.items():
        assert sorted(ph.findall(en)) == sorted(ph.findall(fr)), en
    missing = sorted(set(i18n.EN.values()) - set(FR))
    assert not missing, missing[:10]
    for en, fr in FR.items():
        assert sorted(ph.findall(en)) == sorted(ph.findall(fr)), en
    html = (root / "index.html").read_text("utf-8")
    assert '<script src="/static/i18n.fr.js' in html and '<option value="fr">Français</option>' in html
    try:
        i18n.set_lang("fr")
        assert i18n.tr("Tuntematon komento. Laheta OHJE") == "Commande inconnue. Envoyez CMD"
        assert i18n.num(1.25, 2) == "1,25" and i18n.bearing(270) == "O"
        assert clean_text("Œuvre « Noël » à Tôkyô", True) == 'OEuvre "Noel" a Tokyo'
    finally:
        i18n.set_lang("fi")


def test_propagation_pskreporter():
    """Radiosää: lokaattorit, kaistat, MQTT-paketit, raporttien luokittelu ja yhteenveto, MQTT-asiakas paikallista palvelinta vasten."""
    import time as _t
    from types import SimpleNamespace
    from bmmonitor import propagation as P
    assert P.loc_latlon("KP20le") == P.loc_latlon("KP20LE") and P.loc_latlon("kp20") == (60.5, 25.0)
    assert P.loc_latlon("JO62qm34xx") and P.loc_latlon("") is None and P.loc_latlon("ZZ99") is None
    assert P.grid4(60.17, 24.94) == "KP20" and P.latlon_loc6(60.17, 24.94) == "KP20le"
    assert P.band_of("20m") == "20m" and P.band_of("", 144_174_000) == "2m" and P.band_of(None, 432_174_000) == "70cm"
    assert P.base_call("oh/dl1abc/p") == "DL1ABC" and P.base_call("OH2BEA") == "OH2BEA"
    assert P.topics_for("F/M0LTE", "KP20", True, True) == ["pskr/filter/v2/+/+/F.M0LTE/#", "pskr/filter/v2/+/+/+/+/KP20/#",
                                                             "pskr/filter/v2/+/+/+/+/+/KP20/#"]
    assert P.topics_for("", "", True, True) == []
    assert P.mqtt_connect_packet("x")[:2] == b"\x10\x0d" and P.mqtt_subscribe_packet(1, ["a/b"])[:2] == b"\x82\x08"
    assert P._varint(321) == b"\xc1\x02"
    cfg = {"propagation": {**P.DEFAULT_PROP, "enabled": True}, "services": {"location": {"lat": 60.17, "lon": 24.94}}}
    mon = SimpleNamespace(cfg=cfg, dirty=False, qso=SimpleNamespace(my_call=lambda: "OH2BEA"))
    f = P.PropFeed(mon)
    now = _t.time()
    assert f.add_spot({"sq": 1, "f": 14074000, "b": "20m", "md": "FT8", "rp": -12, "t": now - 60, "sc": "OH2BEA", "sl": "KP20le",
                       "rc": "K1ABC", "rl": "FN42aa"})
    assert not f.add_spot({"sq": 1, "sc": "OH2BEA", "sl": "KP20le", "rc": "K1ABC", "rl": "FN42aa", "t": now})   # sama sq
    assert not f.add_spot({"sq": 2, "sc": "X", "sl": "??", "rc": "Y", "rl": "FN42", "t": now})                  # ei sijaintia
    f.add_spot({"sq": 3, "b": "2m", "md": "FT8", "rp": 3, "t": now - 30, "sc": "ES2HIJ", "sl": "KO29", "rc": "OH2XA", "rl": "KP20kf"})
    f.add_spot({"sq": 4, "b": "40m", "md": "CW", "t": now - 30, "sc": "OH2XD", "sl": "KP20", "rc": "G4STU", "rl": "IO91"})
    f.add_spot({"sq": 5, "b": "40m", "md": "CW", "t": now - 7200, "sc": "OH2XD", "sl": "KP20", "rc": "W6X", "rl": "CM87"})
    q = f.query(60)
    kinds = sorted((p["kind"], p["b"], p["call"]) for p in q["points"])
    assert kinds == [("heard", "2m", "ES2HIJ"), ("mine", "20m", "K1ABC"), ("sent", "40m", "G4STU")]
    s20 = next(b for b in q["summary"] if b["b"] == "20m")
    assert s20["mine"] == 1 and 6000 < s20["far"] < 7500 and s20["modes"] == ["FT8"]
    assert [b["b"] for b in q["summary"]] == ["40m", "20m", "2m"]
    assert len(f.query(360)["points"]) == 4 and [p["b"] for p in f.query(60, bands=["2m"])["points"]] == ["2m"]
    assert [p["md"] for p in f.query(60, modes=["CW"])["points"]] == ["CW"]

    async def broker_test():
        got = {}

        async def handle(reader, writer):
            typ, _, body = await P.mqtt_read_packet(reader)
            got["connect"] = (typ, body)
            writer.write(b"\x20\x02\x00\x00")                        # CONNACK
            typ, _, body = await P.mqtt_read_packet(reader)
            got["sub"] = body
            writer.write(b"\x90\x03\x00\x01\x00")                    # SUBACK
            payload = json.dumps({"sq": 99, "b": "15m", "md": "FT8", "rp": -5, "t": _t.time(), "sc": "OH2BEA", "sl": "KP20le",
                                  "rc": "ZS6JKL", "rl": "KG33"}).encode()
            topic = b"pskr/filter/v2/15m/FT8/OH2BEA/ZS6JKL/KP20/KG33/224/462"
            body = len(topic).to_bytes(2, "big") + topic + payload
            writer.write(b"\x30" + P._varint(len(body)) + body)
            await writer.drain()
            await asyncio.sleep(0.3)
            writer.close()

        srv = await asyncio.start_server(handle, "127.0.0.1", 0)
        g = P.PropFeed(mon)
        g.host, g.port, g.tls = "127.0.0.1", srv.sockets[0].getsockname()[1], False
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(g._mqtt_once(["pskr/filter/v2/+/+/OH2BEA/#"]), 5)
        srv.close()
        return got, g

    got, g = asyncio.run(broker_test())
    assert got["connect"][0] == 1 and b"MonitorForAmateurRadio-" in got["connect"][1]
    assert b"pskr/filter/v2/+/+/OH2BEA/#" in got["sub"]
    assert [(p["call"], p["kind"]) for p in g.query(60)["points"]] == [("ZS6JKL", "mine")]


def test_propagation_rbn():
    """RBN: rivien jäsennys, etuliitteet → sijainti, oma kutsu / oman maan skimmerit, telnet-asiakas paikallista palvelinta vasten."""
    import calendar
    import time as _t
    from types import SimpleNamespace
    from bmmonitor import propagation as P
    from bmmonitor.prefixes import call_latlon
    assert call_latlon("OH2BEA") == call_latlon("OH6BG-#") and call_latlon("W1AW/7") == call_latlon("K7ABC")
    assert call_latlon("OH/DL1ABC") == call_latlon("OH1A") and call_latlon("KH6ABC") != call_latlon("K6ABC")
    assert call_latlon("UA9XX") != call_latlon("UA3XX") and call_latlon("QQ1A") is None
    now = calendar.timegm((2026, 11, 27, 20, 25, 0, 0, 0, 0))
    sp = P.parse_rbn("DX de OH6BG-#:    14025.1  OH2BEA         CW    18 dB  22 WPM  CQ      2024Z", now)
    assert sp == {"skimmer": "OH6BG", "khz": 14025.1, "dx": "OH2BEA", "md": "CW", "rp": 18, "wpm": 22, "t": now - 60}
    assert P.parse_rbn("DX de DL8LAS-#: 7012.5 K1ABC RTTY 12 dB 45 BPS CQ 2359Z", now)["t"] == now - 86400 + (23 * 60 + 59 - 20 * 60 - 25) * 60
    assert P.parse_rbn("garbage") is None
    cfg = {"propagation": {**P.DEFAULT_PROP, "enabled": True}, "services": {"location": {"lat": 60.17, "lon": 24.94}}}
    mon = SimpleNamespace(cfg=cfg, dirty=False, qso=SimpleNamespace(my_call=lambda: "OH2BEA"))
    f = P.PropFeed(mon)
    t = _t.time()
    assert f.add_rbn({"skimmer": "K3LR", "dx": "OH2BEA", "khz": 14025.0, "md": "CW", "rp": 12, "wpm": 25, "t": t})
    assert f.add_rbn({"skimmer": "OH6BG", "dx": "JA1XYZ", "khz": 7012.0, "md": "CW", "rp": 8, "wpm": 20, "t": t})
    assert not f.add_rbn({"skimmer": "DL8LAS", "dx": "JA1XYZ", "khz": 7012.0, "md": "CW", "rp": 8, "t": t})     # muu maa
    pts = {(p["kind"], p["call"], p["src"]) for p in f.query(60)["points"]}
    assert pts == {("mine", "K3LR", "rbn"), ("heard", "JA1XYZ", "rbn")}

    async def server_test():
        got = {}

        async def handle(reader, writer):
            writer.write(b"Welcome to RBN\r\nPlease enter your call: ")
            await writer.drain()
            got["login"] = await reader.readline()
            writer.write(f"DX de W3LPL-#:  21030.0  OH2BEA  CW  20 dB  24 WPM  CQ  {_t.strftime('%H%M', _t.gmtime())}Z\r\n".encode())
            await writer.drain()
            await asyncio.sleep(0.2)
            writer.close()

        srv = await asyncio.start_server(handle, "127.0.0.1", 0)
        g = P.PropFeed(mon)
        g.rbn_host, g.rbn_port = "127.0.0.1", srv.sockets[0].getsockname()[1]
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(g._rbn_once("OH2BEA"), 5)
        srv.close()
        return got, g

    got, g = asyncio.run(server_test())
    assert got["login"] == b"OH2BEA\r\n"
    assert [(p["call"], p["b"], p["wpm"]) for p in g.query(60)["points"]] == [("W3LPL", "15m", 24)]


def test_tropo_estimate():
    """Tropoarvio: refraktiivisuus, gradientti ja luokat, Open-Meteon vastauksen jäsennys, OVATION-ruudukko, hälytykset."""
    import time as _t
    from collections import deque
    from types import SimpleNamespace
    from bmmonitor import tropo as T
    from bmmonitor import propagation as P
    assert 300 < T.refractivity(15, 60, 1013.25) < 340
    assert T.index_of(-40) == 0 and T.index_of(-70) == 1 and T.index_of(-120) == 2 and T.index_of(-200) == 3 and T.index_of(None) == 0
    assert T.min_gradient([(2, 320), (300, 310), (1000, 250)]) == pytest.approx((250 - 310) / 0.7, rel=1e-3)
    pts = T.grid_points(60.2, 24.9)
    assert len(pts) == 49 and (60.2, 24.9) in pts and min(p[0] for p in pts) == pytest.approx(55.7)

    def hourly(t2, rh2, ps, lv):
        h = {"time": [1000, 4600], "temperature_2m": [t2, t2], "relative_humidity_2m": [rh2, rh2], "surface_pressure": [ps, ps]}
        for p, (t, rh, z) in lv.items():
            h[f"temperature_{p}hPa"], h[f"relative_humidity_{p}hPa"], h[f"geopotential_height_{p}hPa"] = [t, t], [rh, rh], [z, z]
        return {"elevation": 10, "hourly": h}
    normal = hourly(10, 70, 1010, {1000: (9.5, 70, 90), 925: (5, 65, 760), 850: (0, 60, 1450)})
    duct = hourly(2, 95, 1015, {1000: (12, 20, 130), 925: (11, 20, 800), 850: (6, 30, 1500)})   # kostea kylmä pinta, kuiva lämmin yllä
    high = hourly(10, 70, 960, {1000: (12, 20, -300), 925: (5, 65, 720), 850: (0, 60, 1400)})   # 1000 hPa maan alla
    fc = T.parse_forecast([normal, duct, high], [(60, 25), (61, 25), (62, 25)])
    assert fc["times"] == [1000, 4600] and fc["cells"][0]["idx"][0] == 0 and fc["cells"][1]["idx"][0] == 3
    assert fc["cells"][2]["idx"][0] == 0
    assert T.ovation_cells({"coordinates": [[25, 66, 30], [24, 66, 50], [200, -70, 9], [10, 10, 1]]}) == [[-70, -160, 9], [66, 24, 50]]
    alerts = []
    mon = SimpleNamespace(cfg={"propagation": {**P.DEFAULT_PROP, "enabled": True, "alert_tropo": True, "alert_open": True},
                               "services": {"location": {"lat": 60.17, "lon": 24.94}}},
                          dirty=False, qso=SimpleNamespace(my_call=lambda: "OH2BEA"), touch=lambda: None,
                          hub=SimpleNamespace(announce=lambda *a, **k: None),
                          watcher=SimpleNamespace(seq=0, alerts=deque()))
    tf = T.TropoFeed(mon)
    now = int(_t.time())
    tf.fc = {"times": [now + 3600 * i for i in range(24)],
             "cells": [{"lat": 60.2, "lon": 24.9, "idx": [0, 0, 2, 3] + [0] * 20, "grad": [None] * 24}]}
    tf.check_alert()
    tf.check_alert()                                  # sama päivä ja taso: ei uudestaan
    assert [a["title"] for a in mon.watcher.alerts] == ["Tropoarvio: kanavointi todennäköinen"]
    q = tf.query(0)
    assert q["home_now"] == 0 and q["outlook"][0]["idx"] == 3 and len(q["cells"]) == 1
    f = P.PropFeed(mon)
    f.add_spot({"sq": 1, "b": "2m", "md": "FT8", "t": _t.time() - 60, "sc": "SP2QRS", "sl": "JO94", "rc": "OH2XA", "rl": "KP20kf"})
    f.add_spot({"sq": 2, "b": "2m", "md": "FT8", "t": _t.time() - 60, "sc": "ES2HIJ", "sl": "KO29", "rc": "OH2XA", "rl": "KP20kf"})
    f.check_open()
    f._last_open_check = 0
    f.check_open()                                    # 3 h:n sisällä ei uudestaan
    assert [a["title"] for a in mon.watcher.alerts][0] == "2m auki" and len(mon.watcher.alerts) == 2


def test_qso_candidates_persist(tmp_path):
    """Yhteysehdokkaat säilyvät uudelleenkäynnistyksen yli, käsiteltyä ei luoda uudelleen, yli 7 vrk vanhat poistuvat."""
    import sqlite3
    import time as _t
    from bmmonitor import qso as Q
    db = sqlite3.connect(str(tmp_path / "q.sqlite"))
    d = Q.QsoDetector(lambda c: c == "OH2BEA")
    d.attach(db)
    now = _t.time() - 600
    for i, (call, own) in enumerate([("OH2BEA", True), ("OH3XYZ", False), ("OH2BEA", True)]):
        d.on_event({"Start": now + i * 20, "Stop": now + i * 20 + 10, "SourceCall": call, "SourceID": 1 + i % 2,
                    "DestinationID": 244, "SourceName": "Pekka" if not own else "", "CallTypes": ["group"]})
    assert [c["call"] for c in d.snapshot()] == ["OH3XYZ"]
    d2 = Q.QsoDetector(lambda c: c == "OH2BEA")
    d2.attach(db)                                     # "uudelleenkäynnistys"
    assert [(c["call"], c["name"], c["turns"]) for c in d2.snapshot()] == [("OH3XYZ", "Pekka", 3)]
    cid = d2.snapshot()[0]["id"]
    d2.close(cid)
    d3 = Q.QsoDetector(lambda c: c == "OH2BEA")
    d3.attach(db)
    assert d3.snapshot() == [] and cid in d3.closed
    d3._upsert(cid, "OH3XYZ", [{"start": now, "stop": now + 5, "own": False, "name": "", "via": "", "ctx": None, "tg": 244}],
               private=False, other_info={})
    assert d3.snapshot() == []                        # käsiteltyä ei luoda uudelleen
    old = {"id": "TG:244:OH9X:1", "call": "OH9X", "end": _t.time() - 8 * 86400, "start": 0}
    db.execute("INSERT INTO candidates VALUES (?, ?, ?)", (old["id"], old["end"], json.dumps(old)))
    db.commit()
    d4 = Q.QsoDetector(lambda c: c == "OH2BEA")
    d4.attach(db)
    assert "TG:244:OH9X:1" not in d4.cands and db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0


def test_lan_access(tmp_path, monkeypatch):
    """Selainkäyttö lähiverkossa: salasana, kirjautuminen, kielletyt toiminnot, Vain katselu, salaisuudet, väärät yritykset."""
    import aiohttp
    from aiohttp import web
    from bmmonitor import lan as L
    from bmmonitor import secret_store
    from bmmonitor.app import Monitor, make_app
    store = {}                     # ei oikeaa salasanavarastoa (macOS:n avainnippu voi jäädä odottamaan CI:ssä)
    monkeypatch.setattr(secret_store, "put", lambda n, v: store.__setitem__(n, v) or True)
    monkeypatch.setattr(secret_store, "get", lambda n: store.get(n))
    monkeypatch.setattr(secret_store, "delete", lambda n: store.pop(n, None))
    h = L.hash_password("salasana1", iters=1000)
    assert L.verify_password("salasana1", h) and not L.verify_password("väärä", h) and not L.verify_password("x", "roskaa")
    assert L.normalize_lan({"port": "9000", "mode": "view", "enabled": 1}, {})["port"] == 9000
    with pytest.raises(ValueError):
        L.normalize_lan({"port": 80}, {})
    assert L.has_secret_change({"log": {"qrz_key": "abc"}}, ["qrz_key"]) and not L.has_secret_change({"log": {"qrz_key": "********"}}, ["qrz_key"])
    assert L.has_secret_change({"messaging": {"passcode": "1234"}}, []) and not L.has_secret_change({"units": "metric"}, [])
    sec = {"v": h + "|gen1"}
    acc = L.LanAccess(lambda: sec["v"])
    for _ in range(5):
        with pytest.raises(PermissionError):
            acc.login("1.2.3.4", "x", h)
    assert acc.blocked_for("1.2.3.4") > 0
    with pytest.raises(PermissionError):
        acc.login("1.2.3.4", "salasana1", h)               # estetty, vaikka salasana oikein
    tok = acc.login("5.6.7.8", "salasana1", h)
    assert acc.check(tok) and not acc.check("x") and len(acc.sessions) == 1
    assert L.LanAccess(lambda: sec["v"]).check(tok)                 # säilyy uudelleenkäynnistyksen yli
    assert not acc.check(tok[:-1] + ("A" if tok[-1] != "A" else "B"))  # väärennetty allekirjoitus
    assert acc.renew(tok) is None                                     # tuore eväste: ei uusita vielä
    exp, nonce, _ = tok.split(".")
    old = f"{int(time.time()) + 86400}.{nonce}"
    old_tok = f"{old}.{acc._mac(old)}"
    assert acc.check(old_tok) and acc.renew(old_tok)                  # lähellä vanhenemista → uusi eväste
    gone = f"{int(time.time()) - 1}.{nonce}"
    assert not acc.check(f"{gone}.{acc._mac(gone)}")                  # vanhentunut
    sec["v"] = h + "|gen2"                                            # uusi sukupolvi = kaikki ulos
    assert not acc.check(tok)
    sec["v"] = h + "|gen1"
    acc.logout(tok)
    assert not acc.check(tok)
    sec["v"] = ""
    with pytest.raises(PermissionError):
        acc.login("9.9.9.9", "salasana1", "")
    cfg = {"map_tiles": "https://t/{z}/{x}/{y}.png", "messaging": {"server": "rotate.aprs2.net", "port": 14580,
           "feeds": [{"url": "https://a/feed"}]}, "nets": {"sources": [{"url": "https://c/x.ics"}]},
           "aprsrx": {"host": "127.0.0.1", "port": 8001, "radios": [{"type": "kiss_tcp", "host": "127.0.0.1", "port": 8001}]}}
    assert not L.has_address_change({"units": "metric", "messaging": {"server": "rotate.aprs2.net", "port": "14580",
                                     "feeds": [{"url": "https://a/feed", "enabled": False}]}}, cfg)
    assert not L.has_address_change({"nets": {"sources": []}, "aprsrx": {"radios": [{"type": "kiss_tcp", "host": "127.0.0.1",
                                     "port": 8001, "enabled": False}, {"type": "serial", "serial": "COM5"}]}}, cfg)
    for bad in ({"map_tiles": "http://192.168.1.1/{z}/{x}/{y}"}, {"messaging": {"server": "10.0.0.1"}},
                {"messaging": {"feeds": [{"url": "http://192.168.1.1/admin"}]}}, {"nets": {"sources": [{"url": "webcal://x/y.ics"}]}},
                {"aprsrx": {"radios": [{"type": "agwpe", "host": "10.0.0.5", "port": 22}]}}, {"aprsrx": {"port": 22}}):
        assert L.has_address_change(bad, cfg), bad
    assert "<svg" in (L.qr_svg("http://192.168.1.2:8766/") or "<svg")

    async def run():
        mon = Monitor(tmp_path / "config.json")
        p1, p2 = _free_port(), _free_port()
        runner = web.AppRunner(make_app(mon))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", p1).start()
        mon.runner, mon.local_port = runner, p1
        loc, rem = f"http://127.0.0.1:{p1}", f"http://127.0.0.1:{p2}"
        async with aiohttp.ClientSession() as s:
            r = await s.post(loc + "/api/lan", json={"enabled": True, "port": p2})
            assert (await r.json())["ok"] is False                       # salasana puuttuu
            r = await s.post(loc + "/api/lan", json={"enabled": True, "port": p2, "password": "lyhyt"})
            assert r.status == 400
            r = await s.post(loc + "/api/lan", json={"enabled": True, "port": p2, "password": "salasana1"})
            j = await r.json()
            assert j["ok"] and j["running"] and j["config"]["lan"]["pw"] == "********"
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            r = await s.get(rem + "/", allow_redirects=False)
            assert r.status == 302 and r.headers["Location"] == "/login"
            assert (await s.get(rem + "/api/config")).status == 401
            assert (await s.get(rem + "/api/snapshot")).status == 401
            assert "password" in (await (await s.get(rem + "/login")).text())
            r = await s.post(rem + "/api/lan/login", json={"password": "väärä"})
            assert r.status == 401
            r = await s.post(rem + "/api/lan/login", json={"password": "salasana1"})
            assert (await r.json())["ok"]
            snap = await (await s.get(rem + "/api/snapshot")).json()
            assert "talkgroups" in snap and "version" in snap
            assert (await (await s.post(rem + "/api/clientlog", json={"msg": "testi"})).json())["ok"]
            me = await (await s.get(rem + "/api/lan/me")).json()
            assert me == {"remote": True, "mode": "full"}
            cfg = await (await s.get(rem + "/api/config")).json()
            assert cfg["lan"]["pw"] == "********" and "pw_store" not in str(cfg.get("lan", {}).get("pw"))
            r = await s.post(rem + "/api/config", json={"units": "metric"})
            assert (await r.json())["ok"]                                  # täysi käyttö: tavallinen asetus käy
            for path, body in (("/api/shutdown", {}), ("/api/update/install", {}), ("/api/lan", {"enabled": False}),
                               ("/api/config", {"bm_api_key": "uusi"}), ("/api/config", {"lan": {"enabled": False}}),
                               ("/api/checkin", {"action": "sheet_disconnect"}),
                               ("/api/config", {"messaging": {"feeds": [{"url": "http://192.168.1.1/"}]}})):
                assert (await s.post(rem + path, json=body)).status == 403, path
            assert (await s.get(rem + "/api/feedback/bundle")).status == 403
            assert (await s.get(rem + "/api/feeds/preview?url=http://192.168.1.1/")).status == 403
            r = await s.post(rem + "/api/config", json={"units": "metric"}, headers={"Origin": "http://evil.example"})
            assert r.status == 403
        async with aiohttp.ClientSession() as s:                           # paikallinen: Vain katselu päälle
            r = await s.post(loc + "/api/lan", json={"mode": "view"})
            assert (await r.json())["ok"]
            assert (await (await s.get(loc + "/api/lan/me")).json())["remote"] is False
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            await s.post(rem + "/api/lan/login", json={"password": "salasana1"})
            assert (await s.get(rem + "/api/config")).status == 200
            assert (await s.post(rem + "/api/config", json={"units": "metric"})).status == 403
            assert (await s.post(rem + "/api/lan/logout")).status == 200
            assert (await s.get(rem + "/api/config")).status == 401
            await s.post(rem + "/api/lan/login", json={"password": "salasana1"})
            assert (await s.get(rem + "/api/config")).status == 200
            mon.lan = L.LanAccess(mon.lan.secret)                           # "uudelleenkäynnistys": eväste kelpaa yhä
            assert (await s.get(rem + "/api/config")).status == 200
            async with aiohttp.ClientSession() as s2:
                assert (await s2.post(loc + "/api/lan/logout_all")).status == 200
            assert (await s.get(rem + "/api/config")).status == 401          # uusi sukupolvi mitätöi evästeen
        async with aiohttp.ClientSession() as s:
            await s.post(loc + "/api/lan", json={"enabled": False})
        assert mon.lan_site is None
        await runner.cleanup()

    asyncio.run(run())


def test_lan_host_and_ws_chunks():
    """DNS rebinding -suoja (Host-otsake) ja suuren tilan lähetys osissa."""
    import socket as _s
    from bmmonitor import lan as L
    from bmmonitor import app as A
    mac = "lo0: flags=8049<UP>\n\tinet 127.0.0.1 netmask 0xff000000\nen0: flags=8863<UP>\n\tinet6 fe80::1\n\tinet 192.168.1.20 netmask 0xffffff00\nutun3: flags=8051<UP>\n\tinet 100.101.2.3 --> 100.101.2.3\n"
    assert L._parse_ifconfig(mac) == [("lo0", "127.0.0.1"), ("en0", "192.168.1.20"), ("utun3", "100.101.2.3")]
    assert L._parse_ip_addr("2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0\n3: docker0    inet 172.17.0.1/16") == \
        [("eth0", "10.0.0.5"), ("docker0", "172.17.0.1")]
    assert L._parse_win_json('{"IPAddress":"192.168.1.9","InterfaceAlias":"Wi-Fi"}') == [("Wi-Fi", "192.168.1.9")]
    assert L._parse_win_json('[{"IPAddress":"172.20.0.1","InterfaceAlias":"vEthernet (WSL)"}]')[0][0].startswith("vEthernet")
    assert L.VIRTUAL_RE.search("vEthernet (WSL)") and L.VIRTUAL_RE.search("utun3") and not L.VIRTUAL_RE.search("Wi-Fi") \
        and not L.VIRTUAL_RE.search("Ethernet") and not L.VIRTUAL_RE.search("en0")
    for ok in ("127.0.0.1:8765", "192.168.1.20:8766", "[::1]:8765", "localhost:8765", "localhost", _s.gethostname() + ":1", ""):
        assert L.host_allowed(ok), ok
    for bad in ("evil.example", "evil.example:8765", "127.0.0.1.nip.io:8765"):
        assert not L.host_allowed(bad), bad
    sent = []

    class FakeWs:
        async def send_str(self, m):
            sent.append(m)
    msg = json.dumps({"x": "ä" * 1000})
    old = A.WS_CHUNK
    try:
        A.WS_CHUNK = 300
        asyncio.run(A.send_ws(FakeWs(), msg))
    finally:
        A.WS_CHUNK = old
    assert len(sent) > 1 and all(p.startswith("\x01") for p in sent)
    parts = {}
    for p in sent:
        head, data = p[1:].split("|", 1)
        _tag, i, n = head.split("/")
        parts[int(i)] = data
    assert "".join(parts[i] for i in range(int(n))) == msg


def test_static_files_intact_to_slow_client(tmp_path):
    """Windowsin SelectorEventLoopissa asyncion sendfile-varatie sotki hitaalle vastaanottajalle (iPad Wi-Fi:ssä)
    lähetetyt tiedostot. Ohjelma käyttää aiohttp:n omaa lähetystä (AIOHTTP_NOSENDFILE); testi pakottaa varatien
    ja pienen lähetyspuskurin kuten Windowsissa."""
    import socket as _s
    import threading
    from asyncio import exceptions
    from aiohttp import web, web_fileresponse
    import bmmonitor  # noqa: F401
    from bmmonitor.app import Monitor, make_app
    assert web_fileresponse.NOSENDFILE
    data = os.urandom(300_000)
    (tmp_path / "f.bin").write_bytes(data)

    async def h(req):
        req.transport.get_extra_info("socket").setsockopt(_s.SOL_SOCKET, _s.SO_SNDBUF, 4096)
        return web.FileResponse(tmp_path / "f.bin")

    def client(port, path, out):
        s = _s.socket()
        s.setsockopt(_s.SOL_SOCKET, _s.SO_RCVBUF, 4096)
        s.connect(("127.0.0.1", port))
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
        buf = b""
        while True:
            time.sleep(0.001)
            c = s.recv(4096)
            if not c:
                break
            buf += c
        out.append(buf.split(b"\r\n\r\n", 1)[1])

    async def run():
        loop = asyncio.get_running_loop()

        async def no_native(*a, **k):
            raise exceptions.SendfileNotAvailableError("windows")
        loop._sendfile_native = no_native
        app = web.Application()
        app.router.add_get("/", h)
        r = web.AppRunner(app)
        await r.setup()
        port = _free_port()
        await web.TCPSite(r, "127.0.0.1", port).start()
        out = []
        t = threading.Thread(target=client, args=(port, "/", out))
        t.start()
        while t.is_alive():
            await asyncio.sleep(0.02)
        await r.cleanup()
        assert out[0] == data
        # sivu: skripteissä versio, jottei vanhaa kopiota käytetä välimuistista
        mon = Monitor(tmp_path / "config.json")
        r = web.AppRunner(make_app(mon))
        await r.setup()
        port = _free_port()
        await web.TCPSite(r, "127.0.0.1", port).start()
        async with aiohttp.ClientSession() as s:
            html = await (await s.get(f"http://127.0.0.1:{port}/")).text()
        await r.cleanup()
        from bmmonitor import __version__
        assert f'src="/static/i18n.js?v={__version__}"' in html and f'leaflet.css?v={__version__}"' in html

    import aiohttp
    asyncio.run(run())
