# Monitor for Amateur Radio

**Työpöytäsovellus radioamatöörin asemalle** – samassa ohjelmassa [BrandMeister](https://brandmeister.network)-DMR-verkon
seuranta, **APRS** omien radioiden ja TNC:iden kautta (viestit, kartta asemasi kuulemista asemista, RF-kuuluvuus ja digit),
**yhteysloki** QRZ Logbook-, eQSL- ja HamQTH-lähetyksellä, **check-in-netit** yhteiseen Google-taulukkoon, **satelliittien ylilennot** sekä
avoimen datan **hälytykset** (säävaroitukset, avaruussää, revontulet, salamat, säteily, tieliikenne) tekstiviesteinä radioosi.
Windows, macOS ja Linux; kaikki toimii omalla koneellasi.

Ohjelma sai alkunsa BrandMeister-puheryhmien seurannasta (*Monitor for BrandMeister*); julkaisutiedostot ja ohjelman
sisäiset nimet käyttävät edelleen sitä, jotta päivitykset ja tallennetut asetukset toimivat.

🇬🇧 [English README](README.md)

> [!NOTE]
> **Vakaa versio 0.17.0.** Uusimmat ominaisuudet tulevat ensin testiversioihin (beta), jotka voi ottaa käyttöön
> kohdasta *Ohjelma → Versio ja päivitykset*. Virheilmoitukset ja palaute:
> [Issues](https://github.com/hannulma/monitor-for-amateur-radio/issues).

![Seuranta](docs/screenshot-monitor-fi.png)

## Lataus ja asennus

Lataa uusin versio **[Releases](https://github.com/hannulma/monitor-for-amateur-radio/releases)**-sivulta
([Code signing policy](CODE_SIGNING.md) · [tietosuoja](PRIVACY.md)):

| Käyttöjärjestelmä | Tiedosto |
|---|---|
| Windows 10/11 | `MonitorForBrandMeister-<versio>-windows-setup.exe` (asennusohjelma) tai `…-windows-portable.zip` (ilman asennusta) |
| macOS 11+ (Intel ja Apple Silicon M1–M4) | `MonitorForBrandMeister-<versio>-macos-universal.dmg` |
| Linux x86_64 | `MonitorForBrandMeister-<versio>-linux-x86_64.tar.gz` |
| Python 3.9+ (mikä tahansa käyttöjärjestelmä) | `monitor_for_brandmeister-<versio>-py3-none-any.whl` |

Ohjelma käynnistää koneellesi pienen paikallisen palvelimen (`http://127.0.0.1:8765/`, mitään ei avata verkkoon).
Windowsissa ja macOS:ssä käyttöliittymä avautuu **omaan ikkunaansa**. Ikkunan sulkeminen jättää ohjelman taustalle:
seuranta ja hälytykset jatkuvat, ja ikkunan saa takaisin kuvakkeesta (Windows: ilmaisinalue, macOS: valikkorivi).
Kokonaan ohjelma suljetaan kuvakkeen valikosta, kohdasta **Ohjelma → Sulje ohjelma** tai macOS:ssä Cmd+Q:lla.
Selaimessa käyttämisen voi valita kohdassa **Ohjelma → Ulkoasu → Käyttöliittymä**; Linuxissa ohjelma avautuu
aina selaimeen. Jos ohjelma on jo käynnissä, uusi käynnistys vain avaa ikkunan tai sivun.

### Ensimmäinen käynnistys

Ohjelmia ei ole allekirjoitettu (allekirjoitusvarmenteet maksavat), joten käyttöjärjestelmä varoittaa:

- **Windows:** SmartScreen näyttää *"Windows suojasi tietokonettasi"* → **Lisätietoja → Suorita silti**.
  Asennusohjelma ei vaadi järjestelmänvalvojan oikeuksia, ja siinä voi valita käynnistyksen Windowsin mukana.
- **macOS:** avaa `.dmg` ja vedä ohjelma *Ohjelmat*-kansioon. Ensimmäisellä käynnistyksellä macOS estää
  avaamisen → **Järjestelmäasetukset → Tietosuoja ja turvallisuus → Avaa silti** (tai oikea klikkaus → *Avaa*).
- **Linux:** `tar xzf MonitorForBrandMeister-*.tar.gz && ./monitor-for-brandmeister/monitor-for-brandmeister`

### Asennus pipillä

```
pip install monitor_for_brandmeister-<versio>-py3-none-any.whl
monitor-for-brandmeister        (tai: python -m bmmonitor)
```

Valitsimet: `--port 9000`, `--no-browser` (omassa ikkunassa: käynnisty taustalle), `--browser` (selain oman
ikkunan sijaan), `--demo` (keksitty testiliikenne), `--diag` (näyttää mitä
BrandMeister lähettää), `-v` (enemmän lokia).

## Välilehdet

Välilehdet on ryhmitelty ohuilla pystyviivoilla: **BrandMeister · Tilastot · Kaverit · Laitteet · Check-in │ APRS · Syötteet │ Lokit │ Palvelut · Satelliitit · Radiosää │ Ohjelma**.

### BrandMeister
- **Puheryhmät:** lähetys juuri nyt (vihreä LÄHETYS), viimeisin yhteys, puhuja, kesto, toistin/hotspot,
  avausten määrä ja yhteenlaskettu puheaika. Välilehden nimen perässä näkyy antennikuvake, kun jossakin seuratussa
  ryhmässä on lähetys käynnissä. Kaiutinpainike aloittaa ryhmän **kuuntelun** (useita ryhmiä
  voi kuunnella yhtä aikaa; ryhmiä 1–90 ei voi kuunnella).
- **Viimeisimmät puhelut:** 10, 25, 50 tai 100 viimeisintä, suodatettavissa puheryhmän mukaan. Käynnissä olevat lähetykset näkyvät
  listan ylimpinä LÄHETYS-merkillä ja kasvavalla kestolla; päättyessään ne siirtyvät tavallisiksi riveiksi.
- **Kirjaa yhteys (QSO) lokiin:** kun puhut, toinen asema vastaa ja puhut vielä uudelleen samassa puheryhmässä (oma–toinen–oma, vuorojen väli enintään 3 min; pelkkä check-in-netin kuittaus ei riitä) tai
  pidätte yksityispuhelun, yhteys näkyy ehdokkaana. *Lisää lokiin* avaa valmiiksi täytetyn lomakkeen (tila DMR); *Ohita* poistaa ehdokkaan.
  Ehdokkaat odottavat (myös ohjelman uudelleenkäynnistyksen yli), kunnes lisäät tai ohitat ne, kuitenkin enintään 7 päivää.
  Loki tallentuu oletuksena paikallisesti; lähetys QRZ.comiin, eQSL:ään ja HamQTH:hon on valinnainen.
- **Netit tänään ja huomenna:** FinDMR-kierros ([6kw.fi](https://6kw.fi/findmr/)), W0WC:n BrandMeister-nettikalenteri
  ja omat ICS-kalenterit sekä oma lista. Ajat omassa ajassa, "käynnissä – liikennettä" kun netin puheryhmässä
  puhutaan, kuuntelu- ja check-in-painikkeet, muistutus ennen seurattujen puheryhmien nettejä.

### Lokit
- **Loki:** omat yhteydet (aika UTC, kutsu, nimi, kaista, tila, raportit, kommentti), haku, suodatus tilan mukaan,
  muokkaus ja poisto, yhteyden lisäys käsin sekä vienti **ADIF**- (.adi) ja CSV-tiedostoksi.
- **Tila (mode):** pikapainikkeet SSB · CW · AM · FM · RTTY · PSK31 · D-STAR · DMR · C4FM sekä valikko kaikista ADIF-tiloista
  (esim. FT8, FT4, JS8, SSTV, M17). Digitaaliset puhetilat viedään ADIF-muodossa DIGITALVOICE / DMR, DSTAR tai C4FM ja PSK31 muodossa PSK / PSK31.
  Raportin oletus vaihtuu tilan mukaan: puhe 59, CW ja datatilat 599. BrandMeisterista tunnistetut yhteydet ovat aina DMR.
- **Etenemistapa (PROP_MODE):** BrandMeisterista tunnistetut yhteydet merkitään internet-avusteisiksi (ADIF `PROP_MODE=INTERNET`),
  samoin käsin lisätyt DMR-, D-STAR-, C4FM- ja M17-yhteydet oletuksena; valittavissa myös toistin, satelliitti ja EchoLink.
  Lokin voi suodattaa etenemistavan mukaan, ja jokaisessa palvelussa voi valita *Älä lähetä internetin kautta pidettyjä
  yhteyksiä* (ne eivät kelpaa useimpiin diplomeihin; ohitetun voi lähettää käsin ↻-painikkeella).
- **Asetukset** (välilehden alimpana): oma kutsu ja lokaattori, oletusraportti, -kaista ja -tila (käsin lisättävät), tunnistus päälle/pois sekä QRZ Logbook, eQSL ja HamQTH.
- **QRZ Logbook:** lokiin lisätyt yhteydet lähetetään QRZ.comin lokikirjaan (vaatii QRZ-tilauksen, XML-taso tai ylempi).
  API-avain tallennetaan salasanavarastoon. Jokaisen yhteyden lähetystila näkyy lokissa; epäonnistuneet voi lähettää uudelleen.
- **eQSL:** lokiin lisätyt yhteydet lähetetään eQSL.cc:hen (ilmainen tili): käyttäjätunnus, salasana ja valinnainen QTH-lempinimi.
  **eQSL:n rajapinta ei hyväksy yli 14 merkin salasanaa** – vaihda tarvittaessa lyhyempi salasana eQSL.cc:ssä (ohjelma varoittaa).
  Muokkaukset eivät päivity eQSL:ään jälkikäteen.
- **HamQTH:** lokiin lisätyt yhteydet lähetetään HamQTH.comin lokikirjaan heti; myös muokkaukset ja poistot päivittyvät. Tarvitset
  oman API-avaimen (HamQTH → Settings → Apps and API keys, alkaa `hqk_`); salasanaa ei tarvita. *Lokin lähetys: [HamQTH.com](https://www.hamqth.com/)*
  – HamQTH toivoo, että rekisteröidyt ja pidät profiilisi ajan tasalla.

![Loki](docs/screenshot-logs-fi.png)

### APRS
- **APRS-viestit:** kutsusi kaikkien SSID:iden saapuvat ja lähtevät APRS-viestit, myös BrandMeisterin kautta
  DMR-radiosta lähtevät. Viestiä klikkaamalla voit vastata suoraan (valittavissa millä SSID:llä). Uusien viestien
  määrä näkyy APRS-välilehden nimen perässä; työpöytäilmoituksen voi ottaa käyttöön APRS-asetuksista.
  Halutessasi viestit, kuittaukset ja komentojen vastaukset lähtevät myös radiolla
  (APRS → Asetukset → APRS-viestit radiolla; reitti automaattinen / APRS-IS / radio / radio ja APRS-IS, oletuksena pois).
- **Radiot ja TNC:t:** yksi tai useampi radio yhtä aikaa (APRS → Asetukset → Radiot ja TNC:t): **KISS TCP** (Graywolf,
  Dire Wolf; oletus `127.0.0.1:8001`), **KISS TNC sarjaportissa** (USB tai Bluetooth, esim. Mobilinkd, Kenwood TH-D74/D75
  KISS-tilassa), **Kenwood TM-D710 / TH-D72** ja **Kantronics KPC-3+** (kokeellinen: TNC kytketään KISS-tilaan
  automaattisesti), **AGWPE** (Dire Wolf, soundmodem) ja **simuloitu radio** testaukseen. Äänikortin kautta liitetylle radiolle
  käytä Graywolfia tai Dire Wolfia. Lähetykseen käytetään ensimmäistä yhdistettyä radiota, jolle lähetys on sallittu.
- **APRS-kartta:** radioiden kuulemista paketeista.
  Kartan tasot (24 h / 7 vrk / 30 vrk): **RF-kuuluvuus** – lämpökartta siitä, mistä asemasi kuulee paketteja
  suoraan ja mistä digipiitterien kautta; **APRS-asemat** – radiolla kuultujen asemien viimeisin sijainti (iGaten radioon välittämät asemat näkyvät onttoina
  eivätkä ole mukana RF-kuuluvuudessa); **Digipiitterit** – digiä klikkaamalla näkyy sen kautta kuultu alue;
  **Sääasemat** – lämpötila, tuuli, kosteus, ilmanpaine ja sade; **Objektit** – objektit ja itemit (esim. toistimet);
  **APRS-IS-asemat** – valinnainen, APRS-IS:n asemat kotisijaintisi ympäriltä (säde asetuksissa). **Laivat (AIS)** – valinnainen,
  laivat kotisijaintisi ympäriltä Fintrafficin Digitrafficista (Suomi ja Itämeri, ei avainta) tai aisstream.io:sta (koko maailma, ilmainen avain);
  näytettävät alustyypit valitaan kartan yläpuolelta *Laivat*-valikosta, ja laiturissa tai ankkurissa olevat voi piilottaa. Taulukoissa digien osuudet ja yleisimmät polut. Hälytys, kun tuttu digi tai valitsemasi asema
  (esim. iGate) hiljenee – vain silloin, kun oma vastaanotto toimii. Paketit säilytetään koneella 30 vrk.
  Karttapohja: OpenStreetMap (© OpenStreetMap contributors), tiilet haetaan ohjelman kautta omalla
  tunnisteella ja välimuistitetaan; osoitteen voi vaihtaa.
- **APRS-IS-yhteys ja vastaanottajat:** oma APRS-IS-tunnus, passcode, palvelin, radioiden kutsut,
  viestiasetukset ja testiviesti.
- **Komennot radiosta:** ks. alempana.

![APRS-kartta ja laivat](docs/screenshot-aprs-fi.png)

### Kaverit
Kaverilistan kutsut tai DMR-ID:t. Hälytys (työpöytäilmoitus, äänimerkki, halutessa tekstiviesti radioon),
kun kaveri avaa missä tahansa verkossa tai vain seuratuissa ryhmissä. *Hiljaisuusaika* estää toistuvat
hälytykset saman yhteyden aikana. Välilehdellä on myös kaverien tila ja kaikkien hälytysten loki.

### Laitteet
Seurattavat toistimet ja hotspotit (ID tai haku kutsulla): tila, viimeksi nähty, pysyvät ja dynaamiset
puheryhmät, viimeisin liikenne. Hälytys, kun laite putoaa verkosta tai palaa. **BrandMeister API-avaimella**
(BrandMeister → Asetukset) omaa laitetta voi hallita: pysyvän puheryhmän lisäys/poisto, dynaamisten tyhjennys, puhelun
katkaisu ja yhteyden nollaus. Avain luodaan BrandMeister SelfCaressa.

### Tilastot
Puhelut tunneittain/päivittäin (14 päivän historia), eniten puhuneet, vilkkaimmat ryhmät sekä BrandMeisterin
oma historiakysely: ryhmässä kuullut asemat puhekertoineen.

![Tilastot](docs/screenshot-stats-fi.png)

### Check-in
Viikoittaisten check-in-nettien (esim. FinDMR TG 244, Viro TG 248, Worldwide TG 91) kirjaamiseen.
Aloita kierros valitsemalla netti, kierrosnumero, päivämäärä ja operaattori. Puheryhmässä avaavat asemat
tulevat listaan **saapumisjärjestyksessä, eikä lista koskaan järjesty uudelleen**: uusi avaus samalta
asemalta ei siirrä riviä, kirjattu rivi jää paikalleen himmennettynä ja lista ei vieritä itsestään
(uusista kertoo "↓ N uutta asemaa" -painike). Merkinnät: **LÄHETYS** (puhuu nyt), **päällekkäin** (avasi
toisen päälle eikä välttämättä kuulunut) ja **uusi**. Kun jo kirjattu asema lähettää uudelleen, lokin
yläpuolelle tulee ilmoitus (esim. "OH1X (NRO 5, jo kirjattu) lähettää nyt"); sitä klikkaamalla lista
vierittyy aseman riville.

Kun kuulet aseman, paina **Kuultu – kirjaa** tai kirjoita hakuun pari kutsun merkkiä ja paina Enter.
Lokinumero (NRO) annetaan kirjausjärjestyksessä. Kierroksen loki on samoissa sarakkeissa kuin FinDMR:n
kierrostaulukko: NRO, kutsu, RPT/HOTSPOT, KUNTA, Lisätietoja ja Oper. RPT/HOTSPOT ja KUNTA täytetään
BrandMeisterin laitetiedoista (toistin → tunnus ja paikkakunta, paikkakunta vain kerran; Tetra →
"TetraPack", Hyteran PoC → "POC", Yaesu Fusion → "Fusion", Open DMR Terminal → "Open DMR", muuten
"Hotspot"; maakohtaisessa netissä toisen maan asema → "Ulkomaat"), ja ne sekä Lisätietoja ovat muokattavissa. Taulukon alasvetovalikkoon
(Toistimet-data) puuttuva toistin lisätään listalle automaattisesti.

**Google-taulukko:** kun taulukon omistaja on lisännyt taulukkoon skriptin (ohjelmassa *Check-in → Google-taulukko → Ohje* on vaiheittainen ohje ja painikkeet skriptitiedostojen
kopiointiin; ks. myös [sheets/README.md](sheets/README.md)),
ylläpitäjä yhdistää ohjelman taulukkoon skriptin osoitteella ja kertakäyttöisellä parituskoodilla. Sen
jälkeen kirjaukset menevät taulukkoon heti, lokinumeron antaa taulukko ja useampi ylläpitäjä voi kirjata
samaan aikaan – kunkin ylläpitäjän kirjaukset näkyvät muillakin muutamassa sekunnissa. Jokaisella
ylläpitäjällä on oma avain (Windowsissa ja macOS:ssä käyttöjärjestelmän salasanavarastossa), pyynnöt
allekirjoitetaan, eikä taulukkoon voi kirjoittaa ilman omistajan lisäämää avainta.

Ilman taulukkoa **Kopioi uudet rivit** vie edellisen kopioinnin jälkeen kirjatut rivit leikepöydälle ilman
otsikkoriviä sarakkeisiin A–F: liitä ne Google-taulukon ensimmäiseen tyhjään riviin sarakkeeseen A. Lokin saa myös CSV-tiedostona, ja
päättynyt kierros tallentuu automaattisesti CSV:nä kansioon `checkin/`. Keskeneräinen kierros säilyy, vaikka
ohjelma käynnistettäisiin uudelleen.

### Palvelut
Jokaisella palvelulla on oma **päälle/pois-kytkin**; kaikki ovat oletuksena pois päältä. Aseta ensin
**oma sijainti** koordinaatteina (`60.17, 24.94`), lokaattorina (`KP20LE`) tai selaimen sijainnilla.

| Palvelu | Lähde | Asetukset |
|---|---|---|
| ⚡ Salamahälytys (vain Suomi) | Ilmatieteen laitos | säde, uuden hälytyksen väli (heti uudestaan, jos salamat lähestyvät yli 5 km), vain maasalamat |
| ⚠ Säävaroitukset | Ilmatieteen laitoksen CAP-varoitukset tai MeteoAlarm (38 Euroopan maata) | vähimmäistaso (keltainen/oranssi/punainen), liikennesää (IL); varoitus tulee kun sijaintisi on varoitusalueella. MeteoAlarmissa valitaan maa, ja jos maan varoituksissa ei ole aluerajoja, alue nimellä tai EMMA-tunnuksella |
| ☀ Avaruussää | NOAA SWPC | Kp-raja (oletus 5), auringonpurkaukset ≥ M5, NOAA:n K-ennusteet |
| 🌌 Revontulihälytys | Ilmatieteen laitoksen magnetometrit tai NOAA:n OVATION-ennuste (koko maailma, raja %, suurin arvo 300 km säteellä) | raja nT/h (raja = mahdollisia, 2× = todennäköisiä, 4× = voimakkaita), vain pimeällä, ei täysin pilvisellä; viestissä pilvisyys |
| ☢ Säteilyvalvonta (vain Suomi) | STUK (~250 asemaa) | raja (oletus 0,4 µSv/h = STUK:n hälytysraja), säde tai koko Suomi |
| 🚧 Tieliikenne (vain Suomi) | Fintraffic Digitraffic | säde oman sijainnin tai muun pisteen ympärillä, maakunnat, tienumerot, tiedotetyypit |
| 🛰 Satelliittien ylilennot | CelesTrak (ratatiedot), SatNOGS DB (taajuudet), SGP4-laskenta | ISS ja aktiiviset radioamatöörisatelliitit: seuraavat ylilennot suuntineen ja taajuuksineen, ISS:n näkyvät ylilennot, hälytys valituista satelliiteista (oletuksena ISS, 30 min ennen nousua, korkeuskulma ≥ 20°) |

**Satelliitit-välilehti** näyttää hälytettäviksi valitut satelliitit maailmankartalla: paikka reaaliajassa, maajälki, kuuluvuusalue, yö ja päivä sekä nimitagissa seuraava aikaväli, jolloin satelliitti on sijaintisi yhteysalueella. Klikattu satelliitti näytetään taivaskartalla (seuraavan ylilennon reitti ilmansuuntineen ja korkeuskulmineen). Karttaa voi zoomata (rulla, nipistys, +/−) ja siirtää raahaamalla, ja valittua satelliittia voi seurata. Karttapohja: Natural Earth (public domain).

Ensimmäisen tarkistuksen jälkeen hälytetään vain **uusista** tapahtumista; voimassa olevat näkyvät kortissa.
Viestit lyhennetään mahtumaan yhteen 67 merkin radioviestiin, esim.
`Tie 101 Espoo, Kalevalantie: Liikennevalot, epäkunnossa` tai
`Keltainen liikennesää: lumi/räntä, to 14–pe 12 (30 %)` ("nyt" = jo voimassa, "24" = keskiyöhön asti).
Koko teksti aukeaa ohjelmassa otsikkoa klikkaamalla.

![Palvelut](docs/screenshot-services-fi.png)

### Radiosää
- **Missä minut kuultiin** – PSK Reporterin reaaliaikaiset raportit FT8-, FT4-, JS8- ja muista digilähetyksistäsi sekä
  Reverse Beacon Networkin (RBN) CW- ja RTTY-havainnot maailmankartalla isoympyräviivoina bandin väreillä.
- **Mille bandeille keli on auki** – mitä omassa 4-merkkisessä lokaattoriruudussasi kuullaan ja mistä sieltä lähetetyt
  kuullaan (PSK Reporter) sekä mitä oman maasi skimmerit kuulevat (RBN). *Bandit nyt* -taulukossa raportit, kauimmainen
  asema ja pääsuunnat bandeittain. Suodattimet: aikaikkuna, bandit ja lähetysmuoto.
- **VHF/UHF-keli** – ohjelman oma, suuntaa antava tropoarvio kotisi ympäriltä Open-Meteon säämallista (refraktiivisuuden
  pystygradientti alimmilla painepinnoilla, ennuste 72 tuntia), NOAA:n OVATION-revontuliovaali, oma APRS-asemasi
  tropomittarina (kauimmainen suoraan kuultu verrattuna tavalliseen) sekä Kp ja revontulitaso.
- Tasot kuten APRS-kartassa (minut kuultiin, HF- ja VHF/UHF-raportit, tropoarvio, revontuliovaali, yö/päiväraja) ja
  valinnaiset hälytykset, kun 6 m, 4 m, 2 m tai 70 cm aukeaa omalta alueelta tai tropoarvio muuttuu voimakkaaksi.
- Ohjelma tilaa vain omaan kutsuusi ja lokaattoriruutuusi liittyvät raportit (PSK Reporterin MQTT-jakelu, M0LTE;
  data N1DQ) ja kirjautuu RBN:ään omalla kutsullasi; mitään ei lähetetä. RBN-asemien sijainti päätellään kutsun
  etuliitteestä maan tarkkuudella. Tarkempiin tropokarttoihin välilehdeltä on linkki Hepburnin tropoennusteeseen.

![Radiosää](docs/screenshot-propagation-fi.png)

### Syötteet
RSS-, Atom- ja JSON Feed -syötteet (esim. BrandMeisterin uutiset, SRAL): uuden uutisen otsikko lähetetään
radioon APRS-välilehdellä asetetuille vastaanottajille. Syötteiden tila ja viestiloki; syötteet ja viestiasetukset ovat välilehden alimpana Asetukset-osiossa.
Kullakin syötteellä on **etuliite** (näkyy viestin alussa), **osoite** ja valinnainen **komento**: kun lähetät radiosta
Brandmeisterin numeroon 244999 yksityisviestin `<oma APRS-IS-tunnus> <komento>`, ohjelma vastaa syötteen uusimmalla
otsikolla (vaatii *APRS → Asetukset → Komennot radiosta → Vastaa komentoihin*; `OHJE` listaa komennot).

### Asetukset ja Ohjelma
Toimintojen asetukset ovat kunkin välilehden alimmassa, pienennettävässä **Asetukset**-osiossa:
- **BrandMeister:** seurattavat puheryhmät, lyhyiden avausten ohitus, CSV-loki, oma kutsu ja BrandMeister API-avain.
- **APRS:** radiot ja TNC:t, APRS-viestit radiolla, APRS-IS-yhteys ja vastaanottajat, komennot radiosta.
- **Syötteet:** syötteet ja viestit radioon.
- **Lokit:** oma asema, oletustila ja tunnistus, QRZ Logbook, eQSL, HamQTH.

**Ohjelma**-välilehdellä ovat **kieli**, **Ulkoasu** (teema: vaalea, tumma tai käyttöjärjestelmän mukaan;
käyttöliittymä omassa ikkunassa tai selaimessa; **näytettävät välilehdet**), **päivitykset** sekä palaute, vikailmoitukset ja
**Sulje ohjelma**. Käyttämättömät välilehdet voi piilottaa; niiden toiminnot jatkavat taustalla, ja Ohjelma-välilehti on aina näkyvissä.

- **Selainkäyttö lähiverkossa** (Ohjelma → Selainkäyttö lähiverkossa, oletus pois) – ohjelmaa voi käyttää puhelimella,
  tabletilla tai toisella koneella kotiverkossa. Etälaitteelta kirjaudutaan salasanalla (vähintään 8 merkkiä);
  käyttöoikeudeksi *Täysi käyttö* tai *Vain katselu*. Etälaitteelta ei voi koskaan sulkea tai päivittää ohjelmaa,
  muuttaa avaimia, salasanoja, palvelin- ja syöteosoitteita tai näitä asetuksia eikä ladata palautteen lokipakettia.
  Sivulla näkyvät kaikki koneen osoitteet sovittimen nimen kanssa (virtuaali- ja VPN-sovittimet merkittyinä, myös
  *koneen-nimi.local*) ja QR-koodi kullekin. Kirjautuminen säilyy ohjelman uudelleenkäynnistyksen ja päivityksen yli
  30 päivää; *Kirjaa etälaitteet ulos* tai salasanan vaihto mitätöi kaikki kirjautumiset. Windowsissa sivu näyttää
  palomuurin tilan ja painikkeen *Salli palomuurissa* (pyytää järjestelmänvalvojan luvan). Yhteys on salaamaton
  HTTP – älä avaa porttia reitittimestä internetiin.

  **Jos etälaite ei saa yhteyttä:** avaa osoite ensin ohjelman koneella selaimeen; tarkista, että Windowsin
  verkkoprofiili on *Yksityinen* eikä *Julkinen*; kokeile muita listattuja osoitteita (komentokehotteessa `ipconfig`
  näyttää koneen osoitteet); varmista, ettei laite ole vierasverkossa tai reitittimen AP-eristyksen takana.
- **Kieli (suomi / English / Deutsch / Español / Français)** koskee käyttöliittymää, ohjelman omia hälytyksiä ja radioon lähetettäviä vastauksia.
  Ensimmäisellä käynnistyksellä kieli valitaan käyttöjärjestelmän kielen mukaan (muuten englanti). Radiokomennot toimivat
  suomeksi ja englanniksi (`OHJE`/`CMD`, `KUKA`/`WHO`, `NYT`/`NOW`; saksaksi, espanjaksi ja ranskaksi käytetään englanninkielisiä). Saksan-, espanjan- ja ranskankielinen
  käännös on konekäännös – korjausehdotukset ovat tervetulleita palautetoiminnolla. Ilmatieteen laitoksen varoitukset ja
  Digitrafficin tiedotteet ovat lähteen kielellä (suomeksi).
- **Palaute / lokit** (Ohjelma-välilehti) lähettää viestin ja zip-paketin ylläpidolle. Salaisuudet (API-avain, passcode,
  check-in-avain) poistetaan ja sijainti pyöristetään lokaattoriksi; paketin voi ladata ja tarkistaa ennen lähetystä.
- **Mittayksiköt (km / mailit)** koskevat kaikkia näytettyjä etäisyyksiä ja nopeuksia sekä hälytyksiä. Asetukset
  tallennetaan aina kilometreinä, joten yksikön vaihto ei muuta säteitä.
- **Päivitykset:** ohjelma tarkistaa GitHubista kerran päivässä, onko uusi versio saatavilla (voi kytkeä pois).
  Windowsissa (asennusohjelmalla asennettu), macOS:ssä ja Linuxissa **Päivitä nyt** lataa uuden version,
  tarkistaa sen julkaisun `SHA256SUMS.txt`-tarkistussummaa vasten, asentaa sen ja käynnistää ohjelman uudelleen –
  ikkuna tai selainvälilehti avautuu uudelleen itse. Testiversiot (beta) tarjotaan, jos *Asenna myös testiversiot (beta)* on valittu,
  muuten vain vakaat versiot. Asennuksettomassa Windows-versiossa ja pip-asennuksessa näytetään latauslinkki tai komento.

| Merkintä | Tarkoittaa |
|---|---|
| `91` | täsmälleen TG 91 |
| `244*` | kaikki 244-alkuiset: 244, 2441, 24420 … |
| `244?` | 244 ja yksi numero perään: 2440–2449 |

## Viestit radioon (APRS ↔ DMR)

Viestit kulkevat APRS-IS-verkon kautta. BrandMeisterin APRS-yhdyskäytävä välittää APRS-viestin
DMR-tekstiviestinä radiolle, jonka DMR-ID:hen kutsu ja SSID on liitetty SelfCaressa.

1. **BrandMeister SelfCare:** liitä radiosi DMR-ID:hen APRS-SSID (esim. 7) → radion tunnus on `OH2ABC-7`.
   *Text Capture* pitää viestin tallessa, vaikka radio olisi pois päältä.
2. **Ohjelman APRS-IS-tunnus:** oma kutsu ja SSID, jota mikään muu ohjelma ei käytä (esim. `OH2ABC-13`).
   Jos esim. Graywolf käyttää samaa tunnusta, APRS-IS katkaisee toisen yhteyden.
3. **Passcode:** jätä tyhjäksi, niin se lasketaan kutsusta.
4. **Vastaanottajat:** radion tunnus (esim. `OH2ABC-7`), voi olla useita.
5. **Tallenna** ja **Lähetä testiviesti**.

Viestiloki näyttää tilan: *kuitattu*, *ei kuittausta* (kolme yritystä) tai *hylätty*. Asetus
**Älä odota kuittausta** lähettää viestin kerran ilman viestinumeroa (ei koskaan kahdesti, mutta
perillemenoa ei voi seurata).

### Komennot radiosta

Lähetä radiosta yksityisviesti BrandMeisterin APRS-palvelunumeroon (maakoodi + 999, Suomessa **244999**)
muodossa `<ohjelman APRS-IS-tunnus> <komento>`, esim. `OH2ABC-13 TG244`.

| Komento | Vastaus |
|---|---|
| `OHJE` | komentolista |
| `TG244` | kuka puhuu nyt tai puhui viimeksi, milloin ja kauanko |
| `KUKA OH2ABC` (tai pelkkä kutsu) | milloin ja missä asema viimeksi kuultiin |
| `NYT` | käynnissä olevat lähetykset seuratuissa ryhmissä |
| syötteen komento, esim. `SAA` | syötteen uusin otsikko |

Oletuksena komentoja saa lähettää vain oma kutsu ja vastaanottajien kutsut (enintään 10/min).

## Tiedostot

Asetukset, lokit ja tilastot tallentuvat vain omalle koneelle:
- Windows: `%APPDATA%\BMMonitor\`
- macOS: `~/Library/Application Support/BMMonitor/`
- Linux: `~/.config/BMMonitor/`

| Tiedosto | Sisältö |
|---|---|
| `config.json` | asetukset (BrandMeister API-avain ja oma APRS-passcode ovat Windowsissa ja macOS:ssä käyttöjärjestelmän salasanavarastossa, muuten tässä tiedostossa) |
| `calls.csv` | seuratut puhelut (aukeaa Excelissä) |
| `monitor.log` | loki, kun ohjelma toimii ilman komentoikkunaa |
| `qso_log.sqlite` | oma loki (Lokit-välilehti) |
| `checkin.json`, `checkin/*.csv` | käynnissä oleva check-in-kierros ja päättyneiden kierrosten lokit |
| `tg_names.json`, `feeds_state.json` | välimuistit |

## Kehitys

```
git clone https://github.com/hannulma/monitor-for-amateur-radio
cd monitor-for-amateur-radio
pip install -e ".[test]"
pytest
python -m bmmonitor --demo
```

**Uusi versio:** päivitä `__version__` tiedostoon `bmmonitor/__init__.py` ja pushaa `main`-haaraan. Jos versiota
ei ole vielä julkaistu, GitHub Actions rakentaa Windows-asennusohjelman,
macOS-levykuvan (universal2: Intel ja Apple Silicon), Linux-ohjelman ja Python-paketin, luo tagin `v<versio>` ja julkaisee ne Releases-sivulle.
Tekninen tausta ja rajapinnat: [docs/TECHNICAL.md](docs/TECHNICAL.md).

## Tietosuoja ja allekirjoitus

Ohjelma ei kerää käyttäjistään tietoja ylläpitäjälle (ei käyttötilastoja eikä analytiikkaa).
[Tietosuojaseloste](PRIVACY.md) kertoo, mihin palveluihin ohjelma ottaa yhteyttä, kun kyseinen toiminto on
käytössä, ja miten kunkin saa pois.

**Code signing policy:** Windows-versioita valmistellaan SignPath Foundationin ilmaiseen koodin allekirjoitukseen
(hakemus kesken; siihen asti versiot ovat allekirjoittamattomia). Allekirjoitetuissa versioissa lukee:
*Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).* Roolit ja käytäntö: [CODE_SIGNING.md](CODE_SIGNING.md).

## Vastuuvapaus

Tämä on itsenäinen harrastusprojekti, **ei BrandMeister-tiimin virallinen tuote**, eikä BrandMeister ole
hyväksynyt tai tue sitä. Käytä verkkoa ja sen rajapintoja [BrandMeisterin sääntöjen](https://wiki.brandmeister.network/index.php/Policies_and_Rules)
ja radioamatööriluvan mukaisesti.

## Lisenssi

[MIT](LICENSE) © 2026 hannulma
