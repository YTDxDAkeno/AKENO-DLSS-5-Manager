"""AKENO visual identity: palette and Qt style sheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase

BG = "#060a18"
BG_SIDEBAR = "#080d20"
CARD = "#0c1229"
CARD_2 = "#101836"
CARD_HOVER = "#131d40"
BORDER = "#1a2550"
BORDER_HI = "#2b3a78"
TEXT = "#e8ebf7"
TEXT_DIM = "#9ba4c9"
TEXT_MUTED = "#6b749c"
BLUE = "#3b82f6"
VIOLET = "#8b5cf6"
VIOLET_DEEP = "#6d28d9"
CYAN = "#22d3ee"
GREEN = "#22c55e"
AMBER = "#f5b014"
RED = "#ef4444"
MAGENTA = "#d946ef"

ACCENTS = {"violet": (VIOLET, BLUE), "blue": (BLUE, CYAN), "cyan": (CYAN, BLUE)}

STATE_COLORS = {"ok": GREEN, "warn": AMBER, "fail": RED, "pending": TEXT_MUTED, "unknown": TEXT_MUTED,
                "confirmed": GREEN, "experimental": AMBER, "insufficient": TEXT_DIM, "incompatible": RED,
                "blocked": RED, "verified": GREEN, "needs_review": AMBER, "unverified": AMBER}


def qc(hex_: str, alpha: int | None = None) -> QColor:
    c = QColor(hex_)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def ui_font(size: float = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    families = set(QFontDatabase.families())
    for fam in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"):
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
QToolTip {{ background: {CARD_2}; color: {TEXT}; border: 1px solid {BORDER_HI}; padding: 6px; border-radius: 6px; }}
QLabel {{ background: transparent; }}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QLabel[role="muted"] {{ color: {TEXT_MUTED}; font-size: 9pt; }}
QLabel[role="h1"] {{ font-size: 20pt; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 13pt; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="value"] {{ color: {TEXT}; }}
QLabel[role="key"] {{ color: {TEXT_DIM}; }}
QLabel a {{ color: {a1}; }}
#Card {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {CARD_2}, stop:1 {CARD});
        border: 1px solid {BORDER}; border-radius: 14px; }}
#SubCard {{ background: rgba(255,255,255,0.025); border: 1px solid {BORDER}; border-radius: 10px; }}
#Sidebar {{ background: {BG_SIDEBAR}; border-right: 1px solid {BORDER}; }}
QPushButton {{ background: {CARD_2}; border: 1px solid {BORDER_HI}; border-radius: 8px; padding: 7px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: {CARD_HOVER}; border-color: {a1}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; border-color: {BORDER}; background: {CARD}; }}
QPushButton:focus {{ border-color: {a1}; }}
QPushButton[kind="primary"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a1}, stop:1 {a2}); border: none; font-weight: 600; }}
QPushButton[kind="primary"]:disabled {{ background: {CARD_2}; color: {TEXT_MUTED}; border: 1px solid {BORDER}; }}
QPushButton[kind="danger"] {{ border-color: {RED}; }}
QPushButton[kind="nav"] {{ background: transparent; border: none; border-radius: 10px; text-align: left; padding: 11px 14px; font-size: 11pt; color: {TEXT_DIM}; }}
QPushButton[kind="nav"]:hover {{ background: rgba(139,92,246,0.10); color: {TEXT}; }}
QPushButton[kind="nav"]:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(139,92,246,0.35), stop:1 rgba(59,130,246,0.08)); color: {TEXT}; font-weight: 600; }}
QPushButton[kind="chip"] {{ border-radius: 14px; padding: 5px 12px; }}
QPushButton[kind="chip"]:checked {{ background: {a1}; border-color: {a1}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: #0a1026; border: 1px solid {BORDER_HI}; border-radius: 8px; padding: 6px 8px; color: {TEXT};
    selection-background-color: {a1}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {a1}; }}
QComboBox QAbstractItemView {{ background: {CARD_2}; border: 1px solid {BORDER_HI}; selection-background-color: {a1}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER_HI}; background: #0a1026; }}
QCheckBox::indicator:checked {{ background: {a1}; border-color: {a1}; }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a2}, stop:1 {a1}); border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HI}; border-radius: 4px; min-width: 30px; }}
QTableWidget, QTreeWidget, QListWidget {{ background: #0a1026; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: {BORDER}; alternate-background-color: #0c1330; }}
QHeaderView::section {{ background: {CARD_2}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; }}
QTableWidget::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {{ background: rgba(139,92,246,0.35); }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 5px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 5px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a2}, stop:1 {a1}); }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; padding: 8px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {a1}; }}
QMessageBox, QDialog {{ background: {CARD}; }}
QMenu {{ background: {CARD_2}; border: 1px solid {BORDER_HI}; }}
QMenu::item:selected {{ background: {a1}; }}
"""
