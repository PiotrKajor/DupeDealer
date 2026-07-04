#!/usr/bin/env python3
"""Aplikacja okienkowa (PySide6) dla bota duplikatów Steam — nakładka na
dupedealer.py + steam_auth.py. NIE duplikuje logiki: woła funkcje
make_session / fetch_inventory / marketable_items / pick_duplicates /
fetch_price / sell_item oraz logowanie ze steam_auth.

Każde żądanie sieciowe leci w wątku roboczym (QThread) — GUI nie zamarza.
Sekrety (hasło, token, ciasteczka) nie trafiają do UI ani do logu.
Ofert bot NIE potwierdza — po wystawieniu potwierdzasz ręcznie w apce
Steam Mobile (Potwierdzenia -> Zatwierdź wszystko).

Uruchomienie: python dupedealer_gui.py
Build .exe:   build.bat (PyInstaller, patrz README)
"""
import base64
import os
import re
import sys
import time
import urllib.parse

import requests
import steam_auth
import dupedealer as core
import tiny_qr
from gui_theme import build_qss, ACCENT, GREEN, RED, TEXT_DIM, YELLOW

from PySide6.QtCore import Qt, QSize, QThread, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QImage, QPainter,
                           QPainterPath, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDoubleSpinBox, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

# Na Windowsie token trzymamy w %APPDATA%\DupeDealer (brak linuksowego
# $HOME/ścieżek /etc); na Linuksie zostaje domyślna ścieżka steam_auth.
if os.name == 'nt':
    steam_auth.set_token_path(os.path.join(
        os.environ.get('APPDATA') or os.path.expanduser('~'),
        'DupeDealer', 'refresh_token'))

APPS = [("Karty Steam (753/6)", '753', '6', 'Trading Card'),
        ("TF2 (440/2)", '440', '2', ''),
        ("CS2 (730/2)", '730', '2', ''),
        ("Dota 2 (570/2)", '570', '2', '')]
CURRENCIES = [("PLN (zł)", '6', 'zł'), ("EUR (€)", '3', '€'), ("USD ($)", '1', '$')]
CONFIRM_HINT = "Teraz: apka Steam Mobile → Potwierdzenia → Zatwierdź wszystko."


