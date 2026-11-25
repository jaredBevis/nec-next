"""Colours and the application style sheet."""
from .compat import QtGui, QtWidgets, pg

BG = "#0d1117"
PANEL = "#151b23"
PANEL2 = "#1c2430"
BORDER = "#2a3441"
TEXT = "#e6edf3"
MUTED = "#8b98a9"
ACCENT = "#38bdf8"     # sky
ACCENT2 = "#a78bfa"    # violet
GOOD = "#34d399"
WARN = "#fbbf24"
BAD = "#f87171"

SERIES = {
    "swr": ACCENT, "r": "#38bdf8", "x": "#f472b6", "absz": "#a3a3a3",
    "gain": GOOD, "fb": WARN, "total": ACCENT, "vert": "#f472b6", "horiz": "#a3e635",
    "rl": ACCENT2,
}

FONT_UI = "Segoe UI, Inter, Ubuntu, Noto Sans, DejaVu Sans, sans-serif"
FONT_MONO = "Cascadia Mono, JetBrains Mono, Consolas, DejaVu Sans Mono, monospace"

STYLE = f"""
* {{ font-family: {FONT_UI}; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QWidget#central {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QDialog {{ background: {PANEL}; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QMenuBar {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 6px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {PANEL2}; border-radius: 6px; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {PANEL2}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QToolBar {{ background: {BG}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; spacing: 6px; }}
QToolButton {{ padding: 6px 12px; border-radius: 8px; border: 1px solid transparent; }}
QToolButton:hover {{ background: {PANEL2}; border: 1px solid {BORDER}; }}
QToolButton:pressed {{ background: {BORDER}; }}
QToolButton#primary {{ background: {ACCENT}; color: #04121c; font-weight: 600; }}
QToolButton#primary:hover {{ background: #7dd3fc; }}
QToolButton:disabled {{ color: {MUTED}; }}
QDoubleSpinBox#digits {{ padding-right: 6px; }}
QDoubleSpinBox#digits::up-button, QDoubleSpinBox#digits::down-button {{ width: 0px; height: 0px; border: none; }}
QDoubleSpinBox#digits::up-arrow, QDoubleSpinBox#digits::down-arrow {{ width: 0px; height: 0px; }}
QToolButton#digit {{ padding: 2px 5px; min-width: 14px; border-radius: 5px; color: {TEXT}; }}
QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 6px 14px; border-radius: 8px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: #04121c; border: none; font-weight: 600; }}
QPushButton:checked {{ background: {ACCENT}; color: #04121c; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {ACCENT}; selection-color: #04121c; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {PANEL2}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{ width: 14px; border: none; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 10px 8px 8px 8px; background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QPlainTextEdit, QTextEdit {{ background: {BG}; border: none; font-family: {FONT_MONO}; font-size: 10.5pt;
    selection-background-color: #1f3a52; }}
QTableWidget, QTableView, QListWidget, QTreeWidget {{ background: {BG}; border: none; gridline-color: {BORDER};
    selection-background-color: #1f3a52; alternate-background-color: {PANEL}; }}
QTreeWidget::item:selected, QTableWidget::item:selected {{ background: #1f3a52; color: {TEXT}; }}
QTreeWidget::item {{ padding: 2px 0; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 4px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 24px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {BORDER}; margin: 2px; border-radius: 1px; }}
QSplitter::handle:hover {{ background: {ACCENT}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QStatusBar {{ background: {BG}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 2px; max-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#h1 {{ font-size: 13pt; font-weight: 600; }}
QFrame#tile {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#tileTitle {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#tileValue {{ font-size: 15pt; font-weight: 600; }}
QLabel#tileSub {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#error {{ color: {BAD}; }}
"""


def apply(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    c = QtGui.QColor
    R = QtGui.QPalette.ColorRole
    pal.setColor(R.Window, c(BG))
    pal.setColor(R.WindowText, c(TEXT))
    pal.setColor(R.Base, c(BG))
    pal.setColor(R.AlternateBase, c(PANEL))
    pal.setColor(R.Text, c(TEXT))
    pal.setColor(R.Button, c(PANEL2))
    pal.setColor(R.ButtonText, c(TEXT))
    pal.setColor(R.Highlight, c(ACCENT))
    pal.setColor(R.HighlightedText, c("#04121c"))
    pal.setColor(R.ToolTipBase, c(PANEL2))
    pal.setColor(R.ToolTipText, c(TEXT))
    pal.setColor(R.PlaceholderText, c(MUTED)) if hasattr(R, "PlaceholderText") else None
    app.setPalette(pal)
    app.setStyleSheet(STYLE)
    pg.setConfigOptions(antialias=True, background=PANEL, foreground=MUTED)


def colormap(t):
    """Perceptual 'turbo'-like colour map for t in [0,1]; returns (N,4) float RGBA."""
    import numpy as np
    t = np.clip(np.asarray(t, dtype=float), 0, 1)
    # control points (dark blue -> cyan -> green -> yellow -> red)
    stops = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    cols = np.array([
        [0.19, 0.07, 0.50],
        [0.10, 0.55, 0.95],
        [0.20, 0.85, 0.55],
        [0.98, 0.80, 0.15],
        [0.92, 0.22, 0.15],
    ])
    r = np.interp(t, stops, cols[:, 0])
    g = np.interp(t, stops, cols[:, 1])
    b = np.interp(t, stops, cols[:, 2])
    return np.stack([r, g, b, np.ones_like(r)], axis=-1)
