"""a-stock-data 的复盘适配器；保留上游源、日期、金额单位和覆盖限制。"""
import functools
import calendar as month_calendar
import json
import math
import re
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .vendor import astock as api
from .vendor import convertibles as cb_api
from .calendar import official_holiday_calendar
from .index_history_fallbacks import INDEX_MARKETS, fetch_index_history, fetch_jsl_equal_weight

REPOSITORY = "https://github.com/simonlin1212/a-stock-data"
REVISION = "29b07d2e37005b9fd568bca1262f6e572b5f9407"
CB_REVISION = "f21534946c42be3ad048ac938eecb1bad84cb8ab"
HOLIDAYS_URL = "https://www.tdx.com.cn/url/holiday/"
# 同日的期货和期权共用官方 CSV/XML，避免重复下载及不同版本混用。
api._cffex_daily_table = functools.lru_cache(maxsize=4)(api._cffex_daily_table)



def records(frame):
    # pandas 的缺失值不能作为 NaN 发布；JSON 往返保持标准 Python 类型。
    return json.loads(frame.to_json(orient="records", force_ascii=False, double_precision=10))


def normalize_package(row):
    return {"symbol": row["market"] + row["code"], "name": row["name"], "date": row["date"],
            "close": row["close"], "previous_close": row["prev_close"], "amount": row["amount"],
            "volume": row["volume"], "provider": "tdx_daily_package"}


