"""Cross-platform theme palettes and QSS (Win / Linux / macOS).

Persists selection via QSettings. No third-party theme frameworks.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
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
    bg="#f3f4f6",
    sidebar="#eaecef",
    text="#1f2328",
    muted="#6b7280",
    accent="#2f855a",
    success="#2f855a",
    danger="#d03b3b",
    surface="#ffffff",
    border="#dcdfe4",
    log_bg="#1e1f22",
    log_fg="#d4d4d4",
)

DARK = ThemePalette(
    name="Dark",
    bg="#1b1c1f",
    sidebar="#202125",
    text="#e6e6e6",
    muted="#9a9ca3",
    accent="#4cb782",
    success="#5cc58e",
    danger="#f06262",
    surface="#2a2b30",
    border="#3a3c42",
    log_bg="#141518",
    log_fg="#d4d4d4",
)

NORD = ThemePalette(
    name="Nord",
    bg="#2e3440",
    sidebar="#2b303b",
    text="#eceff4",
    muted="#a7b1c2",
    accent="#88c0d0",
    success="#a3be8c",
    danger="#bf616a",
    surface="#3b4252",
    border="#4c566a",
    log_bg="#242933",
    log_fg="#e5e9f0",
)

MAC_LIGHT = ThemePalette(
    name="macOS Light",
    bg="#f5f5f7", sidebar="#ebebee", text="#1d1d1f", muted="#6e6e73",
    accent="#0a7aff", success="#248a3d", danger="#d70015",
    surface="#ffffff", border="#dcdce1", log_bg="#1d1d1f", log_fg="#e5e5ea",
)

GITHUB_LIGHT = ThemePalette(
    name="GitHub Light",
    bg="#f6f8fa", sidebar="#eef1f4", text="#1f2328", muted="#656d76",
    accent="#0969da", success="#1a7f37", danger="#d1242f",
    surface="#ffffff", border="#d0d7de", log_bg="#0d1117", log_fg="#e6edf3",
)

GITHUB_DARK = ThemePalette(
    name="GitHub Dark",
    bg="#0d1117", sidebar="#010409", text="#e6edf3", muted="#7d8590",
    accent="#2f81f7", success="#3fb950", danger="#f85149",
    surface="#161b22", border="#30363d", log_bg="#010409", log_fg="#e6edf3",
)

CATPPUCCIN_LATTE = ThemePalette(
    name="Catppuccin Latte",
    bg="#e6e9ef", sidebar="#dce0e8", text="#4c4f69", muted="#6c6f85",
    accent="#8839ef", success="#40a02b", danger="#d20f39",
    surface="#eff1f5", border="#ccd0da", log_bg="#1e1e2e", log_fg="#cdd6f4",
)

CATPPUCCIN_MOCHA = ThemePalette(
    name="Catppuccin Mocha",
    bg="#1e1e2e", sidebar="#181825", text="#cdd6f4", muted="#a6adc8",
    accent="#cba6f7", success="#a6e3a1", danger="#f38ba8",
    surface="#28283d", border="#45475a", log_bg="#11111b", log_fg="#cdd6f4",
)

TOKYO_NIGHT = ThemePalette(
    name="Tokyo Night",
    bg="#1a1b26", sidebar="#16161e", text="#c0caf5", muted="#787fa6",
    accent="#7aa2f7", success="#9ece6a", danger="#f7768e",
    surface="#24283b", border="#3b4261", log_bg="#13141c", log_fg="#c0caf5",
)

DRACULA = ThemePalette(
    name="Dracula",
    bg="#282a36", sidebar="#21222c", text="#f8f8f2", muted="#9ea3c4",
    accent="#bd93f9", success="#50fa7b", danger="#ff5555",
    surface="#323443", border="#44475a", log_bg="#191a21", log_fg="#f8f8f2",
)

ROSE_PINE_DAWN = ThemePalette(
    name="Rosé Pine Dawn",
    bg="#faf4ed", sidebar="#f2e9e1", text="#575279", muted="#797593",
    accent="#286983", success="#56949f", danger="#b4637a",
    surface="#fffaf3", border="#dfdad9", log_bg="#232136", log_fg="#e0def4",
)

GRUVBOX_DARK = ThemePalette(
    name="Gruvbox Dark",
    bg="#282828", sidebar="#1d2021", text="#ebdbb2", muted="#a89984",
    accent="#fe8019", success="#b8bb26", danger="#fb4934",
    surface="#32302f", border="#504945", log_bg="#1d2021", log_fg="#ebdbb2",
)

PRESETS: dict[str, ThemePalette] = {
    p.name: p
    for p in (
        MAC_LIGHT, GITHUB_LIGHT, CATPPUCCIN_LATTE, ROSE_PINE_DAWN, LIGHT,
        GITHUB_DARK, CATPPUCCIN_MOCHA, TOKYO_NIGHT, DRACULA, GRUVBOX_DARK, DARK, NORD,
    )
}


def settings() -> QSettings:
    return QSettings(ORG, APP)


def asset_path(name: str) -> Path | None:
    """定位打包或源码目录中的静态资源（如 ``icon.png``）。

    PyInstaller onefile 解包到 ``sys._MEIPASS``；源码运行时在仓库根目录。
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / name)
    here = Path(__file__).resolve().parent
    candidates.append(here / "assets" / name)
    candidates.append(here / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def app_icon() -> QIcon:
    """应用图标；缺少资产时返回空图标（Qt 会回退到默认）。"""
    for name in ("icon.png", "icon.ico", "icon.icns"):
        path = asset_path(name)
        if path is not None:
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def load_palette() -> ThemePalette:
    s = settings()
    name = str(s.value("theme/name", MAC_LIGHT.name))
    base = PRESETS.get(name, MAC_LIGHT)
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


def _mix(a: str, b: str, t: float) -> str:
    """Blend color a toward b by t (0..1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name()


def is_dark(palette: ThemePalette) -> bool:
    return QColor(palette.bg).lightnessF() < 0.5


def on_accent(palette: ThemePalette) -> str:
    """Readable text color on top of the accent color."""
    return "#1b1c1f" if QColor(palette.accent).lightnessF() > 0.62 else "#ffffff"


def _icon_dir(palette: ThemePalette) -> str:
    """Write theme-colored SVG glyphs (checkmark, chevron) to a cache dir; return its posix path."""
    import tempfile
    from pathlib import Path

    folder = Path(tempfile.gettempdir()) / "vid_download_theme"
    folder.mkdir(parents=True, exist_ok=True)
    icons = {
        f"check_{on_accent(palette)[1:]}.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            f'<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="{on_accent(palette)}" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        f"chevron_{palette.muted[1:]}.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            f'<path d="M4 6l4 4 4-4" fill="none" stroke="{palette.muted}" '
            'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
    }
    for name, svg in icons.items():
        path = folder / name
        if not path.exists():
            try:
                path.write_text(svg, encoding="utf-8")
            except OSError:
                pass
    return folder.as_posix()


def build_qss(palette: ThemePalette) -> str:
    """Build Fusion-friendly QSS from palette colors."""
    p = palette
    icons = _icon_dir(p)
    check_icon = f"{icons}/check_{on_accent(p)[1:]}.svg"
    chevron_icon = f"{icons}/chevron_{p.muted[1:]}.svg"
    hover = _mix(p.surface, p.text, 0.07)
    pressed = _mix(p.surface, p.text, 0.13)
    accent_hover = _mix(p.accent, p.text if is_dark(p) else "#000000", 0.12)
    accent_soft = _mix(p.surface, p.accent, 0.18)
    danger_soft = _mix(p.surface, p.danger, 0.14)
    chunk_bg = _mix(p.surface, p.text, 0.08)
    fg_on_accent = on_accent(p)
    return f"""
QMainWindow, QDialog {{
    background: {p.bg};
    color: {p.text};
}}
QWidget {{
    color: {p.text};
}}
QToolTip {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 4px 8px;
}}
QFrame#sidebar {{
    background: {p.sidebar};
    border-right: 1px solid {p.border};
}}
QFrame#card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
}}
QFrame#card QLabel {{
    background: transparent;
}}
QLabel#appTitle {{
    font-size: 17px;
    font-weight: 700;
}}
QLabel#titleLabel {{
    font-size: 15px;
    font-weight: 600;
    color: {p.text};
}}
QLabel#sectionLabel {{
    font-weight: 600;
    color: {p.muted};
    margin-top: 6px;
}}
QLabel#mutedLabel, QLabel#statLabel {{
    color: {p.muted};
    font-size: 12px;
}}
QLabel#statusLabel {{
    color: {p.text};
    font-weight: 500;
}}

