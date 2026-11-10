# 收盘之后 · 股票每日复盘

行情优先使用 [a-stock-data](https://github.com/simonlin1212/a-stock-data)，通达信官方 MCP 作为备选。北京时间 18:00，周一至周五触发，使用完整交易日历跳过休市；不扩展到下单交易。

## 已验证的链路

已使用真实接口生成 2026-10-13 日报：8 个指数、5,560 只 A 股样本、11 只 ETF、16 个股指期货合约、676 个指数期权合约；行情日期与报告日期一致，校验剔除 0 条。主源版本固定为 `29b07d2e37005b9fd568bca1262f6e572b5f9407`（V3.10.2），选用的发布代码与 Apache-2.0 许可保存在 `review/vendor/`。

`a-stock-data` 是含 Python 代码的技能文件，并非远程 MCP。当前接入它的腾讯指数历史、通达信官网盘后包及中金所官方日行情。每项数据保留实际提供者、URL、抓取时间和上游版本。通达信官网盘后包是该工具包的内部接口，通达信 MCP 是单独的备选通道。

深交所整月日历曾返回 503，随后实际日报运行已恢复并成功校验；备选采用通达信官网完整年度休市表，必须同时覆盖沪深北及中金所、包含全部主要节假日。未发布或不完整的年度会失败，不按普通工作日猜测开市。

站点 ID 保存在根目录 `.openai/hosting.json`，R2 绑定为 `BUCKET`。当前网站可公开阅读，报告写入使用独立服务端校验。访问受众与任务实时状态以 `get_site` 为准，更新时保留现有设置。

## 当前覆盖限制

| 模块 | 已接入 | 仍缺失或限制 |
| --- | --- | --- |
| 指数 | 八个宽基收盘、前收盘、成交额、MA5/20/60 | 没有历史成交额，不计算五日成交额比 |
| A 股 | 盘后包按 A 股号段筛选的有成交证券、涨跌样本和成交额；页面仅展示汇总，不展示逐只行情列表 | 未核对上市名册，不标记全市场；个股行业分类尚未接入 |
| 行业强弱 | 10 个中证全指行业观察指数，按报告日及官方前交易日的指数收盘计算涨跌幅 | 金融与地产合并展示，非完整行业或概念板块全集，不是成分股等权收益；旧报告缺失数据不回填 |
| ETF | 配置的 11 只观察标的 | 不代表全部 ETF；同日 NAV 缺失，折溢价留空，不推断资金流 |
| 股指期货 | IF/IH/IC/IM 可用合约、前结算涨跌、同日现货基差、持仓 | 官方 CSV 与 XML 核对日期、收盘、成交量、持仓；成交额由万元转元 |
| 期权 | 中金所指数期权收盘、成交、持仓、行权价 | ETF 期权链、准确到期日和 IV 尚未验证；不计算缺少到期日的 PCR |

新增“市场统计总览”包含年/月/周/日涨跌分布、均值、中位数、转债及正股统计、主要指数与BIAS25，排除个人实盘。各周期独立计算并显示覆盖样本数；当前个股长期复权历史未接入，相应列留空。指数历史扩展至上一年12月，并增加中证2000、中证全指及北证50观察代码；北证历史缺失时仅保留可验证的当日行情。转债使用固定V3.10.4摘取实现取得债股映射，并使用报告日盘后报价，不混用当前价格回填历史。详见[统计口径](docs/market-matrix.md)、[数据覆盖](docs/market-matrix-data.md)和[特色指数来源备选](docs/index-source-options.md)。

日报状态为 partial，页面明确显示缺失项。盘后包通常在收盘后数小时发布，18:00 若尚未发布则标记缺失，保留其他可用模块；所有模块缺失时失败，不发布空报告。

板块强弱曾因个股记录不含行业分类字段而始终为空。现在通过可选 `market_data.sector_indices` 单独采集有明确日期的行业指数日线，不依赖逐股分类；失败显示原因，不将当前快照套用至历史日期。详见[行业强弱口径](docs/sector-strength.md)。

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

页面支持资产切换、历史日期、搜索、图表、JSON 导出和数据状态；不包含模拟行情。GET /api/reports 返回原始日报、历史日期及派生字段market_matrix；POST 校验有效行情，按日期、生成时间与哈希保存不可变版本并读回；GET /api/workflow 提供后续任务的非敏感运行约定。

`review.publish` 通过隐藏 stdin 接收 url、token、report_path。Token 只从本站 get_site 的 siwc_bypass_bearer_token 取得，用 OAI-Sites-Authorization 发送；拒绝所有重定向，凭据不落盘。写入后对整份报告读回比对。代码变化才重新部署，日常只更新 R2。

后续任务从本站已部署版本恢复固定源码及 requirements.txt，按 CLOUD_RUN.txt 执行。现有18:00任务保留，不重复创建。任务启用状态以 native 工具为准。


## GitHub 与 Sites 发布

GitHub `main` 是唯一维护分支。线上v1的完整树已由PR #2导入，保留双方历史；旧PR #1只保留作历史参考。不要直接在Sites源仓库另行修改源码。

1. 在GitHub提交改动并合并到`main`。Actions执行离线测试、锁定依赖安装和生产构建，生成`site-release-<完整SHA>`制品，内含`site-release.tar`和`release.json`。详见[CI说明](docs/CI.md)。
2. 在可用Cloud环境中取回该GitHub提交及其成功的构建制品，确认`release.json`中的项目ID、完整SHA、tree和tar SHA-256。也可在同一清洁提交本地执行：

   ```bash
   npm run install:ci
   node scripts/prepare-site-release.mjs --output /absolute/path/site-release.tar
   ```

3. 用原Site的`create_source_repository_write_credential`取得短期访问，通过隐藏stdin运行：

   ```bash
   node scripts/sync-sites-source.mjs --archive /absolute/path/site-release.tar --release /absolute/path/release.json
   ```

   stdin格式为`{"credential": 原样返回的凭据对象}`。脚本只接受当前GitHub main和匹配项目/制品；源库存在未同步的新提交时拒绝推送；不force push、不落盘凭据。
4. 使用脚本返回的精确`project_id`、`commit_sha`、`archive`调用原生`save_site_version`。按`get_site`实际受众选择部署工具。GitHub Actions仅构建受检制品，当前不持有Sites凭据，也不声称一次GitHub push就完成生产发布；最终发布通过本会话的Sites原生工具执行。
5. 发布成功后验证：

   ```bash
   python3 scripts/verify_live_release.py --url https://stock-closing-review.rong-yang73.chatgpt.site --release /absolute/path/release.json --with-service-access
   ```

   按隐藏stdin提示传入`{"token": get_site的service token}`。验证`/api/build-info`与GitHub完整SHA/tree相等、页面及历史报告可读、匿名写入返回401、服务身份能进入Schema校验；用空对象检查，不创建日报记录。原生Site保存版本同时记录同一提交，GitHub与Sites源库因此使用同一个SHA。

`GET /api/build-info`返回公开的提交、tree、项目ID和构建时间，用于排查代码漂移。运行中密钥、日报数据和工作目录不进入GitHub或发布制品。

生产secret `REVIEW_WRITE_TOKEN` 存在Sites环境设置中，值使用`review.publish.derive_write_token`由本站service token派生；不写入源码、CI或浏览器。`review.publish`自动派生并附加应用写入头。service token轮换时，重新配置派生secret并部署以应用新环境版本。公开读接口不暴露该凭据。

回滚使用Sites已保存版本，并核对其源码及运行环境。v1仅保留作迁移基线，其写入路由依赖旧的私有访问假设，不应作为公开站点的长期回滚目标。
