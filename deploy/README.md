# Linux + Supervisor 部署

程序本身是一次性 CLI。`scripts/mom_select_scheduler.py` 使用 APScheduler 常驻调度，按上海时区在周一至周五 13:05 直接调用 `mom_select.cli.run()`；Supervisor 只负责保持调度器运行和收集日志。

## 安装

```bash
cd /opt
git clone <repository-url> mom-select
cd mom-select
uv sync --no-dev
PLAYWRIGHT_BROWSERS_PATH=/opt/mom-select/.playwright \
  uv run playwright install --with-deps chromium
cp config/portfolio.example.csv config/portfolio.csv
```

`config/portfolio.csv` 是人工维护的持仓文件，不要把真实账户凭据放入项目。

服务器不需要预装 Chrome。上面的命令会下载 Playwright 自带的无头 Chromium，并在 Ubuntu/Debian 上安装所需系统运行库。若当前用户没有安装系统依赖的权限，请先使用 `sudo` 执行依赖安装，或联系管理员安装 `libnss3`、`libatk-bridge2.0-0`、`libgtk-3-0` 等 Chromium 运行库。

安装 Supervisor（以 Debian/Ubuntu 为例）：

```bash
sudo apt-get update
sudo apt-get install -y supervisor
```

复制并修改 `deploy/supervisor/mom-select-scheduler.conf`：

```bash
sudo cp deploy/supervisor/mom-select-scheduler.conf /etc/supervisor/conf.d/
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status mom-select-scheduler
```

## 验证

先用调度器的单次模式检查路径、权限和数据源：

```bash
.venv/bin/python -m scripts.mom_select_scheduler \
  --config /opt/mom-select/config/config.yaml \
  --run-once
```

正式调度不需要 cron。APScheduler 使用 `CronTrigger(day_of_week="mon-fri", hour=13, minute=5)`，并设置单实例、合并错过触发和 5 分钟误触发宽限。Supervisor 使用 `uv sync` 创建的 `/opt/mom-select/.venv/bin/python`，不会依赖全局 Python 或全局 uv。周末自动跳到下周一；中国法定节假日没有单独日历时，程序可能在该日尝试一次并因无行情退出本次执行，随后继续等待下一工作日。若需要严格跳过节假日，可接入交易日历。

查看日志：

```bash
sudo supervisorctl tail -f mom-select-scheduler
tail -f /var/log/mom-select-scheduler-error.log
```

报告默认写入 `/opt/mom-select/reports/`。运行时间、缓存目录和池文件均可在 Supervisor 的 `command` 行中通过参数调整。

PNG 生成优先使用系统 Chrome/Edge；服务器没有系统浏览器时自动使用上述 Playwright Chromium。Supervisor 配置中的 `PLAYWRIGHT_BROWSERS_PATH` 必须与安装浏览器时使用的目录一致，并确保 `ubuntu` 用户对该目录有读取和执行权限。

如果不想维护宿主机浏览器，推荐直接使用项目根目录的 Docker Compose 配置；它把 Chromium 和中文字体封装在镜像中，详见根目录 `docker-compose.yaml`。

## 手工补跑

调度器不会补跑错过的日期。需要补跑时直接执行：

```bash
.venv/bin/python -m mom_select.cli --date 2026-08-11 --debug --no-save-state
```
