"""Cross-platform theme palettes and QSS (Win / Linux / macOS).

Persists selection via QSettings. No third-party theme frameworks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Iterable

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

ORG = "vid_download"
APP = "MultiPlatformDownloader"


@dataclass(frozen=True)
class ThemePalette:
    name: str
    bg: str
    sidebar: str
    text: str
    muted: str
    accent: str
    success: str
    danger: str
    surface: str
    border: str
    log_bg: str
    log_fg: str


LIGHT = ThemePalette(
    name="Light",
    bg="#f5f5f5",
    sidebar="#ebebeb",
    text="#222222",
    muted="#666666",
    accent="#3a7d44",
    success="#2e7d32",
    danger="#c62828",
    surface="#ffffff",
    border="#c8c8c8",
    log_bg="#1e1e1e",
    log_fg="#d4d4d4",
)

DARK = ThemePalette(
    name="Dark",
    bg="#1e1e1e",
    sidebar="#252526",
    text="#e0e0e0",
    muted="#9e9e9e",
    accent="#4caf50",
    success="#81c784",
    danger="#ef5350",
    surface="#2d2d2d",
    border="#3c3c3c",
    log_bg="#121212",
    log_fg="#d4d4d4",
)

NORD = ThemePalette(
    name="Nord",
    bg="#2e3440",
    sidebar="#3b4252",
    text="#eceff4",
    muted="#d8dee9",
    accent="#88c0d0",
    success="#a3be8c",
    danger="#bf616a",
    surface="#434c5e",
    border="#4c566a",
    log_bg="#242933",
    log_fg="#e5e9f0",
)

PRESETS: dict[str, ThemePalette] = {
    LIGHT.name: LIGHT,
    DARK.name: DARK,
    NORD.name: NORD,
}


def settings() -> QSettings:
    return QSettings(ORG, APP)


def load_palette() -> ThemePalette:
    s = settings()
    name = str(s.value("theme/name", LIGHT.name))
    base = PRESETS.get(name, LIGHT)
    accent = str(s.value("theme/accent", "") or "").strip()
    bg = str(s.value("theme/bg", "") or "").strip()
    if accent and QColor(accent).isValid():
        base = replace(base, accent=accent)
    if bg and QColor(bg).isValid():
        base = replace(base, bg=bg)
    return base


def save_palette(palette: ThemePalette, *, custom_accent: bool = False, custom_bg: bool = False) -> None:
    s = settings()
    s.setValue("theme/name", palette.name if palette.name in PRESETS else "Custom")
    if custom_accent:
        s.setValue("theme/accent", palette.accent)
    else:
        s.remove("theme/accent")
    if custom_bg:
        s.setValue("theme/bg", palette.bg)
    else:
        s.remove("theme/bg")
    s.sync()


def preset_names() -> list[str]:
    return list(PRESETS.keys())


def build_qss(palette: ThemePalette) -> str:
    """Build Fusion-friendly QSS from palette colors."""
    p = palette
    return f"""
QMainWindow {{
    background: {p.bg};
    color: {p.text};
}}
QWidget {{
    color: {p.text};
}}
QFrame#sidebar {{
    background: {p.sidebar};
    border-right: 1px solid {p.border};
}}
QLabel#titleLabel {{
    font-size: 15px;
    font-weight: 600;
    color: {p.text};
}}
QLabel#sectionLabel {{
    font-weight: 600;
    color: {p.text};
    margin-top: 4px;
}}
QLabel#mutedLabel {{
    color: {p.muted};
    font-size: 11px;
}}
QPushButton {{
    padding: 5px 12px;
    min-height: 22px;
}}
QPushButton#primaryBtn {{
    font-weight: 600;
    background: {p.accent};
    color: #ffffff;
    border: 1px solid {p.accent};
    border-radius: 3px;
}}
QPushButton#primaryBtn:disabled {{
    background: {p.border};
    border-color: {p.border};
    color: {p.muted};
}}
QPushButton:disabled {{
    color: {p.muted};
}}
QTableWidget {{
    background: {p.surface};
    gridline-color: {p.border};
    border: 1px solid {p.border};
    alternate-background-color: {p.sidebar};
}}
QHeaderView::section {{
    background: {p.sidebar};
    color: {p.text};
    border: 1px solid {p.border};
    padding: 4px;
}}
QPlainTextEdit#logPanel {{
    background: {p.log_bg};
    color: {p.log_fg};
    border: 1px solid {p.border};
    font-family: monospace;
}}
QProgressBar {{
    border: 1px solid {p.border};
    background: {p.surface};
    text-align: center;
    min-height: 18px;
    color: {p.text};
}}
QProgressBar::chunk {{
    background: {p.accent};
}}
QLabel#cookieOk {{
    color: {p.success};
}}
QLabel#cookieMissing {{
    color: {p.danger};
}}
QFrame#platformCard {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 4px;
    padding: 4px;
}}
QLineEdit, QComboBox {{
    background: {p.surface};
    border: 1px solid {p.border};
    padding: 3px 6px;
    color: {p.text};
}}
QStatusBar {{
    background: {p.sidebar};
    color: {p.muted};
}}
"""


def apply_theme(app: QApplication | None, palette: ThemePalette | None = None) -> ThemePalette:
    """Apply stylesheet to the QApplication. Returns the palette used."""
    pal = palette or load_palette()
    target = app or QApplication.instance()
    if target is not None:
        target.setStyleSheet(build_qss(pal))
    return pal


def with_accent(palette: ThemePalette, accent_hex: str) -> ThemePalette:
    color = QColor(accent_hex)
    if not color.isValid():
        return palette
    return replace(palette, accent=color.name())
