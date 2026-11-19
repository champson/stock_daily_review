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

大虾皮个人中心的 Token 通过运行环境的 `DAXIAPI_TOKEN` 提供。Cloud HTTP 密钥可以由代理替换环境中的占位值，应用只需按正常 Bearer 方式发送，不读取或打印真实密钥。默认请求使用第一方发行版 `daxiapi-cli@3.3.1` 的 VCP 路径，保留环境 HTTPS 代理与 TLS 校验。

2026-11-19 已实测 HTTP 200、`errCode=0`，`data` 实际为 Markdown 围栏中的 TOON 表格，含 80 条日期为 `11-18` 的记录及“展示股票数据有限”尾注。解析器核验行数、字段数、唯一列名、证券代码和一致的数据日期，支持引号内逗号与 JSON 转义；未知文本、截断表格、混合日期和无日期空表均失败。接口只提供展示子集，`candidate_pool_complete=false`。

TOON 的 `date` 没有年份，仅能在获取日前十天内结合明确的报告日核验年月日；该窗口是本地防误用限制，不是服务端历史查询能力。保存 `publisher_date_raw`、`data_date` 和 `fetched_at`，`snapshot_date=null`、`historical_membership_verified=false`。数据日期不冒充 VCP 信号日或名单生成日；不能将今天取得的名单用于证明过去的成员资格。旧的显式 `data.date/rows` JSON 合约保留兼容，但未宣称其为已观察到的在线格式。

```bash
python3 -m review.sepa candidates --date YYYY-MM-DD \
  --output-dir work/sepa/private-probe
```

成功时控制台仅输出日期、状态和条数。已验证的候选原始载荷保存在指定的私有目录。401/403、超时、响应结构不匹配或日期过期都是失败；目前 TOON 空表没有足够的日期证明，不能记作成功的空名单。真实候选和报告保留在用户私有环境。[第一方 CLI 指南](https://daxiapi.com/article/help/2026-06-18-daxiapi-cli-guide/)

## 每日入口

```bash
python3 -m pip install -r requirements-sepa.txt
python3 -m review.sepa.daily --output-root work/sepa/archive \
  --calendar-archive-dir work/sepa/calendar
```

每日运行使用北京时间，未传 `--date` 时取北京时间当天。核验官方日历后，休市直接跳过。交易日获取大虾皮候选，然后进入独立的市场 RS、趋势模板和 VCP 流程。成功报告按日期保存在私有目录，重复执行同一成功日自动跳过。失败诊断与有效历史分开保存，恢复后可重跑。候选非空且市场 RS 未达到覆盖门槛时，正式日任务会留下失败诊断。`--allow-unranked` 仅用于独立目录中的研究运行。

## 与原每日复盘一起运行

大虾皮[盘后 VCP 页面](https://daxiapi.com/xingtai/vcp)明确说明“数据在交易日下午5点左右更新”（2026-11-19核验）；这不是 API 准点保证。原来的“每日股票收盘复盘”任务保留北京时间18:00，统一执行 `CLOUD_RUN.txt` 与 `python -m review.daily`。独立的 SEPA 任务已暂停，不再创建第二个任务。生产源码随原站点通过 GitHub main 发布。

联合入口先生成市场复盘，再生成或复用同日 SEPA 归档。SEPA 失败时，保留有效的市场模块，并写入明确的同日不可用状态；不会把过期名单或昨天的分析改成今天。官方休市直接跳过。新月份可通过 A 股 MCP 取得完整官方月历并归档到 `work/sepa/calendar`。

网站 `/sepa` 和首页使用同一份报告、同一日期选择器与历史列表；`/api/sepa` 提供只读的 SEPA 字段。导出的网页数据包含独立趋势/RS/VCP计算、最后120根腾讯前复权日线和均线，成交量统一为股。大虾皮原始响应、私有路径和完整事件重放日志不进入网页。网页数据仍通过原 `/api/reports` 的服务写入鉴权和不可变R2版本保存，日常无需部署代码。旧报告没有SEPA字段时明确显示尚未包含分析。

网页近期筛选使用同份日报 `market_data.calendar_basis.bias25_dates` 中已核验的官方交易日，取含报告日在内的最后5日；不按自然日、星期规则或个股K线根数推算。重点关注仅包含仍有效的 A 类待突破，以及首次收盘突破位于窗口内、且已放量确认的 B 类；后续放量确认和回踩不重置首次突破日期。日历缺失时不猜测突破新旧，待突破及全部候选仍可查看。`/api/sepa` 的 `focus` 是派生展示字段，原始 `report` 不改写。每段收缩展示高点日到低点日、低点确认日，并在图表中标出可见区间。

运行环境需能读取 `DAXIAPI_TOKEN` 并访问 `daxiapi.com`、`www.tdx.com.cn`、`web.ifzq.gtimg.cn`；不把HTTP密钥复制到普通环境变量。私有 `work/sepa/archive` 保留完整研究证据；新建Cloud实例不代表这些私有文件已经迁移，网页上的派生分析历史则在原Site的R2中持久保存。

2026-11-19 在已配置 HTTP 密钥的 Cloud 中完成 2026-11-18 真实日报联调：接口的 80 条候选全部取得独立腾讯前复权日线，报告和 80 张图表成功生成；通达信 RS 可比样本为 5,106 / 5,216（97.89%），满足 90% / 1,000 个样本的配置门槛。166 个产物的大小、SHA-256 和私有权限均读回核验，同日重新启动 CLI 返回 `already_completed`。五个 MCP 工具经新启动的 stdio 服务验收，其中归档报告与磁盘内容一致，单股重放与日报的趋势/VCP 结果一致。212 项 Python 测试通过。此记录证明当前实例的运行和读回，不代表已经验证未来计划任务的执行或跨实例历史迁移。

## 选股 MCP 服务

安装 `requirements-sepa.txt` 后，从仓库根目录启动：

```bash
SEPA_ARCHIVE_ROOT=/absolute/private/archive python3 -m review.sepa.mcp_server
```

客户端通过 stdio 调用 `vcp_candidates`、`sepa_screen`、`vcp_analyze`、`sepa_rank`、`sepa_report`。报告读取只在服务端配置的私有归档根内进行。实时工具与 CLI 共用同一来源和计算逻辑。`--transport streamable-http` 适用于本机调试，只绑定回环地址。Cloud App 若需远程连接，须另外部署受访问控制的 HTTPS 服务并接入该客户端。

VCP 引擎版本2冻结尚在枢轴上方的突破形态，避免同日新确认低点替换旧枢轴而抹掉突破；确认突破后，新整理只能使用该次突破之后的收缩。网页保留首次价格突破、放量确认及历史形态标识。旧版成功日任务在再次运行时，先校验完整归档和行情哈希，再离线重放到 `work/sepa/archive/.revisions/vcp-2/YYYY-MM-DD`，原始归档不覆盖，行情日期、获取时间和候选来源保持原样。已生成的同版本重放继续幂等复用。