QPushButton {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 5px 14px;
    min-height: 22px;
}}
QPushButton:hover {{
    background: {hover};
}}
QPushButton:pressed {{
    background: {pressed};
}}
QPushButton:disabled {{
    color: {p.muted};
    background: {_mix(p.surface, p.bg, 0.5)};
    border-color: {_mix(p.border, p.bg, 0.4)};
}}
QPushButton#primaryBtn {{
    font-weight: 600;
    background: {p.accent};
    color: {fg_on_accent};
    border: 1px solid {p.accent};
    padding: 6px 22px;
}}
QPushButton#primaryBtn:hover {{
    background: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton#primaryBtn:disabled {{
    background: {_mix(p.accent, p.bg, 0.6)};
    border-color: {_mix(p.accent, p.bg, 0.6)};
    color: {_mix(fg_on_accent, p.bg, 0.3)};
}}
QPushButton#dangerBtn:enabled {{
    color: {p.danger};
    border-color: {_mix(p.border, p.danger, 0.5)};
}}
QPushButton#dangerBtn:enabled:hover {{
    background: {danger_soft};
}}
QPushButton#navBtn {{
    background: transparent;
    border: none;
    border-radius: 8px;
    text-align: left;
    padding: 9px 12px;
    font-size: 14px;
    color: {p.muted};
}}
QPushButton#navBtn:hover {{
    background: {_mix(p.sidebar, p.text, 0.07)};
    color: {p.text};
}}
QPushButton#navBtn:checked {{
    background: {_mix(p.sidebar, p.accent, 0.18)};
    color: {p.text};
    font-weight: 600;
}}
QToolButton#themeSwatch {{
    background: transparent;
    border: 2px solid transparent;
    border-radius: 12px;
    padding: 6px;
    color: {p.text};
    font-size: 12px;
}}
QToolButton#themeSwatch:hover {{
    background: {hover};
}}
QToolButton#themeSwatch:checked {{
    border-color: {p.accent};
    background: {accent_soft};
    font-weight: 600;
}}
QPushButton#platformBtn {{
    text-align: left;
    padding: 7px 12px;
    font-weight: 600;
    border-radius: 8px;
}}
QPushButton#platformBtn:checked {{
    background: {accent_soft};
    border: 1px solid {p.accent};
    color: {p.text};
}}

