# mom-select

本项目从聚宽策略模板 `original.py` 中提取市场判断和ETF动量选择逻辑，生成供人工复核的本地建议报告。`original.py` 固定作为回溯模板，不参与本地程序运行，也不应修改。

当前支持两种模式：13:05盘中模式用于贴近聚宽原策略生成当日信号；收盘模式使用完整日线生成下一交易日复盘参考。程序不会连接账户、不会查询资金、不会自动下单。

## 已实现

- 通过4个A股指数与MA10判断正常期/走弱期，并保存状态机结果。
- 正常期扫描全市场ETF，按名称行业分组选择近3日流动性最佳标的，并与过滤后的114只固定池合并；走弱期只分析原全球、跨境和商品池。
- 使用25日近期加权的对数价格回归计算年化趋势、R²和动量分。
- 应用动量、R²/MA10、成交量、最近3日跌幅和成交额过滤。
- 当前持仓处于第一名90%得分范围内时优先保留。
- 无风险ETF通过时输出货币ETF `511880.XSHG` 作为防御目标。
- 输出Markdown、JSON、设计后的HTML长图报告及对应PNG图片。
- PNG 使用 Playwright 渲染；Linux 服务器无需安装桌面浏览器，安装 Playwright 自带的 Chromium 即可。
- ETF行情缓存为CSV，支持断网后使用 `--offline` 复现。

动态池复现 `original.py` 的名称清洗和前两个字符分组规则，因此也保留原规则可能误分行业的局限。固定池维护在 `config/etf_pool.csv`。

## 安装

```powershell
uv sync --dev
```

Linux 长期运行可使用 Supervisor 托管内置调度器，详见 [`deploy/README.md`](deploy/README.md)。调度器按上海时区在工作日 13:05 调用一次 CLI，不需要额外配置 cron。

## 运行

生产部署推荐使用 Docker Compose：复制 `config/config.example.yaml` 为 `config/config.yaml`，复制 `.env.example` 为 `.env`，填写通知密钥后执行 `docker compose up -d`。镜像内已包含 Playwright Chromium、Linux 运行库和 `Noto Sans CJK` 中文字体，宿主机不需要安装浏览器。

GitHub Actions 不会因推送分支或创建 Tag 自动发布镜像。提交 Pull Request 时会执行 Ruff 静态检查和依赖安全检查；需要构建并发布镜像时，在 Actions 页面手动运行 `Docker` workflow。Dependabot 每周检查 uv 项目依赖和 GitHub Actions 版本。

交易日13:05-13:10生成盘中建议（默认模式）：

```powershell
uv run mom-select
```

收盘后生成复盘建议：

```powershell
uv run mom-select --mode close
```

盘中模式必须在线运行，只接受当天行情，并按13:05累计成交量折算预计全天量。它不会把盘中快照写入完整日K缓存，也不会写回正式市场状态。同日文件名带 `-intraday`，不会覆盖收盘报告。

在非13:05时段调试盘中流程：

```powershell
uv run mom-select --debug
```

DEBUG模式将策略时钟和成交量折算点固定为当天13:05，但行情仍是运行时获取到的当日最新快照，因此只用于检查程序流程，不能作为实盘信号。调试报告带 `-intraday-debug` 后缀，不覆盖正式报告，也不会写回市场状态。

也可指定历史日期：

```powershell
uv run main.py --date 2026-08-11 --debug
```

历史DEBUG使用指定日期的完整日K模拟13:05。由于公开日线数据无法还原当时13:05的真实价格和累计成交量，程序不会对全天成交量做1.92倍折算，结果只能用于流程和排名近似检查。

正常期默认启用全市场动态ETF池。首次运行需要下载全市场ETF近3日流动性数据并建立缓存，耗时会明显长于后续运行。需要快速排查固定池时可使用：

```powershell
uv run mom-select --fixed-pool-only
```

指定历史截止日，并避免覆盖当前市场状态：

```powershell
uv run mom-select --mode close --date 2026-08-11 --no-save-state
```

提供实际持仓：

```powershell
Copy-Item config/portfolio.example.csv portfolio.csv
uv run mom-select --portfolio portfolio.csv
```

持仓文件字段：

```csv
code,name,amount,avg_cost
518880.XSHG,黄金ETF,1000,7.152
```

缓存建立后可离线复现：

```powershell
uv run mom-select --mode close --offline --date 2026-08-11 --no-save-state
```

报告生成到 `reports/`，同一日期包含Markdown、JSON、HTML和PNG四种格式。HTML可直接用浏览器打开，PNG为适合查看和分享的整页长图。行情缓存位于 `data/cache/`。

## 数据源说明

默认适配器优先使用东方财富公开日K接口，失败时切换腾讯公开日K，并在本地缓存前复权数据。腾讯日线不直接提供成交额，备用路径使用OHLC均价乘成交量估算并在报告中提示。公开接口没有稳定性或服务等级保证，因此程序设置了安全边界：

- 全市场ETF清单优先使用东方财富板块接口，接口限流时切换东方财富基金清单筛选场内ETF，并缓存到 `data/cache/_etf_universe.csv`。
- 动态池流动性使用信号日前3个完整交易日；动态池新增代码若缺少完整25日历史，会在报告中计入失败并降低覆盖率，不会用短历史强行计算动量。

- 4个市场指数缺失时直接停止，不生成建议。
- 当日15:10前拒绝把未完成日K作为收盘信号。
- 盘中模式严格限制在13:05-13:10，校验4个指数和ETF快照日期、行情时间与数据覆盖率。
- 候选池有效数据覆盖不足80%时，报告标记为不可执行。
- 每份报告展示失败数量、全部过滤结果和人工检查清单。

如用于长期运行，建议后续实现经过授权的Tushare、QMT或商业行情适配器，并进行双数据源交叉校验。

## 与原策略的差异

- 盘中模式与原策略一样使用13:05实时价和预计全天成交量；收盘模式使用完整收盘日线。
- 本地动态池复现原策略的全市场名称分组与流动性代表选择；历史回放使用当前可见ETF清单，仍存在幸存者偏差。
- 原策略自动下单和分钟止损；本地版只输出建议。
- 本地版统一了加权回归和加权R²的权重口径。
- 本地版使用持久化状态机，不保留原策略“弱势持续时重置开始日”的问题。
- 本地版成交额门槛固定为近3日日均1000万元，不使用随全市场成交额变化的经验除数。

这些变化意味着本地结果不会与聚宽13:05信号逐日完全一致，需要单独回测和模拟观察。

## 测试

```powershell
uv run pytest
```

## 人工执行原则

报告只是算法信号。下单前需要人工核对ETF公告、停牌、涨跌停、申赎状态，以及跨境/商品/LOF产品的溢价率和相关市场休市情况。换仓时应先确认卖出成交，再考虑买入，并记录实际成交价和拒绝信号的原因。
