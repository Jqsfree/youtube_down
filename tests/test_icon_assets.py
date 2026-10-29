"""图标资产存在性校验（打包与窗口图标都依赖它们）。"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


@pytest.mark.parametrize("name", ["icon.svg", "icon.png", "icon.ico"])
def test_required_icon_assets_exist(name: str) -> None:
    path = ASSETS / name
    assert path.is_file(), f"缺少 {name}，请运行 python scripts/make_icon.py"
    assert path.stat().st_size > 0


def test_icon_png_is_1024() -> None:
    data = (ASSETS / "icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (1024, 1024)


def test_icon_ico_has_multiple_sizes() -> None:
    data = (ASSETS / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    assert count >= 6


def test_icon_icns_present_on_macos() -> None:
    path = ASSETS / "icon.icns"
    if not path.is_file():
        pytest.skip("非 macOS 环境未生成 icns")
    assert path.read_bytes()[:4] == b"icns"


def test_theme_asset_path_and_icon() -> None:
    from PySide6.QtWidgets import QApplication

    import theme

    app = QApplication.instance() or QApplication([])  # noqa: F841 - 需要 QGuiApplication
    assert theme.asset_path("icon.png") is not None
    assert theme.asset_path("does-not-exist.png") is None
    icon = theme.app_icon()
    assert not icon.isNull()
