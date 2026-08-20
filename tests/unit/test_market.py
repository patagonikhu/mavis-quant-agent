"""A股市场规则单元测试"""

import datetime

import pytest

from app.market.rules import (
    get_price_limit,
    is_price_limit_hit,
    get_market_code,
    validate_lot_size,
    calc_lot_count,
    get_change_pct,
    get_market_session,
    is_trading_time,
)
from app.market.calendar import (
    is_weekend,
    is_trading_day,
    get_recent_trading_days,
    get_next_trading_day,
    get_market_status,
)


# ---- 涨跌停测试 ----

class TestPriceLimit:
    def test_main_board(self):
        upper, lower = get_price_limit("600519", 100.0)
        assert upper == 110.0
        assert lower == 90.0

    def test_chinext(self):
        upper, lower = get_price_limit("300750", 100.0)
        assert upper == 120.0
        assert lower == 80.0

    def test_star(self):
        upper, lower = get_price_limit("688981", 100.0)
        assert upper == 120.0
        assert lower == 80.0

    def test_st(self):
        upper, lower = get_price_limit("600519", 10.0, is_st=True)
        assert upper == 10.5
        assert lower == 9.5


class TestPriceLimitHit:
    def test_up_limit(self):
        assert is_price_limit_hit("600519", 110.0, 100.0) == "up_limit"

    def test_down_limit(self):
        assert is_price_limit_hit("600519", 90.0, 100.0) == "down_limit"

    def test_no_limit(self):
        assert is_price_limit_hit("600519", 105.0, 100.0) == ""


# ---- 市场代码测试 ----

class TestMarketCode:
    def test_sh_main(self):
        assert get_market_code("600519") == "沪市主板"

    def test_sz_main(self):
        assert get_market_code("000001") == "深市主板"

    def test_chinext(self):
        assert get_market_code("300750") == "创业板"

    def test_star(self):
        assert get_market_code("688981") == "科创板"

    def test_bse(self):
        assert get_market_code("830799") == "北交所"


# ---- 交易单位测试 ----

class TestLotSize:
    def test_valid_lot(self):
        assert validate_lot_size(100) is True
        assert validate_lot_size(200) is True
        assert validate_lot_size(500) is True

    def test_invalid_lot(self):
        assert validate_lot_size(50) is False
        assert validate_lot_size(150) is False
        assert validate_lot_size(0) is False
        assert validate_lot_size(-100) is False

    def test_calc_lot_count(self):
        assert calc_lot_count(500) == 5
        assert calc_lot_count(100) == 1


# ---- 涨跌幅计算 ----

class TestChangePct:
    def test_positive(self):
        assert get_change_pct(110, 100) == 10.0

    def test_negative(self):
        assert get_change_pct(90, 100) == -10.0

    def test_zero(self):
        assert get_change_pct(100, 100) == 0.0

    def test_zero_pre_close(self):
        assert get_change_pct(100, 0) == 0.0


# ---- 交易时段测试 ----

class TestMarketSession:
    def test_morning_trading(self):
        dt = datetime.datetime(2026, 6, 12, 10, 0)  # 周五 10:00
        assert get_market_session(dt) == "morning"

    def test_afternoon_trading(self):
        dt = datetime.datetime(2026, 6, 12, 14, 0)  # 周五 14:00
        assert get_market_session(dt) == "afternoon"

    def test_lunch_break(self):
        dt = datetime.datetime(2026, 6, 12, 12, 0)  # 周五 12:00
        assert get_market_session(dt) == "lunch_break"

    def test_weekend(self):
        dt = datetime.datetime(2026, 6, 13, 10, 0)  # 周六
        assert get_market_session(dt) == "closed"

    def test_is_trading_time(self):
        dt = datetime.datetime(2026, 6, 12, 10, 0)
        assert is_trading_time(dt) is True

    def test_is_not_trading_time(self):
        dt = datetime.datetime(2026, 6, 12, 20, 0)
        assert is_trading_time(dt) is False


# ---- 交易日历测试 ----

class TestCalendar:
    def test_weekend(self):
        saturday = datetime.date(2026, 6, 13)
        sunday = datetime.date(2026, 6, 14)
        assert is_weekend(saturday) is True
        assert is_weekend(sunday) is True

    def test_weekday(self):
        friday = datetime.date(2026, 6, 12)
        assert is_weekend(friday) is False

    def test_trading_day(self):
        friday = datetime.date(2026, 6, 12)
        assert is_trading_day(friday) is True

    def test_weekend_not_trading(self):
        saturday = datetime.date(2026, 6, 13)
        assert is_trading_day(saturday) is False

    def test_recent_trading_days(self):
        days = get_recent_trading_days(5, datetime.date(2026, 6, 12))
        assert len(days) == 5
        # 不应包含周末
        for d in days:
            assert d.weekday() < 5

    def test_next_trading_day(self):
        friday = datetime.date(2026, 6, 12)
        next_day = get_next_trading_day(friday)
        assert next_day == datetime.date(2026, 6, 15)  # 周一

    def test_market_status(self):
        dt = datetime.datetime(2026, 6, 12, 10, 0)
        status = get_market_status(dt)
        assert "交易" in status
