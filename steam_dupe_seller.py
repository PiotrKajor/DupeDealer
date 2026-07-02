#!/usr/bin/env python3
"""Wystawia duplikaty kart Steam na rynku — zawsze zostawia 1 z każdego rodzaju.
Potwierdzenia robisz RĘCZNIE w apce Steam Mobile (Potwierdzenia -> Zatwierdź wszystko).

Logowanie ogarnia steam_auth.py (refresh token + QR-login z linkiem na Telegram) —
NIE trzeba już wklejać ciasteczek. Pierwsze uruchomienie interaktywnie zaloguje przez QR.

DOMYŚLNIE DRY-RUN. Realnie wystawia dopiero z flagą --sell.
--noninteractive: tryb cron (nie czeka na logowanie, tylko alertuje na TG).
Zależność: requests, steam_auth (protobuf==3.20.3, steam).
"""
import argparse, os, re, sys, time
import requests
import steam_auth

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


def selftest():
    assert buyer_price_to_receive(4) == 2, buyer_price_to_receive(4)
    assert buyer_price_to_receive(100) == 88, buyer_price_to_receive(100)
    assert parse_price('0,04 zł') == 4 and parse_price('$1.23') == 123
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

    s = requests.Session()
    s.cookies.update({'steamLoginSecure': ck['steamLoginSecure'], 'sessionid': session})
    s.headers.update({'User-Agent': 'Mozilla/5.0',
                      'Referer': f"https://steamcommunity.com/profiles/{steamid}/inventory",
                      'X-Requested-With': 'XMLHttpRequest'})

    # --- inventory (count max 2000 dla tego endpointu) ---
    inv = s.get(f"https://steamcommunity.com/inventory/{steamid}/{appid}/{contextid}",
                params={'l': 'english', 'count': 2000}, timeout=30).json()
    if not inv or not inv.get('assets'):
        sys.exit("Pusty/niedostępny inventory — sprawdź ciasteczka (mogły wygasnąć).")
    desc = {(d['classid'], d['instanceid']): d for d in inv['descriptions']}

    wanted = [t.strip() for t in args.types.split(',') if t.strip()]  # pusty = bez filtra typu
    cards = []
    for a in inv['assets']:
        d = desc[(a['classid'], a['instanceid'])]
        typ = d.get('type', '')
        if d.get('marketable') and (not wanted or any(w in typ for w in wanted)):
            cards.append({'assetid': a['assetid'], 'name': d['market_hash_name']})

    from collections import Counter
    counts = Counter(c['name'] for c in cards)
    seen, to_sell = Counter(), []
    for c in cards:
        if seen[c['name']] < counts[c['name']] - 1:   # zostaw jeden z każdego rodzaju
            to_sell.append(c); seen[c['name']] += 1

    print(f"Przedmiotów: {len(cards)}, rodzajów: {len(counts)}, duplikatów do sprzedania: {len(to_sell)}")

    price_cache = {}
    for c in to_sell:
        name = c['name']
        if name not in price_cache:
            r = s.get("https://steamcommunity.com/market/priceoverview/",
                      params={'appid': appid, 'market_hash_name': name, 'currency': args.currency},
                      timeout=30).json()
            price_cache[name] = parse_price(r['lowest_price']) if r.get('lowest_price') else 0
            time.sleep(args.delay)  # ponytail: stały odstęp, priceoverview ~20 żądań/min
        buyer = price_cache[name] - args.undercut
        receive = buyer_price_to_receive(buyer)
        if receive <= 0:
            print(f"  ! brak/za niska cena: {name} — pomijam"); continue

        line = f"  {name}: kupujący {buyer}gr -> dostajesz {receive}gr (asset {c['assetid']})"
        if not args.sell:
            print(line + " [dry-run]"); continue

        resp = s.post("https://steamcommunity.com/market/sellitem/",
                      data={'sessionid': session, 'appid': appid, 'contextid': contextid,
                            'assetid': c['assetid'], 'amount': 1, 'price': receive},
                      headers={'Referer': f"https://steamcommunity.com/profiles/{steamid}/inventory"},
                      timeout=30).json()
        ok = resp.get('success')
        print(line + (" ✓ wystawione (potwierdź w apce)" if ok else f" ✗ {resp.get('message', resp)}"))
        time.sleep(args.delay)

    if not args.sell:
        print("\nDRY-RUN. Dodaj --sell aby naprawdę wystawić.")
    else:
        print("\nTeraz: apka Steam Mobile -> Potwierdzenia -> Zatwierdź wszystko.")


if __name__ == '__main__':
    main()
