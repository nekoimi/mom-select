from __future__ import annotations

import json
from dataclasses import asdict
from html import escape

from mom_select.core.reporting import ReportPaths, render_html_to_png
from mom_select.stock.models import StockAdviceReport


def render_markdown(report: StockAdviceReport) -> str:
    lines = [
        f"# 个股趋势排名日报 {report.as_of.isoformat()}",
        "",
        f"- 全市场股票：{report.universe_size}",
        f"- 必要条件预筛后：{report.prefiltered_size}",
        f"- 完成分析：{report.analyzed_count}",
        f"- 数据状态：{'可供人工复核' if report.actionable else '数据不足，不可执行'}",
        "",
        f"## 适合当前介入的趋势候选（最多{report.entry_candidate_limit}只，"
        f"本次{len(report.entry_candidates)}只）",
        "",
        "| # | 股票 | 入场分 | 距MA20 | 20日 | 60日超额 | R² | ATR | 距60日高点 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, item in enumerate(report.entry_candidates, 1):
        lines.append(
            f"| {index} | {item.name} `{item.code}` | {item.entry_score:.2f} | "
            f"{item.distance_ma20:.2%} | {item.return_20d:.2%} | "
            f"{item.relative_strength_60d:.2%} | {item.r_squared:.3f} | "
            f"{item.atr_ratio:.2%} | {item.drawdown_from_60d_high:.2%} |"
        )
    if not report.entry_candidates:
        lines.append("| - | 无标的通过当前介入条件 | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            f"## 高动量排名（最多{report.momentum_limit}只，"
            f"本次{len(report.rankings)}只）",
            "",
            "| # | 股票 | 动量分 | 20日 | 60日 | 120日 | R² | ATR | 最大回撤 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for index, item in enumerate(report.rankings, 1):
        lines.append(
            f"| {index} | {item.name} `{item.code}` | {item.score:.2f} | "
            f"{item.return_20d:.2%} | {item.return_60d:.2%} | {item.return_120d:.2%} | "
            f"{item.r_squared:.3f} | {item.atr_ratio:.2%} | {item.max_drawdown:.2%} |"
        )
    lines.extend(["", "## 筛选统计", ""])
    for reason, count in sorted(report.exclusion_counts.items()):
        lines.append(f"- {reason}：{count}")
    if report.warnings:
        lines.extend(["", "## 风险提示", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(["", "> 仅供策略研究与人工复核，不构成投资建议，不执行交易下单。"])
    return "\n".join(lines) + "\n"


def render_html(report: StockAdviceReport) -> str:
    weekdays = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
    report_date = (
        f"{report.as_of.year}年{report.as_of.month}月{report.as_of.day}日 "
        f"{weekdays[report.as_of.weekday()]}"
    )
    momentum_rows = "".join(
        f'<tr class="{"leader" if index <= 3 else ""}">'
        f'<td class="rank">{index}</td>'
        f"<td><strong>{escape(item.name)}</strong><small>{escape(item.code)}</small></td>"
        f'<td class="number">{item.close:.2f}</td><td class="number score">{item.score:.2f}</td>'
        f'<td class="number">{item.return_20d:.2%}</td>'
        f'<td class="number">{item.return_60d:.2%}</td>'
        f'<td class="number">{item.return_120d:.2%}</td>'
        f'<td class="number">{item.relative_strength_60d:.2%}</td>'
        f'<td class="number">{item.r_squared:.3f}</td>'
        f'<td class="number">{item.atr_ratio:.2%}</td>'
        f'<td class="number">{item.max_drawdown:.2%}</td>'
        f'<td class="align-status {"positive" if item.ma_aligned else "neutral"}">'
        f"{'多头' if item.ma_aligned else '未形成'}</td></tr>"
        for index, item in enumerate(report.rankings, 1)
    ) or '<tr><td colspan="12" class="empty">没有股票完成排名</td></tr>'
    entry_rows = "".join(
        f'<tr class="{"candidate-leader" if index <= 3 else ""}">'
        f'<td class="rank">{index}</td>'
        f"<td><strong>{escape(item.name)}</strong><small>{escape(item.code)}</small></td>"
        f'<td class="number">{item.close:.2f}</td>'
        f'<td class="number entry-score">{item.entry_score:.2f}</td>'
        f'<td class="number">{item.distance_ma20:.2%}</td>'
        f'<td class="number">{item.return_20d:.2%}</td>'
        f'<td class="number">{item.relative_strength_60d:.2%}</td>'
        f'<td class="number">{item.r_squared:.3f}</td>'
        f'<td class="number">{item.atr_ratio:.2%}</td>'
        f'<td class="number">{item.drawdown_from_60d_high:.2%}</td></tr>'
        for index, item in enumerate(report.entry_candidates, 1)
    ) or '<tr><td colspan="10" class="empty">没有股票通过当前介入条件</td></tr>'
    counts = "".join(
        f'<li><span>{escape(reason)}</span><strong>{count:,}</strong></li>'
        for reason, count in sorted(report.exclusion_counts.items())
    ) or "<li><span>没有排除项</span><strong>0</strong></li>"
    warnings = "".join(f"<li>{escape(item)}</li>" for item in report.warnings)
    status = "可供人工复核" if report.actionable else "数据不足，不可执行"
    status_class = "ready" if report.actionable else "blocked"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>个股趋势排名日报 {report.as_of.isoformat()}</title>
<style>
  :root {{ --ink:#20262b; --muted:#69757e; --line:#dce1e4; --paper:#fff; --canvas:#edf0f2; --charcoal:#20262b; --accent:#b44b3d; --accent-dark:#8f382f; --accent-soft:#f8ebe8; --blue:#3e657d; --blue-soft:#edf4f7; --amber:#a66a19; --amber-soft:#fff4df; }}
  * {{ box-sizing:border-box; }}
  html {{ background:var(--canvas); }}
  body {{ margin:0; color:var(--ink); background:var(--canvas); font-family:"Microsoft YaHei UI","Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif; font-size:14px; line-height:1.55; letter-spacing:0; }}
  .report {{ width:1180px; margin:0 auto; background:var(--paper); box-shadow:0 12px 40px rgba(32,38,43,.13); }}
  header {{ padding:38px 48px 32px; color:#fff; background:var(--charcoal); border-bottom:6px solid var(--accent); }}
  .brand-row {{ display:flex; align-items:center; justify-content:space-between; gap:24px; }}
  .brand {{ color:#efb9b1; font:700 13px/1 Arial,sans-serif; letter-spacing:2px; }}
  .date {{ color:#bfc7cc; font-size:13px; text-align:right; }}
  .date strong {{ display:block; margin-bottom:3px; color:#fff; font-size:15px; }}
  h1 {{ margin:17px 0 8px; font-size:34px; line-height:1.2; letter-spacing:0; }}
  header p {{ margin:0; color:#cbd1d5; }}
  .summary {{ display:grid; grid-template-columns:1.25fr 1fr 1fr 1fr; border-bottom:1px solid var(--line); background:#faf9f9; }}
  .summary-item {{ min-width:0; padding:22px 24px 20px; border-right:1px solid var(--line); }}
  .summary-item:last-child {{ border-right:0; }}
  .label {{ display:block; margin-bottom:6px; color:var(--muted); font-size:12px; font-weight:700; }}
  .value {{ display:block; overflow-wrap:anywhere; font-size:20px; font-weight:800; line-height:1.25; }}
  .value.ready {{ color:var(--accent-dark); }} .value.blocked {{ color:var(--amber); }}
  main {{ padding:32px 48px 40px; }} section {{ margin-bottom:34px; }}
  .section-head {{ display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:13px; border-bottom:2px solid var(--ink); }}
  .section-title {{ display:flex; align-items:center; gap:10px; padding-bottom:8px; }}
  .section-index {{ display:inline-flex; width:25px; height:25px; align-items:center; justify-content:center; color:#fff; background:var(--accent); font:700 11px/1 Arial,sans-serif; }}
  h2 {{ margin:0; font-size:19px; line-height:1.2; letter-spacing:0; }}
  .section-note {{ max-width:700px; padding-bottom:8px; color:var(--muted); font-size:11px; text-align:right; }}
  .table-wrap {{ width:100%; overflow:hidden; border:1px solid var(--line); }}
  table {{ width:100%; border-collapse:collapse; table-layout:fixed; font-size:12px; }}
  th {{ padding:9px 7px; color:#56636b; background:#f3f5f6; border-bottom:1px solid #cbd3d7; font-size:10px; text-align:right; white-space:nowrap; }}
  td {{ padding:9px 7px; border-bottom:1px solid #e6eaec; vertical-align:middle; }}
  th:first-child,td:first-child {{ padding-left:14px; }} th:last-child,td:last-child {{ padding-right:14px; }}
  th:nth-child(2),td:nth-child(2) {{ text-align:left; }} tbody tr:nth-child(even) {{ background:#fbfbfb; }}
  tbody tr.leader {{ background:var(--accent-soft); }} tbody tr.candidate-leader {{ background:var(--blue-soft); }} td small {{ display:block; margin-top:1px; color:var(--muted); font:10px/1.3 Consolas,monospace; }}
  .rank {{ color:var(--muted); font:700 12px/1 Consolas,monospace; text-align:center; }}
  .number {{ text-align:right; font-variant-numeric:tabular-nums; }} .score {{ color:var(--accent-dark); font-weight:800; }}
  .entry-score {{ color:var(--blue); font-weight:800; }}
  .align-status {{ text-align:center; font-size:10px; font-weight:700; }} .align-status.positive {{ color:var(--accent-dark); }} .align-status.neutral {{ color:var(--blue); }}
  .empty {{ padding:24px; color:var(--muted); text-align:center!important; }}
  .counts {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1px; margin:0; padding:1px; background:var(--line); list-style:none; }}
  .counts li {{ display:flex; min-width:0; justify-content:space-between; gap:10px; padding:12px 14px; background:#fff; }}
  .counts span {{ overflow-wrap:anywhere; color:var(--muted); font-size:12px; }} .counts strong {{ color:var(--blue); font-variant-numeric:tabular-nums; }}
  .checklist {{ display:grid; grid-template-columns:1fr 1fr; gap:10px 28px; margin:0; padding:0; list-style:none; }}
  .checklist li {{ position:relative; padding:9px 0 9px 27px; border-bottom:1px solid var(--line); }}
  .checklist li::before {{ content:""; position:absolute; left:0; top:13px; width:13px; height:13px; border:2px solid #8a969d; }}
  .notice {{ padding:18px 22px; border:1px solid #e4bdb7; background:#fff7f5; }}
  .notice > div {{ display:flex; align-items:center; gap:10px; }} .notice ul {{ margin:10px 0 0 37px; padding:0; color:#75372f; }}
  footer {{ display:flex; justify-content:space-between; gap:30px; padding:20px 48px; color:#78848b; background:#f2f4f5; border-top:1px solid var(--line); font-size:11px; }}
  @media(max-width:900px) {{ .report {{ width:100%; box-shadow:none; }} header,main {{ padding-left:24px; padding-right:24px; }} .summary,.counts {{ grid-template-columns:1fr 1fr; }} .section-head {{ align-items:flex-start; flex-direction:column; gap:5px; }} .section-note {{ text-align:left; }} .table-wrap {{ overflow-x:auto; }} table {{ min-width:1060px; }} }}
  @media print {{ html,body {{ background:#fff; }} .report {{ box-shadow:none; }} }}
</style>
</head>
<body>
<article class="report">
  <header>
    <div class="brand-row"><span class="brand">MOM SELECT · STOCK TREND</span><span class="date"><strong>{report_date}</strong>收盘趋势排名 · 数据日期 {report.as_of.isoformat()}</span></div>
    <h1>个股趋势策略简报</h1>
    <p>全市场动态筛选与20/60/120日趋势排名 · 供下一交易日人工复核</p>
  </header>
  <div class="summary">
    <div class="summary-item"><span class="label">数据状态</span><span class="value {status_class}">{status}</span></div>
    <div class="summary-item"><span class="label">全市场股票</span><span class="value">{report.universe_size:,}</span></div>
    <div class="summary-item"><span class="label">必要预筛后</span><span class="value">{report.prefiltered_size:,}</span></div>
    <div class="summary-item"><span class="label">当前介入候选</span><span class="value">{len(report.entry_candidates)}</span></div>
  </div>
  <main>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">01</span><h2>适合当前介入的趋势候选</h2></div><span class="section-note">最多{report.entry_candidate_limit}只 · 本次{len(report.entry_candidates)}只 · 均线多头、相对强弱为正，并限制短期涨幅、距MA20、ATR及距60日高点回撤</span></div>
      <div class="table-wrap"><table><colgroup><col style="width:5%"><col style="width:19%"><col style="width:9%"><col style="width:10%"><col style="width:10%"><col style="width:10%"><col style="width:11%"><col style="width:8%"><col style="width:8%"><col style="width:10%"></colgroup><thead><tr><th>#</th><th>股票</th><th>现价</th><th>入场分</th><th>距MA20</th><th>20日</th><th>60日超额</th><th>R²</th><th>ATR</th><th>距60日高点</th></tr></thead><tbody>{entry_rows}</tbody></table></div>
    </section>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">02</span><h2>高动量排名</h2></div><span class="section-note">最多{report.momentum_limit}只 · 本次{len(report.rankings)}只 · 仅收录达到动量分门槛的标的，不等同于当前介入优先级</span></div>
      <div class="table-wrap"><table><colgroup><col style="width:4%"><col style="width:14%"><col style="width:7%"><col style="width:7%"><col style="width:7%"><col style="width:7%"><col style="width:7%"><col style="width:9%"><col style="width:6%"><col style="width:7%"><col style="width:9%"><col style="width:10%"></colgroup><thead><tr><th>#</th><th>股票</th><th>现价</th><th>动量分</th><th>20日</th><th>60日</th><th>120日</th><th>60日超额</th><th>R²</th><th>ATR</th><th>最大回撤</th><th>均线状态</th></tr></thead><tbody>{momentum_rows}</tbody></table></div>
    </section>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">03</span><h2>筛选统计</h2></div><span class="section-note">全市场预筛、历史数据完整性与流动性检查的排除数量</span></div>
      <ul class="counts">{counts}</ul>
    </section>
    <section>
      <div class="section-head"><div class="section-title"><span class="section-index">04</span><h2>人工复核检查</h2></div></div>
      <ul class="checklist"><li>核对公告、停复牌、涨跌停和风险警示状态</li><li>确认股价仍低于100元且成交额满足策略门槛</li><li>关注短期涨幅过大、ATR偏高及深度回撤标的</li><li>结合所属行业与大盘环境判断趋势持续性</li><li>数据日期或价格异常时不采用本次排名</li><li>本报告仅提供关注排名，不生成买卖委托</li></ul>
    </section>
    {f'<section class="notice"><div><span class="section-index">!</span><h2>风险提示</h2></div><ul>{warnings}</ul></section>' if warnings else ''}
  </main>
  <footer><span>仅供策略研究与人工复核，不构成投资建议；系统不连接账户，也不会自动下单</span><span>生成时间 {escape(report.generated_at)}</span></footer>
</article>
</body>
</html>"""


def write_reports(report: StockAdviceReport, report_dir) -> ReportPaths:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.as_of.isoformat()}-stock-ranking-close"
    markdown_path = report_dir / f"{stem}.md"
    json_path = report_dir / f"{stem}.json"
    html_path = report_dir / f"{stem}.html"
    image_path = report_dir / f"{stem}.png"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    html_path.write_text(render_html(report), encoding="utf-8")
    render_html_to_png(html_path, image_path)
    return ReportPaths(markdown_path, json_path, html_path, image_path)
