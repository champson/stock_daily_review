"""a-stock-data 的复盘适配器；保留上游源、日期、金额单位和覆盖限制。"""
import functools
import json
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .vendor import astock as api
from .calendar import official_holiday_calendar

REPOSITORY = "https://github.com/simonlin1212/a-stock-data"
REVISION = "29b07d2e37005b9fd568bca1262f6e572b5f9407"
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

    def calendar(self, day):
        parsed = date.fromisoformat(day)
        try:
            rows = records(api.trading_calendar(parsed.year, parsed.month))
            source = "a-stock-data / 深交所"
            url = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
            warning = None
        except (RuntimeError, ValueError, OSError) as exc:
            if not self.settings.get("tdx_official_calendar_fallback"):
                raise
            with urllib.request.urlopen(HOLIDAYS_URL, timeout=30) as response:
                html = response.read(500_000).decode("gb18030")
            rows = official_holiday_calendar(html, parsed.year, parsed.month)
            source, url = "通达信官方休市表（备选）", HOLIDAYS_URL
            warning = f"深交所月历不可用，已使用完整年度官方休市表：{type(exc).__name__}"
        matched = [r for r in rows if r["date"] == day]
        if len(matched) != 1 or not isinstance(matched[0]["is_open"], bool):
            raise ValueError("交易日历日期或开市标记异常")
        return {"date": day, "open": matched[0]["is_open"], "source": source,
                "source_url": url, "warning": warning, "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}

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
            try:
                history = [r for r in records(api.tencent_kline(symbol, adjust="", count=100)) if r["date"] <= day]
                if not history or history[-1]["date"] != day:
                    raise ValueError("指数历史未覆盖请求日期")
                quote = snapshot.get(item.get("package_symbol", symbol))
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
        self.cache[("indices", day)] = rows
        return {"rows": rows, "complete": False, "partial": bool(issues),
                "observations": issues + ["指数均线使用同口径历史收盘；历史成交额未取得，不计算五日成交额比。"],
                "provenance": sources}

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
    def provenance(kind, rows):
        unique = {(r["source"], r["source_url"], r["fetched_at"]) for r in rows}
        return [{"dataset": kind, "source": "a-stock-data", "provider": p, "source_url": u,
                 "fetched_at": t, "repository": REPOSITORY, "revision": REVISION} for p, u, t in sorted(unique)]
