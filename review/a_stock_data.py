"""a-stock-data 的复盘适配器；保留上游源、日期、金额单位和覆盖限制。"""
import functools
import calendar as month_calendar
import json
import math
import re
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .vendor import astock as api
from .vendor import convertibles as cb_api
from .calendar import official_holiday_calendar

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
                html_key = ("official_holidays_html",)
                if html_key not in self.cache:
                    with urllib.request.urlopen(HOLIDAYS_URL, timeout=30) as response:
                        self.cache[html_key] = response.read(500_000).decode("gb18030")
                rows = self._validated_month(
                    official_holiday_calendar(self.cache[html_key], year, month), year, month)
                source, url = "通达信官方休市表（备选）", HOLIDAYS_URL
                warning = f"深交所月历不可用，已使用完整年度官方休市表：{type(exc).__name__}"
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
        return {"rows": rows, "complete": False, "partial": True,
                "observations": ["样本来自盘后包按 A 股号段筛选的有成交证券；未核对交易所上市名册，不标记为全市场。",
                                 "尚未接入经验证的行业分类，板块强弱缺失。"],
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
                history = [r for r in records(api.tencent_kline(symbol, adjust="", start=start, end=day, count=320)) if r["date"] <= day]
                if not history or history[-1]["date"] != day:
                    raise ValueError("指数历史未覆盖请求日期")
                close = history[-1]["close"]
                if quote and abs(quote["close"] - close) > 0.011:
                    raise ValueError("指数历史与盘后包收盘价不一致")
                row = {"symbol": symbol, "name": item["name"], "date": day, "close": close,
                       "previous_close": quote["prev_close"] if quote else history[-2]["close"] if len(history) > 1 else None,
                       "amount": quote["amount"] if quote else None, "provider": "tencent",
                       "price_basis": "腾讯不复权指数日线", "history": [{"date": r["date"], "close": r["close"]} for r in history]}
                rows.append(row)
                sources.extend(self.provenance("indices", history))
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
        return {"convertibles": convertibles, "calendar_basis": basis}

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
                       listing_date=listing, delist_date=delist, price_basis="通达信不复权转债净价；不含付息")
            rows.append(row)
        source = self.provenance("convertibles", list(package.values()))
        source.extend(self.provenance("convertible_mapping", mapping, revision=CB_REVISION))
        return {"status": "partial" if rows else "unavailable", "sample_size": len(rows),
                "rows": rows, "universe_complete": False, "eligible_mapping_count": eligible,
                "missing_quote_count": unmatched, "uncertain_mapping_count": uncertain,
                "mapping_filter_date": day, "provenance": source,
                "observations": ["转债使用报告日通达信盘后包净价，仅纳入可验证上市、未摘牌且有成交的样本；不含票息。",
                                 "正股由转债映射去重；仅统计同日 A 股样本中可取得的正股。",
                                 "映射来自当前东财全表（含已摘牌）；不是历史时点名册，历史覆盖完整性未经核验。",
                                 f"可识别在市转债 {eligible} 只，缺少有效当日报价 {unmatched} 只，上市/摘牌或正股映射不明 {uncertain} 只。",
                                 "转债及正股没有经验证的同口径复权历史，年/月/周统计留空。"]}

    @staticmethod
    def _positive(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0

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
    def provenance(kind, rows, revision=REVISION):
        unique = {(r["source"], r["source_url"], r["fetched_at"]) for r in rows}
        return [{"dataset": kind, "source": "a-stock-data", "provider": p, "source_url": u,
                 "fetched_at": t, "repository": REPOSITORY, "revision": revision} for p, u, t in sorted(unique)]
