"""AKENO visual identity: palette (design-package tokens) and Qt style sheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase

# Design tokens from the AKENO design package (docs/theme_tokens.json).
BG = "#060D1C"
BG_SIDEBAR = "#071020"
CARD = "#0D192B"
CARD_2 = "#101D33"
CARD_HOVER = "#15264A"
BORDER = "#1C2A42"
BORDER_HI = "#263A5A"
BORDER_BTN = "#354A70"
TEXT = "#F4F6FF"
TEXT_DIM = "#97A9C7"
TEXT_MUTED = "#66748E"
BLUE = "#168EFF"
VIOLET = "#8E42FF"
VIOLET_DEEP = "#4E31D5"
CYAN = "#12C7FF"
GREEN = "#20D991"
AMBER = "#FFC847"
RED = "#FF5B73"
MAGENTA = "#CD53F8"
RADIUS_CARD = 12
RADIUS_BUTTON = 9
SIDEBAR_WIDTH = 254
HEADER_HEIGHT = 110

ACCENTS = {"violet": (VIOLET, BLUE), "blue": (BLUE, CYAN), "cyan": (CYAN, BLUE)}

# Gradient pairs for meters and premium buttons.
GRAD_BLUE = (BLUE, CYAN)
GRAD_VIOLET = (VIOLET_DEEP, VIOLET)
GRAD_NEON = (BLUE, VIOLET)
GRAD_GREEN = ("#13B87A", GREEN)
GRAD_PINK = (VIOLET, MAGENTA)

STATE_COLORS = {"ok": GREEN, "warn": AMBER, "fail": RED, "pending": TEXT_MUTED, "unknown": TEXT_MUTED,
                "confirmed": GREEN, "experimental": AMBER, "insufficient": TEXT_DIM, "incompatible": RED,
                "blocked": RED, "verified": GREEN, "needs_review": AMBER, "unverified": AMBER, "info": BLUE,
                "running": CYAN}


def qc(hex_: str, alpha: int | None = None) -> QColor:
    c = QColor(hex_)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def ui_font(size: float = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    families = set(QFontDatabase.families())
    for fam in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"):
        if fam in families:
            f = QFont(fam)
            break
    else:
        f = QFont()
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def stylesheet(accent: str = "violet") -> str:
    a1, a2 = ACCENTS.get(accent, ACCENTS["violet"])
    return f"""
QWidget {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, #Root {{ background: {BG}; }}
QToolTip {{ background: {CARD_2}; color: {TEXT}; border: 1px solid {BORDER_BTN}; padding: 6px; border-radius: 6px; }}
QLabel {{ background: transparent; }}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QLabel[role="muted"] {{ color: {TEXT_MUTED}; font-size: 9pt; }}
QLabel[role="h1"] {{ font-size: 20pt; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 13pt; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="value"] {{ color: {TEXT}; }}
QLabel[role="key"] {{ color: {TEXT_DIM}; }}
QLabel a {{ color: {a1}; }}
#Card {{ background: qlineargradient(x1:0, y1:0, x2:0.4, y2:1, stop:0 {CARD_2}, stop:1 {CARD});
        border: 1px solid {BORDER_HI}; border-radius: {RADIUS_CARD}px; }}
#SubCard {{ background: rgba(16,29,51,0.72); border: 1px solid {BORDER_HI}; border-radius: 10px; }}
#Sidebar {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #081226, stop:1 {BG_SIDEBAR});
        border-right: 1px solid {BORDER}; }}
QPushButton {{ background: #142340; border: 1px solid {BORDER_BTN}; border-radius: {RADIUS_BUTTON}px; padding: 7px 14px;
        color: #E7EDFF; font-weight: 600; }}
QPushButton:hover {{ background: #20345B; border-color: #6B5DFA; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; border-color: #1A2940; background: #0F1828; }}
QPushButton:focus {{ border-color: {a1}; }}
QPushButton[kind="primary"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {VIOLET_DEEP}, stop:1 {a1});
        border: 1px solid #8958FC; color: white; }}
QPushButton[kind="primary"]:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #5F43E8, stop:1 #A066FF);
        border-color: #C4B5FD; }}
QPushButton[kind="primary"]:disabled {{ background: #0F1828; color: {TEXT_MUTED}; border: 1px solid #1A2940; }}
QPushButton[kind="danger"] {{ border-color: {RED}; }}
QPushButton[kind="nav"] {{ background: transparent; border: none; border-left: 3px solid transparent; border-radius: 10px;
        text-align: left; padding: 10px 14px 10px 13px; font-size: 11pt; font-weight: 500; color: {TEXT_DIM}; }}
QPushButton[kind="nav"]:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(22,142,255,0.16),
        stop:1 rgba(22,142,255,0.02)); color: {TEXT}; }}
QPushButton[kind="nav"]:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(142,66,255,0.45),
        stop:0.55 rgba(78,49,213,0.22), stop:1 rgba(22,142,255,0.04)); border-left: 3px solid {a1}; color: {TEXT};
        font-weight: 600; }}
QPushButton[kind="nav"]:focus {{ border-left: 3px solid {a2}; }}
QPushButton[kind="chip"] {{ border-radius: 14px; padding: 5px 12px; }}
QPushButton[kind="chip"]:checked {{ background: {a1}; border-color: {a1}; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {a2}; padding: 2px 4px; font-weight: 600; }}
QPushButton[kind="link"]:hover {{ color: {TEXT}; text-decoration: underline; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: #091427; border: 1px solid {BORDER_HI}; border-radius: 8px; padding: 6px 8px; color: {TEXT};
    selection-background-color: {a1}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {a1}; }}
QComboBox QAbstractItemView {{ background: {CARD_2}; border: 1px solid {BORDER_BTN}; selection-background-color: {a1}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER_BTN}; background: #091427; }}
QCheckBox::indicator:checked {{ background: {a1}; border-color: {a1}; }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a2}, stop:1 {a1}); border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_BTN}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {a1}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_BTN}; border-radius: 4px; min-width: 30px; }}
QTableWidget, QTreeWidget, QListWidget {{ background: #091427; border: 1px solid {BORDER_HI}; border-radius: 10px;
    gridline-color: {BORDER}; alternate-background-color: #0B1830; }}
QHeaderView::section {{ background: {CARD_2}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; }}
QTableWidget::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {{ background: rgba(142,66,255,0.35); }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 5px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 5px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a2}, stop:1 {a1}); }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; padding: 8px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {a1}; }}
QMessageBox, QDialog {{ background: {CARD}; }}
QMenu {{ background: {CARD_2}; border: 1px solid {BORDER_BTN}; }}
QMenu::item:selected {{ background: {a1}; }}
QStatusBar {{ background: {BG}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
"""
