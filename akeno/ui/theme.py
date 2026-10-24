"""AKENO design system: colour, type, spacing and shape tokens, and the application style sheet.

Rules (docs/DESIGN_SYSTEM.md):
* Colour: dark cool-neutral surfaces in three steps, one brand accent (violet unless the user picks blue or cyan)
  for selection, focus and primary actions, and state colours (success, warning, danger, info) only where they
  carry a state. Decorative colour is not used.
* Type: page title, section title, subsection, body, secondary and metadata. Every step is at least 9 pt and meets
  WCAG AA contrast on the surfaces it is used on.
* Space: a 4 px grid. Pages have 28 px side margins, cards 20 px padding, related controls 8 px gaps.
* Shape: 10 px cards, 7 px controls, full pills. Depth comes from surface steps and 1 px borders, not glows.

The older token names (VIOLET, BLUE, CARD, BORDER_HI...) stay importable, because painted widgets use them.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

# -- surfaces (back to front) ---------------------------------------------------------------------------------
BG = "#080C15"            # window background
BG_SIDEBAR = "#0A0F1A"    # navigation rail, status bar
SURFACE = "#0F1522"       # cards and panels
SURFACE_2 = "#141C2B"     # raised: table headers, hovered rows, secondary buttons
SURFACE_3 = "#1A2436"     # pressed and selected neutral
INPUT = "#0B111C"         # text fields, lists and tables (sunken)
BORDER = "#1C2536"        # card borders and dividers
BORDER_HI = "#263248"     # control borders
BORDER_BTN = "#323F58"    # hovered control borders, menus
# -- text ----------------------------------------------------------------------------------------------------
TEXT = "#E8EDF6"          # primary (15:1 on SURFACE)
TEXT_DIM = "#A3AEC2"      # secondary (8:1)
TEXT_MUTED = "#7C8AA2"    # metadata (5:1)
TEXT_DISABLED = "#566179"
# -- states --------------------------------------------------------------------------------------------------
GREEN = "#34D399"         # success / verified
AMBER = "#F6C152"         # warning / experimental
RED = "#F7687C"           # error / blocked / destructive
INFO = "#4C9DFF"          # information, links outside the accent
CYAN = "#3CC8F0"          # live data (running, meters)
# -- brand accent (set by set_accent) ------------------------------------------------------------------------
VIOLET = "#8B6CFF"
VIOLET_DEEP = "#6D4AFF"
BLUE = INFO
MAGENTA = VIOLET
ACCENT = VIOLET           # indicators, focus rings, selected borders
ACCENT_STRONG = VIOLET_DEEP   # primary button fill (white text 5:1)
ACCENT_TEXT = "#B9A8FF"   # accent-coloured text on dark surfaces
# legacy names still used by painted widgets
CARD = SURFACE
CARD_2 = SURFACE_2
CARD_HOVER = SURFACE_3

# -- type scale (pt) -----------------------------------------------------------------------------------------
FONT_BODY = 10.0
FONT_SMALL = 9.0
FONT_OVERLINE = 8.0
FONT_H3 = 10.5
FONT_H2 = 12.0
FONT_H1 = 17.0
FONT_DISPLAY = 19.0       # a game's name on its page

# -- space and shape (px at 100 %) ---------------------------------------------------------------------------
SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL = 4, 8, 12, 16, 24
PAGE_MARGIN = 28
PAGE_TOP = 22
CARD_PADDING = (20, 16, 20, 18)
CARD_GAP = 16
CONTROL_HEIGHT = 32
RADIUS_CARD = 10
RADIUS_BUTTON = 7
RADIUS_PILL = 11
SIDEBAR_WIDTH = 236
HEADER_HEIGHT = 0         # the artwork header was removed: page titles carry the page


@dataclass(frozen=True)
class Accent:
    base: str        # indicators, focus, selection borders
    strong: str      # primary fills (white text >= 4.5:1)
    hover: str
    pressed: str
    text: str        # accent text on dark surfaces
    rgb: tuple[int, int, int]

    def soft(self, alpha: float = 0.14) -> str:
        r, g, b = self.rgb
        return f"rgba({r},{g},{b},{alpha})"


ACCENT_SETS = {
    "violet": Accent("#8B6CFF", "#6D4AFF", "#7B5CFF", "#5E3EE8", "#B9A8FF", (139, 108, 255)),
    "blue": Accent("#4C9DFF", "#2563EB", "#3473F0", "#1D4FC4", "#9CC8FF", (76, 157, 255)),
    "cyan": Accent("#3CC8F0", "#0E7FA8", "#1290BD", "#0B6A8C", "#9BE3F7", (60, 200, 240)),
}
ACCENTS = {k: (a.base, a.text) for k, a in ACCENT_SETS.items()}
_current = ACCENT_SETS["violet"]

# Meter and chart pairs: the accent, information and success only.
GRAD_BLUE = (INFO, "#7DB8FF")
GRAD_VIOLET = (VIOLET_DEEP, VIOLET)
GRAD_NEON = (VIOLET_DEEP, VIOLET)
GRAD_GREEN = ("#1FAF7A", GREEN)
GRAD_PINK = (VIOLET, ACCENT_TEXT)
CHART = (VIOLET, INFO, GREEN, AMBER, "#E879F9", CYAN)     # series colours, in this order

STATE_COLORS = {"ok": GREEN, "warn": AMBER, "fail": RED, "pending": TEXT_MUTED, "unknown": TEXT_MUTED,
                "confirmed": GREEN, "experimental": AMBER, "insufficient": TEXT_DIM, "incompatible": RED,
                "blocked": RED, "verified": GREEN, "needs_review": AMBER, "unverified": AMBER, "info": INFO,
                "running": CYAN}


def accent() -> Accent:
    return _current


def set_accent(name: str | None) -> Accent:
    """Make ``name`` the accent of everything painted from now on (pages are rebuilt when it changes)."""
    global _current, ACCENT, ACCENT_STRONG, ACCENT_TEXT, VIOLET, VIOLET_DEEP, MAGENTA
    _current = ACCENT_SETS.get(name or "violet", ACCENT_SETS["violet"])
    ACCENT, ACCENT_STRONG, ACCENT_TEXT = _current.base, _current.strong, _current.text
    VIOLET, VIOLET_DEEP, MAGENTA = _current.base, _current.strong, _current.base
    return _current


def qc(hex_: str, alpha: int | None = None) -> QColor:
    c = QColor(hex_)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def ui_font(size: float = FONT_BODY, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
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


def palette() -> QPalette:
    """Dark palette for whatever the style sheet does not cover (native message box parts, Fusion details)."""
    p = QPalette()
    roles = {QPalette.ColorRole.Window: SURFACE, QPalette.ColorRole.WindowText: TEXT,
             QPalette.ColorRole.Base: INPUT, QPalette.ColorRole.AlternateBase: SURFACE_2,
             QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: SURFACE_2,
             QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.ToolTipBase: SURFACE_3,
             QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.Highlight: _current.strong,
             QPalette.ColorRole.HighlightedText: "#FFFFFF", QPalette.ColorRole.Link: _current.text,
             QPalette.ColorRole.PlaceholderText: TEXT_MUTED, QPalette.ColorRole.BrightText: "#FFFFFF",
             QPalette.ColorRole.Light: BORDER_BTN, QPalette.ColorRole.Midlight: BORDER_HI,
             QPalette.ColorRole.Mid: BORDER, QPalette.ColorRole.Dark: BG, QPalette.ColorRole.Shadow: "#000000"}
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_DISABLED))
    return p


def apply(app, accent_name: str | None = None) -> None:
    """Style the whole application: Fusion (the same look on every Windows version), palette, font, style sheet."""
    set_accent(accent_name)
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setFont(ui_font(FONT_BODY))
    app.setStyleSheet(stylesheet(accent_name))


def _glyph(name: str) -> str:
    from akeno.paths import resource_dir
    return (resource_dir() / "ui" / name).as_posix()


def stylesheet(accent_name: str | None = "violet") -> str:
    a = set_accent(accent_name)
    soft, soft_hi = a.soft(0.14), a.soft(0.22)
    down, up = _glyph("chevron-down.svg"), _glyph("chevron-up.svg")
    down_off, up_off = _glyph("chevron-down-disabled.svg"), _glyph("chevron-up-disabled.svg")
    check, dash = _glyph("check.svg"), _glyph("dash.svg")
    return f"""
