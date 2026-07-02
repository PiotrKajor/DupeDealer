#!/usr/bin/env python3
"""Sesja web Steam bez ręcznego wklejania ciasteczek.

Trzyma refresh token w ~/.steam_refresh_token (przestawialne przez env
STEAM_TOKEN_FILE albo set_token_path() — GUI na Windowsie używa
%APPDATA%\\DupeDealer\\refresh_token) i po cichu generuje z niego
ciasteczko `steamLoginSecure` (bez interakcji). Gdy tokenu brak lub wygasł:
startuje QR-login, wysyła link Steam na Telegram (otwierasz na telefonie z apką
Steam -> Zatwierdź), odpytuje aż zatwierdzisz, zapisuje nowy token.

CLI:
    python3 steam_auth.py --login    # wymuś logowanie QR (link na Telegram)
    python3 steam_auth.py --cookie   # wypisz aktualne ciasteczka (debug)

Zależności: requests, protobuf==3.20.3, steam (użyte TYLKO protobufy z pakietu).
"""
import argparse, base64, json, os, secrets as pysecrets, sys, time
import requests
import rsa
from steam.protobufs import steammessages_auth_pb2 as A

API = "https://api.steampowered.com/IAuthenticationService/{}/v1/"
# Ścieżka refresh tokenu: env STEAM_TOKEN_FILE > domyślna linuksowa. GUI na Windowsie
# przestawia ją przez set_token_path() na %APPDATA%\DupeDealer\refresh_token.
TOKEN_FILE = os.environ.get("STEAM_TOKEN_FILE") or os.path.expanduser("~/.steam_refresh_token")
# Opcjonalny plik z sekretami (STEAM_LOGIN/STEAM_PASSWORD/TG_*) w formacie KEY=VALUE.
# Ścieżkę nadpisujesz env STEAM_SECRETS_FILE; najprościej podać dane wprost przez env.
SECRETS = os.environ.get("STEAM_SECRETS_FILE") or "/etc/DupeDealer/secrets"
DEVICE_NAME = "DupeDealer"
PLATFORM_WEB = A.k_EAuthTokenPlatformType_WebBrowser  # 2
OS_WEB = -500  # EOSType Web
POLL_TIMEOUT = 180  # s na zatwierdzenie w apce


def set_token_path(path):
    """Przestawia ścieżkę pliku refresh tokenu (dla GUI/Windows)."""
    global TOKEN_FILE
    TOKEN_FILE = path


def _secrets():
    d = {}
    try:
        for line in open(SECRETS):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                d[k] = v
    except (FileNotFoundError, PermissionError):
        pass
    return d


def _tg(text):
    s = _secrets()
    tok = os.environ.get("TG_TOKEN") or s.get("TG_TOKEN")
    chat = os.environ.get("TG_CHAT_ID") or s.get("TG_CHAT_ID")
    if not tok or not chat:
        print("[tg] brak TG_TOKEN/TG_CHAT_ID — pomijam powiadomienie")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": text, "disable_web_page_preview": True},
                      timeout=15)
    except Exception as e:
        print("[tg] błąd:", e)


def _call(method, msg, http="POST"):
    """Wywołanie protobuf do IAuthenticationService, zwraca surowe bajty odpowiedzi.

    Read-only metody (GetPasswordRSAPublicKey) są GET z param w query, reszta POST.
    """
    enc = base64.b64encode(msg.SerializeToString()).decode()
    url = API.format(method)
    if http == "GET":
        r = requests.get(url, params={"input_protobuf_encoded": enc}, timeout=30)
    else:
        r = requests.post(url, data={"input_protobuf_encoded": enc}, timeout=30)
    r.raise_for_status()
    return r.content


def _steamid_from_jwt(tok):
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


def _save_token(rt):
    d = os.path.dirname(TOKEN_FILE)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(rt.strip() + "\n")
    os.chmod(TOKEN_FILE, 0o600)


def _load_token():
    try:
        return open(TOKEN_FILE).read().strip() or None
    except FileNotFoundError:
        return None