QLineEdit, QComboBox, QPlainTextEdit#urlEdit {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {p.accent};
    selection-color: {fg_on_accent};
}}
QLineEdit:hover, QComboBox:hover, QPlainTextEdit#urlEdit:hover {{
    border-color: {_mix(p.border, p.text, 0.25)};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit#urlEdit:focus {{
    border: 1px solid {p.accent};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {p.muted};
}}
QComboBox QAbstractItemView {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    selection-background-color: {accent_soft};
    selection-color: {p.text};
    outline: 0;
}}

QComboBox {{
    padding-right: 24px;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox::down-arrow {{
    image: url("{chevron_icon}");
    width: 12px;
    height: 12px;
}}

QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {_mix(p.border, p.text, 0.25)};
    border-radius: 4px;
    background: {p.surface};
}}
QCheckBox::indicator:hover {{
    border-color: {p.accent};
}}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{check_icon}");
}}

QTableWidget {{
    background: {p.surface};
    alternate-background-color: {_mix(p.surface, p.text, 0.03)};
    border: 1px solid {p.border};
    border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: {accent_soft};
    selection-color: {p.text};
    outline: 0;
}}
QTableWidget::item {{
    padding: 0 8px;
    border: none;
}}
QHeaderView::section {{
    background: {p.surface};
    color: {p.muted};
    font-weight: 600;
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 6px 8px;
}}
QTableCornerButton::section {{
    background: {p.surface};
    border: none;
}}

QPlainTextEdit#logPanel {{
    background: {p.log_bg};
    color: {p.log_fg};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 4px;
    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, Menlo, monospace;
    font-size: 12px;
}}

QProgressBar {{
    border: none;
    border-radius: 5px;
    background: {chunk_bg};
    text-align: center;
    min-height: 10px;
    max-height: 10px;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {p.accent};
    border-radius: 5px;
}}
QProgressBar#batchProgress {{
    min-height: 20px;
    max-height: 20px;
    border-radius: 10px;
    color: {p.text};
    font-weight: 600;
}}
QProgressBar#batchProgress::chunk {{
    border-radius: 10px;
}}

