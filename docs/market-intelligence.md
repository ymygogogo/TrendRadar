# TrendRadar 全球事件与 A 股行业情报

## 背景与方案概述

目标是在 PVE 以半小时为周期收集国内外资讯，及时提醒证据充分的重大事件，并汇总其他重点事件。趋势与影响分数用于筛选，交易和公司层面的数据验证留到后续阶段。

| 上游能力 | 处理 |
|---|---|
| RSS、热榜、原始 SQLite、Docker cron、渠道发送器 | 复用 |
| RSS 列表、模型地址、周期、阈值和渠道 | 通过配置扩展 |
| 目标源代理、官方 HTML 列表、来源状态 | 增加适配并沿用原生抓取器 |
| 跨语言事件、版本、行业推断、投递任务 | 独立 SQLite 和情报模块 |

上游 AI 支持批量分类、翻译和推送摘要。原有 RSS 索引按源和 URL/GUID 去重，原有推送记录按时段保存；事件和渠道投递需要跨天的状态。部署复用明利项目以镜像标识验证、健康检查和失败回退的做法。RSS 和正式公开政策页优先，Reuters、AP、FT 等待验证合规索引或授权接口。

```mermaid
flowchart LR
    C[TrendRadar 每 30 分钟运行] --> R[国内热榜及中外来源]
    R --> S[上游原始数据库]
    R --> M[情报 SQLite]
    M --> A[AlayaNew 检验及行业推断]
    A --> M
    M --> W[企业微信预警与简报]
    G[GitHub Actions 测试及镜像构建] --> P[PVE 拉取校验和及镜像]
    P --> C
```

## PVE 运行

在独立 Ubuntu 24.04 虚拟机安装 Docker、Compose v2、jq 和 curl。将 `deploy/pve/update.sh` 安装为 `/opt/trendradar/update.sh`，把示例环境文件安装为同目录内权限 0600 的 `runtime.env`。将两份 systemd 单元安装到 `/etc/systemd/system/` 并执行 `systemctl daemon-reload` 和 `systemctl enable --now trendradar-update.timer`。

`runtime.env` 配置 `AI_API_BASE`、`AI_MODEL`、`AI_API_KEY` 和 `MARKET_WEWORK_WEBHOOK_URL`。通过 `MARKET_ANALYSIS_ENABLED` 和 `MARKET_NOTIFICATION_ENABLED` 分别启用。覆盖海外代理可设 `MARKET_INTERNATIONAL_PROXY`。只配海外代理时国内官方源仍直连。周期通过 `CRON_SCHEDULE` 调整。评分阈值、简报和分析额度在 `config/config.yaml` 的 `market_intelligence` 段配置，更新默认配置后由镜像发布生效。

```text
cd /opt/trendradar
sudo docker compose --env-file image.env exec -T trendradar python -m market_intelligence check-sources
sudo docker compose --env-file image.env exec -T trendradar python -m market_intelligence health
sudo docker compose --env-file image.env logs --since 1h trendradar
sudo journalctl -u trendradar-update.service
```

定时简报默认北京时间 21:30，最多 8 条。紧急提醒要求近 24 小时的官方报道并通过模型证据校验。只有标题的官方索引缺少精确发布时间时可进入简报，紧急提醒保持关闭。分数与出处写入 SQLite。若企微已接受请求后进程在落库前中断，机器人接口没有幂等键，此极短窗口可导致重发。

## 升级、回退与检查

同一时点只运行一个部署实例。发布脚本校验归档 SHA-256 与 Docker 镜像 ID，停止写入后备份配置和完整 SQLite 目录。失败恢复旧镜像和升级前数据。诊断时先检查发布日志，确认当前镜像 ID 和健康状态，再检查真实抓取结果。手工回退时先停更新定时器和容器，使用指定备份里的 compose 文件和 image.env。仅在数据格式确实需要回退时恢复 SQLite，并在恢复前备份现有数据，以免覆盖发布后新增的新闻或投递记录。

测试关注真实来源的 DNS、超时和响应变化，跨语言相似报道与不同事件，短时间恶化、模型格式错误、同一事件的官方确认、重复启动、通知失败和恢复，以及运行耗时和资源占用。定期记录至少 24 小时的来源健康。国内政策源需要维护列表规则。模型调用预算、真实渠道投递和发布故障恢复以现场验收为准。