QWidget {{ color: {TEXT}; font-size: {FONT_BODY}pt; }}
QMainWindow, #Root {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER_BTN}; padding: 6px 8px; border-radius: 6px; }}
QLabel {{ background: transparent; }}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QLabel[role="muted"] {{ color: {TEXT_MUTED}; font-size: {FONT_SMALL}pt; }}
QLabel[role="h1"] {{ font-size: {FONT_H1}pt; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: {FONT_H2}pt; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: {FONT_H3}pt; font-weight: 600; }}
QLabel[role="display"] {{ font-size: {FONT_DISPLAY}pt; font-weight: 600; }}
QLabel[role="overline"] {{ color: {TEXT_MUTED}; font-size: {FONT_OVERLINE}pt; font-weight: 700; letter-spacing: 1.2px; }}
QLabel[role="value"] {{ color: {TEXT}; }}
QLabel[role="key"] {{ color: {TEXT_DIM}; }}
QLabel[role="error"] {{ color: {RED}; }}
QLabel[role="stat"] {{ font-size: 15pt; font-weight: 600; }}
QLabel a {{ color: {a.text}; }}
#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; }}
#SubCard {{ background: rgba(255,255,255,0.018); border: 1px solid {BORDER}; border-radius: 8px; }}
#Sidebar {{ background: {BG_SIDEBAR}; border-right: 1px solid {BORDER}; }}
#SidebarFooter {{ border-top: 1px solid {BORDER}; }}
QFrame[role="divider"] {{ background: {BORDER}; border: none; min-height: 1px; max-height: 1px; }}
QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER_HI}; border-radius: {RADIUS_BUTTON}px; padding: 6px 14px;
        color: {TEXT}; font-weight: 600; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {BORDER_BTN}; }}