def resource_path(name):
    """Ścieżka do pliku dołączonego do buildu (PyInstaller onefile) lub obok źródła."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def fmt_cents(cents, suffix):
    return f"{cents // 100},{cents % 100:02d} {suffix}".rstrip()


AVATAR_SIZE = 34        # bok awatara w nagłówku (px)
THUMB_SIZE = 32         # bok miniatury przedmiotu w tabeli (px)
FULL_MAX = 220          # maks. szerokość pełnego obrazka w dymku po najechaniu (px)


def square_pixmap(data, size, radius=6):
    """Bajty obrazka -> kwadratowy QPixmap `size`×`size` z lekko zaokrąglonymi rogami
    (awatar). Wyśrodkowane przycięcie do kwadratu. None gdy dane są złe."""
    src = QPixmap()
    if not data or not src.loadFromData(data):
        return None
    src = src.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    if src.width() != size or src.height() != size:      # przytnij nadmiar do kwadratu
        src = src.copy((src.width() - size) // 2, (src.height() - size) // 2, size, size)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, src)
    p.end()
    return out


def thumb_and_tooltip(data):
    """Bajty obrazka -> (miniatura QPixmap, HTML dymka z pełnym obrazkiem).

    Pełny obraz idzie do dymka jako data-URI base64 — dzięki temu Qt renderuje go
    bez dogrywania niczego z sieci przy najechaniu. (None, '') gdy dane są złe.
    """
    pix = QPixmap()
    if not data or not pix.loadFromData(data):
        return None, ''
    thumb = pix.scaled(THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    b64 = base64.b64encode(data).decode('ascii')
    width = min(pix.width(), FULL_MAX)
    tip = f'<img src="data:image/png;base64,{b64}" width="{width}">'
    return thumb, tip


# ---------------------------------------------------------------- workery ---
class AuthWorker(QThread):
    """Sprawdzenie sesji / logowanie push / logowanie QR — wszystko poza GUI-wątkiem."""
    status = Signal(str)
    qr_ready = Signal(str)              # challenge_url do narysowania
    done = Signal(object, str, str)     # cookies, persona, avatar_url
    failed = Signal(str)

    def __init__(self, mode, account=None, password=None, parent=None):
        super().__init__(parent)
        self.mode, self._account, self._password = mode, account, password
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            if self.mode == 'check':
                ck = steam_auth.get_cookies(interactive=False)
            elif self.mode == 'push':
                self.status.emit("Otwórz apkę Steam Mobile i zatwierdź logowanie (masz ~3 min)…")
                rt = steam_auth.credentials_login(self._account, self._password,
                                                  should_cancel=lambda: self._cancel)
                ck = steam_auth.web_cookies(rt)
            else:  # qr
                resp = steam_auth.begin_qr()
                self.qr_ready.emit(resp.challenge_url)
                self.status.emit("Zeskanuj kod QR w apce Steam Mobile (masz ~3 min)…")
                rt = steam_auth.poll_until_approved(resp, should_cancel=lambda: self._cancel)
                steam_auth._save_token(rt)
                ck = steam_auth.web_cookies(rt)
            persona, avatar = self._profile(ck)
            self.done.emit(ck, persona, avatar)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self._password = None   # hasło tylko w pamięci, na czas logowania

    @staticmethod
    def _profile(ck):
        """(nick, url_awatara) z publicznego XML profilu. Awatar może być pusty."""
        persona, avatar = ck['_steamid'], ''
        try:
            r = requests.get(f"https://steamcommunity.com/profiles/{ck['_steamid']}/?xml=1",
                             timeout=15)
            m = re.search(r'<steamID><!\[CDATA\[(.*?)\]\]>', r.text)
            if m:
                persona = m.group(1)
            a = re.search(r'<avatarMedium><!\[CDATA\[(.*?)\]\]>', r.text)
            if a:
                avatar = a.group(1)
        except Exception:
            pass
        return persona, avatar


class InventoryWorker(QThread):
    loaded = Signal(object, int, int)   # rows, total_items, kinds
    failed = Signal(str)

    def __init__(self, session, steamid, appid, contextid, types, parent=None):
        super().__init__(parent)
        self.s, self.steamid = session, steamid
        self.appid, self.contextid, self.types = appid, contextid, types

    def run(self):
        try:
            inv = core.fetch_inventory(self.s, self.steamid, self.appid, self.contextid)
            if not inv or not inv.get('assets'):
                self.failed.emit("Pusty/niedostępny ekwipunek — sesja mogła wygasnąć.")
                return
            items = core.marketable_items(inv, self.types)
            counts, to_sell = core.pick_duplicates(items)
            icons = {}                       # name -> hash obrazka (preferuj duży)
            for it in items:
                icons.setdefault(it['name'], it.get('icon_large') or it.get('icon') or '')
            groups = {}
            for c in to_sell:
                groups.setdefault(c['name'], []).append(c['assetid'])
            rows = [{'name': n, 'total': counts[n], 'assets': ids,
                     'icon': icons.get(n, '')}
                    for n, ids in groups.items()]
            self.loaded.emit(rows, len(items), len(counts))
        except Exception as e:
            self.failed.emit(f"Błąd pobierania ekwipunku: {e}")


class PriceWorker(QThread):
    """Wycena leniwa: priceoverview jest ostro rate-limitowane (~20/min),
    więc stały odstęp `delay` między żądaniami i cache po nazwie."""
    priced = Signal(str, int)       # name, buyer_cents (-1 = błąd)
    progress = Signal(int, int)
    done = Signal()

    def __init__(self, session, appid, currency, names, cache, delay, parent=None):
        super().__init__(parent)
        self.s, self.appid, self.currency = session, appid, currency
        self.names, self.cache, self.delay = names, cache, delay
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _sleep(self):
        end = time.time() + self.delay
        while time.time() < end and not self._cancel:
            time.sleep(0.1)

    def run(self):
        todo = [n for n in self.names if n not in self.cache]
        for n in self.names:
            if n in self.cache:
                self.priced.emit(n, self.cache[n])
        for i, name in enumerate(todo, 1):
            if self._cancel:
                return
            try:
                cents = core.fetch_price(self.s, self.appid, name, self.currency)
                self.cache[name] = cents
                self.priced.emit(name, cents)
            except Exception:
                self.priced.emit(name, -1)   # np. rate limit — nie cache'ujemy
            self.progress.emit(i, len(todo))
            if i < len(todo):
                self._sleep()
        self.done.emit()


class SellWorker(QThread):
    sold = Signal(str, str, bool, str, int)  # name, assetid, ok, msg, receive
    progress = Signal(int, int)
    done = Signal(int, int, int)             # ok_count, fail_count, sum_receive

    def __init__(self, session, sessionid, steamid, appid, contextid, tasks, delay,
                 parent=None):
        super().__init__(parent)
        self.s, self.sessionid, self.steamid = session, sessionid, steamid
        self.appid, self.contextid, self.tasks, self.delay = appid, contextid, tasks, delay
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ok_n = fail_n = total = 0
        for i, (name, assetid, receive) in enumerate(self.tasks, 1):
            if self._cancel:
                break
            try:
                resp = core.sell_item(self.s, self.sessionid, self.steamid,
                                      self.appid, self.contextid, assetid, receive)
                ok = bool(resp.get('success'))
                msg = '' if ok else str(resp.get('message', resp))
            except Exception as e:
                ok, msg = False, str(e)
            if ok:
                ok_n += 1; total += receive
            else:
                fail_n += 1
            self.sold.emit(name, assetid, ok, msg, receive)
            self.progress.emit(i, len(self.tasks))
            if i < len(self.tasks):
                end = time.time() + self.delay
                while time.time() < end and not self._cancel:
                    time.sleep(0.1)
        self.done.emit(ok_n, fail_n, total)


class WalletWorker(QThread):
    """Odczyt salda portfela Steam poza GUI-wątkiem (jedno żądanie do /market/)."""
    ready = Signal(int, str)        # grosze/centy (-1 = błąd/brak), symbol waluty

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.s = session

    def run(self):
        try:
            cents, sym = core.fetch_wallet(self.s)
        except Exception:
            cents, sym = None, ''
        self.ready.emit(cents if cents is not None else -1, sym)


class ImageWorker(QThread):
    """Pobiera obrazki (awatar, miniatury) sekwencyjnie poza GUI-wątkiem.

    `jobs` = lista (klucz, url). Dla każdego emituje ready(klucz, bajty) —
    bajty puste przy błędzie/pustym URL. QPixmap budujemy dopiero w GUI-wątku.
    """
    ready = Signal(str, bytes)      # klucz, bajty obrazka

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = list(jobs)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        for key, url in self.jobs:
            if self._cancel:
                return
            data = b''
            if url:
                try:
                    data = requests.get(url, timeout=20).content
                except Exception:
                    data = b''
            self.ready.emit(key, data)


# ---------------------------------------------------------------- dialogi ---
class LoginDialog(QDialog):
    """Login+hasło dla logowania push. Hasło NIE jest nigdzie zapisywane —
    idzie tylko do credentials_login() (RSA) i znika."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logowanie Steam")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(10)
        title = QLabel("Zaloguj do Steam")
        title.setObjectName("AppTitle")
        lay.addWidget(title)
        info = QLabel("Po wysłaniu danych Steam wyśle push do apki mobilnej —\n"
                      "kliknij tam „Zatwierdź”. Hasło nie jest zapisywane na dysku.")
        info.setObjectName("HintLabel")
        lay.addWidget(info)
        self.user = QLineEdit(); self.user.setPlaceholderText("login Steam")
        self.pw = QLineEdit(); self.pw.setPlaceholderText("hasło")
        self.pw.setEchoMode(QLineEdit.Password)
        lay.addWidget(self.user); lay.addWidget(self.pw)
        row = QHBoxLayout(); row.addStretch(1)
        cancel = QPushButton("Anuluj"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Zaloguj"); ok.setProperty("class", "accent")
        ok.setDefault(True); ok.clicked.connect(self.accept)
        row.addWidget(cancel); row.addWidget(ok)
        lay.addLayout(row)
        self.resize(360, 220)


class QrDialog(QDialog):
    """Rysuje kod QR (tiny_qr) do zeskanowania w apce Steam."""
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logowanie QR")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        title = QLabel("Zeskanuj w apce Steam")
        title.setObjectName("AppTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        self.qr_label = QLabel("Pobieram kod QR…")
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumSize(280, 280)
        self.qr_label.setStyleSheet("background: white; border-radius: 12px;")
        lay.addWidget(self.qr_label)
        hint = QLabel("Apka Steam Mobile → Steam Guard → „Zeskanuj kod QR”.\n"
                      "Nie otwieraj linku ręcznie — trzeba go ZESKANOWAĆ.")
        hint.setObjectName("HintLabel")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)
        btn = QPushButton("Anuluj")
        btn.clicked.connect(self.reject)
        lay.addWidget(btn, alignment=Qt.AlignCenter)

    def show_qr(self, url):
        matrix = tiny_qr.encode(url)
        n = len(matrix)
        quiet = 4                                   # strefa ciszy — wymagana do skanu
        side = n + 2 * quiet
        img = QImage(side, side, QImage.Format_RGB32)
        img.fill(QColor('white'))
        dark = QColor('#0b0e14')
        for r, row in enumerate(matrix):
            for c, v in enumerate(row):
                if v:
                    img.setPixelColor(c + quiet, r + quiet, dark)
        scale = max(1, 280 // side)                 # całkowita skala = równe moduły
        pix = QPixmap.fromImage(img.scaled(
            side * scale, side * scale, Qt.KeepAspectRatio, Qt.FastTransformation))
        self.qr_label.setPixmap(pix)

    def reject(self):
        self.cancelled.emit()
        super().reject()


# ------------------------------------------------------------ główne okno ---
class NumericItem(QTableWidgetItem):
    """Item sortowany po wartości liczbowej (UserRole), nie po tekście."""

    def __lt__(self, other):
        return (self.data(Qt.UserRole) or 0) < (other.data(Qt.UserRole) or 0)


class MainWindow(QMainWindow):
    (COL_CHECK, COL_ICON, COL_NAME, COL_TOTAL, COL_SELL,
     COL_PRICE, COL_RECV, COL_ASSET) = range(8)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DupeDealer")
        self.resize(1100, 760)
        ico = resource_path("app.ico")
        if os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))

        self.session = None          # requests.Session po zalogowaniu
        self.steamid = self.sessionid = None
        self.price_cache = {}        # market_hash_name -> buyer_cents (per appid+waluta)
        self._cache_key = None
        self._loaded_appid = None    # appid ekwipunku w tabeli (do linku na rynek)
        self.rows = {}               # name -> {'total','assets','buyer','items':{...}}
        self._workers = []
        self._price_worker = None
        self._thumb_worker = None
        self._thumb_cache = {}       # url obrazka -> (miniatura QPixmap, HTML dymka)
        self._filling = False
        self._qr_dialog = None

        self._build_ui()
        self._log("DupeDealer — bot NIE potwierdza ofert; po wystawieniu "
                  "zatwierdzasz je ręcznie w apce Steam Mobile.")
        self._start_auth_check()

    # ------------------------------------------------------------- układ ---
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        # nagłówek: tytuł + status logowania + przyciski logowania
        header = QFrame(); header.setObjectName("Header")
        h = QHBoxLayout(header); h.setContentsMargins(16, 12, 16, 12)
        tbox = QVBoxLayout(); tbox.setSpacing(0)
        t1 = QLabel("DupeDealer"); t1.setObjectName("AppTitle")
        t2 = QLabel("duplikaty kart → rynek Steam (zawsze zostaje 1 sztuka)")
        t2.setObjectName("AppSubtitle")
        tbox.addWidget(t1); tbox.addWidget(t2)
        h.addLayout(tbox)
        h.addStretch(1)
        # awatar konta (ukryty do zalogowania) + saldo portfela
        self.avatar_label = QLabel()
        self.avatar_label.setObjectName("Avatar")
        self.avatar_label.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        self.avatar_label.setVisible(False)
        h.addWidget(self.avatar_label)
        h.addSpacing(14)
        self.status_dot = QLabel("●"); self.status_dot.setObjectName("StatusDot")
        idbox = QVBoxLayout(); idbox.setSpacing(0)
        self.status_label = QLabel("Sprawdzam logowanie…")
        self.status_label.setObjectName("StatusLabel")
        self.wallet_label = QLabel("")
        self.wallet_label.setObjectName("WalletLabel")
        self.wallet_label.setVisible(False)
        idbox.addWidget(self.status_label); idbox.addWidget(self.wallet_label)
        h.addWidget(self.status_dot); h.addLayout(idbox)
        h.addSpacing(12)
        self.btn_login_push = QPushButton("Zaloguj (push w apce)")
        self.btn_login_push.clicked.connect(self._login_push)
        self.btn_login_qr = QPushButton("Zaloguj (QR)")
        self.btn_login_qr.clicked.connect(self._login_qr)
        h.addWidget(self.btn_login_push); h.addWidget(self.btn_login_qr)
        v.addWidget(header)

        # parametry + wczytanie ekwipunku
        card = QFrame(); card.setObjectName("Card")
        p = QHBoxLayout(card); p.setContentsMargins(16, 12, 16, 12); p.setSpacing(10)
        p.addWidget(QLabel("Ekwipunek:"))
        self.app_combo = QComboBox()
        for label, appid, ctx, types in APPS:
            self.app_combo.addItem(label, (appid, ctx, types))
        self.app_combo.currentIndexChanged.connect(self._app_changed)
        p.addWidget(self.app_combo)
        p.addWidget(QLabel("Typy:"))
        self.types_edit = QLineEdit("Trading Card")
        self.types_edit.setToolTip("po przecinku, np. 'Trading Card,Emoticon'; "
                                   "puste = wszystkie marketable duplikaty")
        self.types_edit.setMinimumWidth(170)
        p.addWidget(self.types_edit, 1)
        p.addWidget(QLabel("Waluta:"))
        self.cur_combo = QComboBox()
        for label, code, suffix in CURRENCIES:
            self.cur_combo.addItem(label, (code, suffix))
        p.addWidget(self.cur_combo)
        p.addWidget(QLabel("Undercut:"))
        self.undercut_spin = QSpinBox(); self.undercut_spin.setRange(0, 99)
        self.undercut_spin.setSuffix(" gr")
        self.undercut_spin.setToolTip("o ile groszy zejść poniżej ceny kupującego")
        self.undercut_spin.valueChanged.connect(self._recompute_receives)
        p.addWidget(self.undercut_spin)
        p.addWidget(QLabel("Odstęp:"))
        self.delay_spin = QDoubleSpinBox(); self.delay_spin.setRange(0.5, 15.0)
        self.delay_spin.setSingleStep(0.5); self.delay_spin.setValue(3.5)
        self.delay_spin.setSuffix(" s")
        self.delay_spin.setToolTip("przerwa między żądaniami — Steam mocno rate-limituje")
        p.addWidget(self.delay_spin)
        self.btn_load = QPushButton("Wczytaj ekwipunek")
        self.btn_load.setProperty("class", "accent")
        self.btn_load.setEnabled(False)
        self.btn_load.clicked.connect(self._load_inventory)
        p.addWidget(self.btn_load)
        v.addWidget(card)

        # tabela
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["", "", "Nazwa", "Ilość", "Do sprzedania", "Cena rynku", "Dostajesz", "Asset ID"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        for col in (self.COL_CHECK, self.COL_ICON, self.COL_TOTAL, self.COL_SELL,
                    self.COL_PRICE, self.COL_RECV):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(THUMB_SIZE + 8)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setMouseTracking(True)          # do kursora „rączki" nad miniaturą
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.entered.connect(self._cell_entered)
        v.addWidget(self.table, 1)

        # pasek akcji pod tabelą
        actions = QHBoxLayout()
        self.btn_all = QPushButton("Zaznacz wszystko")
        self.btn_all.clicked.connect(lambda: self._set_all_checks(True))
        self.btn_none = QPushButton("Odznacz wszystko")
        self.btn_none.clicked.connect(lambda: self._set_all_checks(False))
        actions.addWidget(self.btn_all); actions.addWidget(self.btn_none)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("HintLabel")
        actions.addSpacing(12)
        actions.addWidget(self.summary_label)
        actions.addStretch(1)
        self.btn_dry = QPushButton("Podgląd (dry-run)")
        self.btn_dry.setEnabled(False)
        self.btn_dry.clicked.connect(self._dry_run)
        self.btn_sell = QPushButton("Wystaw zaznaczone")
        self.btn_sell.setProperty("class", "danger")
        self.btn_sell.setEnabled(False)
        self.btn_sell.clicked.connect(self._sell_selected)
        actions.addWidget(self.btn_dry); actions.addWidget(self.btn_sell)
        v.addLayout(actions)

        # postęp
        prow = QHBoxLayout()
        self.progress = QProgressBar(); self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10); self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label = QLabel(""); self.progress_label.setObjectName("HintLabel")
        prow.addWidget(self.progress, 1); prow.addWidget(self.progress_label)
        v.addLayout(prow)

        # log
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setFixedHeight(150)
        v.addWidget(self.log)

    # ---------------------------------------------------------- pomocnicze ---
    def _log(self, text, color=None):
        if color:
            self.log.appendHtml(f'<span style="color:{color}">{text}</span>')
        else:
            self.log.appendPlainText(text)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _track(self, worker):
        self._workers.append(worker)
        worker.finished.connect(lambda: self._workers.remove(worker))
        worker.start()
        return worker

    def _currency(self):
        return self.cur_combo.currentData()          # (code, suffix)

    def _set_status(self, text, color):
        self.status_label.setText(text)
        self.status_dot.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------ logowanie ---
    def _start_auth_check(self):
        self._set_status("Sprawdzam logowanie…", TEXT_DIM)
        w = AuthWorker('check')
        w.done.connect(self._auth_ok)
        w.failed.connect(lambda e: self._auth_out())
        self._track(w)

    def _auth_ok(self, ck, persona, avatar=''):
        self.steamid, self.sessionid = ck['_steamid'], ck['sessionid']
        self.session = core.make_session(ck)
        self._set_status(f"Zalogowany jako {persona}", GREEN)
        self.btn_load.setEnabled(True)
        self.btn_login_push.setEnabled(True)
        self.btn_login_qr.setEnabled(True)
        if self._qr_dialog:
            self._qr_dialog.accept()
            self._qr_dialog = None
        self._log(f"✓ Zalogowano jako {persona}.", GREEN)
        if avatar:
            w = ImageWorker([('__avatar__', avatar)])
            w.ready.connect(self._avatar_ready)
            self._track(w)
        ww = WalletWorker(self.session)
        ww.ready.connect(self._wallet_ready)
        self._track(ww)

    def _auth_out(self, msg="Wylogowany"):
        self.session = None
        self._set_status(msg, RED)
        self.avatar_label.clear()
        self.avatar_label.setVisible(False)
        self.wallet_label.clear()
        self.wallet_label.setVisible(False)
        self.btn_load.setEnabled(False)
        self.btn_login_push.setEnabled(True)
        self.btn_login_qr.setEnabled(True)
        if self._qr_dialog:
            self._qr_dialog.close()
            self._qr_dialog = None

    # ------------------------------------------------------- awatar / portfel ---
    def _avatar_ready(self, key, data):
        pix = square_pixmap(data, AVATAR_SIZE)
        if pix:
            self.avatar_label.setPixmap(pix)
            self.avatar_label.setVisible(True)

    def _wallet_ready(self, cents, symbol):
        if cents is None or cents < 0:
            self.wallet_label.setVisible(False)
            return
        self.wallet_label.setText(f"Portfel: {fmt_cents(cents, symbol)}")
        self.wallet_label.setVisible(True)

    def _login_failed(self, err):
        self._auth_out()
        self._log(f"✗ Logowanie nieudane: {err}", RED)

    def _login_push(self):
        dlg = LoginDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        account, password = dlg.user.text().strip(), dlg.pw.text()
        dlg.pw.clear()
        if not account or not password:
            self._log("Podaj login i hasło.", YELLOW)
            return
        self.btn_login_push.setEnabled(False)
        self.btn_login_qr.setEnabled(False)
        w = AuthWorker('push', account, password)
        w.status.connect(lambda s: self._set_status(s, YELLOW))
        w.done.connect(self._auth_ok)
        w.failed.connect(self._login_failed)
        self._track(w)

    def _login_qr(self):
        self.btn_login_push.setEnabled(False)
        self.btn_login_qr.setEnabled(False)
        self._qr_dialog = QrDialog(self)
        w = AuthWorker('qr')
        w.qr_ready.connect(self._qr_dialog.show_qr)
        w.status.connect(lambda s: self._set_status(s, YELLOW))
        w.done.connect(self._auth_ok)
        w.failed.connect(self._login_failed)
        self._qr_dialog.cancelled.connect(w.cancel)
        self._track(w)
        self._qr_dialog.exec()

    # ------------------------------------------------------------ ekwipunek ---
    def _app_changed(self):
        data = self.app_combo.currentData()
        if data:
            self.types_edit.setText(data[2])   # 753/6 -> 'Trading Card', reszta pusto

    def _load_inventory(self):
        if not self.session:
            return
        self._cancel_worker(PriceWorker)
        appid, ctx, _ = self.app_combo.currentData()
        cur, _suffix = self._currency()
        # cache cen jest ważny per appid+waluta — inna gra/waluta = nowy cache
        key = (appid, cur)
        if key != self._cache_key:
            self.price_cache = {}
            self._cache_key = key
        self._loaded_appid = appid       # link „otwórz na rynku" używa appid z tego wczytania
        self.btn_load.setEnabled(False)
        self.btn_dry.setEnabled(False)
        self.btn_sell.setEnabled(False)
        self.progress.setRange(0, 1); self.progress.setValue(0)   # zeruj po poprzednim biegu
        self.progress_label.setText("Pobieram ekwipunek…")
        w = InventoryWorker(self.session, self.steamid, appid, ctx,
                            self.types_edit.text())
        w.loaded.connect(self._inventory_loaded)
        w.failed.connect(self._inventory_failed)
        self._track(w)

    def _inventory_failed(self, err):
        self.btn_load.setEnabled(True)
        self.progress_label.setText("")
        self._log(f"✗ {err}", RED)

    def _inventory_loaded(self, rows, total_items, kinds):
        self.rows = {}
        self._filling = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, rec in enumerate(sorted(rows, key=lambda r: r['name'].lower())):
            name, sell_n = rec['name'], len(rec['assets'])
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked)          # domyślnie wszystko zaznaczone
            icon_it = QTableWidgetItem()
            icon_it.setTextAlignment(Qt.AlignCenter)
            name_it = QTableWidgetItem(name)
            total_it = NumericItem(str(rec['total']))
            total_it.setData(Qt.UserRole, rec['total'])
            total_it.setTextAlignment(Qt.AlignCenter)
            sell_it = NumericItem(str(sell_n))
            sell_it.setData(Qt.UserRole, sell_n)
            sell_it.setTextAlignment(Qt.AlignCenter)
            price_it = NumericItem("…")
            price_it.setData(Qt.UserRole, -2)
            price_it.setForeground(QColor(TEXT_DIM))
            recv_it = NumericItem("—")
            recv_it.setData(Qt.UserRole, -2)
            recv_it.setForeground(QColor(TEXT_DIM))
            asset_it = QTableWidgetItem(", ".join(rec['assets']))
            asset_it.setForeground(QColor(TEXT_DIM))
            for col, it in ((self.COL_CHECK, check), (self.COL_ICON, icon_it),
                            (self.COL_NAME, name_it), (self.COL_TOTAL, total_it),
                            (self.COL_SELL, sell_it), (self.COL_PRICE, price_it),
                            (self.COL_RECV, recv_it), (self.COL_ASSET, asset_it)):
                self.table.setItem(i, col, it)
            self.rows[name] = {'total': rec['total'], 'assets': rec['assets'],
                               'buyer': self.price_cache.get(name),
                               'icon': rec.get('icon', ''),
                               'check': check, 'icon_it': icon_it, 'name_it': name_it,
                               'price_it': price_it, 'recv_it': recv_it}
        self.table.setSortingEnabled(True)
        self._filling = False
        self.btn_load.setEnabled(True)
        dup_total = sum(len(r['assets']) for r in self.rows.values())
        self._log(f"Przedmiotów: {total_items}, rodzajów: {kinds}, "
                  f"duplikatów do sprzedania: {dup_total} ({len(self.rows)} pozycji).")
        if not self.rows:
            self.progress_label.setText("Brak duplikatów do sprzedania.")
            self._update_summary()
            return
        self.btn_dry.setEnabled(True)
        self.btn_sell.setEnabled(True)
        self._update_summary()
        self._load_thumbnails()
        self._start_pricing()

    # ------------------------------------------------------------- miniatury ---
    def _load_thumbnails(self):
        """Wrzuca miniatury z cache od razu, resztę dociąga jeden ImageWorker."""
        self._cancel_worker(ImageWorker)
        jobs = []
        for name, rec in self.rows.items():
            url = core.image_url(rec.get('icon', ''))
            if not url:
                continue
            cached = self._thumb_cache.get(url)
            if cached:
                self._apply_thumb(rec, *cached)
            else:
                jobs.append((name, url))
        if not jobs:
            return
        w = ImageWorker(jobs)
        w.ready.connect(self._thumb_ready)
        self._thumb_worker = w
        self._track(w)

    def _thumb_ready(self, name, data):
        rec = self.rows.get(name)
        if not rec:
            return
        thumb, tip = thumb_and_tooltip(data)
        if not thumb:
            return
        self._thumb_cache[core.image_url(rec.get('icon', ''))] = (thumb, tip)
        self._apply_thumb(rec, thumb, tip)

    def _apply_thumb(self, rec, thumb, tip):
        self._filling = True
        rec['icon_it'].setData(Qt.DecorationRole, thumb)
        # nad miniaturą: pełny obraz + podpowiedź, że klik otwiera przedmiot na rynku
        rec['icon_it'].setToolTip(
            tip + '<div align="center" style="color:#8b94a7">'
            'kliknij, aby otworzyć na rynku</div>')
        rec['name_it'].setToolTip(tip)      # dymek również nad nazwą przedmiotu
        self._filling = False

    def _cell_clicked(self, row, col):
        """Klik w miniaturę -> otwiera stronę przedmiotu na Rynku Społeczności."""
        if col != self.COL_ICON:
            return
        name_item = self.table.item(row, self.COL_NAME)
        appid = self._loaded_appid
        if not name_item or not appid:
            return
        name = name_item.text()
        url = (f"https://steamcommunity.com/market/listings/"
               f"{appid}/{urllib.parse.quote(name, safe='')}")
        QDesktopServices.openUrl(QUrl(url))
        self._log(f"↗ Otwieram na rynku: {name}", ACCENT)

    def _cell_entered(self, index):
        """Kursor „rączki" nad kolumną miniatury (sygnał wymaga mouse trackingu)."""
        if index.column() == self.COL_ICON:
            self.table.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self.table.viewport().unsetCursor()

    # --------------------------------------------------------------- wycena ---
    def _start_pricing(self):
        appid, _, _ = self.app_combo.currentData()
        cur, _ = self._currency()
        names = list(self.rows)
        todo = len([n for n in names if n not in self.price_cache])
        self.progress.setRange(0, max(todo, 1))
        self.progress.setValue(0)
        if todo:
            self.progress_label.setText(f"Wyceniam 0/{todo}…")
        w = PriceWorker(self.session, appid, cur, names, self.price_cache,
                        self.delay_spin.value())
        w.priced.connect(self._price_ready)
        w.progress.connect(self._price_progress)
        w.done.connect(self._pricing_done)
        self._price_worker = w
        self._track(w)

    def _cancel_worker(self, cls):
        for w in list(self._workers):
            if isinstance(w, cls):
                w.cancel()
                w.wait(3000)

    def _price_progress(self, i, total):
        if self.sender() is not self._price_worker:
            return                      # sygnał ze starego, anulowanego workera
        self.progress.setRange(0, total)
        self.progress.setValue(i)
        self.progress_label.setText(f"Wyceniam {i}/{total}…")

    def _pricing_done(self):
        if self.sender() is self._price_worker:
            self.progress_label.setText("Wycena zakończona.")

    def _price_ready(self, name, cents):
        rec = self.rows.get(name)
        if not rec:
            return
        rec['buyer'] = cents if cents >= 0 else None
        _, suffix = self._currency()
        self._filling = True
        if cents < 0:
            rec['price_it'].setText("błąd")
            rec['price_it'].setData(Qt.UserRole, -1)
            rec['price_it'].setForeground(QColor(RED))
        elif cents == 0:
            rec['price_it'].setText("brak ofert")
            rec['price_it'].setData(Qt.UserRole, 0)
            rec['price_it'].setForeground(QColor(YELLOW))
        else:
            rec['price_it'].setText(fmt_cents(cents, suffix))
            rec['price_it'].setData(Qt.UserRole, cents)
            rec['price_it'].setForeground(QColor("#e8ecf4"))
        self._filling = False
        self._update_receive(name)

    def _receive_for(self, rec):
        """Ile dostajesz za sztukę przy aktualnym undercut (0 = nie wystawiać)."""
        if not rec['buyer']:
            return 0
        return core.buyer_price_to_receive(rec['buyer'] - self.undercut_spin.value())

    def _update_receive(self, name):
        rec = self.rows[name]
        recv = self._receive_for(rec)
        _, suffix = self._currency()
        self._filling = True
        if rec['buyer'] is None:
            rec['recv_it'].setText("—"); rec['recv_it'].setData(Qt.UserRole, -2)
            rec['recv_it'].setForeground(QColor(TEXT_DIM))
        elif recv <= 0:
            rec['recv_it'].setText("za tanio"); rec['recv_it'].setData(Qt.UserRole, 0)
            rec['recv_it'].setForeground(QColor(YELLOW))
        else:
            rec['recv_it'].setText(fmt_cents(recv, suffix))
            rec['recv_it'].setData(Qt.UserRole, recv)
            rec['recv_it'].setForeground(QColor(GREEN))
        self._filling = False
        self._update_summary()

    def _recompute_receives(self):
        for name in self.rows:
            self._update_receive(name)

    # ------------------------------------------------------- zaznaczenia/suma ---
    def _item_changed(self, item):
        if not self._filling and item.column() == self.COL_CHECK:
            self._update_summary()

    def _set_all_checks(self, checked):
        self._filling = True
        for rec in self.rows.values():
            rec['check'].setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self._filling = False
        self._update_summary()

    def _selected_tasks(self):
        """[(name, assetid, receive)] dla zaznaczonych wierszy z sensowną ceną."""
        tasks, skipped = [], []
        for name, rec in self.rows.items():
            if rec['check'].checkState() != Qt.Checked:
                continue
            recv = self._receive_for(rec)
            if recv <= 0:
                skipped.append(name)
                continue
            tasks.extend((name, aid, recv) for aid in rec['assets'])
        return tasks, skipped

    def _update_summary(self):
        tasks, _ = self._selected_tasks()
        _, suffix = self._currency()
        total = sum(t[2] for t in tasks)
        self.summary_label.setText(
            f"Zaznaczone: {len(tasks)} ofert → dostajesz ~{fmt_cents(total, suffix)}")

    # ------------------------------------------------------------- sprzedaż ---
    def _dry_run(self):
        tasks, skipped = self._selected_tasks()
        _, suffix = self._currency()
        self._log("— Podgląd (dry-run), nic nie wystawiam —")
        for name, assetid, recv in tasks:
            rec = self.rows[name]
            buyer = rec['buyer'] - self.undercut_spin.value()
            self._log(f"  {name}: kupujący {fmt_cents(buyer, suffix)} → dostajesz "
                      f"{fmt_cents(recv, suffix)} (asset {assetid}) [dry-run]")
        for name in skipped:
            why = "czeka na wycenę" if self.rows[name]['buyer'] is None else "brak/za niska cena"
            self._log(f"  ! {name} — {why}, pomijam", YELLOW)
        total = sum(t[2] for t in tasks)
        self._log(f"Razem: {len(tasks)} ofert, dostajesz ~{fmt_cents(total, suffix)}.")

    def _sell_selected(self):
        tasks, skipped = self._selected_tasks()
        if not tasks:
            self._log("Nic do wystawienia — brak zaznaczonych pozycji z wyceną.", YELLOW)
            return
        _, suffix = self._currency()
        total = sum(t[2] for t in tasks)
        ret = QMessageBox.question(
            self, "Potwierdź wystawienie",
            f"Wystawić {len(tasks)} ofert na rynku Steam?\n\n"
            f"Łącznie dostaniesz ok. {fmt_cents(total, suffix)}.\n"
            f"Pominięte (brak ceny): {len(skipped)}.\n\n"
            "Po wystawieniu KAŻDĄ ofertę musisz zatwierdzić w apce Steam Mobile "
            "(bot nie potwierdza automatycznie).",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return
        appid, ctx, _ = self.app_combo.currentData()
        self.btn_sell.setEnabled(False)
        self.btn_dry.setEnabled(False)
        self.btn_load.setEnabled(False)
        self.progress.setRange(0, len(tasks))
        self.progress.setValue(0)
        self._log(f"— Wystawiam {len(tasks)} ofert —")
        w = SellWorker(self.session, self.sessionid, self.steamid, appid, ctx,
                       tasks, self.delay_spin.value())
        w.sold.connect(self._sold_one)
        w.progress.connect(lambda i, n: (self.progress.setValue(i),
                                         self.progress_label.setText(f"Wystawiam {i}/{n}…")))
        w.done.connect(self._sell_done)
        self._track(w)

    def _sold_one(self, name, assetid, ok, msg, receive):
        _, suffix = self._currency()
        if ok:
            self._log(f"  ✓ {name} — dostajesz {fmt_cents(receive, suffix)} "
                      f"(asset {assetid})", GREEN)
        else:
            self._log(f"  ✗ {name} (asset {assetid}) — {msg}", RED)

    def _sell_done(self, ok_n, fail_n, total_cents):
        _, suffix = self._currency()
        self.btn_sell.setEnabled(True)
        self.btn_dry.setEnabled(True)
        self.btn_load.setEnabled(True)
        self.progress_label.setText("Wystawianie zakończone.")
        self._log(f"Wystawiono {ok_n} ofert (błędów: {fail_n}), "
                  f"razem dostaniesz ~{fmt_cents(total_cents, suffix)}.")
        self._log(CONFIRM_HINT, ACCENT)
        QMessageBox.information(
            self, "Potwierdź oferty w apce!",
            f"Wystawiono {ok_n} ofert (błędów: {fail_n}).\n\n{CONFIRM_HINT}")

    # --------------------------------------------------------------- zamknięcie ---
    def closeEvent(self, event):
        for w in list(self._workers):
            if hasattr(w, 'cancel'):
                w.cancel()
        for w in list(self._workers):
            w.wait(2000)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    app.setApplicationName("DupeDealer")
    ico = resource_path("app.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
