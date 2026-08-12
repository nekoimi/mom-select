from datetime import date

from mom_select.models import AdviceReport, MarketAssessment
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
        dual_period_rankings=[],
        dual_period_target=None,
        current_holdings=[],
        targets=["513360.XSHG"],
        action="买入候选",
        explanation="人工复核后建立目标仓位",
    )

    result = render_html(report)

    assert '<meta charset="utf-8">' in result
    assert "ETF轮动策略简报" in result
    assert "25日趋势 + 10日择时" in result
    assert "人工执行检查" in result
    assert "513360.XSHG" in result
