#!/usr/bin/env python3
"""申报月份与税款所属期换算工具。"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Literal

PeriodCycle = Literal["monthly", "quarterly", "annual", "monthly_or_quarterly"]


def validate_filing_year_period(year: int, period: int) -> None:
    """校验申报月份。"""

    if not isinstance(year, int):
        raise ValueError("`year` 必须是整数。")
    if not isinstance(period, int):
        raise ValueError("`period` 必须是整数。")
    if period < 1 or period > 12:
        raise ValueError("`period` 必须在 1 到 12 之间，表示申报月份。")


def resolve_filing_year_period(
    year: int | None = None,
    period: int | None = None,
    *,
    today: date | None = None,
) -> tuple[int, int]:
    """解析申报月份，默认取当前月份。"""

    current = today or date.today()
    filing_year = year or current.year
    filing_period = period or current.month
    validate_filing_year_period(filing_year, filing_period)
    return filing_year, filing_period


def month_range(year: int, month: int) -> tuple[str, str]:
    """返回指定自然月的起止日期。"""

    validate_filing_year_period(year, month)
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def previous_month_range(year: int, period: int) -> tuple[str, str]:
    """返回申报月份对应的上一个自然月起止日期。"""

    validate_filing_year_period(year, period)
    target_year = year
    target_month = period - 1
    if target_month == 0:
        target_year -= 1
        target_month = 12
    return month_range(target_year, target_month)


def previous_quarter_range(year: int, period: int) -> tuple[str, str]:
    """返回申报月份对应的上一自然季度起止日期。"""

    validate_filing_year_period(year, period)
    current_quarter = ((period - 1) // 3) + 1
    target_quarter = current_quarter - 1
    target_year = year
    if target_quarter == 0:
        target_year -= 1
        target_quarter = 4
    start_month = (target_quarter - 1) * 3 + 1
    start_date, _ = month_range(target_year, start_month)
    _, end_date = month_range(target_year, start_month + 2)
    return start_date, end_date


def previous_year_range(year: int, period: int) -> tuple[str, str]:
    """返回申报月份对应的上一自然年起止日期。"""

    validate_filing_year_period(year, period)
    target_year = year - 1
    return f"{target_year:04d}-01-01", f"{target_year:04d}-12-31"


def resolve_tax_period_range(year: int, period: int, cycle: PeriodCycle) -> tuple[str, str]:
    """按周期把申报月份换算为税款所属期起止日期。"""

    validate_filing_year_period(year, period)
    if cycle == "monthly":
        return previous_month_range(year, period)
    if cycle == "quarterly":
        return previous_quarter_range(year, period)
    if cycle == "annual":
        return previous_year_range(year, period)
    if cycle == "monthly_or_quarterly":
        raise ValueError("该税种周期可能为月报或季报，必须显式指定 `period_cycle` 或直接传所属期起止。")
    raise ValueError("`period_cycle` 仅支持 `monthly`、`quarterly`、`annual`。")


def ensure_current_filing_period(
    year: int,
    period: int,
    *,
    action: str,
    today: date | None = None,
) -> None:
    """确保当前调用使用的是当前申报月份。"""

    filing_year, filing_period = resolve_filing_year_period(year, period, today=today)
    current = today or date.today()
    if (filing_year, filing_period) == (current.year, current.month):
        return
    raise ValueError(
        f"{action} 仅支持当前申报月份：当前为 {current.year:04d}-{current.month:02d}，"
        f"收到 {filing_year:04d}-{filing_period:02d}。`period` 表示申报月份，不是税款所属期月份；"
        "例如办理 3 月所属期月报时，4 月应传 `period=4`。"
    )


def filing_month_label(year: int, period: int) -> str:
    """格式化申报月份标签。"""

    validate_filing_year_period(year, period)
    return f"{year:04d}-{period:02d}"


def format_period_label(start_date: str, end_date: str) -> str:
    """格式化所属期标签。"""

    return f"{start_date}~{end_date}"
