from datetime import date

from mom_select.stock.models import StockAdviceReport, StockMetrics
from mom_select.stock.reporting import render_html, render_markdown


def _report() -> StockAdviceReport:
    metric = StockMetrics(
        code="600001.XSHG",
        name="趋势样例",
        close=32.5,
        score=12.3,
        return_20d=0.1,
        return_60d=0.2,
        return_120d=0.3,
        relative_strength_20d=0.04,
        relative_strength_60d=0.08,
        r_squared=0.91,
        atr_ratio=0.025,
        max_drawdown=-0.08,
        average_turnover=100_000_000,
        ma_aligned=True,
        distance_ma20=0.03,
        drawdown_from_60d_high=-0.02,
        entry_score=82.0,
        change_pct=0.035,
        turnover_rate=0.08,
        snapshot_turnover=500_000_000,
    )
    return StockAdviceReport(
        generated_at="2026-08-13T15:20:00+08:00",
        as_of=date(2026, 8, 13),
        universe_size=5000,
        prefiltered_size=1800,
        analyzed_count=1700,
        rankings=[metric],
        entry_candidates=[metric],
        exclusion_counts={"创业板": 500, "科创板": 500, "ST或退市风险警示": 100},
        exclusions={},
        failed_codes={},
    )


def test_stock_reports_show_ranking_and_never_emit_order_language() -> None:
    markdown = render_markdown(_report())
    html = render_html(_report())

    assert "趋势样例" in markdown
    assert "全市场股票：5000" in markdown
    assert "个股趋势排名日报" in html
    assert "个股趋势策略简报" in html
    assert "全市场动态筛选" in html
    assert "2026年8月13日 星期四" in html
    assert "适合当前介入的趋势候选" in html
    assert "最多30只 · 本次1只" in html
    assert "高动量排名" in html
    assert "最多20只 · 本次1只" in html
    assert "最多30只，本次1只" in markdown
    assert "最多20只，本次1只" in markdown
    assert "入场分" in html
    assert "今日涨幅" in html
    assert "换手率" in html
    assert "5.00亿" in html
    assert 'class="section-index">01' in html
    assert "--accent:#b44b3d" in html
    assert "#167c5a" not in html
    assert "自动下单" in html
    assert "生成于 2026年08月13日 15:20:00（北京时间）" in html
    assert "买入候选" not in markdown
    assert "换仓" not in html


def test_stock_reports_only_render_public_warning_summaries() -> None:
    report = _report()
    report.warnings = ["2只股票的前复权日线使用本地缓存"]

    markdown = render_markdown(report)
    html = render_html(report)

    assert "2只股票的前复权日线使用本地缓存" in markdown
    assert "2只股票的前复权日线使用本地缓存" in html
    assert "ConnectionError" not in markdown
    assert "RemoteDisconnected" not in html