QLabel#cookieOk {{
    color: {p.success};
    font-size: 12px;
    padding-left: 12px;
}}
QLabel#cookieMissing {{
    color: {p.danger};
    font-size: 12px;
    padding-left: 12px;
}}
QFrame#platformCard {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {_mix(p.surface, p.text, 0.25)};
    border-radius: 3px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{
    background: {_mix(p.surface, p.text, 0.4)};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

QSplitter::handle {{
    background: transparent;
}}
QStatusBar {{
    background: {p.sidebar};
    color: {p.muted};
    border-top: 1px solid {p.border};
}}
QStatusBar::item {{
    border: none;
}}
"""


def build_qpalette(palette: ThemePalette) -> QPalette:
    """Qt palette so natively drawn parts (checkboxes, arrows, menus) follow the theme."""
    p = palette
    qp = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.bg,
        QPalette.ColorRole.WindowText: p.text,
        QPalette.ColorRole.Base: p.surface,
        QPalette.ColorRole.AlternateBase: _mix(p.surface, p.text, 0.03),
        QPalette.ColorRole.Text: p.text,
        QPalette.ColorRole.Button: p.surface,
        QPalette.ColorRole.ButtonText: p.text,
        QPalette.ColorRole.Highlight: p.accent,
        QPalette.ColorRole.HighlightedText: on_accent(p),
        QPalette.ColorRole.ToolTipBase: p.surface,
        QPalette.ColorRole.ToolTipText: p.text,
        QPalette.ColorRole.PlaceholderText: p.muted,
        QPalette.ColorRole.Link: p.accent,
        QPalette.ColorRole.Light: _mix(p.surface, "#ffffff", 0.2),
        QPalette.ColorRole.Midlight: _mix(p.surface, p.border, 0.5),
        QPalette.ColorRole.Mid: p.border,
        QPalette.ColorRole.Dark: _mix(p.border, "#000000", 0.3),
        QPalette.ColorRole.Shadow: "#000000",
    }
    for role, color in roles.items():
        qp.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        qp.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.muted))
    return qp


def theme_preview(palette: ThemePalette, width: int = 132, height: int = 76) -> QPixmap:
    """Miniature window mock-up used by the theme gallery."""
    p = palette
    pix = QPixmap(width, height)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(p.border), 1))
    painter.setBrush(QColor(p.bg))
    painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 8, 8)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(p.sidebar))
    painter.drawRoundedRect(QRectF(1, 1, 30, height - 2), 7, 7)
    painter.drawRect(QRectF(20, 1, 11, height - 2))
    painter.setBrush(QColor(p.accent))
    painter.drawRoundedRect(QRectF(6, 12, 19, 5), 2.5, 2.5)
    painter.setBrush(QColor(p.muted))
    for y in (24, 33):
        painter.drawRoundedRect(QRectF(6, y, 15, 4), 2, 2)
    painter.setPen(QPen(QColor(p.border), 1))
    painter.setBrush(QColor(p.surface))
    painter.drawRoundedRect(QRectF(38.5, 9.5, width - 47, 36), 5, 5)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(p.text))
    painter.drawRoundedRect(QRectF(45, 17, 42, 4), 2, 2)
    painter.setBrush(QColor(p.muted))
    painter.drawRoundedRect(QRectF(45, 26, 58, 4), 2, 2)
    painter.setBrush(QColor(p.success))
    painter.drawRoundedRect(QRectF(45, 35, 22, 4), 2, 2)
    painter.setBrush(QColor(p.danger))
    painter.drawRoundedRect(QRectF(71, 35, 14, 4), 2, 2)
    painter.setBrush(QColor(_mix(p.surface, p.text, 0.1)))
    painter.drawRoundedRect(QRectF(38, 53, width - 46, 6), 3, 3)
    painter.setBrush(QColor(p.accent))
    painter.drawRoundedRect(QRectF(38, 53, (width - 46) * 0.6, 6), 3, 3)
    painter.drawRoundedRect(QRectF(width - 38, height - 13, 30, 8), 4, 4)
    painter.end()
    return pix


def apply_theme(app: QApplication | None, palette: ThemePalette | None = None) -> ThemePalette:
    """Apply palette + stylesheet to the QApplication. Returns the palette used."""
    pal = palette or load_palette()
    target = app or QApplication.instance()
    if target is not None:
        target.setPalette(build_qpalette(pal))
        target.setStyleSheet(build_qss(pal))
    return pal


def with_accent(palette: ThemePalette, accent_hex: str) -> ThemePalette:
    color = QColor(accent_hex)
    if not color.isValid():
        return palette
    return replace(palette, accent=color.name())
