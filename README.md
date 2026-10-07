# RPiStream

Serwer streamingu HDMI→USB dla Raspberry Pi. Przechwytuje obraz z karty
HDMI-USB przez V4L2, koduje obraz i dźwięk za pomocą FFmpeg, a następnie
udostępnia strumień HLS przez serwer Flask. Wbudowany panel WWW pozwala
uruchamiać i zatrzymywać transmisję oraz sprawdzać jej stan.

## Wymagania

- Raspberry Pi 3B lub nowsze z Raspberry Pi OS/Debianem
- Karta przechwytująca HDMI→USB zgodna z V4L2
- FFmpeg z obsługą `libx264`, AAC, HLS, V4L2 i ALSA
- Python 3 oraz Flask
- `v4l-utils` do wykrywania urządzeń wideo
- Źródło HDMI; wejście audio ALSA jest domyślnie skonfigurowane jako `hw:2,0`

## Instalacja

Skopiuj pliki projektu na Raspberry Pi i uruchom instalator z katalogu
zawierającego `stream_server.py`:

```bash
sudo bash install.sh
```

Instalator doinstaluje wymagane pakiety, umieści serwer w `/opt`,
skonfiguruje usługę systemd `rpistream` i uruchomi ją automatycznie.
Usługa jest skonfigurowana do działania jako użytkownik `pi`.

Po instalacji otwórz w przeglądarce:

```text
http://<adres-raspberry-pi>:8080
```

### Uruchomienie ręczne

Jeśli nie chcesz instalować usługi systemd:

```bash
sudo apt update
sudo apt install ffmpeg python3-flask v4l-utils
python3 stream_server.py
```

Serwer nasłuchuje na porcie `8080` na wszystkich interfejsach sieciowych.

## Używanie strumienia

| Klient | Adres / konfiguracja |
| --- | --- |
| Panel WWW i odtwarzacz | `http://<adres-raspberry-pi>:8080` |
| VLC | `http://<adres-raspberry-pi>:8080/hls/stream.m3u8` |
| ffplay | `ffplay http://<adres-raspberry-pi>:8080/hls/stream.m3u8` |
| OBS Studio | Dodaj źródło Media Source i podaj adres HLS |

## Domyślna konfiguracja

| Ustawienie | Wartość |
| --- | --- |
| Urządzenie wideo | `/dev/video0` |
| Format wejściowy | `mjpeg` |
| Rozdzielczość | 1280×720 |
| Klatkaż | 30 FPS |
| Bitrate wideo | 2000 kb/s |
| Bitrate audio | 128 kb/s |
| Urządzenie audio ALSA | `hw:2,0` |
| Segment HLS | 2 sekundy |
| Liczba segmentów playlisty | 5 |
| Port HTTP | 8080 |

Konfigurację wejścia można zmienić w panelu podczas uruchamiania transmisji.
Można też ustawić ją przez endpoint `POST /api/config`. Zmiany konfiguracji
serwera nie są trwale zapisywane po jego ponownym uruchomieniu.

## API

| Metoda i ścieżka | Działanie |
| --- | --- |
| `GET /api/status` | Zwraca stan transmisji, konfigurację i informację o HLS |
| `GET /api/devices` | Wykrywa urządzenia wideo |
| `POST /api/start` | Uruchamia FFmpeg; opcjonalnie przyjmuje ustawienia wejścia jako JSON |
| `POST /api/stop` | Zatrzymuje transmisję |
| `POST /api/config` | Aktualizuje konfigurację serwera w pamięci |
| `GET /hls/<plik>` | Udostępnia playlistę i segmenty HLS |

Przykład sprawdzenia stanu:

```bash
curl http://<adres-raspberry-pi>:8080/api/status
```

## Zarządzanie usługą

```bash
sudo systemctl status rpistream
sudo systemctl restart rpistream
sudo systemctl stop rpistream
journalctl -u rpistream -f
```

## Diagnostyka

Sprawdź, czy system widzi kartę przechwytującą i jakie formaty udostępnia:

```bash
v4l2-ctl --list-devices
v4l2-ctl --device /dev/video0 --list-formats-ext
```

W razie potrzeby zmień `device`, `input_format`, rozdzielczość lub urządzenie
audio w panelu albo w konfiguracji na początku `stream_server.py`. Logi
FFmpeg i serwisu można sprawdzić poleceniem `journalctl -u rpistream -f`.
