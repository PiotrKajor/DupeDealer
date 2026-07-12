#!/usr/bin/env python3
"""Wystawia duplikaty kart Steam na rynku — zawsze zostawia 1 z każdego rodzaju.
Potwierdzenia robisz RĘCZNIE w apce Steam Mobile (Potwierdzenia -> Zatwierdź wszystko).

Logowanie ogarnia steam_auth.py (refresh token + QR-login z linkiem na Telegram) —
NIE trzeba już wklejać ciasteczek. Pierwsze uruchomienie interaktywnie zaloguje przez QR.

DOMYŚLNIE DRY-RUN. Realnie wystawia dopiero z flagą --sell.
--noninteractive: tryb cron (nie czeka na logowanie, tylko alertuje na TG).
Zależność: requests, steam_auth (protobuf==3.20.3, steam).

Kluczowe kroki są funkcjami (używa ich też GUI — dupedealer_gui.py):
make_session / fetch_inventory / marketable_items / pick_duplicates /
fetch_price / sell_item.
"""
import argparse, json, os, re, sys, time
from collections import Counter
import requests
import steam_auth

# CDN obrazków ekonomii Steam — do niego doklejamy `icon_url` z opisu przedmiotu.
STEAM_IMAGE_BASE = "https://community.cloudflare.steamstatic.com/economy/image/"

# Numeryczne kody walut portfela (ECurrencyCode) -> symbol do wyświetlenia.
# Nieznany kod = pusty symbol (pokażemy samą kwotę). PL to 6 = zł.
WALLET_SYMBOLS = {
    1: '$', 2: '£', 3: '€', 4: 'CHF', 5: '₽', 6: 'zł', 7: 'R$', 8: '¥',
    9: 'kr', 20: 'CA$', 21: 'A$', 22: 'NZ$', 23: '¥', 24: '₹', 28: 'R',
    29: 'HK$', 30: 'NT$', 41: 'лв', 43: 'Kč', 44: 'kr', 45: 'Ft', 46: 'lei',
}


def image_url(icon: str, size: int = None) -> str:
    """Buduje pełny URL obrazka przedmiotu z `icon_url`/`icon_url_large` z ekwipunku.

    `size` (opcjonalnie) dokleja żądany wymiar w px, np. 96 -> `/96fx96f`.
    Pusty `icon` -> pusty string (brak grafiki dla przedmiotu).
    """
    if not icon:
        return ''
    url = STEAM_IMAGE_BASE + icon
    return f"{url}/{size}fx{size}f" if size else url


def fetch_wallet(s):
    """Saldo portfela Steam -> (grosze/centy, symbol_waluty).

    Parsuje `g_rgWalletInfo` ze strony /market/ (tam Steam wstrzykuje saldo w JS).
    Zwraca (None, '') gdy się nie uda — np. sesja wygasła albo brak portfela.
    """
    try:
        html = s.get("https://steamcommunity.com/market/", timeout=30).text
    except Exception:
        return None, ''
    m = re.search(r'g_rgWalletInfo\s*=\s*(\{.*?\})\s*;', html)
    if m:
        try:
            info = json.loads(m.group(1))
            bal = info.get('wallet_balance')
            if bal is not None:
                cur = info.get('wallet_currency')
                sym = WALLET_SYMBOLS.get(int(cur), '') if cur is not None else ''
                return int(bal), sym
        except (ValueError, TypeError):
            pass
    # awaryjnie: sformatowane saldo w nagłówku strony (symbol nieznany — zostawiamy sam tekst)
    m = re.search(r'id="header_wallet_balance"[^>]*>([^<]+)<', html)
    if m:
        return parse_price(m.group(1)), ''
    return None, ''


