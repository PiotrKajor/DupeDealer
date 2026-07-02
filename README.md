# Steam Dupe Seller

Wystawia **duplikaty kart Steam** (i innych marketable przedmiotów) na rynku Steam —
grupuje ekwipunek po `market_hash_name` i wystawia nadmiar, **zawsze zostawiając po 1 sztuce
każdego rodzaju**. Czyste `requests`, bez przeglądarki.

Dwa warianty: **aplikacja okienkowa Windows** (ciemne GUI, gotowy `.exe`) oraz **skrypt CLI**
(Linux/cron). Oferty potwierdzasz **ręcznie w apce Steam Mobile** — bot ich nie zatwierdza.

**➡️ Pobierz gotowy `.exe`: [Releases](../../releases/latest)**

![Zrzut ekranu aplikacji](docs/screenshot.png)

> ⚠️ **Zastrzeżenie.** Automatyzacja rynku Steam może naruszać
> [Steam Subscriber Agreement](https://store.steampowered.com/subscriber_agreement/).
> Korzystasz na **własną odpowiedzialność i ryzyko** (możliwy ban konta lub rynku).
> Projekt edukacyjny, dostarczany „as is", bez żadnej gwarancji. Nie podawaj danych
> logowania na maszynach, którym nie ufasz.

---

## Funkcje

- **Zawsze zostawia 1 sztukę** każdego rodzaju — wystawia tylko duplikaty.
- **Ciemne GUI** (PySide6): status logowania, sortowalna tabela duplikatów z checkboxami,
  wycena w tle z paskiem postępu, podgląd (dry-run) i realne wystawianie z potwierdzeniem.
- **Logowanie bez wklejania ciasteczek**: push do apki Steam Mobile albo kod QR;
  sesja odtwarzana po cichu z refresh tokenu (ważny wiele miesięcy).
- **Wiele ekwipunków**: karty (753/6), TF2 (440/2), CS2 (730/2), Dota 2 (570/2) + filtr typów.
- **Wycena z rynku** (`priceoverview`) z odjęciem prowizji Steam (~15%) i opcją *undercut*.
- **Ten sam silnik w CLI** — nadaje się do crona; `--selftest` pilnuje logiki.

## Jak to działa

1. Pobiera ekwipunek (`/inventory/<steamid>/<appid>/<contextid>`, nagłówek `Referer`, `count≤2000`).
2. Grupuje po `market_hash_name`, wybiera nadmiar ponad 1 sztukę.
3. Wycenia każdy rodzaj przez `priceoverview` i liczy, ile masz *dostać*, by kupujący
   zapłacił nie więcej niż aktualny lowest price (prowizja Steam ~15%, min 1 gr + 1 gr).
4. Wystawia oferty (`sellitem`). **Potwierdzasz je ręcznie** w apce Steam Mobile
   (Potwierdzenia → Zatwierdź wszystko) — konto z samym mobilnym authenticatorem nie ma
   `identity_secret`, więc bot nie potwierdza automatycznie.

---

## Aplikacja Windows

Najprościej: pobierz **`SteamDupeSeller.exe`** z [Releases](../../releases/latest) i uruchom
(plik nie jest podpisany — SmartScreen może ostrzec: „Więcej informacji" → „Uruchom mimo to").

### Uruchomienie ze źródeł (Windows lub Linux)

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt        # Windows: venv\Scripts\pip
venv/bin/python steam_seller_gui.py             # Windows: venv\Scripts\python
```

### Build `.exe`

Na Windowsie z Pythonem 3.10+ w PATH:

```bat
build.bat
```

Wynik: `dist\SteamDupeSeller.exe` (onefile, bez konsoli, z ikoną). Ręcznie:

```bat
pyinstaller --onefile --windowed --icon app.ico --name SteamDupeSeller ^
    --add-data "app.ico;." --collect-submodules steam.protobufs steam_seller_gui.py
```

Buildy release'owe robi też GitHub Actions (`.github/workflows/release.yml`) — po pushu
taga `v*` albo ręcznie z zakładki *Actions*.

**Pułapki buildu:**

- Pakiet `steam` (ValvePython) ładuje protobufy dynamicznie — bez
  `--collect-submodules steam.protobufs` w gotowym `.exe` zabraknie
  `steammessages_auth_pb2` i logowanie się wysypie.
- Zostaw pin **`protobuf==3.20.3`** (nowszy nie ma `google.protobuf.service`,
  którego wymagają wygenerowane `*_pb2` z pakietu `steam`).
- Gotowy `.exe` przetestuj na czystym Windowsie (bez Pythona): logowanie push **i**
  wczytanie ekwipunku muszą działać.

### Gdzie ląduje token (Windows)

- **Refresh token**: `%APPDATA%\SteamDupeSeller\refresh_token`.
- **Login/hasło** wpisujesz w okienku logowania — **nie są zapisywane na dysk**,
  idą tylko do zaszyfrowanego (RSA) logowania, po czym zostaje sam refresh token.
- **Powiadomienia Telegram** są opcjonalne — bez `TG_TOKEN`/`TG_CHAT_ID` po prostu pomijane.

---

## Skrypt CLI

### Instalacja

```bash
python3 -m venv steam-seller-venv
steam-seller-venv/bin/pip install -r requirements.txt
```

### Logowanie (`steam_auth.py`)

Sesja web bierze się z **refresh tokenu** (domyślnie `~/.steam_refresh_token`, chmod 600).
Z tokenu po cichu generowane jest ciasteczko `steamLoginSecure` kanonicznym flow przeglądarki
(`login.steampowered.com/jwt/finalizelogin` → `settoken`). Ścieżkę tokenu zmieniasz przez env
**`STEAM_TOKEN_FILE`**.

Gdy tokenu brak lub wygasł:

```bash
steam-seller-venv/bin/python steam_auth.py --login   # login+hasło → push „Zatwierdź" w apce
steam-seller-venv/bin/python steam_auth.py --qr      # kod do ZESKANOWANIA w apce (nie klikać!)
steam-seller-venv/bin/python steam_auth.py --cookie  # debug: wypisz aktualne ciasteczka
```

- **`--login`**: login/hasło ze zmiennych env `STEAM_LOGIN`/`STEAM_PASSWORD` (albo z pliku
  wskazanego przez `STEAM_SECRETS_FILE`, format `KEY=VALUE`) → RSA → `BeginAuthSessionViaCredentials`
  → Steam wysyła **push do apki**, klikasz Zatwierdź → `PollAuthSessionStatus` łapie token.
- **`--qr`**: link `s.team/q/...` (opcjonalnie wysyłany też na Telegram). Trzeba go
  **zeskanować** aparatem w apce — kliknięcie linku nie loguje.

### Uruchomienie (`steam_dupe_seller.py`)

**Domyślnie dry-run.** Realnie wystawia dopiero z `--sell`.

```bash
steam-seller-venv/bin/python steam_dupe_seller.py           # podgląd (nic nie robi)
steam-seller-venv/bin/python steam_dupe_seller.py --sell    # realne wystawienie
# potem: apka Steam Mobile → Potwierdzenia → Zatwierdź wszystko
```

| Flaga | Domyślnie | Opis |
|-------|-----------|------|
| `--sell` | off (dry-run) | realnie wystaw oferty |
| `--app` | `753/6` | `appid/contextid`: `753/6`=karty, `440/2`=TF2, `730/2`=CS2, `570/2`=Dota2 |
| `--types` | `Trading Card` | typy po przecinku; **puste `''` = wszystkie marketable duplikaty** |
| `--currency` | `6` | waluta wyceny: `6`=PLN, `3`=EUR, `1`=USD |
| `--undercut` | `0` | o ile groszy zejść poniżej ceny kupującego |
| `--delay` | `3.5` | przerwa między żądaniami (s) — Steam mocno rate-limituje |
| `--noninteractive` | off | tryb cron: gdy logowanie wygasło → alert (opcjonalny TG) i wyjście |
| `--selftest` | — | testy jednostkowe wyceny/parsera i wyjście |

### Automatyzacja (cron)

```cron
# tygodniowo w niedzielę 12:00, tryb bez blokowania (podmień ścieżkę na swoją)
0 12 * * 0 cd ~/steam-dupe-seller && steam-seller-venv/bin/python steam_dupe_seller.py --sell --noninteractive
```

---

## Struktura

| Plik | Rola |
|------|------|
| `steam_dupe_seller.py` | logika: ekwipunek → duplikaty → wycena → wystawienie (funkcje importowalne, wołane też przez GUI) |
| `steam_auth.py` | sesja web Steam z refresh tokenu (push / QR), bez wklejania ciasteczek |
| `steam_seller_gui.py` | aplikacja okienkowa (PySide6) |
| `gui_theme.py` | ciemny motyw QSS |
| `tiny_qr.py` | mini-generator QR bez zależności (kod logowania QR w GUI) |
| `build.bat`, `app.ico`, `.github/workflows/release.yml` | pakowanie `.exe` i publikacja w Releases |

### Pułapki (dla rozwijających)

- `GetPasswordRSAPublicKey` to **GET** (param w query), reszta `Begin*/Poll*` to POST.
- `GenerateAccessTokenForApp` **nie działa** dla web (eresult 15) — stąd droga przez `finalizelogin`.
- `persistence=1` podać **liczbą**; `platform_type = WebBrowser (2)`, `os_type = -500`.
- Endpoint ekwipunku wymaga nagłówka **`Referer`** i `count` **≤ 2000** (5000 → HTTP 400).
- `priceoverview` jest ostro rate-limitowane (~20 żądań/min) → trzymaj `--delay`/`Odstęp`.

## Bezpieczeństwo

- Refresh token (`~/.steam_refresh_token`, na Windowsie `%APPDATA%\SteamDupeSeller\`) ani żadne
  sekrety **nie trafiają do repo** (`.gitignore`) i nie są pokazywane w UI ani logach.
- Login/hasło/token Telegrama bierze się ze zmiennych env lub pliku `STEAM_SECRETS_FILE`;
  hasło w GUI żyje tylko w pamięci na czas logowania — nie jest zapisywane na dysk.
- Bot **nie** ma `identity_secret` / sekretów 2FA — każdą ofertę potwierdzasz ręcznie w apce.

## Licencja

MIT — patrz [LICENSE](LICENSE).
