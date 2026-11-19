# SEPA 云端运行和 MCP

## 深交所月历

A 股 MCP 的 `trading_calendar` 接受 `year` 和 `month`，返回完整自然月的逐日 `is_open`、来源和获取时间。2026-11-19 通过已连接的 MCP 实测取得 2025-11 至 2026-11 的 13 个整月。以 2026-11-18 为报告日，63、126、252 个交易日前分别是 2026-08-13、2026-05-15 和 2025-11-04。该验证只覆盖交易日定位，价格和除权事件仍由各自来源验证。

Cloud Shell 和 ChatGPT 内置 A 股 MCP 的执行网络是两条路径。连接器返回的月历结果可作为 JSON 文件交给导入命令，程序验证官方 URL、年月、完整日期、布尔开市标志和时间戳，归档对应月份并记录来源与哈希。

```bash
python3 -m review.sepa calendar-import \
  --snapshot work/sepa/incoming/2026-11.json \
  --archive-dir work/sepa/calendar
```

传入 `--calendar-archive-dir work/sepa/calendar` 以优先读取该目录。仓库的 `config/calendars/` 包含首批已核验的官方月历。新月份可用相同流程归档。

如果你拥有可直连的同协议 Streamable HTTP MCP，可在运行环境设置 `SEPA_CALENDAR_MCP_URL` 和可选 `SEPA_CALENDAR_MCP_TOKEN`。工具必须支持 `trading_calendar(year, month)`，并返回同一数据结构。ChatGPT 连接器的工具名只在连接器里有效，Shell 不需要猜测其服务 URL。

## 大虾皮预检

大虾皮个人中心的 Token 通过运行环境的 `DAXIAPI_TOKEN` 提供。CLI 的认证值不进入源码、命令参数、报告及 ChatGPT 提示词。默认请求使用第一方发行版 `daxiapi-cli@3.3.1` 的 VCP 路径，保留环境 HTTPS 代理与 TLS 校验。候选快照必须能证明报告日期，历史形态日期作为单独字段保留。鉴权响应的内部字段仍须以真实授权样本验收。

```bash
python3 -m review.sepa candidates --date YYYY-MM-DD \
  --output-dir work/sepa/private-probe
```

成功时控制台仅输出日期、状态和条数。已验证的候选原始载荷保存在指定的私有目录。401/403、超时、响应结构不匹配或日期过期都是失败，空列表只有在成功且已核验当日快照时成立。结合大虾皮的数据使用条款，真实候选和报告保留在用户私有环境。[第一方 CLI 指南](https://daxiapi.com/article/help/2026-06-18-daxiapi-cli-guide/)

## 每日入口

```bash
python3 -m pip install -r requirements-sepa.txt
python3 -m review.sepa.daily --output-root work/sepa/archive \
  --calendar-archive-dir work/sepa/calendar
```

每日运行使用北京时间，未传 `--date` 时取北京时间当天。核验官方日历后，休市直接跳过。交易日获取大虾皮候选，然后进入独立的市场 RS、趋势模板和 VCP 流程。成功报告按日期保存在私有目录，重复执行同一成功日自动跳过。失败诊断与有效历史分开保存，恢复后可重跑。候选非空且市场 RS 未达到覆盖门槛时，正式日任务会留下失败诊断。`--allow-unranked` 仅用于独立目录中的研究运行。

Cloud 计划任务须在能恢复此分支源码、读取 `DAXIAPI_TOKEN` 并持久保存私有归档的环境中启动。实际网络应能访问 `daxiapi.com`、`www.tdx.com.cn` 及行情源 `web.ifzq.gtimg.cn`（备选 `proxy.finance.qq.com`、`ifzq.gtimg.cn`）。月历可通过 MCP 归档提供。先在计划任务使用的环境跑完真实 `candidates` 与 `daily`，再安排北京时间盘后启动，并验证一次新任务实例的生成与读回。现有多资产 Site 发布流程独立运行。

## 选股 MCP 服务

安装 `requirements-sepa.txt` 后，从仓库根目录启动：

```bash
SEPA_ARCHIVE_ROOT=/absolute/private/archive python3 -m review.sepa.mcp_server
```

客户端通过 stdio 调用 `vcp_candidates`、`sepa_screen`、`vcp_analyze`、`sepa_rank`、`sepa_report`。报告读取只在服务端配置的私有归档根内进行。实时工具与 CLI 共用同一来源和计算逻辑。`--transport streamable-http` 适用于本机调试，只绑定回环地址。Cloud App 若需远程连接，须另外部署受访问控制的 HTTPS 服务并接入该客户端。
