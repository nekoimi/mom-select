from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mom_select.etf.backtest import BacktestResult


def _chart_payload(result: BacktestResult) -> str:
    peak = 1.0
    rows = []
    for record in result.records:
        net_value = 1 + record.cumulative_return
        peak = max(peak, net_value)
        rows.append(
            {
                "date": record.date.isoformat(),
                "held": " / ".join(record.held_names or record.held_codes) or "现金",
                "heldCodes": " / ".join(record.held_codes) or "现金",
                "daily": record.daily_return,
                "strategy": record.cumulative_return,
                "benchmark": record.benchmark_cumulative_return,
                "drawdown": net_value / peak - 1,
                "regime": record.regime,
                "action": record.action,
                "next": " / ".join(record.next_targets),
                "portfolio": record.portfolio_value,
                "cash": record.cash,
                "shares": record.position_shares,
                "cost": record.trading_cost,
                "cashDefense": record.cash_defense,
                "unfilled": record.unfilled_target or "",
            }
        )
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def render_backtest_html(result: BacktestResult) -> str:
    payload = _chart_payload(result)
    excess_return = result.final_return - result.benchmark_final_return
    fixed_fees = sum(trade.fixed_fee for trade in result.trades)
    slippage_cost = sum(trade.slippage_cost for trade in result.trades)
    impact_cost = sum(trade.impact_cost for trade in result.trades)
    premium_cost = sum(trade.premium_cost for trade in result.trades)
    buy_count = sum(trade.side == "buy" for trade in result.trades)
    sell_count = sum(trade.side == "sell" for trade in result.trades)

    def cost_share(value: float) -> str:
        return f"{value / result.total_trade_cost:.1%}" if result.total_trade_cost else "0.0%"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ETF 回测报告 {result.actual_start} - {result.actual_end}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #eef1f4; color: #18212b; font-family: Inter, "Microsoft YaHei", sans-serif; }}
  .report {{ width: min(1180px, 100%); margin: 0 auto; background: #fff; min-height: 100vh; padding: 32px 38px 26px; }}
  header {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; border-bottom: 2px solid #18212b; padding-bottom: 18px; }}
  h1 {{ margin: 0 0 7px; font-size: 28px; line-height: 1.2; letter-spacing: 0; }}
  .period, .meta {{ color: #647180; font-size: 13px; }}
  .meta {{ text-align: right; line-height: 1.7; }}
  .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 22px 0 26px; }}
  .metric {{ border: 1px solid #dbe1e7; border-radius: 6px; padding: 13px 14px; min-width: 0; }}
  .metric span {{ display: block; color: #697684; font-size: 12px; margin-bottom: 7px; }}
  .metric strong {{ display: block; font-size: 20px; line-height: 1.15; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .positive {{ color: #c43b3b; }} .negative {{ color: #087f5b; }}
  .chart-heading {{ display: flex; justify-content: space-between; align-items: baseline; gap: 16px; margin-bottom: 8px; }}
  h2 {{ margin: 0; font-size: 17px; letter-spacing: 0; }}
  .legend {{ display: flex; gap: 16px; color: #5f6d7a; font-size: 12px; flex-wrap: wrap; }}
  .legend span::before {{ content: ""; display: inline-block; width: 16px; height: 3px; margin: 0 6px 3px 0; background: var(--color); }}
  .chart-wrap {{ position: relative; width: 100%; height: 720px; border-top: 1px solid #e4e8ec; border-bottom: 1px solid #e4e8ec; }}
  canvas {{ width: 100%; height: 720px; display: block; }}
  .tooltip {{ position: absolute; display: none; pointer-events: none; z-index: 2; width: 238px; background: rgba(20, 28, 36, .96); color: #fff; border-radius: 5px; padding: 10px 12px; font-size: 12px; line-height: 1.65; box-shadow: 0 5px 18px rgba(0,0,0,.2); }}
  .tooltip b {{ display: block; font-size: 13px; margin-bottom: 2px; }}
  .tooltip .muted {{ color: #bac4ce; }}
  .cost-section {{ margin-top: 26px; }}
  .cost-heading {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 10px; }}
  .cost-heading span {{ color: #697684; font-size: 12px; }}
  .cost-table {{ width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }}
  .cost-table th, .cost-table td {{ padding: 10px 12px; border-bottom: 1px solid #e2e7eb; text-align: left; }}
  .cost-table th {{ color: #667482; background: #f5f7f8; font-weight: 500; }}
  .cost-table .number {{ text-align: right; white-space: nowrap; }}
  .cost-table tfoot td {{ border-top: 2px solid #26323d; border-bottom: 0; font-weight: 700; }}
  .cost-note {{ margin: 9px 0 0; color: #697684; font-size: 12px; line-height: 1.6; }}
  footer {{ display: flex; justify-content: space-between; gap: 20px; margin-top: 18px; color: #697684; font-size: 12px; }}
  @media (max-width: 850px) {{
    .report {{ padding: 22px 16px; }} header {{ align-items: flex-start; flex-direction: column; }} .meta {{ text-align: left; }}
    .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .metric strong {{ font-size: 18px; }}
    .chart-heading, .cost-heading {{ align-items: flex-start; flex-direction: column; }}
    .cost-table {{ font-size: 11px; }} .cost-table th, .cost-table td {{ padding: 8px 5px; }} footer {{ flex-direction: column; }}
  }}
</style>
</head>
<body>
<main class="report">
  <header>
    <div><h1>ETF 日频轮动回测</h1><div class="period">{result.actual_start} 至 {result.actual_end}</div></div>
    <div class="meta">前收盘生成信号，下一交易日开盘成交<br>固定 ETF 池 · 整手全仓 · 期末持仓按收盘价估值</div>
  </header>
  <section class="metrics" aria-label="回测指标">
    <div class="metric"><span>总收益</span><strong class="{'positive' if result.final_return >= 0 else 'negative'}">{result.final_return:.2%}</strong></div>
    <div class="metric"><span>年化收益</span><strong class="{'positive' if result.annualized_return >= 0 else 'negative'}">{result.annualized_return:.2%}</strong></div>
    <div class="metric"><span>最大回撤</span><strong class="negative">{result.max_drawdown:.2%}</strong></div>
    <div class="metric"><span>沪深300</span><strong class="{'positive' if result.benchmark_final_return >= 0 else 'negative'}">{result.benchmark_final_return:.2%}</strong></div>
    <div class="metric"><span>超额收益</span><strong class="{'positive' if excess_return >= 0 else 'negative'}">{excess_return:.2%}</strong></div>
    <div class="metric"><span>最终资产</span><strong>¥{result.final_portfolio_value:,.2f}</strong></div>
    <div class="metric"><span>交易成本 / 操作</span><strong>¥{result.total_trade_cost:,.2f} / {result.operation_count}</strong></div>
    <div class="metric"><span>胜率 / 换仓 / 现金</span><strong>{result.win_rate:.1%} / {result.switch_count} / {result.cash_defense_days}日</strong></div>
  </section>
  <section>
    <div class="chart-heading">
      <h2>累计收益、资产、回撤与日收益</h2>
      <div class="legend"><span style="--color:#d45555">策略收益</span><span style="--color:#7d8792">沪深300收益</span><span style="--color:#176b87">换仓点</span><span style="--color:#8f6b32">现金防御</span><span style="--color:#f2d49b">走弱期</span></div>
    </div>
    <div class="chart-wrap"><canvas id="chart"></canvas><div class="tooltip" id="tooltip"></div></div>
  </section>
  <section class="cost-section">
    <div class="cost-heading"><h2>交易费用明细</h2><span>买入 {buy_count} 笔 · 卖出 {sell_count} 笔 · 共 {result.operation_count} 笔操作</span></div>
    <table class="cost-table">
      <thead><tr><th>成本项目</th><th>计算假设</th><th class="number">累计金额</th><th class="number">占总成本</th></tr></thead>
      <tbody>
        <tr><td>固定手续费</td><td>每笔 ¥{result.execution.fixed_fee:.2f}</td><td class="number">¥{fixed_fees:,.2f}</td><td class="number">{cost_share(fixed_fees)}</td></tr>
        <tr><td>滑点成本</td><td>单边 {result.execution.slippage_rate * 10_000:.1f} bp</td><td class="number">¥{slippage_cost:,.2f}</td><td class="number">{cost_share(slippage_cost)}</td></tr>
        <tr><td>冲击成本</td><td>单边 {result.execution.impact_rate * 10_000:.1f} bp</td><td class="number">¥{impact_cost:,.2f}</td><td class="number">{cost_share(impact_cost)}</td></tr>
        <tr><td>ETF 溢价折价成本</td><td>单边 {result.execution.premium_rate * 10_000:.1f} bp</td><td class="number">¥{premium_cost:,.2f}</td><td class="number">{cost_share(premium_cost)}</td></tr>
      </tbody>
      <tfoot><tr><td>总交易成本</td><td>逐笔买卖累计</td><td class="number">¥{result.total_trade_cost:,.2f}</td><td class="number">100.0%</td></tr></tfoot>
    </table>
    <p class="cost-note">固定手续费只包含每笔 5 元的显式费用；其余三项是按成交金额估算的执行成本。总交易成本是资金反复周转产生的逐笔累计值，不代表在期末再次一次性扣除。</p>
  </section>
  <footer><span>起步资金 ¥{result.initial_capital:,.2f} · 固定费 ¥{result.execution.fixed_fee:.2f}/笔 · 滑点 {result.execution.slippage_rate * 10_000:.1f}bp · 冲击 {result.execution.impact_rate * 10_000:.1f}bp · 溢价折价 {result.execution.premium_rate * 10_000:.1f}bp</span><span>仅供策略研究，不构成投资建议</span></footer>
</main>
<script>
const rows={payload};
const initialCapital={result.initial_capital};
const canvas=document.getElementById('chart'), tooltip=document.getElementById('tooltip');
const ctx=canvas.getContext('2d'); let hoverIndex=null;
const C={{text:'#344250',muted:'#7b8793',grid:'#e7ebef',strategy:'#d45555',benchmark:'#7d8792',switch:'#176b87',cash:'#8f6b32',weak:'#f2d49b',up:'#d45555',down:'#24906c'}};
const layout={{left:66,right:76,mainTop:42,mainH:300,ddTop:402,ddH:115,retTop:575,retH:105,bottom:30}};
function money(v){{return '¥'+Math.round(v).toLocaleString('zh-CN')}} function pct(v){{return (v*100).toFixed(2)+'%'}}
function size(){{const dpr=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;canvas.width=w*dpr;canvas.height=h*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);return{{w,h}}}}
function xAt(i,w){{return layout.left+(rows.length<2?0:i/(rows.length-1))*(w-layout.left-layout.right)}}
function yAt(v,min,max,top,h){{return top+h-(v-min)/(max-min||1)*h}}
function axis(w,top,h,min,max,format,title){{ctx.font='11px Inter, Microsoft YaHei';ctx.fillStyle=C.muted;ctx.textAlign='right';ctx.textBaseline='middle';for(let i=0;i<=4;i++){{const v=min+(max-min)*i/4,y=yAt(v,min,max,top,h);ctx.strokeStyle=C.grid;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(layout.left,y);ctx.lineTo(w-layout.right,y);ctx.stroke();ctx.fillText(format(v),layout.left-8,y)}}ctx.save();ctx.translate(14,top+h/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.fillText(title,0,0);ctx.restore()}}
function rightAxis(w,top,h,min,max){{ctx.font='11px Inter, Microsoft YaHei';ctx.fillStyle=C.muted;ctx.textAlign='left';ctx.textBaseline='middle';for(let i=0;i<=4;i++){{const v=min+(max-min)*i/4,y=yAt(v,min,max,top,h);ctx.fillText(money(initialCapital*(1+v)),w-layout.right+8,y)}}ctx.save();ctx.translate(w-10,top+h/2);ctx.rotate(Math.PI/2);ctx.textAlign='center';ctx.fillText('资产金额',0,0);ctx.restore()}}
function line(values,color,min,max,top,h,w,width=2){{ctx.beginPath();values.forEach((v,i)=>{{const x=xAt(i,w),y=yAt(v,min,max,top,h);if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y)}});ctx.strokeStyle=color;ctx.lineWidth=width;ctx.lineJoin='round';ctx.stroke()}}
function draw(){{const{{w,h}}=size(), plotW=w-layout.left-layout.right;if(!rows.length)return;ctx.clearRect(0,0,w,h);const values=rows.flatMap(r=>[r.strategy,r.benchmark]),rawMin=Math.min(0,...values),rawMax=Math.max(0,...values),pad=Math.max((rawMax-rawMin)*.1,.03),vMin=rawMin-pad,vMax=rawMax+pad;
  let begin=0;for(let i=1;i<=rows.length;i++){{if(i===rows.length||rows[i].regime!==rows[begin].regime){{if(rows[begin].regime==='weak'){{const x1=xAt(begin,w),x2=xAt(Math.min(i,rows.length-1),w);ctx.fillStyle=C.weak;ctx.globalAlpha=.42;ctx.fillRect(x1,layout.mainTop,Math.max(x2-x1,plotW/rows.length),layout.mainH);ctx.globalAlpha=1}}begin=i}}}}
  axis(w,layout.mainTop,layout.mainH,vMin,vMax,pct,'累计收益率');rightAxis(w,layout.mainTop,layout.mainH,vMin,vMax);line(rows.map(r=>r.benchmark),C.benchmark,vMin,vMax,layout.mainTop,layout.mainH,w,1.5);line(rows.map(r=>r.strategy),C.strategy,vMin,vMax,layout.mainTop,layout.mainH,w,2.4);
  rows.forEach((r,i)=>{{const x=xAt(i,w),y=yAt(r.strategy,vMin,vMax,layout.mainTop,layout.mainH);if(r.cashDefense){{ctx.fillStyle=C.cash;ctx.fillRect(x-3,y-3,6,6)}}else if(r.action==='换仓'){{ctx.fillStyle=C.switch;ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill()}}}});
  const ddMin=Math.min(-.01,...rows.map(r=>r.drawdown));axis(w,layout.ddTop,layout.ddH,ddMin,0,pct,'回撤');ctx.beginPath();ctx.moveTo(xAt(0,w),yAt(0,ddMin,0,layout.ddTop,layout.ddH));rows.forEach((r,i)=>ctx.lineTo(xAt(i,w),yAt(r.drawdown,ddMin,0,layout.ddTop,layout.ddH)));ctx.lineTo(xAt(rows.length-1,w),yAt(0,ddMin,0,layout.ddTop,layout.ddH));ctx.closePath();ctx.fillStyle='rgba(36,144,108,.20)';ctx.fill();line(rows.map(r=>r.drawdown),C.down,ddMin,0,layout.ddTop,layout.ddH,w,1.4);
  const absRet=Math.max(.005,...rows.map(r=>Math.abs(r.daily)));axis(w,layout.retTop,layout.retH,-absRet,absRet,pct,'日收益');const zero=yAt(0,-absRet,absRet,layout.retTop,layout.retH),barW=Math.max(1,plotW/rows.length*.7);rows.forEach((r,i)=>{{const y=yAt(r.daily,-absRet,absRet,layout.retTop,layout.retH);ctx.fillStyle=r.daily>=0?C.up:C.down;ctx.fillRect(xAt(i,w)-barW/2,Math.min(y,zero),barW,Math.max(1,Math.abs(y-zero)))}});
  ctx.fillStyle=C.muted;ctx.textBaseline='top';ctx.font='11px Inter, Microsoft YaHei';const ticks=Math.min(w<520?3:6,rows.length);for(let i=0;i<ticks;i++){{const idx=Math.round(i*(rows.length-1)/(ticks-1||1));ctx.textAlign=i===0?'left':i===ticks-1?'right':'center';ctx.fillText(rows[idx].date,xAt(idx,w),layout.retTop+layout.retH+10)}}
  if(hoverIndex!==null){{const x=xAt(hoverIndex,w);ctx.strokeStyle='rgba(42,52,62,.45)';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x,layout.mainTop);ctx.lineTo(x,layout.retTop+layout.retH);ctx.stroke();ctx.setLineDash([]);}}
  window.__backtestChartReady=true;
}}
function safe(s){{return String(s).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))}}
canvas.addEventListener('pointermove',e=>{{const rect=canvas.getBoundingClientRect(),w=rect.width,x=e.clientX-rect.left;hoverIndex=Math.max(0,Math.min(rows.length-1,Math.round((x-layout.left)/(w-layout.left-layout.right)*(rows.length-1))));draw();const r=rows[hoverIndex],pending=r.unfilled?` · 未成交 ${{safe(r.unfilled)}}`:'';tooltip.innerHTML=`<b>${{r.date}} · ${{safe(r.held)}}</b><span class="muted">持仓 ${{safe(r.heldCodes)}} · ${{r.shares}}份${{pending}}</span><br>策略收益 ${{pct(r.strategy)}} · 沪深300 ${{pct(r.benchmark)}}<br>资产 ¥${{r.portfolio.toFixed(2)}} · 现金 ¥${{r.cash.toFixed(2)}}<br>日收益 ${{pct(r.daily)}} · 回撤 ${{pct(r.drawdown)}} · 成本 ¥${{r.cost.toFixed(2)}}<br>${{r.regime==='weak'?'走弱期':'正常期'}} · ${{safe(r.action)}}至 ${{safe(r.next)}}`;tooltip.style.display='block';const tw=238,left=Math.min(Math.max(8,x+14),w-tw-8),top=Math.max(8,e.clientY-rect.top-98);tooltip.style.left=left+'px';tooltip.style.top=top+'px'}});
canvas.addEventListener('mouseleave',()=>{{hoverIndex=null;tooltip.style.display='none';draw()}});new ResizeObserver(draw).observe(canvas.parentElement);draw();
</script>
</body>
</html>"""
