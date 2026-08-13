from datetime import date

from mom_select.models import AdviceReport, Holding, MarketAssessment
from mom_select.reporting import render_html


def test_html_report_contains_core_sections_and_utf8_content() -> None:
    report = AdviceReport(
        generated_at="2026-08-12T17:00:00+08:00",
        as_of=date(2026, 8, 12),
        market=MarketAssessment(
            regime="normal",
            previous_regime="normal",
            as_of=date(2026, 8, 12),
            above_count=4,
            below_count=0,
            signals=[],
            explanation="维持正常期",
        ),
        pool_size=114,
        analyzed_count=114,
        failed_codes=[],
        rankings=[],
        eligible=[],
        candidates=[],
        current_holdings=[Holding(code="510300.XSHG", name="沪深300ETF")],
        targets=["513360.XSHG"],
        action="买入候选",
        explanation="人工复核后建立目标仓位",
    )

    result = render_html(report)

    assert '<meta charset="utf-8">' in result
    assert "ETF轮动策略简报" in result
    assert "25日趋势排名" in result
    assert "人工执行检查" in result
    assert "513360.XSHG" in result
    assert "2026年8月12日 星期三" in result
    assert 'class="regime-panel normal"' in result
    assert 'class="market-table"' in result


def test_html_report_shows_target_name_and_places_warnings_after_checklist() -> None:
    report = AdviceReport(
        generated_at="2026-08-12T17:00:00+08:00",
        as_of=date(2026, 8, 12),
        market=MarketAssessment(
            regime="normal",
            previous_regime="normal",
            as_of=date(2026, 8, 12),
            above_count=4,
            below_count=0,
            signals=[],
            explanation="维持正常期",
        ),
        pool_size=1,
        analyzed_count=1,
        failed_codes=[],
        rankings=[],
        eligible=[],
        candidates=[],
        current_holdings=[Holding(code="510300.XSHG", name="沪深300ETF")],
        targets=["510300.XSHG"],
        action="买入候选",
        explanation="人工复核",
        warnings=["示例风险"],
    )
    result = render_html(report)

    assert "沪深300ETF" in result
    assert "510300.XSHG" in result
    assert "买入候选 · 沪深300ETF（510300.XSHG）" in result
    assert result.index("人工执行检查") < result.index("风险提示")


def test_intraday_html_report_labels_realtime_data_and_expected_volume() -> None:
    report = AdviceReport(
        generated_at="2026-08-12T13:05:00+08:00",
        as_of=date(2026, 8, 12),
        market=MarketAssessment(
            regime="normal",
            previous_regime="normal",
            as_of=date(2026, 8, 12),
            above_count=4,
            below_count=0,
            signals=[],
            explanation="维持正常期",
        ),
        pool_size=114,
        analyzed_count=114,
        failed_codes=[],
        rankings=[],
        eligible=[],
        candidates=[],
        current_holdings=[],
        targets=["513360.XSHG"],
        action="买入候选",
        explanation="13:05人工复核",
        run_mode="intraday",
        signal_time="2026-08-12 13:05:00",
    )

    result = render_html(report)

    assert "13:05盘中信号" in result
    assert "预计全天量比" in result
    assert "10日择时" not in result


def test_debug_html_report_is_clearly_labeled() -> None:
    report = AdviceReport(
        generated_at="2026-08-12T20:00:00+08:00",
        as_of=date(2026, 8, 12),
        market=MarketAssessment(
            regime="normal",
            previous_regime="normal",
            as_of=date(2026, 8, 12),
            above_count=4,
            below_count=0,
            signals=[],
            explanation="维持正常期",
        ),
        pool_size=114,
        analyzed_count=114,
        failed_codes=[],
        rankings=[],
        eligible=[],
        candidates=[],
        current_holdings=[],
        targets=[],
        action="等待",
        explanation="本地调试",
        run_mode="intraday",
        signal_time="2026-08-12 13:05:00",
        debug=True,
        actionable=False,
    )

    result = render_html(report)

    assert "13:05盘中信号 · DEBUG" in result
    assert "调试结果" in result


def test_historical_debug_html_does_not_claim_realtime_data() -> None:
    report = AdviceReport(
        generated_at="2026-08-12T20:00:00+08:00",
        as_of=date(2026, 8, 11),
        market=MarketAssessment(
            regime="normal",
            previous_regime="normal",
            as_of=date(2026, 8, 11),
            above_count=4,
            below_count=0,
            signals=[],
            explanation="维持正常期",
        ),
        pool_size=114,
        analyzed_count=114,
        failed_codes=[],
        rankings=[],
        eligible=[],
        candidates=[],
        current_holdings=[],
        targets=[],
        action="调试：等待",
        explanation="本地调试",
        run_mode="intraday",
        signal_time="2026-08-11 13:05:00",
        debug=True,
        historical_simulation=True,
        actionable=False,
    )

    result = render_html(report)

    assert "13:05历史模拟 · DEBUG" in result
    assert "重建2026-08-11 13:05历史信号" in result
    assert "预计全天量比" in result
    assert "13:05实时快照与25日趋势排名" not in result
