__version__ = "0.18.0b6"
__date__ = "2026-11-27"          # julkaisupäivä (Tietoja-rivin vuosiluku)
APP_NAME = "Monitor for Amateur Radio"   # näkyvä nimi; sisäiset tunnisteet (APP_ID, salasanavarasto, tiedostonimet) ennallaan
APP_ID = "monitor-for-brandmeister"

# Tiedostot lähetetään aiohttp:n omalla lukemisella eikä asyncion sendfile-varatiellä: Windowsin
# SelectorEventLoopissa varatie käyttää samaa puskuria uudelleen ennen kuin hidas vastaanottaja (esim. iPad
# Wi-Fi-verkossa) on saanut edellisen osan → selaimen saamat skriptit sotkeutuivat (0.18.0b2–b5).
# Pitää asettaa ennen aiohttp:n tuontia.
import os as _os  # noqa: E402

_os.environ.setdefault("AIOHTTP_NOSENDFILE", "1")
