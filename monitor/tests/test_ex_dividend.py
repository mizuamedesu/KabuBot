from __future__ import annotations

import pandas as pd

from kabubot.scanner import _exclude_extreme_one_day_drops
from kabubot.types import PriceSignal
from kabubot.yfinance_skill import _build_signal


def test_build_signal_marks_ex_dividend_drop() -> None:
    index = pd.to_datetime(["2026-03-27", "2026-03-30"])
    frame = pd.DataFrame(
        {
            "Close": [100.0, 95.0],
            "Volume": [1000, 1200],
            "Dividends": [0.0, 5.0],
        },
        index=index,
    )

    signal = _build_signal("TEST", frame, {"currency": "USD"}, None)

    assert signal.day_change_pct == -5.0
    assert signal.is_ex_dividend_date is True
    assert signal.ex_dividend_date == "2026-03-30"
    assert signal.dividend_per_share == 5.0
    assert signal.dividend_yield_on_previous_close_pct == 5.0
    assert signal.ex_dividend_adjusted_day_change_pct == 0.0
    assert any("ex-dividend date" in note for note in signal.notes)


def test_build_signal_does_not_mark_normal_day() -> None:
    index = pd.to_datetime(["2026-03-30", "2026-03-31"])
    frame = pd.DataFrame(
        {
            "Close": [100.0, 98.0],
            "Volume": [1000, 1200],
            "Dividends": [0.0, 0.0],
        },
        index=index,
    )

    signal = _build_signal("TEST", frame, {"currency": "USD"}, None)

    assert signal.is_ex_dividend_date is False
    assert signal.ex_dividend_date is None
    assert signal.dividend_per_share is None
    assert signal.ex_dividend_adjusted_day_change_pct is None


def test_mechanical_ex_dividend_drop_is_not_excluded_as_bad_price_data() -> None:
    signal = PriceSignal(
        symbol="SPECIAL",
        day_change_pct=-50.0,
        is_ex_dividend_date=True,
        ex_dividend_adjusted_day_change_pct=-2.0,
    )

    included, excluded = _exclude_extreme_one_day_drops([signal])

    assert included == [signal]
    assert excluded == []
