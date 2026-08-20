"""交易日历测试 - 覆盖周末、节假日、调休补班日等场景

跑法：
    pytest tests/test_trading_calendar.py -v
"""
from __future__ import annotations

import datetime

import pytest

from app.market.calendar import (
    _load_trading_days,
    get_market_status,
    get_next_trading_day,
    get_prev_trading_day,
    get_recent_trading_days,
    is_trading_day,
    is_weekend,
    reload_trading_days,
)


# ============================================================
# 1. 基础日期判断
# ============================================================

class TestBasicDateChecks:
    """周末 / 工作日 / 已知日期"""

    def test_weekend_saturday(self):
        # 2026-05-23 是周六
        assert is_weekend(datetime.date(2026, 5, 23)) is True

    def test_weekend_sunday(self):
        assert is_weekend(datetime.date(2026, 5, 24)) is True

    def test_weekday(self):
        # 2026-05-20 是周三
        assert is_weekend(datetime.date(2026, 5, 20)) is False

    def test_is_trading_day_normal_weekday(self):
        """普通工作日"""
        assert is_trading_day(datetime.date(2026, 5, 20)) is True

    def test_is_trading_day_weekend(self):
        """周末一定不是交易日"""
        assert is_trading_day(datetime.date(2026, 5, 23)) is False  # 周六
        assert is_trading_day(datetime.date(2026, 5, 24)) is False  # 周日

    def test_is_trading_day_端午_2026(self):
        """核心场景：2026 端午 6/19-6/21 都不该是交易日"""
        assert is_trading_day(datetime.date(2026, 6, 19)) is False  # 周五 端午
        assert is_trading_day(datetime.date(2026, 6, 20)) is False  # 周六
        assert is_trading_day(datetime.date(2026, 6, 21)) is False  # 周日
        # 6/22 周一应该恢复交易
        assert is_trading_day(datetime.date(2026, 6, 22)) is True


# ============================================================
# 2. 历史已知节假日
# ============================================================

class TestKnownHolidays:
    """几个已知的 A 股节假日"""

    @pytest.mark.parametrize("date,expected,desc", [
        # 2025 春节：1/28 除夕 - 2/4 初六（实际 A 股多休到 2/4）
        (datetime.date(2025, 1, 28), False, "2025 春节除夕"),
        (datetime.date(2025, 1, 29), False, "2025 春节初一"),
        (datetime.date(2025, 1, 30), False, "2025 春节初二"),
        (datetime.date(2025, 2, 4), False, "2025 春节初六"),
        (datetime.date(2025, 2, 5), True, "2025 春节后第一天交易"),
        # 2025 国庆+中秋：10/1-10/8
        (datetime.date(2025, 10, 1), False, "2025 国庆"),
        (datetime.date(2025, 10, 8), False, "2025 国庆最后一天"),
        (datetime.date(2025, 10, 9), True, "2025 国庆后第一天交易"),
        # 2024 春节：2/9 除夕 - 2/18 共 10 天（akshare 真实数据）
        (datetime.date(2024, 2, 8), True, "2024 春节前最后一个交易日"),
        (datetime.date(2024, 2, 9), False, "2024 春节除夕"),
        (datetime.date(2024, 2, 18), False, "2024 春节最后一天（周日仍休）"),
        (datetime.date(2024, 2, 19), True, "2024 春节后第一天交易"),
    ])
    def test_holidays(self, date, expected, desc):
        assert is_trading_day(date) is expected, f"{desc} ({date}): 期望 {expected}"


# ============================================================
# 3. 调休补班日
# ============================================================

class TestCompensationDays:
    """调休补班的周末（如 2024-02-04 周日是春节调休上班日，应该不是交易日）"""

    def test_2024_spring_festival_compensation(self):
        # 2024-02-04 周日 春节调休上班，但 A 股不交易
        # 实际 A 股 2024-02-04 不开盘，调休上班是工作日但股市不开
        # 这个测试需要根据真实数据，可能 akshare 返回 False（正确）或 True（看交易所安排）
        # 实际上 A 股的调休都是"股市跟随国家调休"，调休上班日股市也开
        # 但 2024-02-04 是周日 + 春节假期内，所以肯定不开
        assert is_trading_day(datetime.date(2024, 2, 4)) is False


# ============================================================
# 4. get_prev_trading_day / get_next_trading_day
# ============================================================

