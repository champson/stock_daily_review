# 收盘之后 · 股票每日复盘

目标：ChatGPT Cloud 运行通达信 MCP 数据采集与分析，北京时间 18:00 周一至周五触发，依据交易日历跳过休市日；ChatGPT Site 在线展示日报与历史存档。第一版面向综合市场复盘，具体指标可继续调整。

**当前状态：本地框架已实现；MCP 实际接口映射、Sites 发布和定时任务尚未完成。没有使用真实行情生成报告。**

## 数据流程

Cloud 定时任务 → 通达信 MCP → 日期与字段校验 → 确定性指标 → 私有 Site 写入 → R2 历史存档 → 在线复盘页面。报告更新写数据，站点代码变更才重新发布。

## 报告内容与口径

| 模块 | 第一版内容 | 必需字段 / 限制 |
| --- | --- | --- |
| 指数 | 主要宽基收盘涨跌、MA5/20/60、成交额相对前五日均值 | 同口径历史价格；不足历史长度不计算相应均线 |
| A股 | 成交额、上涨/下跌/平盘、样本覆盖、板块强弱 | 涨跌相对前收盘；只有完整证券池才标记全市场；板块使用数据源分类与样本等权平均 |
| ETF | 涨跌、成交额、同日净值折溢价 | NAV 日期必须与行情一致；不从行情推断资金净流入 |
| 股指期货 | IF/IH/IC/IM 可获取合约、涨跌、基差、持仓 | 涨跌相对前结算；基差=期货收盘−同日现货；合约列表从数据源取得 |
| 期权 | 数据源可获取的指数/ETF期权链、成交与持仓 PCR、提供的 IV | PCR 按标的和到期日分组；缺少两侧合约或字段不计算；不估造 IV |

缺失模块不阻断其他可用模块；过期、重复、非有限值、无收盘价的记录剔除。所有模块缺失时返回非零状态，不发布空报告。

## Cloud 配置

- Python 3.12+，采集程序仅使用标准库。
- 网络允许 `stock.quicktiny.cn`；当前 Sites 工具包下载域名为 `sdmntprsouthcentralus.oaiusercontent.com`。
- 官方配置入口：Settings → Codex Cloud → Environments → 当前环境的 ⋯ → Edit → Internet access → Additional allowed domains。保持 Package managers 并加入所需域名，保存与 Republish 后从更新环境启动新任务。参考：https://developers.openai.com/codex/environments/cloud-environments 。已有任务会保留自己的状态。
- 站点发布后，还需允许站点工具返回的实际主机名，供无人值守写入/读回。
- 如果 MCP 需要认证，通过 Cloud 密钥配置 `TDX_MCP_TOKEN`，不提交密钥到仓库。
- 定时任务必须绑定本环境和可获取的持久源代码。创建任务前须验证源数据、写入权限、存档读回与后续任务源代码获取。

## 发现与映射

```bash
python3 -m review.runner discover --output work/mcp-tools.json
```

确认服务实际提供的 `tools/list`、输入 Schema、输出结构与覆盖范围后，填写 `config/review.json`。**不预设或猜测 MCP 工具名。**

`calendar` 需提供 `tool`、`arguments`、`date_path`、`open_path`。`date_path` 返回 `YYYY-MM-DD`，`open_path` 返回布尔值。参数中的 `$date` 会替换为运行日期。

每个 `datasets` 映射需提供 `tool`、`arguments`、`rows_path` 和 `fields`。路径为 JSON Pointer；字段映射可以为路径字符串，或 `{"path":"/字段","scale":倍数}`，或 `{"path":"/类型","values":{"源值":"规范值"}}`。数值单位必须根据接口文档显式转换。整组行情若需多工具拼接、分页、分市场遍历或文本解析，应先编写专用适配器，不能把单页结果标记成全市场。

规范字段：`symbol`、`name`、`date`、`close`、`previous_close`、`amount`（元）、`volume`、`sector`、`nav`、`nav_date`、`spot`、`spot_date`、`previous_settlement`、`open_interest`、`underlying`、`expiry`、`strike`、`option_type`（call/put）、`implied_volatility`（小数）。

可选 `history` 为按交易日升序排列的 `date`/`close`/`amount` 数组，最后一日应与当前行情一致；需要显式约定复权/价格口径。

```bash
python3 -m review.runner run --date 2026-10-13 --output work/report.json
python3 -m unittest discover -s tests -v
```

## Sites 应用

`site/app/` 为 Vinext 应用的界面、API 与 R2 数据操作源代码。需要用 Sites 提供的 Vinext starter 初始化独立站点目录，再合并这些文件；删除 starter 的 `app/page.tsx`，保留 Sites 构建、身份与部署集成。站点尚未初始化时，此目录不能独立构建。

页面支持综合概览与四类资产切换、历史日报选择、行情搜索、趋势/板块/PCR图表、数据状态与 JSON 导出。页面不带模拟行情。

- `.openai/hosting.json` 声明 `r2: {"binding":"BUCKET"}`（以安装后的 Sites starter 绑定格式为准），D1 不使用。
- `GET /api/reports`：最新报告与日期列表。
- `GET /api/reports?date=YYYY-MM-DD`：指定日期最新版本。
- `POST /api/reports`：校验并写入报告，以日期、生成时间和哈希保存不可变版本，随后读回校验。
- `GET /api/workflow`：未来任务的非敏感运行规则。

**站点必须保持 owner-private。** 浏览器登录由 Sites 处理；无人值守更新通过 `get_site` 返回的 service access 访问。该访问不赋予用户身份或外部 App 授权。若改成公开站点，必须先为写入端增加独立授权；不能直接放开现有写入路径。

`python3 -m review.publish` 接收隐藏 stdin 的 JSON 对象，包含 `url`、`token`、`report_path`。Token 仅来自该站点 `get_site` 的 `siwc_bypass_bearer_token`，用 `OAI-Sites-Authorization` 头发送，只传给该 Site，不保存或写入定时任务提示词。

## 联调后的定时任务

北京时间（Asia/Shanghai）18:00，周一至周五执行。先查询交易日历，休市跳过；读行情，校验与生成；发布并读回验证。失败保留历史报告，不编造替代数据。现阶段仅保存计划，没有创建或启用实际任务。