def begin_qr():
    req = A.CAuthentication_BeginAuthSessionViaQR_Request()
    req.device_friendly_name = DEVICE_NAME
    req.platform_type = PLATFORM_WEB
    req.device_details.device_friendly_name = DEVICE_NAME
    req.device_details.platform_type = PLATFORM_WEB
    req.device_details.os_type = OS_WEB
    resp = A.CAuthentication_BeginAuthSessionViaQR_Response()
    resp.ParseFromString(_call("BeginAuthSessionViaQR", req))
    if not resp.challenge_url:
        raise RuntimeError("BeginAuthSessionViaQR nie zwrócił challenge_url")
    return resp


def poll_until_approved(resp, timeout=POLL_TIMEOUT, should_cancel=None):
    """Odpytuje status sesji aż user zatwierdzi w apce. `should_cancel()` (opcjonalny,
    dla GUI) pozwala przerwać czekanie — sprawdzany co pół sekundy."""
    client_id, request_id = resp.client_id, resp.request_id
    interval = max(2, int(resp.interval or 5))
    deadline = time.time() + timeout
    while time.time() < deadline:
        wake = time.time() + interval
        while time.time() < wake:
            if should_cancel and should_cancel():
                raise RuntimeError("logowanie anulowane")
            time.sleep(0.5)
        pr = A.CAuthentication_PollAuthSessionStatus_Request()
        pr.client_id = client_id
        pr.request_id = request_id
        pres = A.CAuthentication_PollAuthSessionStatus_Response()
        pres.ParseFromString(_call("PollAuthSessionStatus", pr))
        if pres.new_client_id:
            client_id = pres.new_client_id
        if pres.refresh_token:
            return pres.refresh_token
    raise TimeoutError("QR-login: brak zatwierdzenia w apce Steam w limicie czasu")


def qr_login():
    resp = begin_qr()
    _tg("🔐 Bot kart Steam prosi o logowanie.\n\nOtwórz ten link na telefonie z apką "
        "Steam i zatwierdź logowanie (masz ~3 min):\n" + resp.challenge_url)
    print("Link QR (wysłany też na Telegram):", resp.challenge_url)
    rt = poll_until_approved(resp)
    _save_token(rt)
    _tg("✅ Bot kart Steam: zalogowano, token zapisany (ważny wiele miesięcy).")
    print("Zalogowano, token zapisany do", TOKEN_FILE)
    return rt


def _encrypt_password(account_name, password):
    req = A.CAuthentication_GetPasswordRSAPublicKey_Request()
    req.account_name = account_name
    resp = A.CAuthentication_GetPasswordRSAPublicKey_Response()
    resp.ParseFromString(_call("GetPasswordRSAPublicKey", req, http="GET"))
    pub = rsa.PublicKey(int(resp.publickey_mod, 16), int(resp.publickey_exp, 16))
    enc = base64.b64encode(rsa.encrypt(password.encode("utf-8"), pub)).decode()
    return enc, resp.timestamp


def credentials_login(account=None, password=None, should_cancel=None):
    """Logowanie login+hasło z potwierdzeniem w apce Steam (bez linku, bez kodu).

    `account`/`password` podane wprost (GUI) mają pierwszeństwo; bez nich bierze
    STEAM_LOGIN/STEAM_PASSWORD z env lub z pliku SECRETS (patrz na górze modułu).
    Konto z mobilnym authenticatorem -> Steam wysyła push do apki, user klika Zatwierdź.
    """
    s = _secrets()
    account = account or os.environ.get("STEAM_LOGIN") or s.get("STEAM_LOGIN")
    password = password or os.environ.get("STEAM_PASSWORD") or s.get("STEAM_PASSWORD")
    if not account or not password:
        raise RuntimeError("brak loginu/hasła — podaj je wprost albo ustaw "
                           "STEAM_LOGIN/STEAM_PASSWORD (env lub plik STEAM_SECRETS_FILE)")

    enc_pw, ts = _encrypt_password(account, password)
    req = A.CAuthentication_BeginAuthSessionViaCredentials_Request()
    req.account_name = account
    req.encrypted_password = enc_pw
    req.encryption_timestamp = ts
    req.remember_login = True
    req.persistence = 1  # k_ESessionPersistence_Persistent
    req.website_id = "Community"
    req.device_friendly_name = DEVICE_NAME
    req.platform_type = PLATFORM_WEB
    req.device_details.device_friendly_name = DEVICE_NAME
    req.device_details.platform_type = PLATFORM_WEB
    req.device_details.os_type = OS_WEB
    resp = A.CAuthentication_BeginAuthSessionViaCredentials_Response()
    resp.ParseFromString(_call("BeginAuthSessionViaCredentials", req))
    if not resp.client_id:
        raise RuntimeError("logowanie odrzucone (złe hasło?) — brak client_id")

    types = {c.confirmation_type for c in resp.allowed_confirmations}
    if A.k_EAuthSessionGuardType_DeviceConfirmation in types:
        _tg("🔐 Bot kart Steam: otwórz apkę Steam Mobile — pojawi się prośba o "
            "potwierdzenie logowania. Kliknij Zatwierdź (masz ~3 min).")
        print("Otwórz apkę Steam i zatwierdź logowanie...")
    elif A.k_EAuthSessionGuardType_DeviceCode in types:
        raise RuntimeError("konto wymaga KODU z authenticatora (nie push) — użyj --code <kod>")
    else:
        raise RuntimeError(f"nieobsługiwany typ potwierdzenia: {types}")

    rt = poll_until_approved(resp, should_cancel=should_cancel)
    _save_token(rt)
    _tg("✅ Bot kart Steam: zalogowano, token zapisany (ważny wiele miesięcy).")
    print("Zalogowano, token zapisany do", TOKEN_FILE)
    return rt


