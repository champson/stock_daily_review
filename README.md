# 收盘之后 · 股票每日复盘

行情优先使用 [a-stock-data](https://github.com/simonlin1212/a-stock-data)，通达信官方 MCP 作为备选。北京时间 18:00，周一至周五触发，使用完整交易日历跳过休市；不扩展到下单交易。

## 已验证的链路

已使用真实接口生成 2026-10-13 日报：8 个指数、5,560 只 A 股样本、11 只 ETF、16 个股指期货合约、676 个指数期权合约；行情日期与报告日期一致，校验剔除 0 条。主源版本固定为 `29b07d2e37005b9fd568bca1262f6e572b5f9407`（V3.10.2），选用的发布代码与 Apache-2.0 许可保存在 `review/vendor/`。

`a-stock-data` 是含 Python 代码的技能文件，并非远程 MCP。当前接入它的腾讯指数历史、通达信官网盘后包及中金所官方日行情。每项数据保留实际提供者、URL、抓取时间和上游版本。通达信官网盘后包是该工具包的内部接口，通达信 MCP 是单独的备选通道。

深交所整月日历曾返回 503，随后实际日报运行已恢复并成功校验；备选采用通达信官网完整年度休市表，必须同时覆盖沪深北及中金所、包含全部主要节假日。未发布或不完整的年度会失败，不按普通工作日猜测开市。

站点 ID 保存在 `site/.openai/hosting.json`，保持 owner-private，R2 绑定为 `BUCKET`。部署与任务的实时状态以 `get_site` 为准。独立 Vinext 目录为 `/workspace/sites/stock-closing-review`；目录缺失时使用该 Site 的 source helper 恢复，不能重复创建 Site。

## 当前覆盖限制

| 模块 | 已接入 | 仍缺失或限制 |
| --- | --- | --- |
| 指数 | 八个宽基收盘、前收盘、成交额、MA5/20/60 | 没有历史成交额，不计算五日成交额比 |
| A 股 | 盘后包按 A 股号段筛选的有成交证券、涨跌样本和成交额 | 未核对上市名册，不标记全市场；行业分类和板块强弱尚未接入 |
| ETF | 配置的 11 只观察标的 | 不代表全部 ETF；同日 NAV 缺失，折溢价留空，不推断资金流 |
| 股指期货 | IF/IH/IC/IM 可用合约、前结算涨跌、同日现货基差、持仓 | 官方 CSV 与 XML 核对日期、收盘、成交量、持仓；成交额由万元转元 |
| 期权 | 中金所指数期权收盘、成交、持仓、行权价 | ETF 期权链、准确到期日和 IV 尚未验证；不计算缺少到期日的 PCR |

日报状态为 partial，页面明确显示缺失项。盘后包通常在收盘后数小时发布，18:00 若尚未发布则标记缺失，保留其他可用模块；所有模块缺失时失败，不发布空报告。

## 执行

```bash
python3 -m venv work/data-venv
work/data-venv/bin/python -m pip install -r requirements.txt
work/data-venv/bin/python -m review.runner run --date 2026-10-13 --output work/report.json
python3 -m unittest discover -s tests/python -v
node tests/site/report-validation.test.mjs
```

`config/review.json` 的 `source_order` 为 a_stock_data → tongdaxin_mcp。主源按模块失败时才调用有经过验证映射的备选；健康模块保留。主源健康时不初始化通达信 MCP，也不要求其 Key。

通达信 MCP URL 为用户确认的 `https://mcp.tdx.com.cn:3001/mcp`，仍只接受官方 `tdx.com.cn` HTTPS 域名。初始化和 tools/list 成功，唯一工具是 `tdx_wenda_quotes`，Schema 快照在 `config/mcp-discovery.json`；实际调用返回 MISSING_API_KEY。用户将配置 Cloud Secrets `TDX_MCP_TOKEN`；官方认证请求头和真实工具映射仍待确认，不猜测 Bearer 或工具名。获取 Key 参考 [官方说明](https://help.tdx.com.cn/tdxclaw/docs/markdown/tdxclaw-api-key-data.html)。

```bash
python3 -m review.runner discover --output work/mcp-tools.json
python3 -m review.runner probe --tool tdx_wenda_quotes --arguments work/probe-arguments.json --output work/mcp-probe.json
```

probe 参数文件只含查询问题和分页等业务字段，不能包含密钥。

## 站点与无人值守更新

页面支持资产切换、历史日期、搜索、图表、JSON 导出和数据状态；不包含模拟行情。GET /api/reports 返回日报与历史日期；POST 校验有效行情，按日期、生成时间与哈希保存不可变版本并读回；GET /api/workflow 提供后续任务的非敏感运行约定。

`review.publish` 通过隐藏 stdin 接收 url、token、report_path。Token 只从本站 get_site 的 siwc_bypass_bearer_token 取得，用 OAI-Sites-Authorization 发送；拒绝所有重定向，凭据不落盘。写入后对整份报告读回比对。代码变化才重新部署，日常只更新 R2。

后续任务从本站 source repository 获取固定源码及 requirements.txt，按 CLOUD_RUN.txt 执行。只有真实源、无人值守写入回读及源码恢复验证完成后，才检查关联 automations 并创建缺失的 18:00 任务。任务启用状态以 native 工具为准。