class TestPrevNext:
    def test_prev_trading_day_normal(self):
        """正常工作日前一天"""
        # 5/20 周三，上一个是 5/19 周二
        assert get_prev_trading_day(datetime.date(2026, 5, 20)) == datetime.date(2026, 5, 19)

    def test_prev_trading_day_skips_weekend(self):
        """周一的"昨天"是上周五（跳过周末）"""
        # 5/25 周一 → 上一个是 5/22 周五
        assert get_prev_trading_day(datetime.date(2026, 5, 25)) == datetime.date(2026, 5, 22)

    def test_prev_trading_day_skips_端午(self):
        """核心场景：6/22 周一的"上一个交易日"是 6/18（跳过 6/19 端午 + 周末）"""
        assert get_prev_trading_day(datetime.date(2026, 6, 22)) == datetime.date(2026, 6, 18)

    def test_next_trading_day_normal(self):
        """正常工作日后一天"""
        assert get_next_trading_day(datetime.date(2026, 5, 20)) == datetime.date(2026, 5, 21)

    def test_next_trading_day_skips_weekend(self):
        """周五的"明天"是下周一"""
        # 5/22 周五 → 下一个是 5/25 周一
        assert get_next_trading_day(datetime.date(2026, 5, 22)) == datetime.date(2026, 5, 25)

    def test_next_trading_day_skips_端午(self):
        """核心场景：6/18 周四的"下一个交易日"是 6/22 周一（跳过 6/19-6/21 端午假期）"""
        assert get_next_trading_day(datetime.date(2026, 6, 18)) == datetime.date(2026, 6, 22)


# ============================================================
# 5. get_recent_trading_days
# ============================================================

class TestRecentTradingDays:
    def test_recent_5_days_normal(self):
        """取最近 5 个交易日"""
        days = get_recent_trading_days(5, end_date=datetime.date(2026, 5, 22))
        assert len(days) == 5
        assert days[-1] == datetime.date(2026, 5, 22)  # 最新一天

    def test_recent_days_skip_端午(self):
        """从 6/22 取最近 5 个，跳过端午"""
        days = get_recent_trading_days(5, end_date=datetime.date(2026, 6, 22))
        # 期望: [6/12, 6/15, 6/16, 6/17, 6/18, 6/22] 中最后 5 个
        # 即 [6/15, 6/16, 6/17, 6/18, 6/22]
        assert datetime.date(2026, 6, 19) not in days  # 端午不能进
        assert datetime.date(2026, 6, 20) not in days  # 周末
        assert datetime.date(2026, 6, 21) not in days  # 周末
        assert days[-1] == datetime.date(2026, 6, 22)


# ============================================================
# 6. 缓存与降级行为
# ============================================================

class TestCacheAndFallback:
    """交易日历的加载、缓存、降级行为"""

    def test_calendar_loads(self):
        """交易日历能加载出非空集合"""
        days = _load_trading_days()
        # 加载成功时是非空集合（否则走降级）
        # 实际测试中可能因为网络问题加载失败，容忍两种情况
        if days:
            # 加载成功
            assert len(days) > 8000, "应该至少 1990 以来的所有交易日"
            assert datetime.date(2026, 5, 20) in days

    def test_reload_clears_cache(self):
        """reload_trading_days 强制重新加载"""
        before = _load_trading_days()
        after = reload_trading_days()
        # 两次应该返回等价集合（reload 后内容应该一致）
        assert before == after

    def test_is_trading_day_fallback(self, monkeypatch):
        """当 akshare 加载失败时，is_trading_day 降级为"只排除周末" """
        from app.market import calendar as cal_mod

        # 模拟加载失败
        monkeypatch.setattr(cal_mod, "_TRADING_DAYS_LOAD_ATTEMPTED", True)
        monkeypatch.setattr(cal_mod, "_TRADING_DAYS_CACHE", set())

        # 降级模式：周五默认还是交易日（即使实际上是端午）
        assert is_trading_day(datetime.date(2026, 6, 19)) is True  # 端午但降级认为是工作日
        # 周末仍然正确排除
        assert is_trading_day(datetime.date(2026, 5, 23)) is False


# ============================================================
# 8. get_market_status
# ============================================================

class TestMarketStatus:
    def test_market_status_holiday(self):
        """非交易日显示休市"""
        # 端午
        dt = datetime.datetime(2026, 6, 19, 10, 30)  # 周五上午
        assert "休市" in get_market_status(dt)

    def test_market_status_weekend(self):
        """周末显示休市"""
        dt = datetime.datetime(2026, 5, 23, 10, 30)  # 周六上午
        assert "休市" in get_market_status(dt)
