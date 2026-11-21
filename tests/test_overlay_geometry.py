"""Where the overlay puts things, on the monitor layouts where overlays usually break.

Every expected number is worked out by hand in the comments, not by the code under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from starlight.overlay.geometry import (
    Monitor,
    caption_rect,
    card_rect,
    line_end,
    match_monitors,
    orb_point,
    to_logical,
    to_logical_rect,
)

approx = pytest.approx


def mon(name: str, phys: tuple[int, int, int, int], dpr: float = 1.0) -> Monitor:
    """A monitor as Qt on Windows sees it: the native top-left, the size divided by dpr."""
    x, y, w, h = phys
    return Monitor(name, phys, (x, y, w / dpr, h / dpr), dpr)


# The layouts. Physical rects as EnumDisplayMonitors gives them.
SINGLE = [mon("A", (0, 0, 1920, 1080))]
PAUL = [mon("A", (0, 0, 1920, 1080)), mon("B", (1920, 0, 1920, 1080))]
LEFT = [mon("A", (0, 0, 1920, 1080)), mon("B", (-1920, 0, 1920, 1080))]
ABOVE = [mon("A", (0, 0, 1920, 1080)), mon("B", (0, -1080, 1920, 1080))]
AT_125 = [mon("A", (0, 0, 2560, 1440), 1.25)]  # logical 2048 x 1152
AT_150 = [mon("A", (0, 0, 3840, 2160), 1.5)]  # logical 2560 x 1440
MIXED = [mon("A", (0, 0, 1920, 1080)), mon("B", (1920, 0, 3840, 2160), 1.5)]  # B: 2560x1440


# --- match_monitors ----------------------------------------------------------------------


def test_match_single() -> None:
    [m] = match_monitors([(0, 0, 1920, 1080)], [("A", (0, 0, 1920, 1080), 1.0)])
    assert m == Monitor("A", (0, 0, 1920, 1080), (0, 0, 1920, 1080), 1.0)


def test_match_two_same_size_in_other_order_by_position() -> None:
    # The user's two 1080p monitors are the same size, so only the position tells them
    # apart: Qt lists B first, Windows lists A first.
    out = match_monitors(
        [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)],
        [("B", (1920, 0, 1920, 1080), 1.0), ("A", (0, 0, 1920, 1080), 1.0)],
    )
    assert [(m.name, m.physical) for m in out] == [
        ("B", (1920, 0, 1920, 1080)),
        ("A", (0, 0, 1920, 1080)),
    ]


def test_match_by_size_when_positions_differ() -> None:
    # Mixed scaling: Windows lists the 4K one first, Qt the 1080p one. 3840 / 1.5 = 2560
    # and 2160 / 1.5 = 1440 fit B; 1920 / 1.0 fits A. Qt's positions here are made up
    # (not the native ones), so only the size can pair them.
    out = match_monitors(
        [(1920, 0, 3840, 2160), (0, 0, 1920, 1080)],
        [("A", (0, 0, 1920, 1080), 1.0), ("B", (1200, 0, 2560, 1440), 1.5)],
    )
    assert [(m.name, m.physical, m.dpr) for m in out] == [
        ("A", (0, 0, 1920, 1080), 1.0),
        ("B", (1920, 0, 3840, 2160), 1.5),
    ]


def test_match_size_within_2px() -> None:
    # 2561 / 1.25 = 2048.8, within 2 px of 2048: still a match, ahead of the first free one.
    out = match_monitors(
        [(0, 0, 1920, 1080), (1920, 0, 2561, 1440)],
        [("B", (500, 0, 2048, 1152), 1.25)],
    )
    assert out[0].physical == (1920, 0, 2561, 1440)


def test_match_no_size_fits_takes_order() -> None:
    out = match_monitors(
        [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)],
        [("X", (0, 0, 1000, 700), 1.0), ("Y", (5, 5, 1000, 700), 1.0)],
    )
    assert [(m.name, m.physical) for m in out] == [
        ("X", (0, 0, 1920, 1080)),
        ("Y", (1920, 0, 1920, 1080)),
    ]


def test_match_unequal_counts() -> None:
    assert len(match_monitors([(0, 0, 1920, 1080)], [])) == 0
    two_screens = [("A", (0, 0, 1920, 1080), 1.0), ("B", (1920, 0, 1920, 1080), 1.0)]
    assert [m.name for m in match_monitors([(0, 0, 1920, 1080)], two_screens)] == ["A"]


# --- 1. One monitor ----------------------------------------------------------------------


def test_single_monitor() -> None:
    [m] = SINGLE
    assert to_logical((100, 200), SINGLE) == (0, approx((100, 200)))
    assert orb_point(m) == approx((960, 30))  # 1920 / 2, 30 down
    # From the orb's lower edge (30 + 22 = 52) to a box at (100, 800, 200, 50): the box
    # grown by 4 is (96, 796)-(304, 854); x 960 is clamped to 304, y 52 to 796.
    assert line_end((960, 52), (100, 800, 200, 50)) == approx((304, 796))
    # Caption 300x40, no target: right of the orb, 960 + 22 + 16 = 998; 30 - 40/2 = 10.
    assert caption_rect((960, 30), (300, 40), m) == approx((998, 10, 300, 40))
    # Card 320x120: 960 - 160 = 800; under the orb at 30 + 22 + 16 = 68.
    assert card_rect((960, 30), (320, 120), m) == approx((800, 68, 320, 120))


# --- 2. Two side by side (the user's PC) -------------------------------------------------


def test_two_side_by_side() -> None:
    a, b = PAUL
    assert to_logical((2500, 300), PAUL) == (1, approx((2500, 300)))
    assert to_logical((1919, 0), PAUL)[0] == 0  # the last column of A
    assert to_logical((1920, 0), PAUL)[0] == 1  # the first column of B
    assert orb_point(a) == approx((960, 30))
    assert orb_point(b) == approx((2880, 30))  # 1920 + 960
    assert to_logical_rect((2000, 400, 300, 100), PAUL) == (1, approx((2000, 400, 300, 100)))


# --- 3. The second monitor left of the main one ------------------------------------------


def test_second_monitor_left_negative_x() -> None:
    _, b = LEFT
    assert to_logical((-100, 500), LEFT) == (1, approx((-100, 500)))
    assert orb_point(b) == approx((-960, 30))  # -1920 + 960
    assert to_logical_rect((-500, 200, 100, 40), LEFT) == (1, approx((-500, 200, 100, 40)))
    # Caption on B, no target: -960 + 38 = -922.
    assert caption_rect((-960, 30), (300, 40), b) == approx((-922, 10, 300, 40))


# --- 4. One monitor above the other ------------------------------------------------------


def test_second_monitor_above() -> None:
    _, b = ABOVE
    assert to_logical((960, -540), ABOVE) == (1, approx((960, -540)))
    assert to_logical((960, -1), ABOVE)[0] == 1
    assert to_logical((960, 0), ABOVE)[0] == 0
    assert orb_point(b) == approx((960, -1050))  # -1080 + 30
    # Card on B: y = -1050 + 38 = -1012; x = 960 - 160 = 800.
    assert card_rect((960, -1050), (320, 120), b) == approx((800, -1012, 320, 120))


# --- 5. 125 % ----------------------------------------------------------------------------


def test_scaling_125() -> None:
    [m] = AT_125
    assert m.logical == approx((0, 0, 2048, 1152))
    assert to_logical((1000, 500), AT_125) == (0, approx((800, 400)))  # / 1.25
    assert orb_point(m) == approx((1024, 30))  # 2048 / 2
    # (1250, 625, 250, 125) / 1.25 = (1000, 500, 200, 100)
    assert to_logical_rect((1250, 625, 250, 125), AT_125) == (0, approx((1000, 500, 200, 100)))
    # A fraction stays a fraction: 1001 / 1.25 = 800.8
    assert to_logical((1001, 1), AT_125) == (0, approx((800.8, 0.8)))


# --- 6. 150 % ----------------------------------------------------------------------------


def test_scaling_150() -> None:
    [m] = AT_150
    assert m.logical == approx((0, 0, 2560, 1440))
    assert to_logical((3000, 1500), AT_150) == (0, approx((2000, 1000)))  # / 1.5
    assert orb_point(m) == approx((1280, 30))
    assert to_logical_rect((1500, 300, 300, 90), AT_150) == (0, approx((1000, 200, 200, 60)))


# --- 7. Mixed 100 % + 150 % --------------------------------------------------------------


def test_mixed_scaling() -> None:
    a, b = MIXED
    assert b.logical == approx((1920, 0, 2560, 1440))
    # B is converted from its own origin: 1920 + (2520 - 1920) / 1.5 = 2320; 300 / 1.5 = 200.
    assert to_logical((2520, 300), MIXED) == (1, approx((2320, 200)))
    # A stays 1:1.
    assert to_logical((1900, 300), MIXED) == (0, approx((1900, 300)))
    # (3420, 1500, 300, 150) on B: x 1920 + 1500 / 1.5 = 2920, y 1000, 200 x 100.
    assert to_logical_rect((3420, 1500, 300, 150), MIXED) == (1, approx((2920, 1000, 200, 100)))
    assert orb_point(b) == approx((3200, 30))  # 1920 + 2560 / 2
    # The line on B from its orb's lower edge (3200, 52) to that box: grown by 4 it is
    # (2916, 996)-(3124, 1104); x 3200 clamps to 3124, y 52 to 996.
    assert line_end((3200, 52), (2920, 1000, 200, 100)) == approx((3124, 996))
    assert caption_rect((3200, 30), (300, 40), b) == approx((3238, 10, 300, 40))
    assert orb_point(a) == approx((960, 30))


# --- 8. A box across the gap between two monitors ----------------------------------------


def test_box_across_the_gap() -> None:
    # Centre 1800 + 150 = 1950: on B. Clipped to B: 1920..2100, so (1920, 500, 180, 100).
    assert to_logical_rect((1800, 500, 300, 100), PAUL) == (1, approx((1920, 500, 180, 100)))
    # Centre 1700 + 150 = 1850: on A. Clipped to A: 1700..1920, so (1700, 500, 220, 100).
    assert to_logical_rect((1700, 500, 300, 100), PAUL) == (0, approx((1700, 500, 220, 100)))
    # Mixed: centre 1950 is on B. Clipped: (1920, 600, 180, 150); on B's scale
    # x 1920 + 0 = 1920, y 600 / 1.5 = 400, 180 / 1.5 = 120, 150 / 1.5 = 100.
    assert to_logical_rect((1800, 600, 300, 150), MIXED) == (1, approx((1920, 400, 120, 100)))


# --- 9. A box partly off screen ----------------------------------------------------------


def test_box_partly_off_screen() -> None:
    # Centre (1950, 1100) is off the screen: the nearest monitor is A. Clipped to A:
    # x 1800..1920, y 1000..1080, so (1800, 1000, 120, 80).
    assert to_logical_rect((1800, 1000, 300, 200), SINGLE) == (0, approx((1800, 1000, 120, 80)))
    # Above and left of the top-left corner: clipped to (0, 0, 200, 150).
    assert to_logical_rect((-100, -50, 300, 200), SINGLE) == (0, approx((0, 0, 200, 150)))
    # Nothing of it on the screen.
    assert to_logical_rect((2000, 100, 50, 50), SINGLE) is None
    # A point off the screen goes to the nearest edge.
    assert to_logical((2100, -20), SINGLE) == (0, approx((1920, 0)))
    # Off both of the user's monitors, below B: nearest is B, clamped to y 1080.
    assert to_logical((2500, 1300), PAUL) == (1, approx((2500, 1080)))


# --- 10. A target to the left, right and below the orb -----------------------------------


def test_caption_away_from_the_target() -> None:
    a = SINGLE[0]
    orb = (960, 30)
    # Target right (centre 1550 > 960): caption left, 960 - 22 - 16 - 300 = 622.
    assert caption_rect(orb, (300, 40), a, (1500, 500, 100, 40)) == approx((622, 10, 300, 40))
    # Target left (centre 150): caption right, 998.
    assert caption_rect(orb, (300, 40), a, (100, 500, 100, 40)) == approx((998, 10, 300, 40))
    # Target straight below (centre 930 + 30 = 960): right, like no target.
    assert caption_rect(orb, (300, 40), a, (930, 500, 60, 40)) == approx((998, 10, 300, 40))


def test_line_to_targets_left_right_below_and_under_the_orb() -> None:
    # Left: (10, 50) to box (100, 0, 50, 100): left edge 100 - 4 = 96.
    assert line_end((10, 50), (100, 0, 50, 100)) == approx((96, 50))
    # Right: (400, 50) to the same box: right edge 150 + 4 = 154.
    assert line_end((400, 50), (100, 0, 50, 100)) == approx((154, 50))
    # Below: (125, 300): bottom edge 100 + 4 = 104.
    assert line_end((125, 300), (100, 0, 50, 100)) == approx((125, 104))
    # Diagonal: the nearest point is the grown corner (96, 96).
    assert line_end((0, 0), (100, 100, 50, 50)) == approx((96, 96))
    # The orb over the box itself (50, 50) in (0, 0, 200, 100): top, bottom and left are
    # all 54 away (-4 / 104 / -4), right 154; a tie goes to the top: (50, -4).
    assert line_end((50, 50), (0, 0, 200, 100)) == approx((50, -4))
    # Inside, nearest the right: (190, 50): right 204 - 190 = 14.
    assert line_end((190, 50), (0, 0, 200, 100)) == approx((204, 50))


def test_kept_inside_the_monitor() -> None:
    a, b = PAUL
    # A wide caption right of B's orb: 2880 + 38 = 2918 would end past 3840 - 8, so it
    # moves left to 3840 - 8 - 1000 = 2832.
    assert caption_rect((2880, 30), (1000, 40), b) == approx((2832, 10, 1000, 40))
    # Left of A's orb, too wide: 960 - 38 - 1000 = -78 moves right to the margin, 8.
    assert caption_rect((960, 30), (1000, 40), a, (1500, 500, 10, 10)) == approx((8, 10, 1000, 40))
    # A tall caption: 30 - 30 = 0 moves down to 8.
    assert caption_rect((960, 30), (300, 60), a) == approx((998, 8, 300, 60))
    # A card wider than the monitor sits at the left margin: (8, 68).
    assert card_rect((960, 30), (2000, 100), a) == approx((8, 68, 2000, 100))
    # A card too tall for the space under the orb moves up: 1080 - 8 - 1050 = 22.
    assert card_rect((960, 30), (320, 1050), a) == approx((800, 22, 320, 1050))


def test_no_monitors() -> None:
    with pytest.raises(ValueError):
        to_logical((0, 0), [])
    with pytest.raises(ValueError):
        to_logical_rect((0, 0, 1, 1), [])


def test_no_qt_import() -> None:
    import starlight.overlay.geometry as geometry

    source = Path(geometry.__file__).read_text(encoding="utf-8")
    assert "PySide" not in source and "PyQt" not in source
