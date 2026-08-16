from app.core.text_width import display_width, truncate_display_width


def test_display_width_distinguishes_narrow_and_wide_characters() -> None:
    assert display_width("OpenClaw") == 8
    assert display_width("微信") == 4
    assert display_width("OpenClaw 微信") == 13
    assert display_width("…") == 2


def test_display_width_keeps_common_grapheme_clusters_together() -> None:
    assert display_width("e\u0301") == 1
    assert display_width("👨‍👩‍👧‍👦") == 2
    assert display_width("🇨🇳") == 2
    assert truncate_display_width("A👨‍👩‍👧‍👦BC", 4) == "A…"
    assert truncate_display_width("👨‍👩‍👧‍👦ABC", 4) == "👨‍👩‍👧‍👦…"


def test_display_width_truncates_mixed_titles_with_reserved_ellipsis() -> None:
    value = "OpenClaw 微信通知与 Session 管理优化"

    assert truncate_display_width(value, 30) == "OpenClaw 微信通知与 Session…"
    assert display_width(truncate_display_width(value, 30)) <= 30