QPushButton:pressed {{ background: #111827; }}
QPushButton:focus {{ border-color: {a.base}; }}
QPushButton:checked {{ background: {soft}; border-color: {a.base}; }}
QPushButton:disabled {{ color: {TEXT_DISABLED}; background: #0D131E; border-color: {BORDER}; }}
QPushButton[kind="primary"] {{ background: {a.strong}; border: 1px solid {a.strong}; color: #FFFFFF; }}
QPushButton[kind="primary"]:hover {{ background: {a.hover}; border-color: {a.hover}; }}
QPushButton[kind="primary"]:pressed {{ background: {a.pressed}; border-color: {a.pressed}; }}
QPushButton[kind="primary"]:focus {{ border-color: {a.text}; }}
QPushButton[kind="primary"]:disabled {{ background: #161C2A; border-color: {BORDER}; color: {TEXT_DISABLED}; }}
QPushButton[kind="danger"] {{ background: rgba(247,104,124,0.06); border-color: rgba(247,104,124,0.42); color: #FF9FAC; }}
QPushButton[kind="danger"]:hover {{ background: rgba(247,104,124,0.15); border-color: {RED}; color: #FFD3D9; }}
QPushButton[kind="danger"]:disabled {{ background: #0D131E; border-color: {BORDER}; color: {TEXT_DISABLED}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {TEXT_DIM}; }}
QPushButton[kind="ghost"]:hover {{ background: {SURFACE_2}; border-color: {BORDER_HI}; color: {TEXT}; }}
QPushButton[kind="ghost"]:focus {{ border-color: {a.base}; }}
QPushButton[kind="nav"] {{ background: transparent; border: 1px solid transparent; border-radius: {RADIUS_BUTTON}px;
        text-align: left; padding: 6px 10px; font-size: {FONT_BODY}pt; font-weight: 500; color: {TEXT_DIM}; }}
QPushButton[kind="nav"]:hover {{ background: rgba(255,255,255,0.04); color: {TEXT}; }}
QPushButton[kind="nav"]:focus {{ border-color: {BORDER_BTN}; }}
QPushButton[kind="nav"]:checked {{ background: {soft}; color: {TEXT}; font-weight: 600; }}
QPushButton[kind="nav"]:checked:focus {{ border-color: {a.base}; }}
QPushButton[kind="chip"] {{ background: transparent; border: 1px solid {BORDER_HI}; border-radius: {RADIUS_BUTTON}px;
        padding: 5px 12px; color: {TEXT_DIM}; font-weight: 500; }}
QPushButton[kind="chip"]:hover {{ background: {SURFACE_2}; border-color: {BORDER_BTN}; color: {TEXT}; }}
QPushButton[kind="chip"]:checked {{ background: {soft_hi}; border-color: {a.base}; color: {TEXT}; font-weight: 600; }}
QPushButton[kind="chip"]:disabled {{ color: {TEXT_DISABLED}; border-color: {BORDER}; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {a.text}; padding: 2px 2px; font-weight: 600; }}
QPushButton[kind="link"]:hover {{ color: {TEXT}; text-decoration: underline; }}
QPushButton[kind="link"]:disabled {{ color: {TEXT_DISABLED}; }}
QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit {{ background: {INPUT}; border: 1px solid {BORDER_HI};
        border-radius: {RADIUS_BUTTON}px; padding: 5px 9px; color: {TEXT}; selection-background-color: {a.strong};
        selection-color: #FFFFFF; }}
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover, QPlainTextEdit:hover, QTextEdit:hover {{ border-color: {BORDER_BTN}; }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {a.base}; }}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
        color: {TEXT_DISABLED}; background: #0A0F18; border-color: {BORDER}; }}
QLineEdit[readOnly="true"], QPlainTextEdit[readOnly="true"], QTextEdit[readOnly="true"] {{ background: #0A0F18; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none; }}
QComboBox::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url("{down_off}"); }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER_BTN}; padding: 4px; outline: none;
        selection-background-color: {soft_hi}; selection-color: {TEXT}; }}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 0 8px; }}
QAbstractSpinBox {{ padding-right: 4px; min-height: 20px; }}     /* the arrow buttons take their own room */
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 24px; border: none;
        border-left: 1px solid {BORDER}; background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: {RADIUS_BUTTON}px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: {RADIUS_BUTTON}px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {SURFACE_3}; }}
QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {{ background: {soft_hi}; }}
QAbstractSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: url("{up_off}"); }}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: url("{down_off}"); }}
QCheckBox, QRadioButton {{ spacing: 9px; color: {TEXT}; background: transparent; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {TEXT_DISABLED}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER_BTN}; background: {INPUT}; }}
QCheckBox::indicator:hover {{ border-color: {a.base}; }}
QCheckBox::indicator:checked {{ background: {a.strong}; border-color: {a.strong}; image: url("{check}"); }}
QCheckBox::indicator:checked:hover {{ background: {a.hover}; border-color: {a.hover}; }}
QCheckBox::indicator:indeterminate {{ background: {a.strong}; border-color: {a.strong}; image: url("{dash}"); }}
QCheckBox::indicator:disabled {{ background: #0A0F18; border-color: {BORDER}; }}
QCheckBox::indicator:checked:disabled {{ background: #2B2F44; border-color: #2B2F44; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 8px; border: 1px solid {BORDER_BTN}; background: {INPUT}; }}
QRadioButton::indicator:hover {{ border-color: {a.base}; }}
QRadioButton::indicator:checked {{ border-color: {a.strong}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
        fx:0.5, fy:0.5, stop:0 #FFFFFF, stop:0.34 #FFFFFF, stop:0.42 {a.strong}, stop:1 {a.strong}); }}
QSlider {{ background: transparent; min-height: 20px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER_HI}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {a.base}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {a.text}; }}
QSlider::handle:horizontal:disabled {{ background: {TEXT_DISABLED}; }}
QSlider::sub-page:horizontal:disabled {{ background: {BORDER_BTN}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.13); border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: rgba(255,255,255,0.24); }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: rgba(255,255,255,0.13); border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: rgba(255,255,255,0.24); }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractItemView {{ outline: none; }}
QTableWidget, QTreeWidget, QListWidget, QTableView, QTreeView, QListView {{ background: {INPUT}; border: 1px solid {BORDER};
        border-radius: 8px; gridline-color: {BORDER}; alternate-background-color: rgba(255,255,255,0.018);
        selection-background-color: {soft_hi}; selection-color: {TEXT}; }}
QTableView::item, QTreeView::item {{ padding: 2px 6px; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{ background: {soft_hi}; color: {TEXT}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {SURFACE_2}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER};
        padding: 7px 10px; font-weight: 600; font-size: {FONT_SMALL}pt; }}
QTableCornerButton::section {{ background: {SURFACE_2}; border: none; }}
QProgressBar {{ background: {BORDER_HI}; border: none; border-radius: 3px; min-height: 6px; max-height: 6px;
        text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 3px; background: {a.base}; }}
QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; padding: 8px 14px; margin-right: 2px; border: none;
        border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {a.base}; }}
QTabBar::tab:disabled {{ color: {TEXT_DISABLED}; }}
QMessageBox, QDialog {{ background: {SURFACE}; }}
QMessageBox QPushButton, QDialogButtonBox QPushButton {{ min-width: 84px; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER_BTN}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {soft_hi}; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QStatusBar {{ background: {BG_SIDEBAR}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: {BORDER}; }}
"""
