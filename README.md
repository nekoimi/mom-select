<div align="center">

# mom-select

面向人工复核的 ETF 动量轮动信号生成器

[![Docker](https://github.com/nekoimi/mom-select/actions/workflows/docker.yml/badge.svg)](https://github.com/nekoimi/mom-select/actions/workflows/docker.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker Pulls](https://img.shields.io/docker/pulls/nekoimi/mom-select?logo=docker)](https://hub.docker.com/r/nekoimi/mom-select)
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

[功能特性](#功能特性) · [快速开始](#快速开始) · [配置说明](#配置说明) · [部署与运维](#部署与运维) · [参与贡献](#参与贡献)

</div>

`mom-select` 使用公开行情生成 ETF 动量轮动建议和 A 股趋势排名，并输出 Markdown、JSON、HTML 和 PNG 报告。它只提供供人工复核的策略结果，**不连接证券账户，也不会自动下单**。

> [!IMPORTANT]
> 本项目仅供策略研究和技术交流，不构成任何投资建议。使用者应自行核对公告、停牌、涨跌停、溢价率、申赎状态和实际成交条件，并独立承担决策风险。

## 功能特性

- **双业务模块**：ETF 轮动与个股趋势排名拥有独立的数据、策略、报告和调度流程。
- **ETF 双运行模式**：支持 13:05 盘中信号和收盘复盘。
- **ETF 日频回测**：给定日期范围，按每日收盘信号在下一交易日切换标的，并导出逐日收益。
- **市场状态判断**：根据四个市场指数与 MA10 的关系识别正常期和走弱期。
- **动态 ETF 池**：结合固定池、全市场发现、流动性和产品名称规则构建候选池。
- **动量排名**：计算 25 日加权趋势、年化收益和 R²，并执行量能、跌幅和流动性过滤。
- **全市场个股排名**：从 A 股全市场筛选非创业板、非科创板、非 ST，且满足 5 至 55 元、今日涨幅 1% 至 10%、成交额不低于 3 亿元、换手率 3% 至 15% 的股票，再以均线多头、20 日涨幅超过 10%、60 日涨幅超过 20% 确认趋势候选；未达到条件时不凑数。
- **多格式报告**：一次生成 Markdown、JSON、HTML 和 PNG，便于阅读和系统集成。
- **多级数据回退**：历史日线按东方财富、腾讯、Yahoo、AKShare 的顺序回退。
- **离线复盘**：行情按证券缓存为 CSV，可使用已有缓存重建历史结果。
- **消息通知**：支持企业微信机器人、Telegram Bot 和 SMTP 邮件。
- **可靠调度**：APScheduler 配合 AKShare 中国交易日历，仅在交易日执行正式任务。
- **容器化部署**：提供预装 Chromium 和中文字体的 Docker 镜像及 Compose 配置。

## 工作流程

```mermaid
flowchart LR
    A[交易日历] --> B{当天开市?}
    B -- 否 --> C[跳过任务]
    B -- 是 --> D[13:05 ETF 轮动]
    B -- 是 --> E[15:20 个股趋势]
    D --> F[ETF 动量与风险过滤]
    E --> G[全市场预筛与趋势排名]
    F --> H[Markdown / JSON / HTML / PNG]
    G --> H
    H --> I[企业微信 / Telegram / 邮件]
```

## 快速开始

### 使用 Docker Compose（推荐）

运行环境需要 Docker Engine 和 Docker Compose v2。

```bash
git clone https://github.com/nekoimi/mom-select.git
cd mom-select

cp config/config.example.yaml config/config.yaml
cp config/portfolio.example.csv config/portfolio.csv
cp .env.example .env

docker compose up -d
```

Compose 默认使用 [`ghcr.io/nekoimi/mom-select:latest`](https://github.com/nekoimi/mom-select/pkgs/container/mom-select)，并将行情缓存、报告和市场状态保存在 Docker volumes 中。

查看运行日志：

```bash
docker compose logs -f --tail=200 mom-select
```

立即生成 DEBUG 报告并测试通知链路：

```bash
docker compose run --rm mom-select \
  --config /app/config/config.yaml --run-once --debug
```

### 本地运行

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。若系统没有 Chrome 或 Edge，还需安装 Playwright Chromium。

```bash
git clone https://github.com/nekoimi/mom-select.git
cd mom-select

uv sync --dev
uv run playwright install chromium
cp config/portfolio.example.csv config/portfolio.csv
```

常用命令：

```bash
# 生成当前交易日 13:05 盘中信号
uv run mom-select

# 生成收盘复盘
uv run mom-select --mode close

# 固定策略时间为当日 13:05，用于本地流程检查
uv run mom-select --debug

# 指定人工维护的持仓文件
uv run mom-select --portfolio config/portfolio.csv

# 使用本地缓存复查历史收盘信号
uv run mom-select --mode close --offline \
  --date 2026-08-11 --no-save-state

# 回测指定区间（也可增加 --offline 只使用已有缓存）
uv run mom-select etf backtest --start 2026-01-01 --end 2026-06-30

# 覆盖默认成交假设；费率参数单位为 bp（1 bp = 0.01%）
uv run mom-select etf backtest --start 2026-01-01 --end 2026-06-30 \
  --initial-capital 10000 --fee 5 \
  --slippage-bps 5 --impact-bps 2 --premium-bps 10

# 收盘后生成当前交易日个股趋势排名
uv run mom-select stock

# 使用配置文件中的个股参数
uv run mom-select stock --config config/config.yaml

# 使用目标日期已有的全市场快照，联网补齐个股和沪深300历史行情
uv run mom-select stock --date 2026-08-14

# 完全离线复查；要求目标日期快照、个股和沪深300行情均已缓存
uv run mom-select stock --date 2026-08-14 --offline
```

ETF 报告和缓存按配置写入 `reports/etf/`、`data/cache/etf/`；个股默认写入 `reports/stock/`、`data/cache/stock/`。持仓文件可以为空或只保留 CSV 表头，此时 ETF 程序按空仓处理。

ETF 回测固定使用 ETF 池文件。每日收盘生成目标，下一交易日按开盘价模拟成交，因此不会使用当日收盘信号计算当日收益。默认起步资金为 10,000 元，按 100 份一手用剩余资金全仓买入；每笔买入或卖出收取 5 元固定手续费，单边滑点、冲击成本和 ETF 溢价折价代理值分别为 5 bp、2 bp 和 10 bp。模拟买价上浮三项成本之和，卖价相应下调；一次换仓包括卖出和买入两笔操作。卖出后资金不足买入一手目标 ETF 时不产生买入操作和费用，账户进入现金防御，并在后续交易日继续按最新信号尝试建仓。期末持仓按收盘价估值，不额外强制平仓。

每次回测输出逐日 CSV、逐笔成交 CSV、JSON 完整结果、可离线交互的 HTML 图表和 PNG 图片。成交明细包含市场价、模拟成交价、份额、固定手续费及三类隐含成本，图表包含策略与沪深300净值、回撤、日收益、市场状态和换仓位置。历史回测仍受当前 ETF 池带来的幸存者偏差影响；固定 bp 只是缺少历史盘口和 iNAV 数据时的保守代理，可通过命令行参数调整或设为 0。

个股模块会访问全市场公开接口，首次运行需要为通过预筛的股票建立前复权日线缓存，耗时和请求量明显高于 ETF。指定过去日期且未使用 `--offline` 时，程序会读取目标日期已有的全市场快照，并联网补齐个股前复权日线和沪深300行情；公开接口无法还原历史全市场截面，因此目标日期的 `universe.csv` 仍必须已经存在。生产配置默认将 `tasks.stock.enabled` 设为 `false`，确认数据源可用后再显式启用。

## 配置说明

复制示例文件后再修改生产配置，避免将密钥提交到仓库：

```bash
cp config/config.example.yaml config/config.yaml
cp config/portfolio.example.csv config/portfolio.csv
cp .env.example .env
```

主要配置分组：

| 分组 | 用途 |
|---|---|
| `runtime` | 并发数、项目目录和是否仅使用固定 ETF 池 |
| `tasks.etf` | ETF 启停、13:05 调度、路径和动量策略参数 |
| `tasks.stock` | 个股启停、15:20 调度、路径和趋势策略参数 |
| `notifications` | 通知总开关和企业微信、Telegram、邮件渠道 |

完整示例见 [`config/config.example.yaml`](config/config.example.yaml)。敏感字段使用环境变量引用：

```yaml
notifications:
  enabled: true
  channels:
    - type: wechat
      name: default
      webhook: ${WECHAT_WEBHOOK}
```

对应密钥填写到 `.env`：

```dotenv
WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
```

支持的通知渠道：

- 企业微信机器人：依次发送文本和 PNG 图片；机器人图片限制为 2 MiB。
- Telegram Bot：支持 HTTP、HTTPS 和 SOCKS5 代理。
- SMTP 邮件：支持 SSL 或 STARTTLS，并将 PNG 作为附件发送。

通知发送的每个阶段都会写入日志，但不会输出 webhook、Token 或密码。

## 数据源与缓存

| 数据 | 首选来源 | 回退来源 |
|---|---|---|
| 历史日线 | 东方财富 | 腾讯 → Yahoo → AKShare |
| 13:05 实时快照 | 腾讯 | 数据不足时降低覆盖率并将报告标为不可执行 |
| 全市场 ETF 清单 | 东方财富 | 东方财富基金清单 → 本地缓存 |
| 全市场 A 股快照 | AKShare / 腾讯接口 | 东方财富 → 新浪 → 同交易日本地缓存 |
| 个股前复权日线 | AKShare / 东方财富接口 | 腾讯前复权日线 → 每只股票独立增量缓存 |
| 中国交易日历 | AKShare | 本地日历缓存 |

交易日历缓存在 `data/cache/shared/trading_calendar.csv`。网络更新失败时会使用本地缓存；网络和缓存均不可用，或日历尚未覆盖目标日期时，正式调度会安全跳过，避免在未知日期误发信号。

个股必要预筛使用同一交易日的未复权快照判断价格、涨幅、成交额和换手率，趋势指标使用前复权日线。全市场清单低于完整性门槛时会终止任务，避免使用不完整证券列表生成正式排名。

公开接口可能限流、变更或中断。长期生产使用建议接入稳定、合规的授权数据源。

## 部署与运维

### 容器镜像

项目同时发布两个内容相同的镜像：

- GHCR：[`ghcr.io/nekoimi/mom-select:latest`](https://github.com/nekoimi/mom-select/pkgs/container/mom-select)
- Docker Hub：[`nekoimi/mom-select:latest`](https://hub.docker.com/r/nekoimi/mom-select)

```bash
docker pull ghcr.io/nekoimi/mom-select:latest
# 或
docker pull nekoimi/mom-select:latest
```

镜像已包含 Python、uv、Playwright Chromium、中文字体和浏览器运行库。容器以 `uv run --frozen --no-sync` 启动，运行期间不会重新解析或下载 Python 依赖。

Compose 使用 Docker `json-file` 日志驱动，单文件最大 10 MiB，最多保留 5 个文件。所有调度、数据获取、报告生成和通知异常都会输出到标准输出或标准错误，可通过 `docker compose logs` 查看。

### APScheduler

ETF 默认在 `Asia/Shanghai` 时区的周一至周五 13:05 触发。启用个股任务后，它会在交易日 15:20 独立生成收盘排名。两个任务执行前都会检查中国交易日历，异常互不阻塞。

手工调用调度任务：

```bash
python -m scripts.mom_select_scheduler --config config/config.yaml --run-once --task etf
python -m scripts.mom_select_scheduler --config config/config.yaml --run-once --task stock
```

ETF 的 `--run-once --task etf --debug` 不受交易日历限制，便于节假日排查报告和通知链路；正式个股任务仍要求交易日和完整收盘数据。

### Supervisor

不使用 Docker 时，可以使用 APScheduler 配合 Supervisor 常驻运行。完整步骤见 [`deploy/README.md`](deploy/README.md)。

## 项目结构

```text
mom_select/
  core/                     共享交易日历、通知、报告和数学能力
  etf/                      ETF 数据、标的池、策略与报告边界
  stock/                    个股全市场筛选、趋势排名与报告
  cli.py                    兼容入口和 ETF/stock 子命令分发
  settings.py               双业务 YAML 配置
scripts/
  mom_select_scheduler.py   ETF/个股 APScheduler 常驻调度入口
config/                     配置、ETF 池和持仓示例
deploy/                     Supervisor 部署文件
tests/                      pytest 测试
original.py                 原始聚宽回溯模板（保持不修改）
```

## 开发

安装开发依赖并运行检查：

```bash
uv sync --dev
uv run ruff check .
uv run pytest
```

GitHub Actions 会对 Pull Request 执行 Ruff 和依赖安全审查。Docker 镜像通过 Actions 页面手动触发构建，并同时推送到 GHCR 和 Docker Hub；Dependabot 每周检查 Python 依赖和 Actions 版本。

## 参与贡献

Issue 和 Pull Request 都欢迎。提交修改前请：

1. 先搜索 [Issues](https://github.com/nekoimi/mom-select/issues)，确认没有重复问题。
2. 对行为变更补充或更新测试。
3. 运行 Ruff 和完整 pytest 测试。
4. 不要提交 `.env`、真实持仓、通知密钥或账户信息。
5. 保持 `original.py` 不变，以便与原始策略逻辑对照。

报告 Bug 时建议附上运行模式、目标日期、Python 或镜像版本及脱敏后的完整日志。

## 已知限制

- 历史回放使用当前可见 ETF 清单，存在幸存者偏差。
- 腾讯和 Yahoo 的部分日线不直接提供成交额，项目会使用 OHLC 均价乘成交量估算，并在报告中提示。
- 盘中信号基于未完成交易日数据，收盘前价格和排名仍可能变化。
- 个股全市场扫描依赖公开接口，首次建立历史缓存耗时较长，接口限流时可能导致数据覆盖不足。
- 个股历史回放要求存在目标交易日的全市场快照缓存，不能用当前股票状态替代历史状态；非离线运行可自动补齐缺失或损坏的个股与基准历史行情。
- 本项目不检查所有公告、停牌、涨跌停、实时溢价和申赎限制，执行前必须人工核对。

## 许可证

本项目采用 [GNU Affero General Public License v3.0](LICENSE) 开源。分发、修改或通过网络提供本项目衍生服务时，请遵守 AGPL-3.0 的源代码公开要求。
