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
    # ponytail: UA zostaje krótki. Zmierzone: priceoverview BEZ ciasteczek odrzuca
    # (429) pełny UA przeglądarki, a przepuszcza „Mozilla/5.0"; z ciasteczkami
    # przechodzą oba. Podszywanie się pod Chrome nic nie daje, a bywa gorsze.
    s.headers.update({'User-Agent': 'Mozilla/5.0',
                      'Referer': f"https://steamcommunity.com/profiles/{cookies['_steamid']}/inventory",
                      'X-Requested-With': 'XMLHttpRequest'})
    return s


PRICE_TTL = 24 * 3600      # ceny kart pełzają groszami — doba w zupełności wystarcza


def price_cache_path():
    """Plik cache cen: obok tokenu (%APPDATA%\\DupeDealer na Windowsie)."""
    base = (os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'DupeDealer')
            if os.name == 'nt' else os.path.expanduser('~/.dupedealer'))
    return os.path.join(base, 'prices.json')


def load_prices(currency, source='market', path=None):
    """Wczytuje niewygasłe ceny {nazwa: grosze} dla danej waluty.

    Cache TYLKO w pamięci procesu oznaczał, że każde uruchomienie odpytywało Steam od
    nowa o te same karty — a `priceoverview` po przekroczeniu limitu banuje IP na
    godziny. Trzymanie cen na dysku usuwa większość zapytań, nie tylko je spowalnia.
    """
    try:
        with open(path or price_cache_path(), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {name: rec['cents'] for name, rec in data.get(f'{currency}:{source}', {}).items()
            if isinstance(rec, dict) and now - rec.get('ts', 0) < PRICE_TTL}


def save_prices(cache, currency, source='market', path=None):
    """Dopisuje ceny do cache na dysku (inne waluty zostawia nietknięte)."""
    path = path or price_cache_path()
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    now = time.time()
    data.setdefault(f'{currency}:{source}', {}).update(
        {name: {'cents': cents, 'ts': now} for name, cents in cache.items()})
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        return path
    except OSError:
        return ''


MULTISELL_URL = "https://steamcommunity.com/market/multisell"
MULTISELL_BATCH = 40      # tyle nazw naraz mieści się w URL-u bez ryzyka obcięcia


def parse_multisell(html, requested):
    """HTML strony multisell -> {market_hash_name: grosze} (najwyższa oferta kupna).

    Nazwy i ceny siedzą w dwóch osobnych blokach strony, powiązane wyłącznie
    kolejnością — `data-assetid` w wierszu to identyfikator rynkowy, nie ten z naszego
    ekwipunku, więc nie da się po nim mapować. Dlatego zanim cokolwiek zwrócimy,
    sprawdzamy, że obie listy mają tę samą długość i że zestaw nazw dokładnie pokrywa
    to, o co pytaliśmy. Gdy się nie zgadza (Steam pominął pozycję, zmienił układ
    strony), zwracamy pustkę — wołający wróci do priceoverview. Zgadywanie
    przesuniętych cen wystawiłoby karty po cudzych kwotach, a to realna strata.
    """
    names = [json.loads(f'"{raw}"') for raw in
             re.findall(r'"market_hash_name":"((?:[^"\\]|\\.)*)"', html)]
    # leniwe [^>]*? — zachłanne przeskakiwałoby do ostatniego value= w tekście
    prices = re.findall(r'name="sell_\d+_price_paid"[^>]*?value="([^"]*)"', html)
    if not names or len(names) != len(prices) or set(names) != set(requested):
        return {}
    return {name: cents for name, raw in zip(names, prices)
            if (cents := parse_price(raw))}


def fetch_prices_multisell(s, appid, contextid, wanted, batch=MULTISELL_BATCH):
    """Wycena hurtem: JEDNO żądanie na ~40 pozycji zamiast jednego na pozycję.

    Obchodzi limit `priceoverview` (który po przekroczeniu banuje IP na godziny),
    bo strona multisell wycenia całą listę naraz — 23 duplikaty to 1 żądanie zamiast 23.

    UWAGA na semantykę: zwraca cenę **najwyższej oferty kupna** (sprzedaż od ręki),
    a nie najniższej oferty sprzedaży jak priceoverview. Bywa zauważalnie niższa
    (zmierzone: 0,19 zł wobec 0,40 zł), więc te dwa źródła nie mogą trafiać do
    wspólnego cache ani być mieszane bez poinformowania użytkownika.
    """
    names = sorted(set(wanted))
    out = {}
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        r = s.get(MULTISELL_URL, timeout=45,
                  params={'appid': appid, 'contextid': contextid, 'items[]': chunk})
        if r.status_code == 429:
            raise RateLimited(diag_report(r, s))
        out.update(parse_multisell(r.text, chunk))
    return out


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
    """Steam odrzucił zapytanie o cenę (HTTP 429).

    Dwa różne zdarzenia pod jednym kodem:
    - przekroczony limit tempa (~20 żądań/min na IP) — wołający ma wyhamować,
      bo każde kolejne pukanie przedłuża blokadę;
    - odmowa dla klienta/adresu już przy PIERWSZYM żądaniu — wtedy czekanie nic
      nie da i trzeba obejrzeć `diag` (nagłówki odpowiedzi wskazują, kto odciął:
      Steam czy warstwa pośrednia).

    `diag` = surowe dane odpowiedzi do raportu, `None` gdy nie zebrano.
    """

    def __init__(self, diag=None):
        super().__init__("Steam odrzucił zapytanie o cenę (HTTP 429)")
        self.diag = diag


def diag_report(resp, session=None):
    """Tekstowy raport o odrzuconym żądaniu — do wklejenia przy zgłaszaniu błędu.

    Zbiera to, czego nie widać z zewnątrz: nagłówki odpowiedzi (zdradzają, czy odciął
    Steam, czy CDN/proxy po drodze), wersje bibliotek i OpenSSL (odcisk TLS zależy od
    nich, a Steam potrafi po nim filtrować) oraz proxy widziane przez requests.
    """
    import platform, ssl
    lines = [
        f"URL:        {resp.url}",
        f"Status:     {resp.status_code}",
        f"Treść:      {resp.text[:200]!r}",
        f"Nagłówki odpowiedzi:",
    ]
    lines += [f"  {k}: {v}" for k, v in resp.headers.items()]
    lines += [
        f"Nagłówki żądania:",
        *[f"  {k}: {v}" for k, v in resp.request.headers.items() if k.lower() != 'cookie'],
        f"Ciasteczka wysłane: {'tak' if resp.request.headers.get('Cookie') else 'NIE'}",
        f"Python:     {platform.python_version()} / {platform.platform()}",
        f"OpenSSL:    {ssl.OPENSSL_VERSION}",
        f"requests:   {requests.__version__}",
        f"proxy:      {getattr(session, 'proxies', None)} | env: {requests.utils.getproxies()}",
    ]
    return "\n".join(lines)


def fetch_price(s, appid, name, currency):
    """priceoverview -> cena kupującego w groszach (0 = brak oferty na rynku).

    Rzuca RateLimited przy HTTP 429. Odporne na pustą odpowiedź (`null`): przy 429
    ciało to `null`, więc naiwne `.json()['lowest_price']` sypało `AttributeError`.
    """
    resp = s.get("https://steamcommunity.com/market/priceoverview/",
                 params={'appid': appid, 'market_hash_name': name, 'currency': currency},
                 timeout=30)
    if resp.status_code == 429:
        raise RateLimited(diag_report(resp, s))
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

    # cache cen: to on decyduje, ile razy pytamy Steama — a nadmiar pytań = ban IP
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'prices.json')
        save_prices({'A': 24, 'B': 40}, '6', path=p)
        assert load_prices('6', path=p) == {'A': 24, 'B': 40}
        save_prices({'C': 15}, '6', path=p)
        assert load_prices('6', path=p) == {'A': 24, 'B': 40, 'C': 15}  # dopisuje, nie nadpisuje
        assert load_prices('1', path=p) == {}                       # inna waluta = inny zestaw
        # ceny z multisell (oferty kupna) są NIŻSZE — nie mogą wyciec do wyceny rynkowej
        save_prices({'A': 19}, '6', source='buy', path=p)
        assert load_prices('6', source='buy', path=p) == {'A': 19}
        assert load_prices('6', path=p)['A'] == 24
        stale = json.load(open(p, encoding='utf-8'))
        stale['6:market']['A']['ts'] -= PRICE_TTL + 60              # postarz jeden wpis
        json.dump(stale, open(p, 'w', encoding='utf-8'))
        assert load_prices('6', path=p) == {'B': 40, 'C': 15}, "wygasłe ceny mają odpaść"
        assert load_prices('6', path=os.path.join(d, 'nie-ma.json')) == {}

    # multisell: przy jakiejkolwiek niezgodności nazw i cen NIE wolno zgadywać
    html = ('<input name="sell_1_price_paid" value="0,21 zł">'
            '<input name="sell_2_price_paid" value="0,13 zł">'
            '"market_hash_name":"A""market_hash_name":"B"')
    assert parse_multisell(html, ['A', 'B']) == {'A': 21, 'B': 13}
    assert parse_multisell(html, ['A', 'B', 'C']) == {}, "brakująca pozycja = odmowa mapowania"
    assert parse_multisell(html, ['A', 'X']) == {}, "inny zestaw nazw = odmowa mapowania"
    assert parse_multisell('', ['A']) == {}
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
    ap.add_argument('--market-price', action='store_true',
                    help='wycena rynkowa (najniższa oferta sprzedaży) — 1 zapytanie na pozycję;\n domyślnie hurtem przez multisell: 1 zapytanie na całą listę')
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

    source = 'market' if args.market_price else 'buy'
    cached = load_prices(args.currency, source)   # ceny z ostatniej doby — bez pytania Steama
    price_cache = dict(cached)
    if cached:
        print(f"Cache cen: {len(cached)} pozycji z ostatniej doby.")

    if source == 'buy':
        # całą listę wycenia JEDNO zapytanie — priceoverview banuje IP, multisell nie
        todo = sorted({c['name'] for c in to_sell} - set(price_cache))
        if todo:
            try:
                price_cache.update(fetch_prices_multisell(s, appid, contextid, todo))
            except RateLimited as e:
                if e.diag:
                    print("--- odpowiedź Steama ---", e.diag, "---", sep="\n", file=sys.stderr)
                sys.exit("Steam odrzucił nawet wycenę hurtem (HTTP 429) — jedno zapytanie na "
                         "całą listę, więc to nie tempo. Ten adres IP ma blokadę rynku; "
                         "odczekaj ~6 godzin bez ani jednej próby.")

    for c in to_sell:
        name = c['name']
        if name not in price_cache:
            if source == 'buy':
                print(f"  ! brak ceny hurtowej: {name} — pomijam"); continue
            try:
                price_cache[name] = fetch_price(s, appid, name, args.currency)
            except RateLimited as e:
                fresh = {k: v for k, v in price_cache.items() if k not in cached}
                if fresh:
                    save_prices(fresh, args.currency, source)   # co ugrane, to ugrane
                    sys.exit(f"Steam ogranicza zapytania o ceny (HTTP 429 — za dużo żądań "
                             f"z tego IP; wyceniono {len(fresh)}, zapisane w cache). "
                             f"Odczekaj i spróbuj ponownie — kolejny przebieg zapyta "
                             f"tylko o resztę (większy --delay pomaga).")
                if e.diag:               # odmowa od pierwszego żądania — pokaż szczegóły
                    print("--- odpowiedź Steama ---", e.diag, "---", sep="\n", file=sys.stderr)
                sys.exit("Steam odrzucił już pierwsze zapytanie o cenę (HTTP 429). Ten adres IP "
                         "ma nałożoną blokadę rynku — trwa ona kilka godzin i KAŻDA kolejna "
                         "próba ją przedłuża. Nie ponawiaj: odczekaj ~6 godzin bez ani jednego "
                         "uruchomienia, potem zacznij od --delay 10.")
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

    fresh = {k: v for k, v in price_cache.items() if k not in cached}
    if fresh:
        save_prices(fresh, args.currency, source)

    if not args.sell:
        print("\nDRY-RUN. Dodaj --sell aby naprawdę wystawić.")
    else:
        print("\nTeraz: apka Steam Mobile -> Potwierdzenia -> Zatwierdź wszystko.")


if __name__ == '__main__':
    main()
