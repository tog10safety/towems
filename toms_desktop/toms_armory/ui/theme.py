"""Dark military visual language shared by every desktop screen."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0d120e"
SURFACE = "#151d16"
PANEL = "#1b261c"
PANEL_ALT = "#202d20"
BORDER = "#344430"
BORDER_SOFT = "#2a3828"
TEXT = "#e9eedf"
MUTED = "#99a78f"
ACCENT = "#c7a955"
ACCENT_DARK = "#8f7335"
GREEN = "#6f9d62"
RED = "#b45d53"
AMBER = "#d19a45"
BLUE = "#6e93a8"

QSS = f"""
* {{
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 9.5pt;
    color: {TEXT};
}}
QMainWindow, QDialog, QWidget#AppRoot {{
    background: {BG};
}}
QWidget {{
    background: transparent;
}}
QFrame#Sidebar {{
    background: #101710;
    border-right: 1px solid {BORDER_SOFT};
}}
QFrame#TopBar {{
    background: {SURFACE};
    border-bottom: 1px solid {BORDER_SOFT};
}}
QFrame#Card, QFrame#StatCard, QFrame#Panel {{
    background: {PANEL};
    border: 1px solid {BORDER_SOFT};
    border-radius: 7px;
}}
QFrame#StatCard:hover {{
    border-color: {ACCENT_DARK};
}}
QLabel#AppTitle {{
    font-size: 14pt;
    font-weight: 700;
    color: {TEXT};
    letter-spacing: 0.5px;
}}
QLabel#AppSubtitle, QLabel#Muted, QLabel#CardHint {{
    color: {MUTED};
    font-size: 8.2pt;
}}
QLabel#PageTitle {{
    font-size: 20pt;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#PageSubtitle {{
    color: {MUTED};
    font-size: 9pt;
}}
QLabel#SectionTitle {{
    font-size: 11pt;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#StatValue {{
    font-size: 22pt;
    font-weight: 700;
    color: {ACCENT};
}}
QLabel#StatLabel {{
    color: {MUTED};
    font-size: 8pt;
    text-transform: uppercase;
    letter-spacing: 0.7px;
}}
QLabel#Badge {{
    background: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 2px 9px;
    color: {MUTED};
    font-size: 7.6pt;
}}
QLabel#BadgeOk {{
    background: rgba(111,157,98,0.16);
    border: 1px solid rgba(111,157,98,0.45);
    border-radius: 9px;
    padding: 2px 9px;
    color: #a8d39b;
    font-size: 7.6pt;
}}
QLabel#BadgeWarn {{
    background: rgba(209,154,69,0.15);
    border: 1px solid rgba(209,154,69,0.45);
    border-radius: 9px;
    padding: 2px 9px;
    color: #e5bd82;
    font-size: 7.6pt;
}}
QLabel#BadgeDanger {{
    background: rgba(180,93,83,0.15);
    border: 1px solid rgba(180,93,83,0.5);
    border-radius: 9px;
    padding: 2px 9px;
    color: #e9a49d;
    font-size: 7.6pt;
}}
QPushButton {{
    background: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 7px 14px;
    color: {TEXT};
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {ACCENT_DARK};
    background: #263525;
}}
QPushButton:pressed {{
    background: #1a2419;
}}
QPushButton:disabled {{
    color: #5e6a58;
    border-color: #293328;
    background: #161d15;
}}
QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #17140b;
    font-weight: 700;
}}
QPushButton#Primary:hover {{
    background: #d6b963;
    border-color: #d6b963;
}}
QPushButton#Danger {{
    background: transparent;
    border: 1px solid rgba(180,93,83,0.65);
    color: #e4a39b;
}}
QPushButton#Danger:hover {{
    background: rgba(180,93,83,0.15);
}}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {MUTED};
    text-align: left;
    padding: 8px 12px;
}}
QPushButton#Ghost:hover {{
    color: {TEXT};
    background: {PANEL};
}}
QPushButton#Nav {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    color: {MUTED};
    text-align: left;
    padding: 9px 16px;
    font-weight: 600;
}}
QPushButton#Nav:hover {{
    color: {TEXT};
    background: {SURFACE};
}}
QPushButton#Nav:checked {{
    color: {ACCENT};
    background: {PANEL};
    border-left: 3px solid {ACCENT};
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QDateTimeEdit,
QSpinBox, QDoubleSpinBox {{
    background: #111812;
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px 8px;
    selection-background-color: {ACCENT_DARK};
    selection-color: white;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QDateEdit:focus, QDateTimeEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {PANEL_ALT};
    selection-color: {ACCENT};
    outline: none;
}}
QCheckBox {{
    spacing: 7px;
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: #111812;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QTableWidget, QTableView {{
    background: {SURFACE};
    alternate-background-color: #182118;
    border: 1px solid {BORDER_SOFT};
    border-radius: 6px;
    gridline-color: transparent;
    selection-background-color: #2b3b27;
    selection-color: {TEXT};
    outline: none;
}}
QTableWidget::item, QTableView::item {{
    padding: 6px 7px;
    border-bottom: 1px solid #202b1f;
}}
QHeaderView::section {{
    background: #111812;
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 7px;
    font-size: 7.7pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}}
QTableCornerButton::section {{
    background: #111812;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #344430;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_DARK};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #344430;
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER_SOFT};
    border-radius: 6px;
    background: {SURFACE};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 8px 16px;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QMenu {{
    background: {PANEL};
    border: 1px solid {BORDER};
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 22px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {PANEL_ALT};
    color: {ACCENT};
}}
QToolTip {{
    background: #0a0f0b;
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 5px;
}}
QProgressBar {{
    background: #111812;
    border: 1px solid {BORDER};
    border-radius: 4px;
    text-align: center;
    color: {TEXT};
    height: 16px;
}}
QProgressBar::chunk {{
    background: {ACCENT_DARK};
    border-radius: 3px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: #2a3828;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QGroupBox {{
    border: 1px solid {BORDER_SOFT};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 700;
    color: {MUTED};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QSplitter::handle {{
    background: {BORDER_SOFT};
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(SURFACE))
    palette.setColor(QPalette.AlternateBase, QColor("#182118"))
    palette.setColor(QPalette.ToolTipBase, QColor(PANEL))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(PANEL_ALT))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Highlight, QColor(ACCENT_DARK))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#5e6a58"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5e6a58"))
    app.setPalette(palette)
    app.setStyleSheet(QSS)
