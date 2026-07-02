# steam-dupe-seller

Bot wystawiający **duplikaty kart Steam** (i innych marketable przedmiotów) na rynku Steam.
Grupuje ekwipunek po `market_hash_name` i wystawia nadmiar — **zawsze zostawia po 1 sztuce
każdego rodzaju**. Czyste `requests`, bez przeglądarki. Potwierdzenie ofert robisz **ręcznie
w apce Steam Mobile** (Potwierdzenia → Zatwierdź wszystko).

Konto: **KrecikT0K0x** (steamid `76561199087363689`).

---

## Pliki

| Plik | Rola |
|------|------|
| `steam_dupe_seller.py` | logika: pobranie ekwipunku → wybór duplikatów → wycena → wystawienie |
| `steam_auth.py` | sesja web Steam bez ręcznego wklejania ciasteczek (refresh token) |
| `requirements.txt` | `requests`, `rsa`, `protobuf==3.20.3`, `steam` |

## Instalacja

```bash
python3 -m venv steam-seller-venv
steam-seller-venv/bin/pip install -r requirements.txt
```

> **Pułapka: pin `protobuf==3.20.3`.** Nowszy protobuf nie ma `google.protobuf.service`,
> którego wymagają wygenerowane `*_pb2` z pakietu `steam` (ValvePython). Pakiet `steam`
> jest użyty **tylko** dla protobufów `IAuthenticationService` — nie jako klient.

## Logowanie (`steam_auth.py`)

Sesja web bierze się z **refresh tokenu** trzymanego w `~/.steam_refresh_token` (chmod 600,
ważny wiele miesięcy). Z tokenu po cichu generowane jest ciasteczko `steamLoginSecure`
kanonicznym flow przeglądarki: `login.steampowered.com/jwt/finalizelogin` → `settoken`.

Gdy tokenu brak lub wygasł:

```bash
steam-seller-venv/bin/python steam_auth.py --login   # login+hasło + push „Zatwierdź" w apce Steam
steam-seller-venv/bin/python steam_auth.py --qr      # fallback: link do ZESKANOWANIA w apce (nie klikać!)
steam-seller-venv/bin/python steam_auth.py --cookie  # debug: wypisz aktualne ciasteczka
```

- **`--login`** (domyślny sposób): login+hasło z `/etc/skynet/secrets` (`STEAM_LOGIN`,
  `STEAM_PASSWORD`) → RSA-encrypt hasła → `BeginAuthSessionViaCredentials` → Steam wysyła
  **push do apki**, klikasz Zatwierdź → `PollAuthSessionStatus` łapie refresh token.
- **`--qr`**: link `s.team/q/...` wysyłany też na Telegram. **Zeskanuj** go aparatem w apce
  Steam — kliknięcie linku ląduje na stronie pobierania, nie loguje.
- Konto ma tylko **mobilny authenticator** (push), nie kod — brak `identity_secret`,
  więc oferty potwierdzasz ręcznie.

### Pułapki logowania

- `GetPasswordRSAPublicKey` to **GET** (param w query), reszta `Begin*/Poll*` to POST.
- `GenerateAccessTokenForApp` **nie działa** dla web (eresult 15 AccessDenied) — dlatego
  droga przez `finalizelogin`.
- `persistence=1` podać **liczbą** (enum nie jest atrybutem modułu).
- `platform_type = WebBrowser (2)`, `os_type = -500` (EOSType Web).

## Uruchomienie (`steam_dupe_seller.py`)

**Domyślnie dry-run.** Realnie wystawia dopiero z `--sell`.

```bash
# podgląd — co by wystawił, za ile (nic nie robi na koncie):
steam-seller-venv/bin/python steam_dupe_seller.py

# realne wystawienie duplikatów kart:
steam-seller-venv/bin/python steam_dupe_seller.py --sell

# potem: apka Steam Mobile → Potwierdzenia → Zatwierdź wszystko
```

### Flagi

| Flaga | Domyślnie | Opis |
|-------|-----------|------|
| `--sell` | off (dry-run) | realnie wystaw oferty |
| `--app` | `753/6` | `appid/contextid` ekwipunku. `753/6`=karty, `440/2`=TF2, `730/2`=CS2, `570/2`=Dota2 |
| `--types` | `Trading Card` | typy po przecinku (`'Emoticon,Profile Background'`); **puste `''` = wszystkie marketable duplikaty** |
| `--currency` | `6` | waluta wyceny: `6`=PLN, `3`=EUR, `1`=USD |
| `--undercut` | `0` | o ile groszy podbić cenę kupującego w dół |
| `--delay` | `3.5` | przerwa między żądaniami (s) — Steam mocno rate-limituje |
| `--noninteractive` | off | tryb cron: gdy logowanie wygasło → alert TG i wyjście, bez czekania |
| `--selftest` | — | testy jednostkowe wyceny/parsera i wyjście |

### Wycena

`priceoverview` zwraca najniższą ofertę rynku. Funkcja `buyer_price_to_receive()` odejmuje
**prowizję Steam ~15%** (min 1 gr Steam + 1 gr twórcy gry), żeby wyliczyć ile masz dostać,
by kupujący zapłacił nie więcej niż aktualny lowest price. Ceny są cache'owane po nazwie.

## Automatyzacja (cron)

```cron
# user skynet — tygodniowo w niedzielę 12:00, tryb bez blokowania
0 12 * * 0 cd /home/skynet && steam-seller-venv/bin/python steam_dupe_seller.py --sell --noninteractive
```

`--noninteractive` w cronie: gdy refresh token wygasł, zamiast czekać na push wysyła alert
na Telegram i kończy. Wystawione oferty i tak czekają na **ręczne** zatwierdzenie w apce.

## Pułapki (`steam_dupe_seller.py`)

- Endpoint ekwipunku `/inventory/<id>/753/6` wymaga nagłówka **`Referer`** i `count` **≤ 2000**
  (5000 → HTTP 400 z `null`).
- `priceoverview` jest ostro rate-limitowane (~20 żądań/min) → trzymaj `--delay 3.5`.
- Przedmioty bez duplikatów (naklejki, ramki, awatary z eventów) są pomijane z definicji —
  zostaje po 1 sztuce każdego rodzaju.

## Historia użycia

- **2026-06-29** (pierwsze uruchomienie): 272 karty → 103 duplikaty wystawione za ~24,81 zł.
  Z emotkami i tłami (`--types 'Trading Card,Emoticon,Profile Background'`): 170 ofert, ~37 zł.
- **2026-07-02**: przejście na logowanie przez `steam_auth.py` (refresh token) — koniec
  ręcznego wklejania ciasteczek.

## Bezpieczeństwo

- `~/.steam_refresh_token` (chmod 600) i sekrety w `/etc/skynet/secrets` **nie trafiają do repo**
  (`.gitignore`). Login/hasło/token Telegrama bierze się ze zmiennych env lub `/etc/skynet/secrets`.
- Bot **nie** ma `identity_secret` / sekretów 2FA — nie potwierdza ofert automatycznie.
  Każda oferta wymaga ręcznego kliknięcia w apce Steam Mobile.
