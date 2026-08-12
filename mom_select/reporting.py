from __future__ import annotations

import json
import shutil
from html import escape
from dataclasses import asdict
from pathlib import Path
from typing import NamedTuple

from mom_select.models import AdviceReport, EtfMetrics


class ReportPaths(NamedTuple):
    markdown: Path
    json: Path
    html: Path
    image: Path


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
    mode_name = (
        "13:05历史模拟"
        if report.historical_simulation
        else "13:05盘中信号"
        if report.run_mode == "intraday"
        else "收盘复盘"
    )
    if report.debug:
        mode_name += "（DEBUG）"
    if report.historical_simulation:
        data_note = (
            f"本报告重建{report.as_of.isoformat()} 13:05历史信号，"
            "分钟行情缺失时可能使用日K降级。"
        )
    elif report.run_mode == "intraday":
        data_note = (
            f"本报告使用13:05实时快照（{report.signal_time or '时间未知'}）生成，"
            "供当日盘中人工复核。"
        )
    else:
        data_note = "本报告由收盘日线生成，仅供下一交易日人工复核。"
    lines = [
        f"# ETF轮动建议｜{report.as_of.isoformat()}｜{mode_name}",
        "",
        f"> {data_note}不构成投资建议，也不会自动下单。",
        "",
        "## 结论",
        "",
        f"- 是否可执行：{'否（DEBUG）' if report.debug else ('是' if report.actionable else '否')}",
        f"- 市场状态：{regime}",
        f"- 操作建议：{report.action}",
        f"- 策略目标：{', '.join(report.targets) if report.targets else '无'}",
        f"- 说明：{report.explanation}",
        f"- 数据覆盖：{report.analyzed_count}/{report.pool_size}",
        f"- ETF池：发现 {report.discovered_pool_size} 只，固定 {report.fixed_pool_size} 只，动态 {report.dynamic_pool_size} 只",
        f"- 信号时间：{report.signal_time or '收盘日线'}",
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


def _html_status(value: bool, true_text: str = "通过", false_text: str = "未通过") -> str:
    css_class = "positive" if value else "negative"
    text = true_text if value else false_text
    return f'<span class="status {css_class}">{escape(text)}</span>'


def _html_metric_checks(metric: EtfMetrics) -> str:
    checks = (
        ("动量", metric.passed_momentum),
        ("状态", metric.passed_regime_filter),
        ("量能", metric.passed_volume),
        ("跌幅", metric.passed_loss),
        ("流动性", metric.passed_liquidity),
    )
    return "".join(
        f'<span class="check {"pass" if passed else "fail"}">{escape(name)}</span>'
        for name, passed in checks
    )


def render_html(report: AdviceReport) -> str:
    regime_name = "走弱期" if report.market.regime == "weak" else "正常期"
    regime_class = "weak" if report.market.regime == "weak" else "normal"
    target = ", ".join(report.targets) if report.targets else "无"
    target_names = {
        item.code: item.name
        for item in [*report.rankings, *report.eligible, *report.candidates]
    }
    target_names.update(
        {item.code: item.name for item in report.current_holdings if item.name}
    )
    target_summary = (
        "<br>".join(
            f'<span class="target-name">{escape(target_names[code])}</span>'
            f'<small class="target-code">{escape(code)}</small>'
            if target_names.get(code)
            else f'<span class="target-code-only">{escape(code)}</span>'
            for code in report.targets
        )
        if report.targets
        else '<span class="target-name">无</span>'
    )
    is_intraday = report.run_mode == "intraday"
    mode_name = (
        "13:05历史模拟"
        if report.historical_simulation
        else "13:05盘中信号"
        if is_intraday
        else "收盘复盘"
    )
    if report.debug:
        mode_name += " · DEBUG"
    data_time = report.signal_time or f"{report.as_of.isoformat()} 收盘"
    subtitle = (
        f"重建{report.as_of.isoformat()} 13:05历史信号 · 仅用于策略对照"
        if report.historical_simulation
        else "13:05实时快照与25日趋势排名 · 当日盘中人工复核"
        if is_intraday
        else "市场状态与25日趋势排名 · 下一交易日人工复核"
    )
    volume_label = (
        "预计全天量比"
        if report.historical_simulation
        else "预计全天量比"
        if is_intraday
        else "量比"
    )
    warning_items = "".join(f"<li>{escape(item)}</li>" for item in report.warnings)
    market_rows = "".join(
        f"""
        <tr>
          <td><strong>{escape(signal.name)}</strong><small>{escape(signal.code)}</small></td>
          <td>{signal.close:.2f}</td>
          <td>{signal.moving_average:.2f}</td>
          <td>{_html_status(signal.relation == 'above', '均线上方', '均线下方' if signal.relation == 'below' else '持平')}</td>
        </tr>"""
        for signal in report.market.signals
    )
    eligible_rows = "".join(
        f"""
        <tr class="{'leader' if index == 1 else ''}">
          <td class="rank">{index}</td>
          <td><strong>{escape(item.name)}</strong><small>{escape(item.code)}</small></td>
          <td class="number emph">{item.momentum_score:.4f}</td>
          <td class="number">{item.annualized_return:.1%}</td>
          <td class="number">{item.r_squared:.3f}</td>
          <td class="number">{item.volume_ratio:.2f}</td>
          <td class="number">{item.average_turnover / 1e8:.2f}亿</td>
        </tr>"""
        for index, item in enumerate(report.eligible, start=1)
    ) or '<tr><td colspan="7" class="empty">无ETF通过全部过滤</td></tr>'
    ranking_rows = "".join(
        f"""
        <tr>
          <td class="rank">{index}</td>
          <td><strong>{escape(item.name)}</strong><small>{escape(item.code)}</small></td>
          <td class="number emph">{item.momentum_score:.4f}</td>
          <td>{_html_status(item.passed_all)}</td>
          <td class="checks">{_html_metric_checks(item)}</td>
        </tr>"""
        for index, item in enumerate(report.rankings[:20], start=1)
    )
    holdings = "".join(
        f"<li><strong>{escape(item.name or item.code)}</strong><span>{escape(item.code)} · {item.amount:g}份 · 成本 {item.avg_cost:g}</span></li>"
        for item in report.current_holdings
    ) or "<li><strong>未提供持仓</strong><span>当前建议按空仓状态生成</span></li>"
    warning_section = (
        f'<section class="notice"><div><span class="section-index">!</span><h2>风险提示</h2></div><ul>{warning_items}</ul></section>'
        if report.warnings
        else ""
    )
    pool_note = (
        f"发现{report.discovered_pool_size} · 固定{report.fixed_pool_size} · 动态{report.dynamic_pool_size}"
        if report.discovered_pool_size
        else "固定池"
    )
    actionable_text = (
        "调试结果" if report.debug else "可执行" if report.actionable else "不可执行"
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ETF轮动建议 | {report.as_of.isoformat()} | {mode_name}</title>
<style>
  :root {{ --ink:#172126; --muted:#66747b; --line:#d9e0e2; --paper:#ffffff; --canvas:#edf1f2; --green:#167c5a; --green-soft:#e8f4ef; --red:#b63e37; --red-soft:#f9ecea; --amber:#a56212; --amber-soft:#fff4df; --blue:#24618a; }}
  * {{ box-sizing:border-box; }}
  html {{ background:var(--canvas); }}
  body {{ margin:0; color:var(--ink); background:var(--canvas); font-family:"Microsoft YaHei UI","Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif; font-size:14px; line-height:1.55; letter-spacing:0; }}
  .report {{ width:1180px; margin:0 auto; background:var(--paper); box-shadow:0 12px 40px rgba(23,33,38,.12); }}
  header {{ padding:38px 48px 32px; color:#fff; background:#172126; border-bottom:6px solid var(--green); }}
  .brand-row {{ display:flex; align-items:center; justify-content:space-between; gap:24px; }}
  .brand {{ font:700 13px/1 Arial,sans-serif; letter-spacing:2px; color:#9fd4c1; }}
  .date {{ color:#b9c3c7; font-size:13px; }}
  h1 {{ margin:17px 0 8px; font-size:34px; line-height:1.2; letter-spacing:0; }}
  header p {{ margin:0; color:#c9d1d4; font-size:14px; }}
    .summary {{ display:grid; grid-template-columns:1.25fr 1.1fr .8fr .8fr; border-bottom:1px solid var(--line); background:#f8faf9; }}
  .summary-item {{ min-width:0; padding:22px 24px 20px; border-right:1px solid var(--line); }}
  .summary-item:last-child {{ border-right:0; }}
  .label {{ display:block; margin-bottom:6px; color:var(--muted); font-size:12px; font-weight:700; }}
  .value {{ display:block; overflow-wrap:anywhere; font-size:20px; font-weight:800; line-height:1.25; }}
  .value.code {{ color:var(--green); font-size:17px; }}
  .target-name {{ display:block; color:var(--ink); font-weight:800; }}
  .target-code {{ display:block; margin-top:3px; color:var(--green); font:700 13px/1.25 Consolas,monospace; }}
  .target-code-only {{ display:block; color:var(--green); font:700 16px/1.25 Consolas,monospace; }}
  main {{ padding:30px 48px 42px; }}
  section {{ margin-top:32px; }}
  section:first-child {{ margin-top:0; }}
  .section-head {{ display:flex; align-items:flex-end; justify-content:space-between; gap:24px; margin-bottom:12px; border-bottom:2px solid var(--ink); padding-bottom:9px; }}
  .section-title {{ display:flex; align-items:center; gap:10px; }}
  .section-index {{ display:inline-grid; place-items:center; width:27px; height:27px; color:#fff; background:var(--ink); font:700 13px/1 Arial,sans-serif; }}
  h2 {{ margin:0; font-size:19px; line-height:1.25; }}
  .section-note {{ max-width:600px; color:var(--muted); font-size:12px; text-align:right; }}
  .market-grid {{ display:grid; grid-template-columns:1.05fr 1.95fr; gap:28px; align-items:stretch; }}
  .market-grid > table {{ height:100%; }}
  .regime-panel {{ height:100%; padding:24px; border-left:5px solid var(--green); background:var(--green-soft); }}
  .regime-panel.weak {{ border-color:var(--red); background:var(--red-soft); }}
  .regime-name {{ margin:4px 0 7px; font-size:28px; font-weight:800; }}
  .regime-panel p {{ margin:0; color:#405057; }}
  table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
  th {{ padding:9px 10px; color:#526168; background:#f2f5f5; border-bottom:1px solid #cad3d6; font-size:11px; text-align:left; white-space:nowrap; }}
  td {{ padding:10px; border-bottom:1px solid #e5eaec; vertical-align:middle; }}
  tbody tr:nth-child(even) {{ background:#fafbfb; }}
  tbody tr.leader {{ background:var(--green-soft); }}
  tbody tr.leader.secondary {{ background:#eef4f8; }}
  td small {{ display:block; margin-top:1px; color:var(--muted); font:11px/1.35 Consolas,monospace; }}
  .number {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .emph {{ font-weight:800; }}
  .rank {{ width:42px; color:var(--muted); font:700 13px/1 Consolas,monospace; text-align:center; }}
  .status {{ display:inline-block; min-width:58px; padding:2px 7px; font-size:11px; font-weight:700; text-align:center; border:1px solid currentColor; }}
  .positive {{ color:var(--green); background:var(--green-soft); }}
  .negative {{ color:var(--red); background:var(--red-soft); }}
  .checks {{ white-space:nowrap; }}
  .ranking-table th:first-child,.ranking-table td:first-child {{ padding-left:0; text-align:left; }}
  .ranking-table th:nth-child(3),.ranking-table td:nth-child(3),.ranking-table th:nth-child(4),.ranking-table td:nth-child(4) {{ text-align:center; }}
  .ranking-table th:last-child {{ padding-right:0; text-align:right; }}
  .ranking-table td:last-child {{ padding-right:0; }}
  .ranking-table .checks {{ display:flex; justify-content:flex-end; align-items:center; gap:5px; }}
  .ranking-table .check {{ margin:2px 0; }}
  .check {{ display:inline-block; margin:2px 4px 2px 0; padding:2px 5px; font-size:10px; border:1px solid; }}
  .check.pass {{ color:var(--green); border-color:#9dc9b9; background:#f3faf7; }}
  .check.fail {{ color:var(--red); border-color:#ddb0ac; background:#fff7f6; }}
  .notice {{ padding:18px 22px; border:1px solid #edcc96; background:var(--amber-soft); }}
  .notice > div {{ display:flex; align-items:center; gap:10px; }}
  .notice .section-index {{ background:var(--amber); }}
  .notice ul {{ margin:10px 0 0 37px; padding:0; color:#70460e; }}
  .holdings {{ display:grid; grid-template-columns:1fr 1.4fr; gap:28px; align-items:stretch; }}
  .holding-list {{ display:grid; grid-auto-rows:1fr; min-height:100px; margin:0; padding:0; list-style:none; }}
  .holding-list li {{ display:flex; flex-direction:column; justify-content:center; height:100%; padding:15px 18px; border-left:4px solid #aab6ba; background:#f4f6f6; }}
  .holding-list span {{ display:block; color:var(--muted); font-size:12px; }}
  .decision {{ display:flex; min-height:100px; height:100%; flex-direction:column; justify-content:center; padding:15px 18px; border:1px solid var(--line); }}
  .decision strong {{ color:var(--green); font-size:17px; }}
  .decision p {{ margin:5px 0 0; color:var(--muted); }}
  .empty {{ padding:24px; color:var(--muted); text-align:center; }}
  .checklist {{ display:grid; grid-template-columns:1fr 1fr; gap:10px 28px; margin:0; padding:0; list-style:none; }}
  .checklist li {{ position:relative; padding:9px 0 9px 27px; border-bottom:1px solid var(--line); }}
  .checklist li::before {{ content:""; position:absolute; left:0; top:13px; width:13px; height:13px; border:2px solid #819097; }}
  footer {{ display:flex; justify-content:space-between; gap:30px; padding:20px 48px; color:#77858b; background:#f2f5f5; border-top:1px solid var(--line); font-size:11px; }}
  @media (max-width:900px) {{ .report {{ width:100%; box-shadow:none; }} header,main {{ padding-left:24px; padding-right:24px; }} .summary {{ grid-template-columns:1fr 1fr; }} .summary-item {{ border-bottom:1px solid var(--line); }} .market-grid,.holdings {{ grid-template-columns:1fr; }} .section-head {{ align-items:flex-start; flex-direction:column; gap:5px; }} .section-note {{ text-align:left; }} }}
  @media print {{ html,body {{ background:#fff; }} .report {{ box-shadow:none; }} }}
</style>
</head>
<body>
<article class="report">
  <header>
    <div class="brand-row"><span class="brand">MOM SELECT · ETF ROTATION</span><span class="date">{mode_name} · 数据时间 {escape(data_time)}</span></div>
    <h1>ETF轮动策略简报</h1>
    <p>{subtitle}</p>
  </header>
  <div class="summary">
    <div class="summary-item"><span class="label">操作建议</span><span class="value">{escape(report.action)}</span></div>
    <div class="summary-item"><span class="label">25日策略目标</span><span class="value code">{target_summary}</span></div>
    <div class="summary-item"><span class="label">市场状态</span><span class="value">{regime_name}</span></div>
    <div class="summary-item"><span class="label">数据状态</span><span class="value">{actionable_text}<br><small>{report.analyzed_count}/{report.pool_size}</small></span></div>
  </div>
  <main>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">01</span><h2>市场状态</h2></div><span class="section-note">{pool_note} · 至少3/4指数位于MA10下方进入走弱期，至少3/4位于上方恢复正常期</span></div>
      <div class="market-grid">
        <div class="regime-panel {regime_class}"><span class="label">当前状态</span><div class="regime-name">{regime_name}</div><p>{escape(report.market.explanation)}</p></div>
        <table><thead><tr><th>指数</th><th>收盘</th><th>MA10</th><th>位置</th></tr></thead><tbody>{market_rows}</tbody></table>
      </div>
    </section>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">02</span><h2>组合结论</h2></div><span class="section-note">目标由25日加权趋势、R²与风险过滤共同产生</span></div>
      <div class="holdings"><ul class="holding-list">{holdings}</ul><div class="decision"><span class="label">策略说明</span><strong>{escape(report.action)} · {escape(target)}</strong><p>{escape(report.explanation)}</p></div></div>
    </section>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">03</span><h2>25日趋势排名</h2></div><span class="section-note">已通过动量、市场状态、量能、短期跌幅与流动性过滤</span></div>
      <table><thead><tr><th style="width:5%">#</th><th style="width:29%">ETF</th><th style="width:13%;text-align:right">动量分</th><th style="width:13%;text-align:right">趋势年化</th><th style="width:11%;text-align:right">R²</th><th style="width:11%;text-align:right">{volume_label}</th><th style="width:18%;text-align:right">3日均成交额</th></tr></thead><tbody>{eligible_rows}</tbody></table>
    </section>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">04</span><h2>全池动量前20</h2></div><span class="section-note">展示高动量但可能因过热、量能或短期下跌而未通过的标的</span></div>
      <table class="ranking-table"><thead><tr><th style="width:5%">#</th><th style="width:34%">ETF</th><th style="width:14%">动量分</th><th style="width:12%">结果</th><th style="width:35%">过滤检查</th></tr></thead><tbody>{ranking_rows}</tbody></table>
    </section>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">05</span><h2>人工执行检查</h2></div></div>
      <ul class="checklist"><li>核对ETF公告、停牌、涨跌停和申赎状态</li><li>跨境、商品与LOF核对溢价率和相关市场休市</li><li>换仓时先确认卖出成交，再考虑买入</li><li>记录实际成交价及人工拒绝信号的原因</li><li>价格或数据异常时不交易</li><li>{'13:05信号仅供当日盘中人工复核' if is_intraday else '本报告不连接账户，也不会自动下单'}</li></ul>
    </section>
    {warning_section}
  </main>
  <footer><span>仅供策略研究与人工复核，不构成投资建议</span><span>生成时间 {escape(report.generated_at)}</span></footer>
</article>
</body>
</html>"""


def _find_browser() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    for command in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))
    return next((path for path in candidates if path.exists()), None)


def render_html_to_png(html_path: Path, image_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("缺少Playwright，运行 uv sync 后重试") from exc
    browser_path = _find_browser()
    if browser_path is None:
        raise RuntimeError("未找到Chrome或Edge，无法生成PNG报告")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--font-render-hinting=none"],
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1.5)
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        # Capture the report element itself rather than the wider browser
        # viewport, otherwise the canvas background becomes visible as side
        # margins in the exported image.
        page.locator(".report").screenshot(path=str(image_path))
        browser.close()


def write_reports(report: AdviceReport, report_dir: Path) -> ReportPaths:
    report_dir.mkdir(parents=True, exist_ok=True)
    suffix = "-intraday" if report.run_mode == "intraday" else ""
    if report.debug:
        suffix += "-debug"
    stem = f"{report.as_of.isoformat()}-etf-advice{suffix}"
    markdown_path = report_dir / f"{stem}.md"
    json_path = report_dir / f"{stem}.json"
    html_path = report_dir / f"{stem}.html"
    image_path = report_dir / f"{stem}.png"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    html_path.write_text(render_html(report), encoding="utf-8")
    render_html_to_png(html_path, image_path)
    return ReportPaths(markdown_path, json_path, html_path, image_path)
