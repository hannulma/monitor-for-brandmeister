# Monitor for Amateur Radio

[![Release](https://img.shields.io/github/v/release/hannulma/monitor-for-amateur-radio?include_prereleases&label=release)](https://github.com/hannulma/monitor-for-amateur-radio/releases)
[![CI](https://github.com/hannulma/monitor-for-amateur-radio/actions/workflows/ci.yml/badge.svg)](https://github.com/hannulma/monitor-for-amateur-radio/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A desktop companion for the radio amateur** – one app for the [BrandMeister](https://brandmeister.network) DMR network,
**APRS** through your own radios and TNCs (messaging, a map of what your station hears, RF coverage and digipeaters),
a **contact log** with QRZ Logbook, eQSL and HamQTH upload, **check-in nets** with a shared Google Sheet, **satellite passes**, and
open-data **alerts** (weather, space weather, aurora, lightning, radiation, road traffic) delivered to your radio
as text messages. Windows, macOS and Linux; everything runs on your own computer.

The program started as a BrandMeister talkgroup monitor (*Monitor for BrandMeister*); the release files and
internal names still use that name so that updates and saved settings keep working.

🇫🇮 **[Suomenkielinen ohje → README.fi.md](README.fi.md)**

> [!NOTE]
> **Stable release 0.17.0.** New features arrive first in test versions (beta), which you can enable in
> *App → Version and updates*. Bug reports and feedback are very welcome in
> [Issues](https://github.com/hannulma/monitor-for-amateur-radio/issues).
> The user interface is available in **English**, **Finnish**, **German**, **Spanish** and **French** (App → Language and units). Feature settings are in the *Settings* section at the bottom of each tab.

![Monitor view](docs/screenshot-monitor.png)

## Features

The tabs are grouped: **BrandMeister · Statistics · Friends · Devices · Check-in │ APRS · Feeds │ Logs │ Services · Satellites · Propagation │ App**. Tabs you don't use can be hidden (*App → Appearance*); their functions keep running in the background.

### BrandMeister DMR

- **Talkgroup monitor** – watch talkgroups by number or wildcard (`244*`, `2441?`): who is talking right now,
  last activity, duration, via which repeater/hotspot, number of calls and total airtime. An antenna icon after the
  BrandMeister tab name shows when a monitored talkgroup is on the air.
- **Recent calls** – the last 10, 25, 50 or 100 calls, filterable by talkgroup; transmissions in progress are shown at
  the top with an ON AIR badge and a running duration.
- **Live audio** – listen to one or several talkgroups directly in the app (Hoseline audio stream), with the
  current talker, talker alias and level meter.
- **Nets today and tomorrow** – the FinDMR round ([6kw.fi](https://6kw.fi/findmr/)), W0WC's BrandMeister net
  calendar, your own ICS calendars and a list of your own; shown in your local time, marked when the net's talkgroup
  has traffic, with listen / check-in buttons and a reminder for nets in monitored talkgroups.
- **Friend alerts** – desktop notification, sound and/or a DMR text message when a station on your list
  keys up anywhere on the network.
- **Station card** – click any callsign for registry data, APRS SSID, repeaters/hotspots and where and when
  the station was last heard.
- **Repeater & hotspot watch** – online/offline alerts, static and dynamic talkgroups, last traffic. With a
  BrandMeister API key you can also add/remove static talkgroups, drop dynamic groups, drop the current call
  or reset the connection of *your own* devices.
- **Statistics** – activity per hour/day, top talkers, busiest talkgroups, and BrandMeister's own per-talkgroup
  user history.
- **Check-in nets** – log weekly check-in nets (e.g. FinDMR TG 244, Estonia TG 248, Worldwide TG 91). Stations
  keying up in the net talkgroup appear in order of arrival and **rows never move**, even when traffic overlaps;
  overlapping keyups are marked, and a notice above the log shows when an already logged station transmits again. Click **Heard – log** (or type part of the callsign and press Enter) to give the
  station the next log number. The log uses the FinDMR sheet columns (NRO, callsign, RPT/HOTSPOT, KUNTA,
  Lisätietoja, Oper); RPT/HOTSPOT comes from the BrandMeister device (repeater callsign and city, or TetraPack / POC / Fusion / Open DMR / Hotspot by link type). With the optional
  [Google Sheets script](sheets/README.md) (step-by-step guide and copy buttons for the script files in the app:
  Check-in → Google Sheet → Help) entries go straight into the shared sheet and several operators can
  log at once (personal keys via one-time pairing codes, signed requests); without it, **Copy new rows** puts
  the rows on the clipboard ready to paste. Finished rounds are saved as CSV.

![Statistics](docs/screenshot-stats.png)

### APRS

- **APRS messaging** – see all APRS messages to and from every SSID of your callsign (including DMR ↔ APRS
  messages through the BrandMeister APRS gateway) and reply directly from the app. The number of new messages is
  shown after the APRS tab name; a desktop notification can be turned on in the APRS settings.
  Optionally messages, acks and command replies are also transmitted on the radio
  (APRS → Settings → APRS messages on the radio; route automatic / APRS-IS / radio / radio and APRS-IS, off by default).
- **Radios and TNCs** – one or more radios at the same time (APRS → Settings → Radios and TNCs): **KISS TCP** (Graywolf,
  Dire Wolf; default `127.0.0.1:8001`), **KISS TNC on a serial port** (USB or Bluetooth, e.g. Mobilinkd, Kenwood TH-D74/D75
  in KISS mode), **Kenwood TM-D710 / TH-D72** and **Kantronics KPC-3+** (experimental: the TNC is switched to KISS mode
  automatically), **AGWPE** (Dire Wolf, soundmodem) and a **simulated radio** for testing. For a radio on a sound card use
  Graywolf or Dire Wolf. Transmitting uses the first connected radio that is allowed to transmit.
- **APRS map** – built from the packets your radios hear.
  Map layers (24 h / 7 days / 30 days): **RF coverage** – a heat map of where your station hears packets
  directly and via digipeaters; **APRS stations** – the latest position of stations heard on the radio (stations an iGate relays to RF are shown
  hollow and not counted in RF coverage); **Digipeaters** – click one to see the area heard through it;
  **Weather stations** – temperature, wind, humidity, pressure and rain; **Objects** – objects and items (e.g. repeaters);
  **APRS-IS stations** – optional, stations from APRS-IS around your home (radius in the settings). **Ships (AIS)** – optional,
  ships around your home from Fintraffic Digitraffic (Finland and the Baltic Sea, no key) or aisstream.io (worldwide, free key);
  the ship types are chosen from the *Ships* menu above the map, and moored/anchored ships can be hidden. Tables of digipeater shares and the most
  common paths, and an alert when a known digipeater or a station you choose (e.g. an iGate) goes silent –
  only while your own reception works. Base map © OpenStreetMap contributors (tiles fetched through the app
  with its own User-Agent and cached; the tile URL can be changed).
- **Radio commands** – send `CMD`, `TG244`, `WHO OH2ABC`, `NOW` or a feed command from your DMR radio and get the
  answer back as a text message (in Finnish also `OHJE`, `KUKA`, `NYT`).
- **News feeds → DMR SMS** – new RSS/Atom/JSON Feed items sent to your radio as short text messages.

![APRS map with ships](docs/screenshot-aprs.png)

### Logs

- **Contact log** – contacts (QSOs) are recognised from BrandMeister calls when you and another station take turns
  on a talkgroup or have a private call; add them to the log with one click. Candidates wait (also across restarts)
  until you add or skip them, for at most 7 days. Manual entries for any mode (SSB, CW, FM, FT8, D-STAR, C4FM, …;
  quick buttons plus all ADIF modes, RST defaults 59/599). Logs tab: search, filter by mode, edit, ADIF/CSV export.
  The log is stored locally; optional upload to **QRZ Logbook** (requires a QRZ subscription, XML level or higher),
  **eQSL** (free account; note that eQSL's interface does not accept passwords longer than 14 characters) and
  **HamQTH** (free; your own API key from HamQTH → Settings → Apps and API keys – *log upload provided by
  [HamQTH.com](https://www.hamqth.com/)*). Contacts recognised from BrandMeister are marked as internet-assisted
  (ADIF `PROP_MODE=INTERNET`, also DMR/D-STAR/C4FM/M17 by default); each service can be set to skip them, since they do
  not count for most awards. The log can be filtered by propagation mode.

![Logbook](docs/screenshot-logs.png)

### Services and satellites

- **Open-data alert services** (each with its own on/off switch):

  | Service | Source |
  |---|---|
  | ⚡ Lightning near you | Finnish Meteorological Institute (FMI) open data |
  | ⚠ Official weather warnings for your exact location | FMI CAP warnings |
  | ☀ Space weather (Kp index, solar flares) | NOAA SWPC |
  | 🌌 Local aurora alert (magnetometers, darkness and cloud cover aware) | FMI magnetometer network |
  | ☢ Radiation monitoring | STUK (Radiation and Nuclear Safety Authority of Finland) |
  | 🚧 Road traffic announcements by radius, region or road number | Fintraffic Digitraffic |

  Messages are condensed to fit a single 67-character APRS/DMR text message, e.g.
  `Tie 101 Espoo, Kalevalantie: Liikennevalot, epäkunnossa` or
  `Keltainen liikennesää: lumi/räntä, to 14–pe 12 (30 %)`.
- **Satellite passes** – upcoming passes of the ISS and active amateur radio satellites (CelesTrak orbits, SatNOGS DB
  frequencies, SGP4) with visible ISS passes and alerts before rise. The *Satellites* tab shows them on a world map
  (position, ground track, coverage circle, day/night) with a sky plot of the next pass.

### Propagation

- **Where you were heard** – live PSK Reporter reports of your FT8, FT4, JS8 and other digital transmissions and Reverse
  Beacon Network (RBN) spots of your CW/RTTY signal, drawn on a world map as great-circle lines coloured by band.
- **Which bands are open** – what is heard in your own 4-character locator square and where stations from there are heard
  (PSK Reporter), plus what the skimmers in your country hear (RBN); a *Bands now* table with reports, the farthest
  station and the main directions per band. Filters for time window, bands and mode.
- **VHF/UHF conditions** – the program's own approximate tropo estimate around your home, calculated from the Open-Meteo
  weather model (refractivity gradient in the lowest pressure levels, 72-hour outlook), the NOAA OVATION aurora oval,
  your own APRS station as a tropo meter (farthest station heard directly vs. usual) and the Kp index / aurora level.
- Layers like on the APRS map (heard me, HF and VHF/UHF reports, tropo estimate, aurora oval, day/night line) and
  optional alerts when 6 m, 4 m, 2 m or 70 cm opens from your area or the tropo estimate turns strong.
- The program subscribes only to reports about your own callsign and locator square (PSK Reporter MQTT feed by
  M0LTE, data by N1DQ) and logs in to RBN with your callsign; nothing is sent. RBN locations are derived from the
  callsign prefix at country level. For detailed tropo maps the tab links to Hepburn's tropo forecast.

![Propagation](docs/screenshot-propagation.png)

### App

- **Browser access on the local network** (App → Browser access on the local network, off by default) – use the
  program from your phone, tablet or another computer on the home network. Remote devices log in with a password
  (at least 8 characters); choose *Full access* or *View only*. A remote device can never close or update the program,
  change keys, passwords, server and feed addresses or these settings, or download the feedback log bundle. The page
  lists every address of the computer with its adapter name (virtual and VPN adapters marked, plus
  *computer-name.local*) and a QR code for each. A login survives restarts and updates for 30 days; *Log out remote
  devices* or a password change invalidates every login. On Windows the page shows the firewall status and an
  *Allow in firewall* button (asks for administrator approval). The connection is unencrypted HTTP – never forward the
  port to the internet.

  **If a remote device can't connect:** first open the address on the program's computer; check that the Windows
  network profile is *Private*, not *Public*; try the other listed addresses (`ipconfig` in a command prompt shows the
  computer's addresses); make sure the device isn't on a guest network or behind the router's AP isolation.
- **English, Finnish, German (Deutsch), Spanish (Español) and French (Français)** – choose the language on the App tab; on first start it follows the
  operating system language (English otherwise). It covers the user interface, the program's own alerts and the
  replies sent to your radio (radio commands: `CMD`, `WHO`, `NOW`, in Finnish also `OHJE`, `KUKA`, `NYT`). The German,
  Spanish and French translations are machine-made – corrections are welcome through the feedback function. FMI warnings and Digitraffic
  bulletins stay in Finnish, their source language.
- **Light, dark or automatic theme** (follows the operating system).
- **Automatic updates** – the app checks GitHub for a new version once a day (can be turned off). On Windows
  (installer), macOS and Linux, **Update now** downloads the new version, verifies it against the release's
  `SHA256SUMS.txt`, installs it and restarts the app; the window or browser tab comes back by itself. Test versions (beta) are
  offered when *Install test versions too (beta)* is selected, otherwise only stable releases.

Several services use Finnish open data and are most useful in Finland; the BrandMeister features work worldwide.

![Services](docs/screenshot-services.png)

## Download

[Code signing policy](CODE_SIGNING.md) · [Privacy policy](PRIVACY.md)

Get the latest version from **[Releases](https://github.com/hannulma/monitor-for-amateur-radio/releases)**:

| OS | File |
|---|---|
| Windows 10/11 | `MonitorForBrandMeister-<version>-windows-setup.exe` (installer) or `…-windows-portable.zip` |
| macOS 11+ (Intel and Apple Silicon M1–M4) | `MonitorForBrandMeister-<version>-macos-universal.dmg` |
| Linux x86_64 | `MonitorForBrandMeister-<version>-linux-x86_64.tar.gz` |
| Any OS with Python ≥ 3.9 | `pip install monitor_for_brandmeister-<version>-py3-none-any.whl` |

The app runs a small local web server (`http://127.0.0.1:8765/`, nothing is exposed to your network). On Windows
and macOS the user interface opens in **its own window**. Closing the window keeps the app running in the
background – monitoring and alerts continue, and the window comes back from the icon in the notification area
(Windows) or menu bar (macOS). Quit from the icon's menu, **App → Quit**, or Cmd+Q on macOS. You can choose
the browser instead on the **App** tab (*Appearance → User interface*). Tabs you don't use can be hidden there too (*Appearance → Visible tabs*); on Linux the app always uses the browser.
Starting the app again while it is running just shows the window or page.

### First launch (unsigned builds)

The builds are not code-signed (signing certificates cost money for a hobby project):

- **Windows:** SmartScreen may say *“Windows protected your PC”* → click **More info → Run anyway**.
- **macOS:** open the `.dmg`, drag the app to *Applications*. On first launch macOS refuses to open it →
  open **System Settings → Privacy & Security** and click **Open Anyway** (or right-click the app → *Open*).
- **Linux:** `tar xzf MonitorForBrandMeister-*.tar.gz && ./monitor-for-brandmeister/monitor-for-brandmeister`

### Install with pip

```bash
pip install monitor_for_brandmeister-<version>-py3-none-any.whl   # from Releases
monitor-for-brandmeister            # or: python -m bmmonitor
```

Options: `--port 9000`, `--no-browser` (in window mode: start in the background), `--browser` (browser
instead of the app window), `--demo` (fake traffic, no network), `--diag` (show raw data from
BrandMeister), `-v` (verbose).

## Quick start

1. **Talkgroups:** *BrandMeister* tab → *Settings* (at the bottom) → enter talkgroups separated by spaces, e.g. `244* 91 3100`.
   `*` matches any digits, `?` exactly one.
2. **Listen:** click the speaker button next to a talkgroup.
3. **Friends:** *Friends* → add callsigns or DMR IDs, allow desktop notifications.
4. **Devices:** *Devices* → add your hotspot/repeater by ID or search by callsign.
5. **Alerts on your radio (optional):** *APRS* tab → *Settings* → set an APRS-IS login (your callsign with an
   unused SSID, e.g. `OH2ABC-13`) and the recipient = your radio's APRS SSID configured in
   BrandMeister SelfCare (e.g. `OH2ABC-7`), then *Send test message*.
6. **Services:** *Services* → set your location (coordinates or Maidenhead locator) and switch on the services you want. Weather warnings can come from the Finnish Meteorological Institute or MeteoAlarm (38 European countries), and the aurora alert from FMI magnetometers or the NOAA OVATION forecast (worldwide). Satellite passes lists upcoming passes of the ISS and active amateur radio satellites (CelesTrak orbits, SatNOGS DB frequencies, SGP4) with visible ISS passes and alerts before rise. The *Satellites* tab shows the satellites chosen for alerts on a world map (position, ground track, coverage circle, day/night, next in-range window in the name tag) and a sky plot of the next pass. Base map: Natural Earth (public domain).
7. **APRS radios (optional):** *APRS* tab → *Settings* → *Radios and TNCs* → add Graywolf/Dire Wolf (KISS TCP or AGWPE),
   a KISS TNC on a serial port or a Kenwood/Kantronics TNC.
8. **Log:** *Logs* tab → *Settings* → own callsign and locator; optionally QRZ Logbook, eQSL and HamQTH.

### Commands from your radio

Send a private text message to the BrandMeister APRS gateway of your country (*country code + 999*, e.g.
**244999** in Finland) in the form `<app APRS login> <command>`, e.g. `OH2ABC-13 TG244`.

| Command | Answer |
|---|---|
| `CMD` (`OHJE`) | list of commands |
| `TG244` | who is talking now / talked last, when and how long |
| `WHO OH2ABC` (`KUKA`) | when and where the station was last heard |
| `NOW` (`NYT`) | active transmissions in your monitored talkgroups |
| feed command, e.g. `SAA` | latest item of that feed |

Only your own callsign and the configured recipients may send commands by default.

## Privacy

Everything runs on your own computer. Settings, logs and statistics are stored locally:

- Windows: `%APPDATA%\BMMonitor\`
- macOS: `~/Library/Application Support/BMMonitor/`
- Linux: `~/.config/BMMonitor/`

Your optional BrandMeister API key and own APRS-IS passcode are kept in the operating system's keychain (Windows Credential Manager, macOS Keychain); where none is available, in `config.json` in that folder.

No telemetry or analytics. The [privacy policy](PRIVACY.md) lists every service the program contacts when you enable
the corresponding feature, and how to switch each one off.

## Code signing policy

The Windows builds are being prepared for free code signing by the SignPath Foundation (application pending; until
then the builds are unsigned). Signed releases will read: *Free code signing provided by
[SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).*
Team roles, what is signed and how: [CODE_SIGNING.md](CODE_SIGNING.md).

## Data sources and APIs

BrandMeister Last Heard (socket.io) and Halligan API v2, BrandMeister Hoseline audio, APRS-IS, FMI open data,
NOAA SWPC, STUK (via FMI open data), Fintraffic Digitraffic (roads and marine AIS), aisstream.io. See [docs/TECHNICAL.md](docs/TECHNICAL.md) for
endpoints, protocols and implementation notes.

Please be considerate with public services: the app uses conservative polling intervals and the same public
interfaces as the official web pages.

## Development

```bash
git clone https://github.com/hannulma/monitor-for-amateur-radio
cd monitor-for-amateur-radio
pip install -e ".[test]"
pytest
python -m bmmonitor --demo        # UI with fake traffic
```

**Releasing:** bump `__version__` in `bmmonitor/__init__.py` and push to `main`. When the version has not been
released yet, GitHub Actions builds the Windows installer, macOS disk
image (universal2: Intel and Apple Silicon), the Linux binary and the Python package, creates the tag `v<version>` and publishes a release
(versions like `0.9.0b1` are marked as pre-releases). Installed apps pick the new version up through the
built-in update check.

## Disclaimer

This is an independent hobby project and is **not affiliated with, endorsed by or supported by the
BrandMeister team**. BrandMeister is the name of its respective owners. Use the network and its interfaces
according to the [BrandMeister policies](https://wiki.brandmeister.network/index.php/Policies_and_Rules) and
your amateur radio licence.

## License

[MIT](LICENSE) © 2026 hannulma