def buyer_price_to_receive(buyer_cents: int) -> int:
    """Ile dostajesz, by kupujący zapłacił <= buyer_cents (prowizja Steam ~15%, min 1+1)."""
    # ponytail: brute loop — grosze są małe, wzór odwrotny do prowizji jest upierdliwy
    for receive in range(buyer_cents, 0, -1):
        if receive + max(1, receive * 5 // 100) + max(1, receive * 10 // 100) <= buyer_cents:
            return receive
    return 0


def parse_price(s: str) -> int:
    """'0,04 zł' / '$1.23' -> grosze/centy. ponytail: zakłada walutę 2-miejscową."""
    m = re.search(r'(\d+)[.,](\d{2})', s)
    if m:
        return int(m.group(1)) * 100 + int(m.group(2))
    m = re.search(r'(\d+)', s)
    return int(m.group(1)) * 100 if m else 0


def make_session(cookies):
    """requests.Session z ciasteczkami i nagłówkami wymaganymi przez inventory/market.

    `cookies` = wynik steam_auth.get_cookies(). Referer jest OBOWIĄZKOWY dla
    endpointu ekwipunku.
    """
    s = requests.Session()
    s.cookies.update({'steamLoginSecure': cookies['steamLoginSecure'],
                      'sessionid': cookies['sessionid']})
    s.headers.update({'User-Agent': 'Mozilla/5.0',
                      'Referer': f"https://steamcommunity.com/profiles/{cookies['_steamid']}/inventory",
                      'X-Requested-With': 'XMLHttpRequest'})
    return s


def fetch_inventory(s, steamid, appid, contextid):
    """Pobiera ekwipunek (JSON). count max 2000 dla tego endpointu (5000 -> HTTP 400)."""
    return s.get(f"https://steamcommunity.com/inventory/{steamid}/{appid}/{contextid}",
                 params={'l': 'english', 'count': 2000}, timeout=30).json()


def marketable_items(inv, types):
    """Lista {'assetid','name','icon','icon_large'} marketable przedmiotów pasujących do filtra.

    `types` jak w --types: nazwy po przecinku, pusty string = wszystkie marketable.
    `icon`/`icon_large` to hashe z `icon_url`/`icon_url_large` (pełny URL: image_url()).
    """
    desc = {(d['classid'], d['instanceid']): d for d in inv['descriptions']}
    wanted = [t.strip() for t in types.split(',') if t.strip()]  # pusty = bez filtra typu
    items = []
    for a in inv['assets']:
        d = desc[(a['classid'], a['instanceid'])]
        typ = d.get('type', '')
        if d.get('marketable') and (not wanted or any(w in typ for w in wanted)):
            items.append({'assetid': a['assetid'], 'name': d['market_hash_name'],
                          'icon': d.get('icon_url', ''),
                          'icon_large': d.get('icon_url_large', '')})
    return items


def pick_duplicates(items):
    """Nadmiar ponad 1 sztukę każdego rodzaju -> (Counter po nazwie, lista do sprzedania)."""
    counts = Counter(c['name'] for c in items)
    seen, to_sell = Counter(), []
    for c in items:
        if seen[c['name']] < counts[c['name']] - 1:   # zostaw jeden z każdego rodzaju
            to_sell.append(c); seen[c['name']] += 1
    return counts, to_sell


class RateLimited(Exception):
    """Steam odrzucił zapytanie o cenę (HTTP 429 — za dużo żądań z tego IP).

    priceoverview ma limit ~20 żądań/min na IP. Po jego przekroczeniu KAŻDE kolejne
    zapytanie w oknie blokady dostaje 429 (i przedłuża blokadę) — wołający powinien
    wyhamować, a nie pukać dalej.
    """


def fetch_price(s, appid, name, currency):
    """priceoverview -> cena kupującego w groszach (0 = brak oferty na rynku).

    Rzuca RateLimited przy HTTP 429. Odporne na pustą odpowiedź (`null`): przy 429
    ciało to `null`, więc naiwne `.json()['lowest_price']` sypało `AttributeError`.
    """
    resp = s.get("https://steamcommunity.com/market/priceoverview/",
                 params={'appid': appid, 'market_hash_name': name, 'currency': currency},
                 timeout=30)
    if resp.status_code == 429:
        raise RateLimited()
    r = resp.json()
    if not isinstance(r, dict):          # 429/awaria zwraca `null` -> brak danych
        return 0
    return parse_price(r['lowest_price']) if r.get('lowest_price') else 0


def sell_item(s, sessionid, steamid, appid, contextid, assetid, receive_cents):
    """POST /market/sellitem — jedna oferta. Zwraca surową odpowiedź JSON Steam."""
    return s.post("https://steamcommunity.com/market/sellitem/",
                  data={'sessionid': sessionid, 'appid': appid, 'contextid': contextid,
                        'assetid': assetid, 'amount': 1, 'price': receive_cents},
                  headers={'Referer': f"https://steamcommunity.com/profiles/{steamid}/inventory"},
                  timeout=30).json()


def selftest():
    assert buyer_price_to_receive(4) == 2, buyer_price_to_receive(4)
    assert buyer_price_to_receive(100) == 88, buyer_price_to_receive(100)
    assert parse_price('0,04 zł') == 4 and parse_price('$1.23') == 123
    items = [{'assetid': '1', 'name': 'A'}, {'assetid': '2', 'name': 'A'},
             {'assetid': '3', 'name': 'A'}, {'assetid': '4', 'name': 'B'}]
    counts, to_sell = pick_duplicates(items)
    assert counts == Counter({'A': 3, 'B': 1})
    assert [c['assetid'] for c in to_sell] == ['1', '2'], to_sell  # zostaje 1×A i 1×B
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sell', action='store_true', help='realnie wystaw (bez tego dry-run)')
    ap.add_argument('--app', default='753/6',
                    help="appid/contextid ekwipunku: 753/6=karty (dom.), 440/2=TF2, 730/2=CS2, 570/2=Dota2")
    ap.add_argument('--types', default='Trading Card',
                    help="typy do sprzedaży po przecinku, np. 'Emoticon'; pusty ('') = wszystkie marketable duplikaty (TF2/CS)")
    ap.add_argument('--currency', default='6', help='waluta priceoverview (6=PLN, 3=EUR, 1=USD)')
    ap.add_argument('--undercut', type=int, default=0, help='o ile groszy podbić cenę kupującego w dół')
    ap.add_argument('--delay', type=float, default=3.5, help='przerwa między żądaniami (s) — Steam mocno rate-limituje')
    ap.add_argument('--noninteractive', action='store_true',
                    help='tryb cron: gdy logowanie wygasło -> alert TG i wyjście, bez czekania na QR')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    appid, contextid = args.app.split('/')
    ck = steam_auth.get_cookies(interactive=not args.noninteractive)
    steamid = ck['_steamid']
    session = ck['sessionid']

    s = make_session(ck)

    bal, sym = fetch_wallet(s)
    if bal is not None:
        print(f"Portfel Steam: {bal // 100},{bal % 100:02d} {sym}".rstrip())

    inv = fetch_inventory(s, steamid, appid, contextid)
    if not inv or not inv.get('assets'):
        sys.exit("Pusty/niedostępny inventory — sprawdź ciasteczka (mogły wygasnąć).")

    cards = marketable_items(inv, args.types)
    counts, to_sell = pick_duplicates(cards)

    print(f"Przedmiotów: {len(cards)}, rodzajów: {len(counts)}, duplikatów do sprzedania: {len(to_sell)}")

    price_cache = {}
    for c in to_sell:
        name = c['name']
        if name not in price_cache:
            try:
                price_cache[name] = fetch_price(s, appid, name, args.currency)
            except RateLimited:
                sys.exit("Steam ogranicza zapytania o ceny (HTTP 429 — za dużo żądań "
                         "z tego IP). Odczekaj kilkanaście–kilkadziesiąt minut i spróbuj "
                         "ponownie (większy --delay pomaga).")
            time.sleep(args.delay)  # ponytail: stały odstęp, priceoverview ~20 żądań/min
        buyer = price_cache[name] - args.undercut
        receive = buyer_price_to_receive(buyer)
        if receive <= 0:
            print(f"  ! brak/za niska cena: {name} — pomijam"); continue

        line = f"  {name}: kupujący {buyer}gr -> dostajesz {receive}gr (asset {c['assetid']})"
        if not args.sell:
            print(line + " [dry-run]"); continue

        resp = sell_item(s, session, steamid, appid, contextid, c['assetid'], receive)
        ok = resp.get('success')
        print(line + (" ✓ wystawione (potwierdź w apce)" if ok else f" ✗ {resp.get('message', resp)}"))
        time.sleep(args.delay)

    if not args.sell:
        print("\nDRY-RUN. Dodaj --sell aby naprawdę wystawić.")
    else:
        print("\nTeraz: apka Steam Mobile -> Potwierdzenia -> Zatwierdź wszystko.")


if __name__ == '__main__':
    main()
