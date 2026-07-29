"""Theme module unit tests (no display required)."""

from theme import LIGHT, NORD, PRESETS, build_qss, with_accent


def test_build_qss_contains_palette_colors():
    qss = build_qss(LIGHT)
    assert LIGHT.bg in qss
    assert LIGHT.accent in qss
    assert "QFrame#sidebar" in qss


def test_presets_include_light_dark_nord():
    assert set(PRESETS) >= {"Light", "Dark", "Nord"}


def test_with_accent_overrides_color():
    pal = with_accent(NORD, "#ff8800")
    assert pal.accent == "#ff8800"
    assert pal.name == NORD.name
