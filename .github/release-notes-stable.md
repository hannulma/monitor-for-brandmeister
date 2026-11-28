**Vakaa versio 0.18.0.** Tämän version tärkeimmät uutuudet ovat Radiosää-välilehti ja selainkäyttö lähiverkossa (tabletti ja puhelin).
*Stable release 0.18.0 – a Propagation tab and browser access over the local network (tablet and phone).*

### Uutta 0.17.0:n jälkeen / New since 0.17.0

- **Radiosää:** uusi välilehti, joka näyttää maailmankartalla, mille bandeille keli on auki ja missä sinut on kuultu. Lähteinä PSK Reporter (FT8, FT4 ym.) ja Reverse Beacon Network (CW, RTTY), lisäksi oma VHF/UHF-tropoarvio Open-Meteon säädatasta, revontuliovaali, yö–päiväraja, *Bandit nyt* -yhteenveto, APRS-kuuluvuus tropomittarina ja valinnaiset hälytykset (esim. 6 m auki, tropo voimakas).
- **Selainkäyttö lähiverkossa:** ohjelmaa voi käyttää tabletilta tai puhelimelta samassa kotiverkossa (Ohjelma → Selainkäyttö lähiverkossa). Salasana, käyttöoikeus *Täysi käyttö* tai *Vain katselu*, QR-koodi jokaiselle koneen osoitteelle, kirjautuminen säilyy ohjelman uudelleenkäynnistyksen yli. Etälaitteelta ei voi sulkea ohjelmaa, nähdä tai vaihtaa avaimia ja salasanoja eikä muuttaa palvelin- ja syöteosoitteita. Windowsissa palomuurin tila ja *Salli palomuurissa* -painike. Yhteys on salaamaton HTTP – älä avaa porttia internetiin.
- **Yhteysehdokkaat** säilyvät uudelleenkäynnistyksen ja päivityksen yli, kunnes lisäät tai ohitat ne (enintään 7 päivää). Puheryhmässä ehdokas syntyy, kun puhut, toinen asema vastaa ja puhut vielä uudelleen – check-in-netin pelkkä kuittaus ei enää tee ehdokasta.
- Korjauksia: Windows-koneelta lähiverkon laitteille lähetetyt tiedostot saattoivat sotkeutua, jolloin iPad, iPhone tai toisen koneen selain jäi tilaan *Yhdistetään…*; puhelinnäkymän yläpalkki; Radiosään kartan viivat päivämäärärajan yli.

*Propagation tab: a world map of open bands and where you were heard, from PSK Reporter (FT8, FT4 …) and the Reverse Beacon Network (CW, RTTY), plus a VHF/UHF tropo estimate from Open-Meteo weather data, the aurora oval, the day/night terminator, a Bands now summary, APRS reception as a tropo meter and optional alerts (e.g. 6 m open, strong tropo). Browser access over the local network from a tablet or phone (App → Browser access on the local network): password, Full access or View only, a QR code for every address of the computer, logins survive restarts; a remote device can't close the app, see or change keys and passwords, or change server and feed addresses. On Windows the firewall status and an Allow in firewall button. The connection is unencrypted HTTP – never open the port to the internet. Contact candidates are kept across restarts and updates until you add or skip them (at most 7 days); on a talkgroup a candidate needs you to speak, the other station to answer and you to speak again – a check-in net acknowledgement alone no longer counts. Fixes: files sent from a Windows computer to devices on the local network could get corrupted, leaving an iPad, iPhone or another computer's browser at "Connecting…"; the phone layout header; Propagation map paths across the date line.*

### Lataa / Download

| Käyttöjärjestelmä / OS | Tiedosto / File |
|---|---|
| Windows 10/11 – asennusohjelma / installer | `MonitorForBrandMeister-*-windows-setup.exe` |
| Windows – ilman asennusta / portable | `MonitorForBrandMeister-*-windows-portable.zip` |
| macOS 11+ (Intel ja Apple Silicon M1–M4 / *Intel and Apple Silicon*) | `MonitorForBrandMeister-*-macos-universal.dmg` |
| Linux x86_64 | `MonitorForBrandMeister-*-linux-x86_64.tar.gz` |
| Python (pip) | `monitor_for_brandmeister-*.whl` |

Ohjelma päivittyy automaattisesti, jos päivitysten tarkistus on päällä (Ohjelma → Versio ja päivitykset).
*The app updates itself if update checks are on (App → Version and updates).*

[Code signing policy](https://github.com/hannulma/monitor-for-amateur-radio/blob/main/CODE_SIGNING.md) · [Tietosuoja / Privacy](https://github.com/hannulma/monitor-for-amateur-radio/blob/main/PRIVACY.md)

Ohjelmat eivät (vielä) ole allekirjoitettuja: Windows voi näyttää SmartScreen-varoituksen (*Lisätietoja → Suorita silti*) ja macOS vaatii ensimmäisellä kerralla avaamisen *Järjestelmäasetukset → Tietosuoja ja turvallisuus → Avaa silti*.
*The binaries are unsigned; see README for first-launch instructions.*