def web_cookies(refresh_token):
    """Zamienia refresh token na ciasteczka web steamcommunity.com.

    Kanoniczny flow przeglądarki: jwt/finalizelogin -> settoken. Rzuca gdy token
    wygasł/nieważny (wtedy woła się ponowne logowanie).
    """
    sid = pysecrets.token_hex(12)
    s = requests.Session()
    s.cookies.set("sessionid", sid, domain="steamcommunity.com")
    fl = s.post("https://login.steampowered.com/jwt/finalizelogin",
                data={"nonce": refresh_token, "sessionid": sid,
                      "redir": "https://steamcommunity.com/login/home/?goto="},
                headers={"Origin": "https://steamcommunity.com",
                         "Referer": "https://steamcommunity.com/"}, timeout=30).json()
    steamid = fl.get("steamID")
    if not steamid or not fl.get("transfer_info"):
        raise RuntimeError("finalizelogin nie zwrócił sesji (refresh token wygasł?)")
    ti = next(t for t in fl["transfer_info"] if "steamcommunity.com" in t["url"])
    s.post(ti["url"], data={"steamID": steamid, "nonce": ti["params"]["nonce"],
                            "auth": ti["params"]["auth"]}, timeout=30)
    sls = s.cookies.get("steamLoginSecure", domain="steamcommunity.com")
    if not sls:
        raise RuntimeError("settoken nie ustawił steamLoginSecure")
    return {"steamLoginSecure": sls, "sessionid": sid, "_steamid": steamid}


def get_cookies(interactive=True):
    """Ciasteczka do requests.Session dla steamcommunity/market.

    interactive=False (cron): gdy token wygasł -> alert TG i wyjątek, bez blokowania
    na czekanie. interactive=True (CLI): odpala logowanie push.
    """
    rt = _load_token()
    if rt:
        try:
            return web_cookies(rt)
        except Exception as e:
            print("[auth] refresh token nie działa (", e, ")")
    if not interactive:
        _tg("⚠️ Bot kart Steam: brak ważnego logowania. Uruchom `steam_auth.py --login`.")
        raise RuntimeError("brak ważnego refresh tokenu — wymagane logowanie")
    rt = credentials_login()
    return web_cookies(rt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true",
                    help="logowanie login+hasło z potwierdzeniem push w apce Steam")
    ap.add_argument("--qr", action="store_true", help="logowanie linkiem QR (do zeskanowania w apce)")
    ap.add_argument("--cookie", action="store_true", help="wypisz aktualne ciasteczka")
    args = ap.parse_args()
    if args.login:
        credentials_login()
    elif args.qr:
        qr_login()
    elif args.cookie:
        c = get_cookies(interactive=True)
        print("steamid:", c["_steamid"])
        print("sessionid:", c["sessionid"])
        print("steamLoginSecure:", c["steamLoginSecure"][:40], "...")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
