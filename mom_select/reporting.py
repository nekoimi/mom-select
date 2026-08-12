from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from mom_select.models import AdviceReport, EtfMetrics


def _status(value: bool) -> str:
    return "通过" if value else "未通过"


def _metric_reasons(metric: EtfMetrics) -> str:
    checks = {
        "动量": metric.passed_momentum,
        "状态过滤": metric.passed_regime_filter,
        "成交量": metric.passed_volume,
        "短期跌幅": metric.passed_loss,
        "流动性": metric.passed_liquidity,
    }
    return "；".join(f"{name}:{_status(passed)}" for name, passed in checks.items())


def render_markdown(report: AdviceReport) -> str:
    regime = "走弱期" if report.market.regime == "weak" else "正常期"
    lines = [
        f"# ETF轮动建议｜{report.as_of.isoformat()}",
        "",
        "> 本报告由收盘日线生成，仅供下一交易日人工复核，不构成投资建议，也不会自动下单。",
        "",
        "## 结论",
        "",
        f"- 是否可执行：{'是' if report.actionable else '否'}",
        f"- 市场状态：{regime}",
        f"- 操作建议：{report.action}",
        f"- 策略目标：{', '.join(report.targets) if report.targets else '无'}",
        f"- 说明：{report.explanation}",
        f"- 数据覆盖：{report.analyzed_count}/{report.pool_size}",
    ]
    if report.warnings:
        lines.extend(["", "## 风险提示", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(["", "## 市场判断", "", report.market.explanation, ""])
    lines.append("| 指数 | 收盘 | MA | 状态 |")
    lines.append("|---|---:|---:|---|")
    for signal in report.market.signals:
        relation = {"above": "均线上方", "below": "均线下方", "equal": "持平"}[signal.relation]
        lines.append(
            f"| {signal.name}（{signal.code}） | {signal.close:.2f} | {signal.moving_average:.2f} | {relation} |"
        )
    lines.extend(["", "## 当前持仓", ""])
    if report.current_holdings:
        lines.extend(
            f"- {holding.code} {holding.name}，数量 {holding.amount:g}，成本 {holding.avg_cost:g}"
            for holding in report.current_holdings
        )
    else:
        lines.append("未提供持仓；建议仅基于空仓状态生成。")
    lines.extend(["", "## 通过全部过滤的ETF", ""])
    if report.eligible:
        lines.append("| 排名 | ETF | 动量分 | 趋势年化 | R² | 量比 | 近3日均成交额 |")
        lines.append("|---:|---|---:|---:|---:|---:|---:|")
        for index, metric in enumerate(report.eligible, start=1):
            lines.append(
                f"| {index} | {metric.name}（{metric.code}） | {metric.momentum_score:.4f} | "
                f"{metric.annualized_return:.1%} | {metric.r_squared:.3f} | {metric.volume_ratio:.2f} | "
                f"{metric.average_turnover / 1e8:.2f}亿 |"
            )
    else:
        lines.append("无。")
    lines.extend(["", "## 动量排名前20", ""])
    lines.append("| 排名 | ETF | 动量分 | 全部通过 | 检查结果 |")
    lines.append("|---:|---|---:|---|---|")
    for index, metric in enumerate(report.rankings[:20], start=1):
        lines.append(
            f"| {index} | {metric.name}（{metric.code}） | {metric.momentum_score:.4f} | "
            f"{'是' if metric.passed_all else '否'} | {_metric_reasons(metric)} |"
        )
    lines.extend(
        [
            "",
            "## 人工执行检查",
            "",
            "- 核对ETF公告、停牌、涨跌停和申赎状态。",
            "- 跨境、商品和LOF产品需额外核对溢价率及相关市场休市情况。",
            "- 换仓时先确认卖出成交，再决定买入；记录人工拒绝信号的原因。",
            "- 数据异常、价格异常或溢价数据缺失时，可以选择不交易。",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(report: AdviceReport, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.as_of.isoformat()}-etf-advice"
    markdown_path = report_dir / f"{stem}.md"
    json_path = report_dir / f"{stem}.json"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return markdown_path, json_path
