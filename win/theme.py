"""Colours, fonts and the dark Qt stylesheet (black / light blue / white)."""
import sys

BLACK = (10, 13, 18)
BG = (14, 17, 22)
PANEL = (23, 27, 33)
BLUE = (125, 211, 252)          # #7DD3FC
WHITE = (255, 255, 255)
DIM = (158, 173, 194)

# Segoe UI on Windows; Qt falls back to Leelawadee UI (Thai) / Yu Gothic UI (Japanese)
FONT = "Segoe UI" if sys.platform == "win32" else ("Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans")


def css(rgb, a=1.0):
    return f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{a})"


QSS = f"""
QWidget {{ background: {css(BG)}; color: white; font-family: "{FONT}"; font-size: 13px; }}
QLabel#title {{ font-size: 20px; font-weight: 700; }}
QLabel#section {{ color: {css(BLUE)}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#dim, QLabel#stats, QLabel#tip {{ color: {css(DIM)}; }}
QLabel#stats, QLabel#tip {{ font-size: 11px; }}
QFrame#sep {{ background: {css(PANEL)}; max-height: 1px; min-height: 1px; border: none; }}
QPushButton {{ background: {css(PANEL)}; border: 1px solid #2b323c; border-radius: 6px; padding: 5px 12px; }}
QPushButton:hover {{ border-color: {css(BLUE, 0.7)}; }}
QPushButton:disabled {{ color: #5d6672; }}
QPushButton#primary {{ background: {css(BLUE)}; color: {css(BLACK)}; font-weight: 600; border: none; }}
QPushButton#seg {{ border-radius: 0; padding: 5px 10px; }}
QPushButton#seg:checked {{ background: {css(BLUE, 0.9)}; color: {css(BLACK)}; border-color: {css(BLUE)}; }}
QPushButton#segL {{ border-top-right-radius: 0; border-bottom-right-radius: 0; padding: 5px 10px; }}
QPushButton#segR {{ border-top-left-radius: 0; border-bottom-left-radius: 0; padding: 5px 10px; }}
QPushButton#segL:checked, QPushButton#segR:checked {{ background: {css(BLUE, 0.9)}; color: {css(BLACK)}; border-color: {css(BLUE)}; }}
QComboBox {{ background: {css(PANEL)}; border: 1px solid #2b323c; border-radius: 6px; padding: 4px 8px; }}
QComboBox QAbstractItemView {{ background: {css(PANEL)}; selection-background-color: {css(BLUE, 0.35)}; }}
QSlider::groove:horizontal {{ height: 4px; background: #2b323c; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {css(BLUE)}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a434f; background: {css(PANEL)}; }}
QCheckBox::indicator:checked {{ background: {css(BLUE)}; border-color: {css(BLUE)}; }}
QProgressBar {{ background: {css(PANEL)}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {css(BLUE)}; border-radius: 3px; }}
QTableWidget {{ background: {css(PANEL)}; gridline-color: #2b323c; alternate-background-color: #1c2128; }}
QHeaderView::section {{ background: {css(BG)}; color: {css(DIM)}; border: none; padding: 4px; }}
QMenu {{ background: {css(PANEL)}; border: 1px solid #2b323c; }}
QMenu::item:selected {{ background: {css(BLUE, 0.35)}; }}
"""
