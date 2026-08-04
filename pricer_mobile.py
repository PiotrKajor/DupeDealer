#!/usr/bin/env python3
"""Mobilny wycennik DupeDealer — SAMA WYCENA, do odpalenia z iPhone'a (a-Shell).

Po co to: gdy Twoje domowe IP jest przymulone przez Steam (HTTP 429 na całym
rynku), ten skrypt uruchomiony na telefonie leci przez IP sieci komórkowej,
które zwykle nie jest zablokowane — i wycena znów działa.

Cechy:
- tylko biblioteka standardowa Pythona (urllib) — nie trzeba `pip install`,
- BEZ logowania, BEZ hasła, BEZ ciasteczek — używa wyłącznie publicznych
  endpointów (ekwipunek publiczny + priceoverview), więc zero ryzyka dla konta,
- niczego NIE wystawia — sprzedaż nadal robisz na desktopie (DupeDealer.exe).

Uruchomienie na iPhonie (a-Shell z App Store, darmowa):
    curl -O https://raw.githubusercontent.com/PiotrKajor/DupeDealer/master/pricer_mobile.py
    python3 pricer_mobile.py --steamid https://steamcommunity.com/id/TWOJA_NAZWA/ --app 440/2

`--steamid` przyjmuje SteamID64 (17 cyfr), link do profilu (.../id/… lub
.../profiles/…) albo samą nazwę vanity — skrypt sam wyciągnie SteamID64.

Gdy endpoint EKWIPUNKU blokuje (429 — jest najostrzej limitowany), podaj nazwy
wprost, a skrypt w ogóle nie ruszy ekwipunku, tylko wyceni:
    python3 pricer_mobile.py "Name Tag" "Tour of Duty Ticket" --app 440/2

Albo ustaw wartości w bloku KONFIGURACJA niżej (pico pricer_mobile.py) i odpal
bez flag: python3 pricer_mobile.py
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

# ----------------------------------------------------------- KONFIGURACJA ---
STEAMID = ""              # Twój SteamID64 (17 cyfr). Puste = użyj listy ITEMS niżej.
APP = "753/6"             # 753/6=karty, 440/2=TF2, 730/2=CS2, 570/2=Dota2
TYPES = "Trading Card"    # filtr typu; "" = wszystkie marketable duplikaty (TF2/CS)
CURRENCY = "6"            # 6=PLN, 3=EUR, 1=USD
UNDERCUT = 0              # o ile groszy zejść poniżej ceny kupującego
DELAY = 4.0              # przerwa między zapytaniami (s) — rynek ~20/min

# Awaryjnie, gdy Twój ekwipunek jest PRYWATNY (publiczny fetch nie zadziała):
# wklej tu nazwy przedmiotów ręcznie (np. wyeksportowane z desktopa).
ITEMS = [
    # "Winter 2025 Cosmetic Case",
    # "Name Tag",
]
# ---------------------------------------------------------------------------

CUR_SUFFIX = {"1": "$", "2": "£", "3": "€", "5": "₽", "6": "zł"}
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


class RateLimited(Exception):
    """Steam odrzucił zapytanie (HTTP 429) — to IP też jest przymulone."""


def parse_price(s):
    """'0,04 zł' / '$1.23' -> grosze/centy (zakłada walutę 2-miejscową)."""
    m = re.search(r'(\d+)[.,](\d{2})', s)
    if m:
        return int(m.group(1)) * 100 + int(m.group(2))
    m = re.search(r'(\d+)', s)
    return int(m.group(1)) * 100 if m else 0


def buyer_price_to_receive(buyer_cents):
    """Ile dostajesz, by kupujący zapłacił <= buyer_cents (prowizja Steam ~15%)."""
    for receive in range(buyer_cents, 0, -1):
        if receive + max(1, receive * 5 // 100) + max(1, receive * 10 // 100) <= buyer_cents:
            return receive
    return 0


def fmt(cents, suffix):
    return f"{cents // 100},{cents % 100:02d} {suffix}".rstrip()


def _get(url, params=None):
    """GET -> (status, tekst). Zamienia 429 na RateLimited."""
    if params:
        url += "?" + urlencode(params)
    try:
        with urlopen(Request(url, headers=UA), timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except HTTPError as e:
        if e.code == 429:
            raise RateLimited()
        return e.code, e.read().decode("utf-8", "replace")


def resolve_steamid(value):
    """Zamienia to, co poda user, na SteamID64. Akceptuje:
    - gotowy SteamID64 (17 cyfr),
    - link .../profiles/<id>/,
    - link .../id/<vanity>/ albo samą nazwę vanity (rozwiązuje przez ?xml=1).
    """
    value = value.strip().rstrip('/')
    m = re.search(r'/profiles/(\d{17})', value)
    if m:
        return m.group(1)
    if re.fullmatch(r'\d{17}', value):
        return value
    m = re.search(r'/id/([^/?]+)', value)
    vanity = m.group(1) if m else value
    _, body = _get(f"https://steamcommunity.com/id/{quote(vanity)}/", {"xml": "1"})
    m = re.search(r'<steamID64>(\d+)</steamID64>', body)
    if not m:
        sys.exit(f"Nie udało się rozwiązać profilu '{vanity}' na SteamID64 "
                 "(sprawdź nazwę/link albo czy profil jest publiczny).")
    return m.group(1)


def fetch_inventory(steamid, appid, contextid):
    """Publiczny ekwipunek (JSON). Zwraca dict albo None (prywatny/niedostępny)."""
    _, body = _get(f"https://steamcommunity.com/inventory/{steamid}/{appid}/{contextid}",
                   {"l": "english", "count": 2000})
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("assets") else None


def duplicate_names(inv, types):
    """[(nazwa, do_sprzedania, ilosc)] — nadmiar ponad 1 sztukę każdego rodzaju."""
    desc = {(d["classid"], d["instanceid"]): d for d in inv["descriptions"]}
    wanted = [t.strip() for t in types.split(",") if t.strip()]
    names = []
    for a in inv["assets"]:
        d = desc[(a["classid"], a["instanceid"])]
        if d.get("marketable") and (not wanted or any(w in d.get("type", "") for w in wanted)):
            names.append(d["market_hash_name"])
    counts = Counter(names)
    return sorted(((n, counts[n] - 1, counts[n]) for n in counts if counts[n] > 1),
                  key=lambda r: r[0].lower())


def fetch_price(appid, name, currency):
    """priceoverview -> cena kupującego w groszach (0 = brak oferty). Rzuca RateLimited."""
    _, body = _get("https://steamcommunity.com/market/priceoverview/",
                   {"appid": appid, "market_hash_name": name, "currency": currency})
    try:
        r = json.loads(body)
    except ValueError:
        return 0
    if not isinstance(r, dict):
        return 0
    return parse_price(r["lowest_price"]) if r.get("lowest_price") else 0


def selftest():
    assert buyer_price_to_receive(4) == 2
    assert buyer_price_to_receive(100) == 88
    assert parse_price("0,04 zł") == 4 and parse_price("$1.23") == 123
    inv = {"assets": [{"classid": "1", "instanceid": "0"}] * 3 + [{"classid": "2", "instanceid": "0"}],
           "descriptions": [{"classid": "1", "instanceid": "0", "marketable": 1,
                             "type": "Trading Card", "market_hash_name": "A"},
                            {"classid": "2", "instanceid": "0", "marketable": 1,
                             "type": "Trading Card", "market_hash_name": "B"}]}
    assert duplicate_names(inv, "Trading Card") == [("A", 2, 3)], duplicate_names(inv, "Trading Card")
    # resolve_steamid: ścieżki bez sieci (17 cyfr i link /profiles/)
    assert resolve_steamid("76561199087363689") == "76561199087363689"
    assert resolve_steamid("https://steamcommunity.com/profiles/76561199087363689/") == "76561199087363689"
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(description="Mobilny wycennik DupeDealer (a-Shell).")
    ap.add_argument("--steamid", default=STEAMID,
                    help="SteamID64, link do profilu (.../id/… lub .../profiles/…) albo nazwa vanity")
    ap.add_argument("--app", default=APP)
    ap.add_argument("--types", default=TYPES)
    ap.add_argument("--currency", default=CURRENCY)
    ap.add_argument("--undercut", type=int, default=UNDERCUT)
    ap.add_argument("--delay", type=float, default=DELAY)
    ap.add_argument("names", nargs="*",
                    help="nazwy przedmiotów do wyceny WPROST (pomija ekwipunek); "
                         'np. "Name Tag" "Tour of Duty Ticket"')
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    appid, contextid = args.app.split("/")
    suffix = CUR_SUFFIX.get(args.currency, "")

    # 1) skąd bierzemy listę nazw:
    #    a) nazwy podane wprost w linii poleceń — NIE rusza ekwipunku (najbezpieczniej),
    #    b) publiczny ekwipunek z --steamid, c) lista ITEMS w skrypcie.
    if args.names:
        rows = [(n, 1, "?") for n in args.names]
    elif args.steamid:
        try:
            steamid64 = resolve_steamid(args.steamid)
        except RateLimited:
            sys.exit("429 przy rozwiązywaniu profilu — włącz dane komórkowe / inną sieć.")
        print(f"Profil → SteamID64: {steamid64}")
        print(f"Pobieram publiczny ekwipunek ({args.app})…")
        try:
            inv = fetch_inventory(steamid64, appid, contextid)
        except RateLimited:
            sys.exit("429 na ekwipunku — to IP też jest przymulone. Włącz dane "
                     "komórkowe / inną sieć i spróbuj ponownie.")
        if not inv:
            sys.exit("Pusty/niedostępny ekwipunek. Sprawdź SteamID64 albo ustaw "
                     "profil/ekwipunek jako publiczny. Ewentualnie użyj listy ITEMS.")
        rows = duplicate_names(inv, args.types)
    else:
        rows = [(n, 1, "?") for n in ITEMS]
        if not rows:
            sys.exit("Podaj --steamid (SteamID64, link do profilu albo nazwa vanity — "
                     "publiczny ekwipunek) albo wpisz nazwy w liście ITEMS w skrypcie.")

    if not rows:
        print("Brak duplikatów do wyceny.")
        return
    print(f"Do wyceny: {len(rows)} pozycji. Odstęp {args.delay}s (rynek limituje ~20/min).\n")

    header = f"{'Przedmiot':32} {'do sprz.':>8} {'cena rynku':>12} {'dostajesz/szt':>13} {'suma':>12}"
    print(header)
    print("-" * len(header))

    total = 0
    for i, (name, sell_n, _) in enumerate(rows, 1):
        try:
            buyer = fetch_price(appid, name, args.currency)
        except RateLimited:
            print("\n⚠ 429 — Steam ogranicza zapytania z tego IP. Przerywam, żeby nie "
                  "przedłużać blokady. Włącz dane komórkowe / inną sieć i spróbuj później.")
            break
        recv = buyer_price_to_receive(buyer - args.undercut) if buyer else 0
        line_sum = recv * sell_n if isinstance(sell_n, int) else recv
        total += line_sum if isinstance(line_sum, int) else 0
        price_txt = fmt(buyer, suffix) if buyer else "brak ofert"
        recv_txt = fmt(recv, suffix) if recv else "—"
        sum_txt = fmt(line_sum, suffix) if isinstance(line_sum, int) and recv else "—"
        print(f"{name[:32]:32} {str(sell_n):>8} {price_txt:>12} {recv_txt:>13} {sum_txt:>12}")
        if i < len(rows):
            time.sleep(args.delay)

    print("-" * len(header))
    print(f"Razem dostajesz ok.: {fmt(total, suffix)}")
    print("\nWystawianie zrób na desktopie (DupeDealer.exe) — tu tylko wycena.")


if __name__ == "__main__":
    main()
