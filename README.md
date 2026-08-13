# mom-select

从聚宽策略中提取市场判断和 ETF 动量选择逻辑，生成供人工复核的报告。本项目只提供建议，不连接账户、不自动下单；`original.py` 作为原始回溯模板，保持不修改。

## 功能

- 13:05 盘中信号和收盘复盘两种模式
- 市场状态判断、全市场动态 ETF 池和 25 日动量排名
- 输出 Markdown、JSON、HTML 和 PNG 报告
- 行情 CSV 缓存与离线复盘
- 企业微信、Telegram、邮件图片通知
- APScheduler 定时执行

## 本地运行

安装依赖：

```bash
uv sync --dev
```

生成 13:05 盘中建议：

```bash
uv run mom-select
```

收盘复盘：

```bash
uv run mom-select --mode close
```

调试盘中流程：

```bash
uv run mom-select --debug
```

使用持仓文件：

```bash
cp config/portfolio.example.csv config/portfolio.csv
uv run mom-select --portfolio config/portfolio.csv
```

没有持仓时可保留空文件或只保留 CSV 表头，程序会按空仓处理。

历史离线复盘：

```bash
uv run mom-select --mode close --offline \
  --date 2026-08-11 --no-save-state
```

报告写入 `reports/`，行情缓存写入 `data/cache/`。

## 配置

生产配置使用 YAML：

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

`config/config.yaml` 管理路径、策略参数、调度时间和通知渠道。敏感信息使用环境变量引用，例如：

```yaml
webhook: ${WECHAT_WEBHOOK}
```

支持的通知渠道：

- 企业微信机器人
- Telegram Bot，可配置 HTTP/HTTPS/SOCKS5 代理
- SMTP 邮件

## Docker 部署

镜像已包含 Python、uv、Playwright Chromium、中文字体和浏览器运行库，宿主机不需要安装浏览器。

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
docker compose up -d
```

Compose 持久化以下数据：

- `etf_cache`：行情缓存
- `etf_reports`：报告文件
- `etf_state`：市场状态

配置文件和持仓文件以只读方式挂载。镜像包含生产和开发依赖，容器使用镜像内 uv 启动，运行时不会重新同步或下载依赖：

```text
uv run --frozen --no-sync
```

## 定时执行

调度器使用 APScheduler，默认按 `Asia/Shanghai` 时区在工作日 13:05 执行，任务直接调用策略函数。Docker Compose 推荐用于生产环境；Supervisor 配置仍保留在 `deploy/supervisor/`。

立即生成 DEBUG 报告并测试消息通知：

```bash
uv run python -m scripts.mom_select_scheduler \
  --config config/config.yaml --run-once --debug
```

Docker Compose 中执行：

```bash
docker compose run --rm mom-select \
  --config /app/config/config.yaml --run-once --debug
```

通知标题会带 `[DEBUG]`，正文注明不作为正式交易信号。`--debug` 只能和 `--run-once` 一起使用，不会影响常驻定时任务。

## 数据和限制

- 默认使用东方财富公开接口，失败时回退到腾讯接口。
- 公开接口可能限流或中断，长期实盘建议接入稳定的授权数据源。
- 盘中模式只允许在交易日 13:05 附近运行。
- 历史回放使用当前可见 ETF 清单，存在幸存者偏差。
- 信号必须人工核对公告、停牌、涨跌停、溢价率和成交情况。

## 开发检查

```bash
uv run ruff check .
uv run pytest
```

GitHub Actions 的 PR 检查执行 Ruff 和依赖安全审查；Docker 镜像只通过 Actions 页面手动触发构建发布。Dependabot 每周检查项目依赖和 Actions 版本。