class AStockData:
    def __init__(self, settings):
        self.settings = settings
        self.cache = {}

    def package(self, day):
        key = ("package", day)
        if key not in self.cache:
            self.cache[key] = records(api.tdx_daily_package(day))
        return self.cache[key]

    @staticmethod
    def _validated_month(rows, year, month):
        expected = {date(year, month, n).isoformat()
                    for n in range(1, month_calendar.monthrange(year, month)[1] + 1)}
        if (not isinstance(rows, list) or len(rows) != len(expected)
                or any(not isinstance(r, dict) or not isinstance(r.get("is_open"), bool)
                       or r.get("date") not in expected for r in rows)
                or {r["date"] for r in rows} != expected):
            raise ValueError("官方月历日期缺失、重复、错月或开市标记异常")
        return sorted(rows, key=lambda r: r["date"])

    def calendar_month(self, year, month):
        """缓存完整且核验过的官方自然月，不用工作日规则补缺日。"""
        key = ("calendar_month", year, month)
        error_key = ("calendar_month_error", year, month)
        if key in self.cache:
            return self.cache[key]
        if error_key in self.cache:
            raise ValueError(self.cache[error_key])
        try:
            try:
                rows = self._validated_month(records(api.trading_calendar(year, month)), year, month)
                source = "a-stock-data / 深交所"
                url = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
                warning = None
            except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                if not self.settings.get("tdx_official_calendar_fallback"):
                    raise
                try:
                    html_key = ("official_holidays_html",)
                    if html_key not in self.cache:
                        with urllib.request.urlopen(HOLIDAYS_URL, timeout=30) as response:
                            self.cache[html_key] = response.read(500_000).decode("gb18030")
                    rows = self._validated_month(
                        official_holiday_calendar(self.cache[html_key], year, month), year, month)
                    source, url = "通达信官方休市表（备选）", HOLIDAYS_URL
                    warning = f"深交所月历不可用，已使用完整年度官方休市表：{type(exc).__name__}"
                except (RuntimeError, ValueError, KeyError, TypeError, OSError) as fallback_exc:
                    directory = self.settings.get("calendar_archive_directory")
                    if not directory:
                        raise
                    if not isinstance(directory, str):
                        raise ValueError("官方月历归档目录必须是字符串")

                    def unique_keys(pairs):
                        result = {}
                        for name, value in pairs:
                            if name in result:
                                raise ValueError("官方月历归档包含重复 JSON 字段")
                            result[name] = value
                        return result

                    path = Path(directory) / f"{year:04d}-{month:02d}.json"
                    with path.open("rb") as stream:
                        payload = stream.read(131_073)
                    if len(payload) > 131_072:
                        raise ValueError("官方月历归档超过大小限制")
                    archive = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_keys)
                    fields = {"version", "calendar_month", "source", "source_url", "fetched_at", "rows"}
                    expected_url = ("https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
                                    f"?month={year:04d}-{month:02d}")
                    if (not isinstance(archive, dict) or set(archive) != fields
                            or type(archive.get("version")) is not int or archive["version"] != 1
                            or archive.get("calendar_month") != f"{year:04d}-{month:02d}"
                            or archive.get("source") != "深交所（官方月历归档）"
                            or archive.get("source_url") != expected_url
                            or not isinstance(archive.get("fetched_at"), str)):
                        raise ValueError("官方月历归档元数据未通过 schema 核验")
                    fetched = datetime.fromisoformat(archive["fetched_at"])
                    if fetched.tzinfo is None or fetched.utcoffset() is None:
                        raise ValueError("官方月历归档获取时间必须包含时区")
                    if (not isinstance(archive["rows"], list)
                            or any(not isinstance(row, dict) or set(row) != {"date", "is_open"}
                                   for row in archive["rows"])):
                        raise ValueError("官方月历归档逐日字段未通过 schema 核验")
                    rows = self._validated_month(archive["rows"], year, month)
                    result = {"rows": rows, "source": archive["source"],
                              "source_url": archive["source_url"], "fetched_at": archive["fetched_at"],
                              "warning": (f"深交所在线月历及通达信休市表不可用，使用已核验官方月历归档；"
                                          f"在线错误：{type(exc).__name__} / {type(fallback_exc).__name__}。")}
                    self.cache[key] = result
                    return result
            result = {"rows": rows, "source": source, "source_url": url, "warning": warning,
                      "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}
            self.cache[key] = result
            return result
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            self.cache[error_key] = f"{year:04d}-{month:02d} 官方月历不可用：{type(exc).__name__}：{exc}"
            raise ValueError(self.cache[error_key]) from None

    def calendar(self, day):
        parsed = date.fromisoformat(day)
        month = self.calendar_month(parsed.year, parsed.month)
        matched = next(r for r in month["rows"] if r["date"] == day)
        return {"date": day, "open": matched["is_open"],
                **{k: month[k] for k in ("source", "source_url", "warning", "fetched_at")}}

    def calendar_basis(self, day):
        """证明区间基准日及最近25个交易日；任一缺失都保留为未知。"""
        parsed = date.fromisoformat(day)
        used, issues = {}, []

        def is_open(cursor):
            key = (cursor.year, cursor.month)
            if key not in used:
                used[key] = self.calendar_month(*key)
            return next(r["is_open"] for r in used[key]["rows"] if r["date"] == cursor.isoformat())

        cutoffs = {"year": date(parsed.year, 1, 1), "month": parsed.replace(day=1),
                   "week": parsed - timedelta(days=parsed.weekday())}
        anchors = dict.fromkeys(cutoffs)
        for period, cutoff in cutoffs.items():
            try:
                for offset in range(1, 21):
                    cursor = cutoff - timedelta(days=offset)
                    if is_open(cursor):
                        anchors[period] = cursor.isoformat()
                        break
                if anchors[period] is None:
                    issues.append(f"{period} 基准日前20个自然日未找到经官方日历确认的开市日。")
            except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                issues.append(f"{period} 基准日未核验：{exc}")

        bias_dates = []
        try:
            if not is_open(parsed):
                raise ValueError("报告日不是官方开市日")
            for offset in range(65):
                cursor = parsed - timedelta(days=offset)
                if is_open(cursor):
                    bias_dates.append(cursor.isoformat())
                if len(bias_dates) == 25:
                    break
            if len(bias_dates) != 25:
                raise ValueError("最近65个自然日不足25个经官方确认的交易日")
            bias_dates.reverse()
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            bias_dates = []
            issues.append(f"BIAS25 交易日序列未核验：{exc}")
        sources = [{"dataset": "calendar_basis", "calendar_month": f"{year:04d}-{month:02d}",
                    **{k: value[k] for k in ("source", "source_url", "fetched_at")}}
                   for (year, month), value in sorted(used.items())]
        issues.extend(value["warning"] for value in used.values() if value["warning"])
        return {"date": day, "anchors": anchors, "bias25_dates": bias_dates,
                "observations": list(dict.fromkeys(issues)), "provenance": sources}

    def collect(self, kind, day):
        return getattr(self, kind)(day)

    def stocks(self, day):
        package = self.package(day)
        prefixes = {"sh": ("600", "601", "603", "605", "688", "689"),
                    "sz": ("000", "001", "002", "003", "300", "301"), "bj": ("920",)}
        rows = [normalize_package(r) for r in package if r["code"].startswith(prefixes[r["market"]])
                and r["volume"] > 0 and r["amount"] > 0]
        # Keep the same accepted current universe that build_report() will use;
        # a malformed auxiliary field must not re-enter via period prices.
        from .analysis import validate_rows
        self.cache[("stocks", day)] = validate_rows(rows, day, "stocks")[0]
        return {"rows": rows, "complete": False, "partial": True,
                "observations": ["样本来自盘后包按 A 股号段筛选的有成交证券；未核对交易所上市名册，不标记为全市场。",
                                 "个股行业分类尚未接入；行业强弱另按中证全指行业观察指数展示，不是个股等权分组。"],
                "provenance": self.provenance("stocks", package)}

    def indices(self, day):
        rows, issues, sources = [], [], []
        try:
            snapshot = {(r["market"] + r["code"]): r for r in self.package(day)}
        except (RuntimeError, ValueError, OSError) as exc:
            snapshot = {}
            issues.append(f"盘后包成交额未取得：{type(exc).__name__}")
        for item in self.settings["indices"]:
            symbol = item["symbol"]
            quote = snapshot.get(item.get("package_symbol", symbol))
            try:
                start = f"{date.fromisoformat(day).year - 1}-12-01"
                provider, price_basis, volume_unit = "tencent", "腾讯不复权指数日线", "手"
                try:
                    history = [r for r in records(api.tencent_kline(symbol, adjust="", start=start, end=day, count=320)) if r["date"] <= day]
                    if not history or history[-1]["date"] != day:
                        raise ValueError("指数历史未覆盖请求日期")
                except (RuntimeError, ValueError, OSError):
                    if symbol not in INDEX_MARKETS:
                        raise
                    fallback = fetch_index_history(symbol, start, day)
                    history = fallback["rows"]
                    provider, price_basis, volume_unit = "eastmoney", fallback["price_basis"], fallback["volume_unit"]
                close = history[-1]["close"]
                if quote and abs(quote["close"] - close) > 0.011:
                    raise ValueError("指数历史与盘后包收盘价不一致")
                previous_close = quote["prev_close"] if quote else None
                if not quote:
                    try:
                        if not self.calendar(day)["open"]:
                            raise ValueError("报告日未获官方日历确认为开市日")
                        previous = None
                        for offset in range(1, 21):
                            candidate = (date.fromisoformat(day) - timedelta(days=offset)).isoformat()
                            calendar = self.calendar(candidate)
                            if calendar["open"]:
                                previous = calendar
                                break
                        if previous is None:
                            raise ValueError("未取得官方前交易日")
                        previous_close = next((r["close"] for r in history if r["date"] == previous["date"]), None)
                        sources.append({"dataset": "indices_previous_calendar", **previous})
                        if previous_close is None:
                            raise ValueError("历史缺少官方前交易日 " + previous["date"])
                    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                        issues.append(f"{symbol} 当日收益留空：{exc}")
                row = {"symbol": symbol, "name": item["name"], "date": day, "close": close,
                       "previous_close": previous_close,
                       "amount": quote["amount"] if quote else None, "provider": provider,
                       "price_basis": price_basis, "volume_unit": volume_unit,
                       "history": [{"date": r["date"], "close": r["close"],
                                    "volume": r.get("volume") if self._nonnegative(r.get("volume")) else None}
                                   for r in history]}
                rows.append(row)
                sources.extend(self.provenance("indices", history) if provider == "tencent"
                               else self.external_provenance("indices", history))
            except (RuntimeError, ValueError, OSError) as exc:
                issues.append(f"{symbol}：{exc}")
                # 北交所腾讯历史接口明确不支持；仅有同日盘后报价时保留日涨跌幅。
                # 其他指数出现跨源价差时不回退，以免掩盖相互矛盾的收盘价。
                if symbol.startswith("bj") and quote and quote.get("date") == day:
                    row = normalize_package(quote)
                    row.update(symbol=symbol, name=item["name"], price_basis="通达信当日指数收盘；历史缺失")
                    rows.append(row)
                    sources.extend(self.provenance("indices", [quote]))
        self.cache[("indices", day)] = rows
        return {"rows": rows, "complete": False, "partial": bool(issues),
                "observations": issues + ["指数均线使用同口径历史收盘；历史成交额未取得，不计算五日成交额比。"],
                "provenance": sources}

    def market_data(self, day):
        """附加统计源不改变五个主模块；失败留空，不能阻止已有复盘。"""
        try:
            convertibles = self.convertibles(day)
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            convertibles = {"status": "unavailable", "sample_size": 0, "rows": [],
                            "universe_complete": False, "provenance": [],
                            "observations": [f"可转债及正股映射未取得：{type(exc).__name__}：{exc}"]}
        try:
            basis = self.calendar_basis(day)
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            basis = {"date": day, "anchors": {"year": None, "month": None, "week": None},
                     "bias25_dates": [], "provenance": [],
                     "observations": [f"区间统计交易日历未取得：{type(exc).__name__}：{exc}"]}
        try:
            sectors = self.sector_indices(day)
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            configured = self.settings.get("sector_indices", [])
            sectors = {"status": "unavailable", "date": day, "label": "中证全指行业观察指数",
                       "configured_count": len(configured) if isinstance(configured, list) else 0,
                       "sample_size": 0, "previous_trading_date": None,
                       "universe_complete": False, "rows": [], "provenance": [],
                       "observations": [f"行业观察指数未取得：{type(exc).__name__}：{exc}"]}
        try:
            periods = self.period_prices(day, basis, convertibles)
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            from .period_data import empty_period_prices
            periods = empty_period_prices(day, basis, f"跨期价格未取得：{type(exc).__name__}：{exc}")
        return {"convertibles": convertibles, "calendar_basis": basis, "sector_indices": sectors,
                "period_prices": periods, "special_indices": self.special_indices(day, basis)}

    def special_indices(self, day, basis):
        """具名发布者指数；不把样本收益的平均数冒充指数收益。"""
        result = {"date": day, "status": "unavailable", "sample_size": 0,
                  "rows": [], "observations": [], "provenance": [], "errors": []}
        if "jisilu_convertible_equal_weight" not in self.settings.get("special_indices", []):
            return result
        key = "special_convertible_equal_weight"
        try:
            start = f"{date.fromisoformat(day).year - 1}-12-01"
            source = fetch_jsl_equal_weight(start, day)
            history = source["rows"]
            # Require the preceding official open day for daily returns rather
            # than treating a gap in the source as one trading day.
            verified = basis.get("bias25_dates", [])
            previous = verified[-2] if len(verified) >= 2 and verified[-1] == day else None
            previous_close = next((r["close"] for r in history if r["date"] == previous), None)
            row = {"matrix_key": key, "symbol": "jsl_cb_equal_weight", "name": source["name"],
                   "date": day, "close": history[-1]["close"], "previous_close": previous_close,
                   "price_basis": source["price_basis"], "provider": "jisilu",
                   "history": [{"date": r["date"], "close": r["close"]} for r in history]}
            result.update(status="partial", sample_size=1, rows=[row],
                          provenance=self.external_provenance("special_indices", history))
            result["observations"].append("使用集思录发布的转债等权指数点位；不是同花顺正股等权或转债样本均值。")
            if previous_close is None:
                result["observations"].append("前一官方交易日点位未核验，当日收益留空。")
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            message = f"集思录转债等权未取得：{type(exc).__name__}：{exc}"
            result["observations"].append(message)
            result["errors"].append({"matrix_key": key, "message": message, "category": "source_failed"})
        return result

    def period_prices(self, day, basis, convertibles):
        from .period_data import collect_period_prices
        # The main collector already called stocks(); reuse exactly those current
        # candidates rather than fetching or fabricating another universe.
        stocks = self.cache.get(("stocks", day), [])
        bonds = convertibles.get("rows", []) if isinstance(convertibles, dict) else []
        return collect_period_prices(self, day, basis, stocks, bonds)

    def sector_indices(self, day):
        """按报告日及官方前交易日收盘比较行业指数，不依赖个股分类。"""
        parsed = date.fromisoformat(day)
        configured = self.settings.get("sector_indices", [])
        if not isinstance(configured, list):
            raise ValueError("行业观察指数配置必须为数组")
        symbols = set()
        for item in configured:
            if (not isinstance(item, dict) or not isinstance(item.get("symbol"), str)
                    or not re.fullmatch(r"sh\d{6}", item["symbol"])
                    or not isinstance(item.get("name"), str) or not item["name"]
                    or item["symbol"] in symbols):
                raise ValueError("行业观察指数配置代码、名称无效或重复")
            symbols.add(item["symbol"])
        result = {"status": "unavailable", "date": day, "label": "中证全指行业观察指数",
                  "configured_count": len(configured), "sample_size": 0, "universe_complete": False,
                  "previous_trading_date": None, "rows": [], "provenance": [],
                  "observations": ["使用配置的中证全指行业指数观察池，金融地产为合并指数；不代表现行全部一级行业或概念板块。",
                                   "涨跌幅为行业指数收盘相对官方前交易日收盘的价格涨幅，不是个股样本等权平均。"]}
        if not configured:
            result["observations"].append("行业指数观察池尚未配置。")
            return result
        try:
            current_calendar = self.calendar(day)
            if not current_calendar["open"]:
                raise ValueError("行业指数报告日未获官方日历确认为开市日")
            previous = None
            for offset in range(1, 21):
                candidate = (parsed - timedelta(days=offset)).isoformat()
                calendar = self.calendar(candidate)
                if calendar["open"]:
                    previous = calendar
                    break
            if previous is None:
                raise ValueError("前20个自然日内未找到官方日历确认的前交易日")
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            result["observations"].append(f"行业指数交易日历未取得：{type(exc).__name__}：{exc}")
            return result
        previous_date = previous["date"]
        result["previous_trading_date"] = previous_date
        for calendar in (current_calendar, previous):
            result["provenance"].append({"dataset": "sector_indices_calendar", "date": calendar["date"],
                                         **{k: calendar[k] for k in ("source", "source_url", "fetched_at")}})
        start = (parsed - timedelta(days=30)).isoformat()
        for item in configured:
            symbol = item["symbol"]
            try:
                history = records(api.tencent_kline(symbol, adjust="", start=start, end=day, count=40))
                last_date = ""
                for quote in history:
                    stamp = quote.get("date")
                    if (not isinstance(stamp, str) or date.fromisoformat(stamp).isoformat() != stamp
                            or stamp < start or stamp > day or stamp <= last_date
                            or not self._positive(quote.get("close"))):
                        raise ValueError("行业指数历史日期越界、乱序、重复或收盘价无效")
                    last_date = stamp
                if not history or last_date != day:
                    raise ValueError("行业指数历史没有覆盖报告日")
                anchor = next((r for r in history if r["date"] == previous_date), None)
                if anchor is None:
                    raise ValueError(f"行业指数缺少官方前交易日 {previous_date} 的收盘价")
                close, previous_close = history[-1]["close"], anchor["close"]
                change = (close / previous_close - 1) * 100
                if not math.isfinite(change):
                    raise ValueError("行业指数涨跌幅超出有效数值范围")
                result["provenance"].extend(self.provenance("sector_indices", history))
                result["rows"].append({"symbol": symbol, "name": item["name"], "date": day,
                                       "close": close, "previous_close": previous_close,
                                       "previous_date": previous_date, "change_pct": change,
                                       "provider": "tencent", "price_basis": "腾讯不复权行业指数日线"})
            except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                result["observations"].append(f"{symbol}：{exc}")
        result["rows"].sort(key=lambda row: row["change_pct"], reverse=True)
        result["sample_size"] = len(result["rows"])
        if result["sample_size"]:
            result["status"] = "available" if result["sample_size"] == len(configured) else "partial"
        result["observations"].append(f"报告日 {day}，前交易日 {previous_date}；取得 {result['sample_size']} / {len(configured)} 个观察指数。")
        return result

    def convertibles(self, day):
        # 映射主表可能有当前价格，历史回填只能用报告日的报价包。
        mapping = records(cb_api.convertible_bonds(include_delisted=True))
        package = {(r["market"] + r["code"]): r for r in self.package(day)}
        rows, eligible, unmatched, uncertain = [], 0, 0, 0
        seen = set()
        for item in mapping:
            code, stock = item.get("code"), item.get("stock_code")
            if not isinstance(code, str) or not re.fullmatch(r"(?:11|12)\d{4}", code):
                continue
            listing, delist = item.get("listing_date"), item.get("delist_date")
            if (not listing or item.get("status") == "unknown"
                    or (item.get("status") == "delisted" and not delist)):
                uncertain += 1
                continue
            # 不依赖主表按今天生成的 status；历史上已上市且尚未摘牌才纳入。
            if listing > day or (delist and delist <= day):
                continue
            if not isinstance(stock, str) or not re.fullmatch(r"(?:60[0135]|68[89]|00[0123]|30[01])\d{3}", stock):
                uncertain += 1
                continue
            symbol = ("sh" if code.startswith("11") else "sz") + code
            if symbol in seen:
                raise ValueError("可转债映射代码重复")
            seen.add(symbol)
            eligible += 1
            quote = package.get(symbol)
            if (not quote or quote.get("date") != day or not self._positive(quote.get("close"))
                    or not self._positive(quote.get("prev_close")) or not self._positive(quote.get("volume"))):
                unmatched += 1
                continue
            row = normalize_package(quote)
            row.update(underlying_symbol=("sh" if stock.startswith(("6", "9")) else "sz") + stock,
                       listing_date=listing, delist_date=delist, price_basis="通达信转债收盘报价（全价）；收益未另计已支付票息现金流及再投资")
            rows.append(row)
        source = self.provenance("convertibles", list(package.values()))
        source.extend(self.provenance("convertible_mapping", mapping, revision=CB_REVISION))
        return {"status": "partial" if rows else "unavailable", "sample_size": len(rows),
                "rows": rows, "universe_complete": False, "eligible_mapping_count": eligible,
                "missing_quote_count": unmatched, "uncertain_mapping_count": uncertain,
                "mapping_filter_date": day, "provenance": source,
                "observations": ["转债使用报告日通达信盘后包收盘报价，仅纳入可验证上市、未摘牌且有成交的样本；收益未另计已支付票息现金流及再投资。",
                                 "正股由转债映射去重；仅统计同日 A 股样本中可取得的正股。",
                                 "映射来自当前东财全表（含已摘牌）；不是历史时点名册，历史覆盖完整性未经核验。",
                                 f"可识别在市转债 {eligible} 只，缺少有效当日报价 {unmatched} 只，上市/摘牌或正股映射不明 {uncertain} 只。",
                                 "跨期统计使用独立的官方锚点价格及正股除权校验；各周期覆盖见跨期价格说明。"]}

    @staticmethod
    def _positive(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0

    @staticmethod
    def _nonnegative(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    def etfs(self, day):
        package = self.package(day)
        watchlist = set(self.settings["etfs"])
        rows = [normalize_package(r) for r in package if r["market"] + r["code"] in watchlist and r["volume"] > 0]
        return {"rows": rows, "complete": False, "partial": True,
                "observations": [f"ETF 仅覆盖配置的 {len(watchlist)} 只观察标的，不代表全部 ETF。",
                                 "未取得同日 NAV，折溢价留空；不从成交额推断资金流。"],
                "provenance": self.provenance("etfs", package)}

    def futures(self, day):
        raw = records(api.futures_daily(day, "CFFEX"))
        spot = {r["symbol"]: r for r in self.cache.get(("indices", day), [])}
        mapping = {"IF": "sh000300", "IH": "sh000016", "IC": "sh000905", "IM": "sh000852"}
        rows = []
        for r in raw:
            if r["product"] not in mapping or not r["close"]:
                continue
            index = spot.get(mapping[r["product"]])
            rows.append({"symbol": r["symbol"], "name": r["symbol"], "date": r["date"], "close": r["close"],
                         "previous_settlement": r["pre_settle"], "settlement": r["settle"], "volume": r["volume"],
                         "open_interest": r["open_interest"], "amount": r["turnover_10k"] * 10000 if r["turnover_10k"] is not None else None,
                         "spot": index["close"] if index else None, "spot_date": day if index else None, "provider": "cffex"})
        return {"rows": rows, "complete": True, "observations": ["中金所 IF/IH/IC/IM 合约；官方 CSV 与 XML 核对日期、收盘、成交量和持仓。"],
                "provenance": self.provenance("futures", raw)}

    def options(self, day):
        raw = records(api.options_daily(day, "CFFEX"))
        mapping = {"IO": "sh000300", "HO": "sh000016", "MO": "sh000852"}
        rows = []
        for r in raw:
            prefix = r["series"][:2]
            if prefix not in mapping or r["close"] is None:
                continue
            rows.append({"symbol": r["symbol"], "name": r["symbol"], "date": r["date"], "close": r["close"],
                         "previous_close": None, "volume": r["volume"], "open_interest": r["open_interest"],
                         "underlying": mapping[prefix], "series": r["series"], "expiry": None,
                         "option_type": {"C": "call", "P": "put"}[r["option_type"]], "strike": r["strike"],
                         "implied_volatility": None, "provider": "cffex"})
        return {"rows": rows, "complete": False, "partial": True,
                "observations": ["当前覆盖中金所指数期权，ETF 期权链尚未验收。",
                                 "官方文件没有到期日和 IV；到期日未核实，不计算 PCR，不推算 IV。"],
                "provenance": self.provenance("options", raw)}

    @staticmethod
    def external_provenance(kind, rows):
        unique = {(r["source"], r["source_url"], r["fetched_at"]) for r in rows}
        return [{"dataset": kind, "source": p, "provider": p, "source_url": u,
                 "fetched_at": t} for p, u, t in sorted(unique)]

    @staticmethod
    def provenance(kind, rows, revision=REVISION):
        unique = {(r["source"], r["source_url"], r["fetched_at"]) for r in rows}
        return [{"dataset": kind, "source": "a-stock-data", "provider": p, "source_url": u,
                 "fetched_at": t, "repository": REPOSITORY, "revision": revision} for p, u, t in sorted(unique)]
