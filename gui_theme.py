"""Ciemny motyw (QSS) dla steam_seller_gui.py — bez zewnętrznych pakietów motywów.

Paleta: granatowa czerń + steamowy błękit jako akcent. Wszystko zaokrąglone,
hover/pressed na przyciskach, tabela z naprzemiennymi wierszami i płaskim
nagłówkiem, slim scrollbary.
"""

BG = "#11141b"          # tło okna
SURFACE = "#181c26"     # panele/karty
SURFACE2 = "#212736"    # inputy, wiersze parzyste
BORDER = "#2a3245"
TEXT = "#e8ecf4"
TEXT_DIM = "#8b94a7"
ACCENT = "#4fb4ff"      # steamowy błękit
ACCENT_HOVER = "#6cc3ff"
ACCENT_PRESSED = "#3798e2"
GREEN = "#3ddc84"
RED = "#ff5d6c"
YELLOW = "#ffb454"

QSS = f"""
* {{
    font-family: 'Segoe UI', 'Inter', 'Cantarell', 'Noto Sans', sans-serif;
    font-size: 13px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #0b0e14;
    outline: none;
}}
QMainWindow, QDialog, QMessageBox {{ background: {BG}; }}

#Header {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
#AppTitle {{ font-size: 16px; font-weight: 700; }}
#AppSubtitle {{ color: {TEXT_DIM}; font-size: 12px; }}
#StatusDot {{ font-size: 15px; }}
#StatusLabel {{ font-weight: 600; }}
#HintLabel {{ color: {TEXT_DIM}; }}

#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel {{ background: transparent; border: none; }}

QPushButton {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 7px 16px;
    font-weight: 600;
}}
QPushButton:hover {{ background: #2a3143; border-color: #38445c; }}
QPushButton:pressed {{ background: #1b202c; }}
QPushButton:disabled {{ color: #5b6373; background: #1a1e29; border-color: #232a38; }}

QPushButton[class="accent"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 #2f8fdd);
    border: none;
    color: #06121f;
}}
QPushButton[class="accent"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT_HOVER}, stop:1 {ACCENT});
}}
QPushButton[class="accent"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[class="accent"]:disabled {{ background: #27374a; color: #5b6373; }}

QPushButton[class="danger"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ff6b78, stop:1 #d94550);
    border: none;
    color: #1c0608;
}}
QPushButton[class="danger"]:hover {{ background: #ff7d89; }}
QPushButton[class="danger"]:disabled {{ background: #3a2830; color: #6d5a5e; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 16px; border: none; background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 7px; height: 7px;
    image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {TEXT_DIM};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 7px; height: 7px;
    image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent; border-right: 5px solid transparent;
    border-top: 6px solid {TEXT_DIM};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {ACCENT};
    selection-color: #0b0e14;
}}

QTableWidget {{
    background: {SURFACE};
    alternate-background-color: #1c212d;
    border: 1px solid {BORDER};
    border-radius: 12px;
    gridline-color: transparent;
}}
QTableWidget::item {{ padding: 4px 8px; border: none; }}
QTableWidget::item:selected {{ background: #29405e; color: {TEXT}; }}
QHeaderView::section {{
    background: #1d2330;
    color: {TEXT_DIM};
    font-weight: 700;
    border: none;
    border-bottom: 2px solid {BORDER};
    padding: 8px 8px;
}}
QHeaderView::section:first {{ border-top-left-radius: 12px; }}
QHeaderView::section:last {{ border-top-right-radius: 12px; }}
QTableCornerButton::section {{ background: #1d2330; border: none; }}

QProgressBar {{
    background: {SURFACE2};
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 #7cd0ff);
}}

QPlainTextEdit {{
    background: #0c0f15;
    border: 1px solid {BORDER};
    border-radius: 12px;
    font-family: 'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', monospace;
    font-size: 12px;
    padding: 6px;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QTableWidget::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {BORDER};
    border-radius: 5px;
    background: {SURFACE2};
}}
QCheckBox::indicator:hover, QTableWidget::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QTableWidget::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #333d52; border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #43506c; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #333d52; border-radius: 4px; min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: #43506c; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {SURFACE2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
}}
QMessageBox QLabel {{ font-size: 13px; }}
"""
