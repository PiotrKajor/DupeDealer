<div align="center">

<sub><a href="README.md">Polski</a> · <b>English</b></sub>

# 🏷️ DupeDealer

**Lists duplicate Steam cards and items on the market — always keeping one of each kind.**

A desktop app for Windows and a CLI script. No browser, on plain `requests`.

[![Download .exe](https://img.shields.io/badge/Download-DupeDealer.exe-4fb4ff?style=for-the-badge)](../../releases/latest)
&nbsp;
![Platform](https://img.shields.io/badge/Windows-64--bit-2a3245?style=for-the-badge)
&nbsp;
![License](https://img.shields.io/badge/license-MIT-3ddc84?style=for-the-badge)

![App screenshot](docs/screenshot.png)

</div>

> [!WARNING]
> **Use at your own risk.** Automating the Steam market may violate the
> [Steam Subscriber Agreement](https://store.steampowered.com/subscriber_agreement/) and can
> lead to account or market restrictions. An educational project, provided "as is", without
> warranty. Don't enter your credentials on machines you don't trust.

---

## What it does

Do you have dozens of duplicate cards sitting in your Steam inventory? This program groups
items by `market_hash_name`, **keeps one of each kind**, and lists the rest on the market at
a price computed from the current offers. You **confirm the listings yourself** in the Steam
Mobile app — the bot never does it for you.

**Flow:**

1. You log in (a push to the Steam app or a QR code) — no manual cookie pasting.
2. You load the inventory and see a table of duplicates with the market price and the amount
   you "receive".
3. You select what to list, do a preview (dry run) or list for real.
4. You open the Steam Mobile app → **Confirmations → Confirm all**.

## Features

- ✅ **Safe logic** — always keeps 1 of a kind, lists only the surplus.
- 🖥️ **Modern GUI** (PySide6, dark theme): a sortable table with checkboxes, background
  pricing with a progress bar, preview and listing with confirmation.
- 👤 **Account panel after logging in** — profile avatar and Steam wallet balance in the header.
- 🖼️ **Item image** — a thumbnail in the table, and a full-size image in a tooltip on hover.
- 🔐 **Cookie-free login** — a push to the app or a QR code; the session is silently restored
  from a refresh token (valid for many months).
- 🎮 **Multiple inventories** — cards (753/6), TF2 (440/2), CS2 (730/2), Dota 2 (570/2) + a type filter.
- 💰 **Market pricing** (`priceoverview`) with the Steam fee deducted (~15%) and an *undercut* option.
- 🧪 **The same engine in the CLI** — good for cron; a built-in `--selftest`.

---

## Quick start (Windows)

1. Download **`DupeDealer.exe`** from the [Releases tab](../../releases/latest).
2. Run it. The file is not signed, so SmartScreen may warn you —
   "More info" → "Run anyway".
3. **Log in (app push)** or **Log in (QR)** and approve the login on your phone.
4. Choose an inventory → **Load inventory**, wait for pricing.
5. Select the items → **Preview** (does nothing) or **List selected**.
6. Confirm the listings in the Steam Mobile app (Confirmations → Confirm all).

The login token is saved to `%APPDATA%\DupeDealer\refresh_token`.
The password is not saved anywhere on disk.

## Running from source

Works the same on Windows and Linux:

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt        # Windows: venv\Scripts\pip
venv/bin/python dupedealer_gui.py             # Windows: venv\Scripts\python
```

### Building your own `.exe`

On Windows with Python 3.10+ in PATH, just:

```bat
build.bat
```

Result: `dist\DupeDealer.exe` (a single file, no console). The manual equivalent:

```bat
pyinstaller --onefile --windowed --icon app.ico --name DupeDealer ^
    --add-data "app.ico;." --collect-submodules steam.protobufs dupedealer_gui.py
```

Release builds are produced by GitHub Actions (`.github/workflows/release.yml`) after
pushing a `v*` tag.

> **Why `--collect-submodules steam.protobufs`?** The `steam` package loads protobufs
> dynamically — without this, the finished `.exe` would be missing `steammessages_auth_pb2`
> and login would break. Also keep the pin **`protobuf==3.20.3`** (newer versions drop
> `google.protobuf.service`, which the generated `*_pb2` require).

---

## CLI variant

**Dry run by default** — it only lists for real with `--sell`.

```bash
python dupedealer.py            # preview: what it would list and for how much
python dupedealer.py --sell     # actually list the duplicates
```

| Flag | Default | Description |
|-------|-----------|------|
| `--sell` | off (dry run) | actually place the listings |
| `--app` | `753/6` | `appid/contextid`: `753/6`=cards, `440/2`=TF2, `730/2`=CS2, `570/2`=Dota2 |
| `--types` | `Trading Card` | comma-separated types; **empty `''` = all marketable duplicates** |
| `--currency` | `6` | pricing currency: `6`=PLN, `3`=EUR, `1`=USD |
| `--undercut` | `0` | how many cents to go below the buyer's price |
| `--delay` | `3.5` | pause between requests (s) — Steam rate-limits heavily |
| `--noninteractive` | off | cron mode: when login has expired → exit (optional Telegram alert) |
| `--selftest` | — | unit tests for pricing/parsing, then exit |

Command-line login: `python steam_auth.py --login` (push) or `--qr` (a code to scan).

A cron example (weekly, without blocking on login):

```cron
0 12 * * 0 cd ~/DupeDealer && venv/bin/python dupedealer.py --sell --noninteractive
```

## Configuration (environment variables)

Everything is optional — in the GUI you enter your credentials in a dialog.

| Variable | Role |
|---------|------|
| `STEAM_LOGIN`, `STEAM_PASSWORD` | credentials for `--login` (for CLI/cron) |
| `STEAM_TOKEN_FILE` | path to the refresh-token file (default `~/.steam_refresh_token`; Windows GUI: `%APPDATA%\DupeDealer\`) |
| `STEAM_SECRETS_FILE` | an optional `KEY=VALUE` file with the secrets above |
| `TG_TOKEN`, `TG_CHAT_ID` | optional Telegram notifications (without them they're simply skipped) |

## How the price is calculated

`priceoverview` returns the lowest current offer. The `buyer_price_to_receive()` function
subtracts the **Steam fee (~15%**, min. 1 cent for Steam + 1 cent for the game's developer)
to work out the amount you should *receive* so that the buyer pays no more than the current
lowest price. With the *undercut* option you drop a few cents lower still. Prices are cached
by item name.

## Security

- The refresh token and any secrets **never go into the repository** (`.gitignore`), nor into
  the interface or logs.
- The password in the GUI only lives in memory for the duration of login — it is not written
  to disk; the only thing kept persistently is the refresh token (on Windows in `%APPDATA%`).
- The bot **does not have** an `identity_secret` / 2FA secrets, so it does not confirm
  listings automatically — you approve each one manually in the Steam Mobile app.

## Technical notes

For those looking into the code / developing the project:

- The inventory endpoint requires a **`Referer`** header and `count` **≤ 2000** (5000 → HTTP 400).
- `priceoverview` is heavily rate-limited (~20 requests/min) — hence the fixed gap between
  requests (`--delay` / the *Interval* slider) and the cache by name.
- Login: `GetPasswordRSAPublicKey` is a **GET**, the rest of `Begin*/Poll*` are POST;
  `platform_type = WebBrowser (2)`, `os_type = -500`; the web session goes through
  `finalizelogin`.
- The QR code is drawn by its own `tiny_qr.py` (zero dependencies), verified bit-for-bit
  against a reference encoder.

## License

[MIT](LICENSE).
