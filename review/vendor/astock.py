"""Selected a-stock-data V3.10.2 code, Apache-2.0, Simon 林.
Upstream: https://github.com/simonlin1212/a-stock-data
Pinned commit: 29b07d2e37005b9fd568bca1262f6e572b5f9407
Extracted prerequisite definitions and marked blocks without example calls.
Local adapters live in review/a_stock_data.py; network proxy and TLS are inherited.
"""
SH_INDEX = {'000300', '000905', '000016', '000688', '000852', '000010'}

def get_prefix(code: str) -> str:
    """6位代码 → 市场前缀（sh/sz/bj）。支持显式前缀/后缀（sh000016 / 000016.SH）透传以解决歧义。"""
    c = code.lower().strip()
    if c.endswith(('.sh', '.sz', '.bj')):
        return c[-2:]
    if c.endswith(('.xshg', '.xshe')):
        return 'sh' if c.endswith('.xshg') else 'sz'
    if c.startswith(('sh', 'sz', 'bj')):
        return c[:2]
    if c.startswith('92'):
        return 'bj'
    if c.startswith(('5', '6', '9')):
        return 'sh'
    if c.startswith(('4', '8')):
        return 'bj'
    if c in SH_INDEX:
        return 'sh'
    return 'sz'

import re
_TICKER_RE = re.compile('^(?:(sh|sz|bj)(\\d{6})|(\\d{6})(?:\\.(sh|sz|bj|xshg|xshe))?)$', re.IGNORECASE)
_JQ_SUFFIX = {'xshg': 'sh', 'xshe': 'sz'}

def _natural_market(digits: str) -> str:
    """6 位码的自然归属市场。仅用于校验显式前缀是否自相矛盾。
    注意 000xxx 是沪指数/深个股共用的歧义段，由调用处单独处理，不走这里。"""
    if digits.startswith(('4', '8', '92')):
        return 'bj'
    if digits[0] in ('5', '6', '9'):
        return 'sh'
    return 'sz'

def norm_ticker(code: str, stock_only: bool=False) -> str:
    """任意受支持写法 → 纯 6 位数字代码。

    支持 600519 / SH600519 / sh600519 / 600519.SH / BJ920982 等。
    stock_only=True：个股专用接口（研报、一致预期等）传这个，会拒绝显式指数写法。
    ⚠️ 不匹配时**抛 ValueError，绝不静默返回空串或猜一个代码**——
    否则调用方会把「代码格式写错」误读成「这只票没有数据」，
    或者更糟：拿到另一只股票的数据还以为是对的。
    """
    raw = str(code).strip()
    m = _TICKER_RE.match(raw)
    if not m:
        raise ValueError(f'无法把 {code!r} 解析为 6 位股票代码；支持格式：600519 / SH600519 / sh600519 / 600519.SH / 600519.XSHG（聚宽）（前缀与后缀二选一，不能同时写）')
    digits = m.group(2) or m.group(3)
    market = (m.group(1) or m.group(4) or '').lower()
    market = _JQ_SUFFIX.get(market, market)
    if market:
        if digits.startswith('000'):
            if market == 'bj':
                raise ValueError(f'{code!r} 市场标识与号段矛盾：000xxx 不属北交所。')
            if stock_only and market == 'sh':
                raise ValueError(f'{code!r} 指向沪市指数而非个股（沪市无 000xxx 个股），本接口只服务个股。要查同号段的深市个股请显式传 sz{digits}。')
        else:
            nat = _natural_market(digits)
            if market != nat:
                raise ValueError(f'{code!r} 的市场标识与号段矛盾：{digits} 属 {nat} 市，而不是 {market} 市。（改用 {nat}{digits} 或去掉市场标识）')
    return digits

def em_market_code(code: str) -> int:
    """东财 secid 的市场号：**沪=1，深/北=0**（V3.7.0 新增 · #46）。

    ⚠️ 绝不要用 `code.startswith("6")` 判市场 —— 那会把**沪市 ETF（51x）**、
    **科创板 ETF（588x）**、**沪 B 股（900x）** 全部错判成深市，接口返回 `data: null`。
    2026-08-19 实测：510300 / 588000 / 600519 / 688112 / 900901 → m=1；
    300750 / 159915 / 920982 / 832982 → m=0（北交所与深市共用 m=0）。
    """
    return 1 if get_prefix(code) == 'sh' else 0

def em_secid(code: str) -> str:
    """东财 push2/push2his 的 secid，如 `1.600519` / `0.300750`。"""
    return f'{em_market_code(code)}.{norm_ticker(code)}'

def to_joinquant(code: str) -> str:
    """任意受支持写法 → 聚宽代码，如 `600519.XSHG` / `000001.XSHE`（#55）。

    000 段歧义码按 get_prefix() 的规则走：`to_joinquant("sh000001")` → `000001.XSHG`（上证指数），
    `to_joinquant("000001")` → `000001.XSHE`（平安银行）。反方向（聚宽 → 本 Skill）用
    `get_prefix(c) + norm_ticker(c)` 得到 `sh000001` 这类显式前缀写法，再传给各端点。
    北交所：聚宽公开文档与 jqdatasdk 源码（2026-09-20 查）只有 .XSHG / .XSHE，没查到北交所后缀，抛 ValueError 不猜。
    """
    digits, market = (norm_ticker(code), get_prefix(code))
    if market == 'bj':
        raise ValueError(f'{code!r} 是北交所证券；聚宽公开文档未给出北交所后缀，不做转换')
    return f"{digits}.{('XSHG' if market == 'sh' else 'XSHE')}"

import functools
import math
import re
from datetime import date as _date_cls, datetime, timezone

import pandas as pd
import requests

V39_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 一次要翻几十页（§1.4 腾讯逐笔）或偶尔握手卡住（§13.7 新浪期货日 K）的请求走这个会话：复用连接，
# 连接失败、握手或等响应头超时、429 / 5xx 指数退避重试（写法同 EM_SESSION）；读响应体中途超时不重试，
# 也不按服务端给的 Retry-After 等待。
V39_RETRY_SESSION = requests.Session()
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _v39_retry_adapter = HTTPAdapter(max_retries=Retry(
        total=3, connect=3, backoff_factor=0.6, respect_retry_after_header=False,
        status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"]))
    V39_RETRY_SESSION.mount("https://", _v39_retry_adapter)
    V39_RETRY_SESSION.mount("http://", _v39_retry_adapter)
except Exception:
    # 老版本 urllib3（< 1.26）缺 allowed_methods：不重试，也不复用连接（复用的连接被服务端断开时没有重试兜底），同 v3.10.0
    V39_RETRY_SESSION = None


def _v39_http(url, params=None, data=None, headers=None, method="GET", timeout=(10, 40),
              allow_status=(), allow_redirects=True, session=None):
    """非东财的 HTTP 请求：带浏览器 UA。网络错误、非 2xx 一律抛 RuntimeError（不把错误页当数据）；
    allow_status 里的状态码（源用 404 表示「当天没发布」时）原样返回，由调用方判断。
    session：传 V39_RETRY_SESSION 复用连接并自动重试（哪些情况重试见它的定义）；不传每次新建连接、不重试。"""
    merged = {"User-Agent": V39_UA}
    merged.update(headers or {})
    try:
        response = (session or requests).request(method, url, params=params, data=data, headers=merged,
                                                 timeout=timeout, allow_redirects=allow_redirects)
        if response.status_code not in allow_status:
            response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"请求 {url} 失败: {type(exc).__name__}: {exc}") from exc
    return response


def _v39_json(response):
    """解析 JSON；不是 JSON 抛 RuntimeError。json 的解析错误是 ValueError 的子类，
    不转换会被调用方当成「确实没有数据」。"""
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{getattr(response, 'url', '')} 返回的不是 JSON，可能是错误页") from exc


def _v39_date(value):
    """'2026-09-18' / '20260918' / date 对象 → '2026-09-18'；其他写法抛 ValueError。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, _date_cls):
        return value.isoformat()
    text = str(value).strip()
    fmt = "%Y%m%d" if re.fullmatch(r"[0-9]{8}", text) else "%Y-%m-%d"
    return datetime.strptime(text, fmt).date().isoformat()


def _v39_src_date(value):
    """来源返回的日期 → 'YYYY-MM-DD'；认不出抛 RuntimeError（源格式变了，不是参数写错）。"""
    try:
        return _v39_date(value)
    except ValueError as exc:
        raise RuntimeError(f"来源返回了无法识别的日期 {value!r}") from exc


def _v39_num(value):
    """'1,234.50' → 1234.5；空串 / '-' / '--' / None → None；其他非数字抛 RuntimeError
    （来源给了认不出的值是「源的格式变了」，不能和参数错误的 ValueError 混在一起）。
    JSON 布尔值同样抛错：float(True)=1.0 会把格式错误静默写成价格 / 成交量。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise RuntimeError(f"来源在数值字段给了布尔值 {value!r}")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value)
    text = str(value).replace(",", "").strip()
    if text in ("", "-", "--", "None", "null"):
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise RuntimeError(f"来源返回了无法识别的数值 {value!r}") from exc
    return number if math.isfinite(number) else None


def _v39_rows(value, what):
    """来源里可能整段缺失的行列表：字段没有（None）按空处理，其余必须是对象列表。
    写成 `value or []` 会把 {} / '' / 0 这类结构改变也当成空表，静默丢掉整段数据。"""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise RuntimeError(f"{what} 应为对象列表，实际是 {type(value).__name__}: {str(value)[:120]}")
    return value


def _v39_labels(value, what):
    """来源的标签数组（频道名之类）：None 按空处理，其余必须是字符串列表。
    直接 `value or []` 再 join，来源把数组改成字符串时会被拆成单字（'ab' → 'a,b'）。"""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise RuntimeError(f"{what} 应为字符串列表，实际是 {type(value).__name__}: {str(value)[:120]}")
    return value


def _v39_req_num(value, what):
    """必填数值（价格、成交量）：在 _v39_num 之上，空值 / NaN / inf 也抛 RuntimeError，不能当缺失放过。"""
    number = _v39_num(value)
    if number is None:
        raise RuntimeError(f"来源的 {what} 为空或不是有限数值: {value!r}")
    return number


def _v39_contract(func):
    """统一异常契约：来源行缺字段时 row["X"] 会漏出 KeyError，调用方按「参数错 / 没数据」处理就会
    把「来源格式变了」当成正常情况。这里把它转成带函数名和字段名的 RuntimeError。
    （函数内所有按用户参数取字典的地方都先校验过参数，不会走到这里。）"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyError as exc:
            raise RuntimeError(f"{func.__name__}: 来源数据缺少字段 {exc}，格式可能已变") from exc
    return wrapper


def _v39_count(value, what):
    """来源自报的页数 / 条数 → 非负 int。只认 int 或纯数字串；bool 抛错（int(True)=1 会让
    「只返回 1 条」通过完整性核对），其他写法也抛 RuntimeError。"""
    text = str(value).strip() if isinstance(value, (int, str)) and not isinstance(value, bool) else ""
    if not re.fullmatch(r"[0-9]+", text):
        raise RuntimeError(f"{what} 不是非负整数: {value!r}")
    return int(text)


def _v39_frame(rows, source, url, columns=None):
    """统一出表：附 source / source_url / fetched_at。rows 为空时返回带列名的空表，
    是否允许为空由调用方判断（「确实没有」与「接口坏了」要分开处理）。"""
    frame = pd.DataFrame(rows, columns=columns)
    frame["source"] = source
    frame["source_url"] = url
    frame["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return frame


def _em_datacenter_strict(report_name, filter_str="", sort_columns="", sort_types="",
                          page_size=500, max_rows=5000, columns="ALL", extra=None):
    """东财 datacenter 严格版：code=0 取数据；第 1 页就 9201(返回数据为空) → []；其他错误码直接抛。

    与旧 eastmoney_datacenter() 的区别：后者把任何失败都变成 []，调用方分不清
    「这只票确实没有」和「参数写错/被风控」。sortTypes 个数必须与 sortColumns 一致，
    否则东财返回 9501「排序字段和顺序数量不一致」。
    翻页中途失败（第 2 页起 9201、空页、非末页不满页、缺 pages / count、总页数或总条数变了、
    最终条数与 count 不符）抛 RuntimeError，不把部分结果当完整结果返回。
    payload / result 不是对象、data 不是由对象组成的列表，同样抛 RuntimeError。
    只有「第 1 页、pages=1、data 为空」才算确实没有数据。
    max_rows 只在来源自报总数 count > max_rows 时提前截断；count 不超过上限的，一律走完分页并核对总数。
    """
    n_cols = len([c for c in sort_columns.split(",") if c]) if sort_columns else 0
    n_types = len([t for t in sort_types.split(",") if t]) if sort_types else 0
    if n_cols != n_types:
        raise ValueError(f"sortColumns({n_cols}) 与 sortTypes({n_types}) 个数不一致")
    rows, page, first = [], 1, None
    while True:
        params = {"reportName": report_name, "columns": columns, "filter": filter_str,
                  "pageNumber": str(page), "pageSize": str(page_size),
                  "sortColumns": sort_columns, "sortTypes": sort_types,
                  "source": "WEB", "client": "WEB"}
        params.update(extra or {})
        try:
            response = em_get(DATACENTER_URL, params=params, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"东财 {report_name} 请求失败: {type(exc).__name__}: {exc}") from exc
        payload = _v39_json(response)
        if not isinstance(payload, dict):
            raise RuntimeError(f"东财 {report_name} 返回的不是 JSON 对象: {str(payload)[:100]}")
        if payload.get("code") == 9201:
            if page == 1:
                return []
            raise RuntimeError(f"东财 {report_name} 第 {page} 页返回「数据为空」，"
                               f"前面已取 {len(rows)} 条，结果不完整")
        if payload.get("code") != 0 or not payload.get("result"):
            raise RuntimeError(f"东财 {report_name} 返回错误: "
                               f"{payload.get('code')} {payload.get('message')}")
        result = payload["result"]
        if not isinstance(result, dict):
            raise RuntimeError(f"东财 {report_name} 的 result 不是对象: {str(result)[:100]}")
        pages, count, data = result.get("pages"), result.get("count"), result.get("data")
        if data is None:
            data = []
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise RuntimeError(f"东财 {report_name} 第 {page} 页的 data 不是由对象组成的列表，格式可能已变")
        if (any(isinstance(v, bool) or not isinstance(v, int) for v in (pages, count))
                or pages < 1 or count < 0):
            raise RuntimeError(f"东财 {report_name} 缺少分页信息（pages={pages!r}, count={count!r}）")
        if first is None:
            first = (pages, count)
        elif (pages, count) != first:
            raise RuntimeError(f"东财 {report_name} 翻页时总页数 / 总条数从 {first} 变成 {(pages, count)}，"
                               "结果可能错位，请重试")
        if not data and (page > 1 or pages > 1):
            raise RuntimeError(f"东财 {report_name} 第 {page}/{pages} 页是空的，结果不完整")
        if page < pages and len(data) != int(page_size):
            raise RuntimeError(f"东财 {report_name} 第 {page}/{pages} 页只有 {len(data)} 条"
                               f"（非末页应为 {page_size} 条），结果不完整")
        rows.extend(data)
        # 只有来源自报的总数确实超过上限才提前截断；否则（count <= max_rows 却已拿到更多行）
        # 必须走完分页并核对总数，不然「data 比 count 还多」这种格式异常会被当成正常截断放过
        if len(rows) >= max_rows and count > max_rows:
            return rows[:max_rows]
        if page >= pages:
            if len(rows) != count:
                raise RuntimeError(f"东财 {report_name} 翻页后 {len(rows)} 条，与总数 {count} 不符")
            return rows
        page += 1


def _em_day(value):
    """东财日期串 '2026-09-18 00:00:00' → '2026-09-18'；空值 → None；认不出的写法抛 RuntimeError
    （只截前 10 个字符会把 '2026/09/18' 原样放行，再拿去和日期串比较就会比错）。"""
    return _v39_src_date(str(value)[:10]) if value else None

import calendar
import math
import re
from datetime import datetime, timezone
from io import BytesIO

import pandas as pd
import requests


def _official_code(value):
    value = str(value).strip()
    if not re.fullmatch(r"[0-9]{6}", value):
        raise ValueError("代码必须是 6 位纯数字；指数 provider 与证券交易所不是同一概念")
    return value


def _official_date(value):
    value = str(value).strip()
    fmt = "%Y%m%d" if re.fullmatch(r"[0-9]{8}", value) else "%Y-%m-%d"
    return datetime.strptime(value, fmt).date().isoformat()


def _official_number(value, required=False):
    if pd.isna(value) or str(value).strip() in ("", "-", "--"):
        if required:
            raise RuntimeError("官方源缺少必需数值")
        return None
    number = float(str(value).replace(",", ""))
    if not math.isfinite(number):
        raise RuntimeError("官方源返回非有限数值")
    return number


def _official_get(url, params=None, referer=None):
    response = requests.get(
        url, params=params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": referer or url},
        timeout=(10, 40),
    )
    response.raise_for_status()
    return response


def _official_excel(response):
    try:
        frame = pd.read_excel(BytesIO(response.content), dtype=str)
    except (ValueError, OSError) as exc:
        raise RuntimeError("官方源未返回可解析的 Excel；可能未发布或响应结构改变") from exc
    # 两种中证文件的表头空格略有差异，按完整列名去空白后匹配。
    frame.columns = [re.sub(r"\s+", "", str(c)) for c in frame.columns]
    return frame


def _official_columns(frame, names):
    missing = set(names) - set(frame.columns)
    if missing:
        raise RuntimeError("官方数据列缺失: " + ", ".join(sorted(missing)))


def _official_frame(rows, keys, source, url):
    frame = pd.DataFrame(rows)
    if frame.empty or frame.duplicated(keys).any():
        raise RuntimeError("官方数据为空或主键重复，不能当成完整快照")
    frame["source"] = source
    frame["source_url"] = url
    frame["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return frame.sort_values(keys).reset_index(drop=True)


def _official_index_members(index_code, provider, weights):
    index_code = _official_code(index_code)
    if provider not in ("csi", "cni"):
        raise ValueError("provider 必须是 csi（中证）或 cni（国证）")
    if provider == "csi":
        kind = "closeweight" if weights else "cons"
        url = ("https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
               f"autofile/{kind}/{index_code}{kind}.xls")
        response = _official_get(url)
        data = _official_excel(response)
        cols = ["日期Date", "指数代码IndexCode", "成份券代码ConstituentCode",
                "成份券名称ConstituentName", "交易所Exchange"]
        if weights:
            cols.append("权重(%)weight")
    else:
        url = "https://www.cnindex.com.cn/sample-detail/download-history"
        response = _official_get(url, {"indexcode": index_code})
        data = _official_excel(response)
        cols = ["日期", "样本代码", "样本简称", "权重（%）"]
    _official_columns(data, cols)
    rows = []
    for rec in data.to_dict("records"):
        if provider == "csi":
            if str(rec["指数代码IndexCode"]).zfill(6) != index_code:
                raise RuntimeError("中证返回了不同指数的数据")
            code = str(rec["成份券代码ConstituentCode"]).zfill(6)
            exchanges = {"上海证券交易所": "SH", "深圳证券交易所": "SZ", "北京证券交易所": "BJ"}
            exchange = exchanges.get(rec["交易所Exchange"])
            if exchange is None:
                raise ValueError("本端点仅支持沪深北成分，请使用相应市场的数据工具")
            row = {"date": _official_date(rec["日期Date"]), "index_code": index_code,
                   "code": _official_code(code), "name": rec["成份券名称ConstituentName"],
                   "exchange": exchange}
            weight = rec.get("权重(%)weight")
        else:
            # 国证没有交易所列；A 股文件保留六位文本。港股 00700 不能补成 000700/SZ。
            code = _official_code(rec["样本代码"])
            exchange = ("SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3"))
                        else "BJ" if code.startswith(("4", "8", "92")) else None)
            if exchange is None:
                raise ValueError("国证该指数包含本端点不支持的证券类型")
            row = {"date": _official_date(rec["日期"]), "index_code": index_code,
                   "code": code, "name": rec["样本简称"], "exchange": exchange}
            weight = rec["权重（%）"]
        if weights:
            row["weight_percent"] = _official_number(weight, required=True)
        rows.append(row)
    frame = _official_frame(rows, ["date", "code", "exchange"], provider, response.url)
    if frame["date"].nunique() != 1:
        raise RuntimeError("成分文件混有多个日期，不能当作单日快照")
    if weights and (not frame.weight_percent.between(0, 100).all()
                    or not 99 <= frame.weight_percent.sum() <= 101):
        raise RuntimeError("权重范围或合计异常；可能文件残缺或不是百分数口径")
    return frame


def index_constituents(index_code, provider="csi"):
    """最近公布的沪深北成分；date 是源文件日期，不是抓取日。"""
    return _official_index_members(index_code, provider, weights=False)


def index_weights(index_code, provider="csi"):
    """最近公布的指数权重，weight_percent 单位为百分数。"""
    return _official_index_members(index_code, provider, weights=True)


def index_valuation(index_code):
    """中证近期 PE/股息率文件；不含 PB，两种股本口径不混用。"""
    index_code = _official_code(index_code)
    url = ("https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
           f"autofile/indicator/{index_code}indicator.xls")
    response = _official_get(url)
    data = _official_excel(response)
    mapping = {"市盈率1（总股本）P/E1": "pe_total",
               "市盈率2（计算用股本）P/E2": "pe_calculation",
               "股息率1（总股本）D/P1": "dividend_yield_total_percent",
               "股息率2（计算用股本）D/P2": "dividend_yield_calculation_percent"}
    _official_columns(data, ["日期Date", "指数代码IndexCode", *mapping])
    rows = []
    for rec in data.to_dict("records"):
        if str(rec["指数代码IndexCode"]).zfill(6) != index_code:
            raise RuntimeError("中证估值文件返回了不同指数")
        rows.append({"date": _official_date(rec["日期Date"]), "index_code": index_code,
                     **{dest: _official_number(rec[src]) for src, dest in mapping.items()}})
    return _official_frame(rows, ["date", "index_code"], "csi", response.url)


def trading_calendar(year, month):
    """深交所完整自然月日历。未发布或缺日抛错，周末调休不视为交易日。"""
    if type(year) is not int or type(month) is not int or not 1 <= month <= 12:
        raise ValueError("year/month 必须为整数，month 在 1–12 之间")
    last = calendar.monthrange(year, month)[1]
    expected = {datetime(year, month, day).date().isoformat() for day in range(1, last + 1)}
    url = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
    response = _official_get(url, {"month": f"{year}-{month}"})
    data = response.json().get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("深交所尚未返回该月日历；不能推断全月休市")
    rows = []
    for rec in data:
        if str(rec.get("jybz")) not in ("0", "1") or not rec.get("jyrq"):
            raise RuntimeError("深交所日历字段异常")
        rows.append({"date": _official_date(rec["jyrq"]), "is_open": str(rec["jybz"]) == "1"})
    frame = _official_frame(rows, ["date"], "szse", response.url)
    if set(frame.date) != expected:
        raise RuntimeError("日历月份错位或日期不完整，不能继续调度")
    return frame

import time
from datetime import date, datetime, timedelta

# 三个入口是同一后端，但限流各自独立（#52 实测单域名约 600 次后返回空 JSON）。
TENCENT_KLINE_HOSTS = ["https://web.ifzq.gtimg.cn",
                       "https://proxy.finance.qq.com/ifzqgtimg",
                       "https://ifzq.gtimg.cn"]
_TENCENT_HOST_COOLDOWN = 120          # 某入口失败后暂停使用的秒数
_tencent_host_down_until = {}
_TENCENT_MINUTES = ("m1", "m5", "m15", "m30", "m60")
_TENCENT_SPAN_DAYS = {"day": 700, "week": 3650, "month": 18250}   # 每段 < 640 根


def _tencent_kline_call(path, param):
    """按顺序尝试三个入口；空响应或异常视为该入口被限流，冷却后换下一个。返回 (data, 实际成功的入口)。"""
    errors = []
    for host in TENCENT_KLINE_HOSTS:
        if _tencent_host_down_until.get(host, 0) > time.time():
            continue
        try:
            response = _v39_http(host + path, params={"param": param},
                                 headers={"Referer": "https://gu.qq.com/"}, timeout=(8, 20))
            payload = _v39_json(response) if response.text.strip() else {}
        except RuntimeError as exc:
            errors.append(f"{host}: {type(exc.__cause__ or exc).__name__}")
            _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
            continue
        if not isinstance(payload, dict):    # 顶层变成数组 / 字符串：当作这个入口坏了，换下一个
            errors.append(f"{host}: 顶层是 {type(payload).__name__}，不是对象")
            _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
            continue
        if payload.get("msg") == "param error":
            raise ValueError(f"腾讯 K 线参数错误（区间过长或代码不存在）: {param}")
        if isinstance(payload.get("data"), dict) and payload["data"]:
            return payload["data"], host
        errors.append(f"{host}: 空响应")
        _tencent_host_down_until[host] = time.time() + _TENCENT_HOST_COOLDOWN
    raise RuntimeError("腾讯 K 线三个入口均不可用（可能被限流，稍后重试）: " + "; ".join(errors))


@_v39_contract
def tencent_kline(code, period="day", adjust=None, start=None, end=None, count=320):
    """腾讯 K 线 — 日/周/月（默认前复权）与 1/5/15/30/60 分钟（不复权）。

    period: day / week / month / m1 / m5 / m15 / m30 / m60
    adjust: None=日周月默认 qfq、分钟默认不复权；可显式传 'qfq' / 'hfq' / ''（不复权）
    start/end: 仅日周月可用，'YYYY-MM-DD'；给了 start 会自动按段分页（单次最多 640 根）
    count: 不给 start 时取最近 count 根；日周月 ≤ 640，分钟 ≤ 320
    成交量单位：科创板（688/689）是股，其余是手；科创板以外，最近一个交易日的分钟量是整手、下一个交易日才回填成精确股数（见 §1.2 说明）。
    本接口**没有成交额**，需要成交额用 §1.3 通达信盘后包。
    不支持北交所：腾讯对北交所只返回最新 1 根日线，区间与分钟线为空（2026-09-20 实测），直接抛 ValueError。
    """
    period = str(period).lower()
    if get_prefix(code) == "bj":
        raise ValueError("腾讯 K 线不支持北交所（只返回最新 1 根日线、分钟线为空）；"
                         "北交所日线请用 §1.3 tdx_daily_package(date) 按交易日取")
    symbol = get_prefix(code) + norm_ticker(code)
    if period in _TENCENT_MINUTES:
        if adjust not in (None, ""):
            raise ValueError("分钟线只有不复权数据，adjust 请留空")
        if start or end:
            raise ValueError("分钟线只能取最近 count 根，不支持 start/end")
        if not 1 <= int(count) <= 320:
            raise ValueError("分钟线 count 范围 1–320")
        data, host = _tencent_kline_call("/appstock/app/kline/mkline", f"{symbol},{period},,{int(count)}")
        node = data.get(symbol)
        if not isinstance(node, dict) or not isinstance(node.get(period), list):
            raise RuntimeError(f"腾讯分钟线 {symbol} 的返回里没有 {period} 列表，格式可能已变")
        rows, seen = [], set()
        try:
            for item in node[period]:
                stamp = datetime.strptime(item[0], "%Y%m%d%H%M")
                if stamp in seen:
                    raise RuntimeError(f"腾讯分钟线 {symbol} 同一时刻 {stamp} 出现两次，结果不可信")
                seen.add(stamp)
                rows.append({"datetime": stamp.strftime("%Y-%m-%d %H:%M"),
                             "open": _v39_req_num(item[1], "open"), "high": _v39_req_num(item[3], "high"),
                             "low": _v39_req_num(item[4], "low"), "close": _v39_req_num(item[2], "close"),
                             "volume": _v39_req_num(item[5], "volume"),
                             # 第 8 个字段是换手率「基点」，÷100 才是百分数；它不是成交额。
                             "turnover_rate_pct": _v39_num(item[7]) / 100 if len(item) > 7
                             and _v39_num(item[7]) is not None else None})
        except (ValueError, TypeError, IndexError, KeyError) as exc:      # 源格式变了（行变短 / 变成对象），不是参数错
            raise RuntimeError(f"腾讯分钟线 {symbol} 行格式改变: {exc}") from exc
        if not rows:
            raise RuntimeError(f"腾讯分钟线 {symbol} {period} 返回 0 根")
        frame = _v39_frame(rows, "tencent", host + "/appstock/app/kline/mkline")
        frame.insert(0, "code", symbol)
        return frame

    if period not in _TENCENT_SPAN_DAYS:
        raise ValueError("period 只能是 day/week/month 或 m1/m5/m15/m30/m60")
    adjust = "qfq" if adjust is None else adjust
    if adjust not in ("qfq", "hfq", ""):
        raise ValueError("adjust 只能是 'qfq' / 'hfq' / ''")
    if start:
        first = datetime.strptime(_v39_date(start), "%Y-%m-%d").date()
        last = datetime.strptime(_v39_date(end), "%Y-%m-%d").date() if end else date.today()
        if first > last:
            raise ValueError("start 不能晚于 end")
        windows, cursor = [], first
        while cursor <= last:
            stop = min(cursor + timedelta(days=_TENCENT_SPAN_DAYS[period] - 1), last)
            windows.append((cursor.isoformat(), stop.isoformat(), 640))
            cursor = stop + timedelta(days=1)
    else:
        if end:
            raise ValueError("只给 end 时请同时给 start")
        if not 1 <= int(count) <= 640:
            raise ValueError("日周月 count 范围 1–640")
        windows = [("", "", int(count))]

    by_date, used_hosts = {}, []
    for s, e, n in windows:
        data, host = _tencent_kline_call("/appstock/app/fqkline/get",
                                         f"{symbol},{period},{s},{e},{n},{adjust}")
        if host not in used_hosts:
            used_hosts.append(host)
        node = data.get(symbol)
        # 有除权的标的返回 qfqday / hfqweek…；从未除权的标的（以及指数）只返回 day/week/month，
        # 此时复权价与原始价相同。只认其中一个 key 会静默丢掉另一类标的（#52 补充）。
        # 复权 key 在就只用它：它为空而 day 有数据时，拿 day 顶上就是把原始价标成复权价，直接抛错。
        # 空列表是正常的（上市前、长期停牌的区间，实测 688981 在 2019 年返回 day=[]）；
        # 连这两个 key 都没有说明返回格式变了，不能把这一段当成 0 根跳过。
        key = adjust + period
        if not isinstance(node, dict) or not isinstance(node.get(key, node.get(period)), list):
            raise RuntimeError(f"腾讯 K 线 {symbol} {s or '最近'}~{e or ''} 的返回里没有 {key} / {period}，"
                               "格式可能已变")
        items = node.get(key, node.get(period))
        if key != period and key in node and not items and node.get(period):
            raise RuntimeError(f"腾讯 K 线 {symbol} {s}~{e} 的 {key} 为空、{period} 却有数据，"
                               "不能拿原始价冒充复权价")
        try:
            for item in items:
                day = _v39_src_date(item[0])
                # 每段只接受段内日期（实测腾讯按 K 线日期过滤，周 / 月线也不越界）；
                # 越界说明拿到的是别的请求或缓存页，只按总区间过滤会把缺掉的几段静默吞掉
                if s and not s <= day <= e:
                    raise RuntimeError(f"腾讯 K 线 {symbol} 请求 {s}~{e} 却返回了 {day}，结果不可信")
                if day in by_date:          # 各段互不重叠，同一天出现两次只能是源数据有问题
                    raise RuntimeError(f"腾讯 K 线 {symbol} 日期 {day} 出现两次，结果不可信")
                # 必填数值走 _v39_req_num：float() 会把 true 读成 1.0、把 'nan' 放进结果
                by_date[day] = {"date": day, "open": _v39_req_num(item[1], "open"),
                                "high": _v39_req_num(item[3], "high"), "low": _v39_req_num(item[4], "low"),
                                "close": _v39_req_num(item[2], "close"), "volume": _v39_req_num(item[5], "volume")}
        except (ValueError, TypeError, IndexError, KeyError) as exc:      # 源格式变了（行变短 / 变成对象），不是参数错
            raise RuntimeError(f"腾讯 K 线 {symbol} 行格式改变: {exc}") from exc
    rows = [by_date[k] for k in sorted(by_date)]
    if start:
        rows = [r for r in rows if windows[0][0] <= r["date"] <= windows[-1][1]]
    if not rows:
        raise RuntimeError(f"腾讯 K 线 {symbol} {period} 在所给区间内 0 根（未上市/停牌区间/代码有误）")
    # 腾讯 qfq 是「逐次减去每股分红」的等差口径（茅台 2020-01-02：原始 1130.00，qfq 870.741，
    # 差额 259.259 正是此后累计分红），高分红股的早年价格会被减成负数（茅台 2015 年 -117.6）。
    # 负价不能拿去算收益率，直接拒绝；长区间请用 adjust='' 再按 §1.6 比例口径复权。
    if any(min(r["open"], r["high"], r["low"], r["close"]) <= 0 for r in rows):
        raise RuntimeError(f"腾讯 {adjust or '原始'} 价格出现 ≤0（等差复权口径的副作用）；"
                           "请改用 adjust='' 取不复权价，再用 §1.6 sina_adjust_factor + apply_adjust")
    # 分段请求可能落在不同入口，source_url 列出实际用到的全部入口
    frame = _v39_frame(rows, "tencent", " | ".join(h + "/appstock/app/fqkline/get" for h in used_hosts))
    frame.insert(0, "code", symbol)
    frame.insert(1, "adjust", adjust or "none")
    return frame

import io
import math
import re
import struct
import zipfile
import zlib

TDX_PACKAGE_URL = "https://www.tdx.com.cn/products/data/data/g4day/{ymd}.zip"
# 盘后包从这一天起带北交所文件（2026-09-20 二分实测：20220505 无、20220506 有）
TDX_BJ_FIRST_DAY = "20220506"
# 每个市场有收盘价的最少行数。2026-09-20 实测 2021-08-16 ~ 2026-09-18 共 11 个包的最小值：
# 沪 16202、深 3641（2022 年以前深市大部分代码当日无价）、京 87（2022-05-06），下限取明显低于最小值的整数
TDX_MIN_PRICED = {"sh": 10000, "sz": 3000, "bj": 50}


def _tdx_parse_package(content, ymd):
    """解析通达信每日增量包：每个市场一对 .cod（代码表，150 字节/条）+ .md1（行情块，512 字节/块）。
    布局参考 jing2uo/tdx2db（MIT）的 tdx/merge.go，已用 600519/000001/920000 与腾讯收盘价对拍。"""
    archive = zipfile.ZipFile(io.BytesIO(content))
    names = set(archive.namelist())
    rows = []
    for market in ("sh", "sz", "bj"):
        cod_name, md1_name = f"{market}{ymd[2:]}.cod", f"{market}{ymd[2:]}.md1"
        if (market == "bj" and ymd < TDX_BJ_FIRST_DAY
                and cod_name not in names and md1_name not in names):
            continue            # 这天之前的包还没有北交所文件；之后缺文件按格式改变报错
        if cod_name not in names or md1_name not in names:
            raise RuntimeError(f"通达信盘后包缺少 {cod_name}/{md1_name}，格式可能已变")
        cod, md1 = archive.read(cod_name), archive.read(md1_name)
        if len(cod) % 150 or len(md1) % 512:
            raise RuntimeError(f"{market} 代码表或行情块长度不是整块，文件可能被截断")
        if len(cod) // 150 != len(md1) // 512:
            raise RuntimeError(f"{market} 代码表 {len(cod) // 150} 条、行情块 {len(md1) // 512} 块，对不上")
        before, codes, seqs = len(rows), set(), set()
        for offset in range(0, len(cod), 150):
            record = cod[offset:offset + 150]
            # 11 个真实包（2021-08 ~ 2026-09）的代码全是 6 位 ASCII 数字；用 replace 解码会把坏字节变成 '\ufffd00000' 放行
            code = record[0:6].rstrip(b"\x00 ").decode("ascii", "replace")
            seq = struct.unpack("<H", record[32:34])[0]
            if not re.fullmatch(r"[0-9]{6}", code):
                raise RuntimeError(f"通达信盘后包 {market} 代码表出现非 6 位数字代码 {code!r}，格式可能已变")
            if code in codes or seq in seqs:
                raise RuntimeError(f"通达信盘后包 {market} 代码表有重复的代码 / 行情块序号（{code!r}, seq={seq}）")
            codes.add(code)
            seqs.add(seq)
            block = md1[seq * 512:(seq + 1) * 512]
            if len(block) != 512:
                raise RuntimeError(f"{market}{code} 行情块越界（seq={seq}）")
            prev_close = struct.unpack("<d", block[4:12])[0]
            open_, high, low, close = struct.unpack("<4d", block[12:44])
            amount = struct.unpack("<d", block[72:80])[0]
            # NaN 能绕过 close <= 0 被当成有价记录计入下限；11 个真实包里没有一个非有限值
            if not all(math.isfinite(v) for v in (prev_close, open_, high, low, close, amount)):
                raise RuntimeError(f"通达信盘后包 {market}{code} 行情块出现非有限数值，文件可能已损坏")
            if close <= 0:
                continue        # 880/881 等通达信自编板块指数当日无价，整块为 0，不是证券
            volume = struct.unpack("<Q", block[56:64])[0]
            raw_name = record[40:72].split(b"\x00")[0]
            try:                # replace 会把坏字节变成「�」当成正常名称返回（11 个真实包实测零替换字符）
                name = raw_name.decode("gbk").strip()
            except UnicodeDecodeError as exc:
                raise RuntimeError(f"通达信盘后包 {market}{code} 的名称不是 GBK，文件可能已损坏") from exc
            if not name:
                raise RuntimeError(f"通达信盘后包 {market}{code} 有价格却没有名称，文件可能已损坏")
            rows.append({"date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", "market": market,
                         "code": code,
                         "name": name,
                         "prev_close": round(prev_close, 4), "open": round(open_, 4),
                         "high": round(high, 4), "low": round(low, 4),
                         "close": round(close, 4), "volume": volume,
                         "amount": round(amount, 2)})
        # 逐市场核对下限：只检查总行数会让沪深撑过门槛、北交所静默缺失
        if len(rows) - before < TDX_MIN_PRICED[market]:
            raise RuntimeError(f"通达信盘后包 {market} 市场只有 {len(rows) - before} 条有价记录"
                               f"（实测下限 {TDX_MIN_PRICED[market]}），文件可能残缺或格式已变")
    return rows


@_v39_contract
def tdx_daily_package(date):
    """通达信官网每日盘后包 — 某一交易日沪深北全部证券的日线（含成交额）。

    走 HTTP 下载（约 2.7MB），与 #52 失效的 TCP 行情命令是两条路。
    个股 volume 单位是「股」、amount 单位是「元」；指数等特殊代码的 volume 为通达信原值。
    非交易日或当日包尚未发布时官网返回 404，本函数抛 ValueError，不返回空表。
    历史包实测 2022-01-04、2023-01-03 可取，2021-01-04 已 404，未逐日验证；
    2022-05-06 之前的包没有北交所文件，只返回沪深，之后缺北交所文件会报错。
    某个市场有价记录少于 TDX_MIN_PRICED 的实测下限、代码不是 6 位数字或重复、行情块序号重复、
    代码表与行情块条数对不上、价格 / 成交额不是有限数，
    都按文件残缺抛 RuntimeError，不把部分市场当全市场返回。
    """
    ymd = _v39_date(date).replace("-", "")
    url = TDX_PACKAGE_URL.format(ymd=ymd)
    response = _v39_http(url, timeout=(10, 90), allow_status=(404,))
    if response.status_code == 404:
        raise ValueError(f"{date} 没有通达信盘后包：非交易日、当日包尚未发布（通常收盘后数小时），或早于官网保留范围（实测 2021-01-04 已没有）")
    if not response.content.startswith(b"PK"):
        raise RuntimeError("通达信盘后包不是 zip 文件，可能是错误页")
    try:
        rows = _tdx_parse_package(response.content, ymd)
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:      # 以 PK 开头但压缩包坏了 / 下载被截断
        raise RuntimeError(f"通达信盘后包 {url} 无法解压: {type(exc).__name__}: {exc}") from exc
    return _v39_frame(rows, "tdx", url)

import csv
import io
import json
import re
from xml.etree import ElementTree

FUTURES_EXCHANGES = ("SHFE", "INE", "CZCE", "CFFEX", "GFEX")
_SHFE_HOSTS = {"SHFE": "https://www.shfe.com.cn", "INE": "https://www.ine.cn"}
CZCE_FILE_URL = "https://www.czce.com.cn/cn/DFSStaticFiles/{kind}/{year}/{ymd}/{name}.txt"
CZCE_FIRST_DAY = "20150921"      # 郑商所现行文件路径的第一天；更早是另一套无表头 CSV，未接入
CFFEX_DAILY_URL = "http://www.cffex.com.cn/fzjy/mrhq/{ym}/{dd}/{ymd}_1.csv"
CFFEX_DAILY_XML = "http://www.cffex.com.cn/fzjy/mrhq/{ym}/{dd}/index.xml"
CFFEX_RANK_URL = "http://www.cffex.com.cn/sj/ccpm/{ym}/{dd}/{product}_1.csv"
# 各品种持仓排名文件的第一天（2026-09-20 逐个二分实测，前一交易日都是 302 缺文件）
CFFEX_RANK_FIRST_DAY = {"IF": "20100416", "IH": "20150416", "IC": "20150416", "IM": "20220722",
                        "TS": "20180817", "TF": "20130906", "T": "20150320", "TL": "20230421"}
GFEX_DAILY_URL = "http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList"
SINA_HQ_URL = "https://hq.sinajs.cn/list="
SINA_FUT_KLINE_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{code}="
                      "/InnerFuturesNewService.getDailyKLine")
SGE_DAILY_URL = "https://www.sge.com.cn/graph/Dailyhq"
_CFFEX_SINA_PRODUCTS = ("IF", "IH", "IC", "IM", "TS", "TF", "T", "TL")
_DCE_HINT = ("大商所官网有 JS 反爬（纯 HTTP 返回 412），不提供官方日行情；大商所品种（豆粕 M、铁矿 I、塑料 L…）"
             "请用 futures_kline('M0') 取逐日 K 线（新浪），futures_realtime('M0') 取实时/收盘快照")
_FUT_COLUMNS = ["date", "exchange", "symbol", "product", "open", "high", "low", "close", "settle",
                "pre_settle", "volume", "open_interest", "oi_change", "turnover_10k"]
_OPT_COLUMNS = ["date", "exchange", "symbol", "series", "option_type", "strike", "open", "high",
                "low", "close", "settle", "pre_settle", "volume", "open_interest", "oi_change",
                "turnover_10k", "delta", "iv_pct", "series_iv_pct"]
_RANK_COLUMNS = ["date", "exchange", "level", "symbol", "rank", "volume_member", "volume",
                 "volume_chg", "long_member", "long_oi", "long_chg", "short_member", "short_oi",
                 "short_chg"]


def _fut_price(value):
    """期货/期权价格：0 不是有效价格（无成交时交易所填 0 或空），统一成 None。"""
    number = _v39_num(value)
    return None if number == 0 else number


def _fut_product(code):
    """合约代码的品种字母（rb2610 -> rb、IF2609 -> IF）；不是字母开头说明来源格式变了。"""
    match = re.match(r"[A-Za-z]+", str(code))
    if not match:
        raise RuntimeError(f"合约代码 {code!r} 不是字母开头，格式可能已变")
    return match.group(0)


def _fut_exchange(exchange):
    exchange = str(exchange).upper()
    if exchange == "DCE":
        raise ValueError(_DCE_HINT)
    if exchange not in FUTURES_EXCHANGES:
        raise ValueError("exchange 只能是 " + " / ".join(FUTURES_EXCHANGES) + "（大商所见 futures_kline / futures_realtime）")
    return exchange


def _shfe_json(exchange, path, ymd, key, allow_missing=False):
    """上期所 / 上期能源的 .dat（实为 JSON）。非交易日官网 404；allow_missing 时返回 (None, url)。
    key 是调用方要读的行列表字段（o_curinstrument / o_cursor）；顶层不是对象、它不是由对象组成的列表，抛 RuntimeError。"""
    url = f"{_SHFE_HOSTS[exchange]}/data/tradedata/{path}{ymd}.dat"
    first = _INE_FIRST_DAY.get(path) if exchange == "INE" else None
    if first and ymd < first:
        # 能源中心首日前有些日子也发文件，但里面没有该类合约；按「当时还没有」处理，不能报成格式改变
        if allow_missing:
            return None, url
        raise ValueError(f"上期能源该类数据从 {first} 起才有（{ymd} 早于首日）")
    response = _v39_http(url, timeout=(10, 60), allow_status=(404,))
    payload = None
    if response.status_code != 404:
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except ValueError as exc:       # 含 UnicodeDecodeError：错误页不是「没有数据」
            raise RuntimeError(f"{exchange} {url} 返回的不是 JSON，可能是错误页") from exc
        rows = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise RuntimeError(f"{exchange} {url} 的 {key} 不是由对象组成的列表，格式可能已变")
        reported = payload.get("report_date")
        if reported is not None and str(reported) != ymd:
            raise RuntimeError(f"{exchange} 返回的 report_date={reported}，不是 {ymd}")
        # 早年文件没有 report_date（上期所 2015 年的排名文件），只能以文件名里的日期为准；
        # 能源中心 2019 年的排名文件则是「没有 report_date + 列表为空」的空壳，按没有数据处理
        if reported is None and not any(v for v in payload.values() if isinstance(v, list)):
            payload = None
    if payload is None:
        if allow_missing:
            return None, url
        raise ValueError(f"{exchange} {ymd} 没有数据：非交易日、尚未发布或该品种当时未上市")
    return payload, url


def _czce_text(kind, name, ymd):
    if ymd < CZCE_FIRST_DAY:
        raise ValueError(f"郑商所数据从 {CZCE_FIRST_DAY} 起接入（更早的文件是另一套格式）")
    url = CZCE_FILE_URL.format(kind=kind, year=ymd[:4], ymd=ymd, name=name)
    response = _v39_http(url, timeout=(10, 60), allow_status=(404,))
    if response.status_code == 404:
        raise ValueError(f"郑商所 {ymd} 没有 {name}：非交易日或尚未发布")
    try:
        text = response.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = response.content.decode("gbk")   # 2017 年及以前的文件是 GBK
    if f"({ymd[:4]}-{ymd[4:6]}-{ymd[6:]})" not in text.split("\n", 1)[0] + text[:200]:
        raise RuntimeError(f"郑商所 {name} 标题里的日期不是 {ymd}")
    return text, url


# 郑商所 2021 年前的表头叫「品种月份 / 品种代码」「空盘量」，与现在的「合约代码」「持仓量」是同一列
_CZCE_HEADER_ALIAS = {"品种月份": "合约代码", "品种代码": "合约代码", "空盘量": "持仓量"}


def _czce_table(text, header_prefix):
    """郑商所竖线分隔表：返回 (表头, 数据行)，千分位逗号已去掉，小计/总计已剔除。"""
    lines = [line for line in text.splitlines() if "|" in line]
    header = [_CZCE_HEADER_ALIAS.get(c.strip(), c.strip()) for c in lines[0].split("|")] if lines else []
    if not header or header[0] != header_prefix:
        raise RuntimeError("郑商所文件表头改变")
    rows = []
    for line in lines[1:]:
        cells = [c.strip().replace(",", "") for c in line.split("|")]
        if not cells[0] or cells[0].endswith(("小计", "总计", "合计")):
            continue                            # 期权文件还有「AP合计」这类品种合计行
        rows.append(dict(zip(header, cells)))
    return header, rows


def _cffex_csv(url, allow_missing=False):
    response = _v39_http(url, timeout=(10, 60), allow_status=(302, 404), allow_redirects=False)
    # 中金所缺文件时 302 跳到 error_404 页面
    if response.status_code in (302, 404):
        if allow_missing:
            return None
        raise ValueError(f"中金所没有该文件（非交易日或尚未发布）: {url}")
    return list(csv.reader(io.StringIO(response.content.decode("gbk"))))


def _cffex_daily_table(ymd):
    """中金所日行情 CSV（期货 + 期权同一个文件）。CSV 里没有交易日列，同目录的 index.xml 每行都带
    tradingday；用它核对交易日，并逐合约核对成交量 / 收盘价 / 持仓量，对不上就抛 RuntimeError，
    不把别的交易日的文件标成这一天（2010–2026 抽 5 天实测两者逐合约一致）。"""
    url = CFFEX_DAILY_URL.format(ym=ymd[:6], dd=ymd[6:], ymd=ymd)
    table = _cffex_csv(url)
    if not table or table[0][:3] != ["合约代码", "今开盘", "最高价"]:
        raise RuntimeError("中金所日行情表头改变")
    xml_url = CFFEX_DAILY_XML.format(ym=ymd[:6], dd=ymd[6:])
    response = _v39_http(xml_url, timeout=(10, 60), allow_status=(302, 404), allow_redirects=False)
    if response.status_code in (302, 404):
        raise RuntimeError(f"中金所 {ymd} 有行情 CSV 却没有 index.xml，无法核对交易日: {xml_url}")
    if b"<!DOCTYPE" in response.content or b"<!ENTITY" in response.content:
        raise RuntimeError(f"中金所 index.xml 含 DOCTYPE / ENTITY，拒绝解析: {xml_url}")
    try:
        nodes = ElementTree.fromstring(response.content).findall("dailydata")
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"中金所 index.xml 无法解析: {exc}") from exc
    witness = {}
    for node in nodes:
        values = {k: (node.findtext(k) or "").strip() for k in
                  ("instrumentid", "tradingday", "volume", "closeprice", "openinterest")}
        if values["tradingday"] != ymd:
            raise RuntimeError(f"中金所 index.xml 的交易日是 {values['tradingday']}，不是 {ymd}")
        witness[values["instrumentid"]] = tuple(_v39_num(values[k]) for k in ("volume", "closeprice", "openinterest"))
    header = [c.strip() for c in table[0]]
    csv_rows = {}
    for rec in table[1:]:
        code = rec[0].strip() if rec else ""
        if not code or code in ("小计", "合计", "总计"):
            continue
        r = dict(zip(header, rec))
        csv_rows[code] = tuple(_v39_num(r.get(k)) for k in ("成交量", "今收盘", "持仓量"))
    if not witness or len(witness) != len(nodes) or csv_rows != witness:
        diff = sorted(set(csv_rows) ^ set(witness)) or sorted(k for k in csv_rows if csv_rows[k] != witness.get(k))
        raise RuntimeError(f"中金所 {ymd} 行情 CSV 与 index.xml 对不上（{len(csv_rows)} / {len(witness)} 个合约，"
                           f"例如 {diff[:3]}），不能确认 CSV 属于这一天")
    return table, url


_CFFEX_RANK_SUB = ["会员简称", "成交量", "比上一交易日增减", "会员简称", "持买单量", "比上一交易日增减",
                   "会员简称", "持卖单量", "比上一交易日增减"]


def _cffex_rank_rows(table, product, ymd):
    """中金所持仓排名 CSV → 行。只在核对过两行表头的「排名」段里取数，列顺序不对就抛错。
    2015 年前后的旧文件在排名段前面还有一段「会员类别」合计（表头同样以 交易日,合约 开头），跳过。"""
    out, in_rank, i = [], False, 0
    while i < len(table):
        cells = [c.strip() for c in table[i]]
        if cells[:2] == ["交易日", "合约"]:
            in_rank = cells[2:3] == ["排名"]
            if in_rank:
                sub = [c.strip() for c in table[i + 1][3:12]] if i + 1 < len(table) else []
                if cells[3:12:3] != ["成交量排名", "持买单量排名", "持卖单量排名"] or sub != _CFFEX_RANK_SUB:
                    raise RuntimeError(f"中金所 {product} 持仓排名表头变了: {cells} / {sub}")
                i += 1
        elif cells[2:3] and cells[2].isdigit():
            if not in_rank:
                raise RuntimeError(f"中金所 {product} 持仓排名在排名表头之前出现数据行: {cells}")
            if len(cells) < 12:
                raise RuntimeError(f"中金所 {product} 持仓排名行缺列: {cells}")
            if cells[0] != ymd:
                raise RuntimeError(f"中金所 {product} 持仓排名交易日是 {cells[0]}，不是 {ymd}")
            out.append({"level": "contract", "symbol": cells[1], "rank": int(cells[2]),
                        "volume_member": _rank_member(cells[3]), "volume": _v39_num(cells[4]),
                        "volume_chg": _v39_num(cells[5]),
                        "long_member": _rank_member(cells[6]), "long_oi": _v39_num(cells[7]),
                        "long_chg": _v39_num(cells[8]),
                        "short_member": _rank_member(cells[9]), "short_oi": _v39_num(cells[10]),
                        "short_chg": _v39_num(cells[11])})
        i += 1
    return out


def _gfex_rows(ymd, trade_type):
    response = _v39_http(GFEX_DAILY_URL, method="POST", data={"trade_date": ymd, "trade_type": trade_type},
                         headers={"Referer": "http://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml"})
    payload = _v39_json(response)
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        raise RuntimeError(f"广期所返回错误: {str(payload)[:200]}")
    # 广期所的行里没有交易日字段，只在 param 里回显请求参数；回显不符说明拿到的不是这次请求的结果。
    # 非交易日它返回空表（只剩合计行），不会回退到最近交易日（2026-09-19 周六实测）。
    param = payload.get("param")
    if not isinstance(param, dict):
        raise RuntimeError(f"广期所没有回显请求参数（param 应为对象）: {str(payload)[:200]}")
    data = _v39_rows(payload.get("data"), "广期所 data")
    if param.get("trade_date") not in ([ymd], ymd) or param.get("trade_type") not in ([str(trade_type)], str(trade_type)):
        raise RuntimeError(f"广期所回显的请求参数 {param} 与请求的 {ymd}/{trade_type} 不符")
    rows = []
    for r in data:
        if str(r.get("variety", "")).endswith(("小计", "总计")):
            continue
        # 实测（2022-12-22、2026-09-18/19）只有小计 / 总计行没有 delivMonth；字段改名时不能把合约行全部滤掉、报成非交易日
        if not r.get("delivMonth"):
            raise RuntimeError(f"广期所合约行缺 delivMonth，格式可能已变: {str(r)[:120]}")
        rows.append(r)
    if not rows:
        raise ValueError(f"广期所 {ymd} 没有行情：非交易日或尚未发布")
    return rows, response.url


# 能源中心各文件的第一天（2026-09-20 二分实测）：日行情 2018-03-26 开业即有；持仓排名 2020-07-03 起
# （07-02 仍是空壳，当时上期所排名里也没有能源品种）；期权日行情 2021-06-21 原油期权上市起
# （06-15~18 已发文件但里面没有期权合约，2026-09-22 复测）
_INE_FIRST_DAY = {"future/dailydata/kx": "20180326", "future/dailydata/pm": "20200703",
                  "option/dailydata/kx": "20210621"}


def _shfe_ine_ids(path, ymd, key, field):
    """上期所文件里混有上期能源的品种；取能源中心同一天的 ID 集合用来剔除。
    能源中心该文件第一天之前没有文件（或是空壳），上期所文件里也没有能源品种，返回空集合；
    第一天起缺文件不能当成「没有能源品种」，否则 sc 等合约会被标成上期所，直接抛 RuntimeError。"""
    payload, url = _shfe_json("INE", path, ymd, key, allow_missing=True)
    ids = {str(r[field]).strip() for r in payload[key]} if payload else set()
    if not ids and ymd >= _INE_FIRST_DAY[path]:
        raise RuntimeError(f"上期能源 {ymd} 的对照文件缺失或为空（{url}），无法从上期所数据里剔除能源品种；"
                           "可能尚未发布，稍后重试")
    return ids


@_v39_contract
def futures_daily(date, exchange):
    """期货日行情（交易所官方收盘数据）— 上期所 / 上期能源 / 郑商所 / 中金所 / 广期所。

    exchange: 'SHFE' / 'INE' / 'CZCE' / 'CFFEX' / 'GFEX'；大商所（DCE）官网有反爬，见 futures_kline / futures_realtime。
    一行一个合约（不含小计），settle 为当日结算价，turnover_10k 单位万元，价格为 0 的统一成 None。
    上期所的官方文件里也包含上期能源的品种（原油、20号胶等），这里按能源中心同日文件剔除，
    所以 SHFE 与 INE 两次调用不会重复。非交易日抛 ValueError。
    实测可用起点：上期所 2002-01-07 起（2021 年及以前没有成交额，turnover_10k 为 None）；
    上期能源 2018-03 开业；郑商所 2015-09-21 起（更早是另一套格式，未接入）；中金所 2010-04-16 开业即有；广期所 2022-12 开业。
    """
    exchange = _fut_exchange(exchange)
    day = _v39_date(date)
    ymd = day.replace("-", "")
    rows = []
    if exchange in ("SHFE", "INE"):
        payload, url = _shfe_json(exchange, "future/dailydata/kx", ymd, "o_curinstrument")
        skip = (_shfe_ine_ids("future/dailydata/kx", ymd, "o_curinstrument", "PRODUCTID")
                if exchange == "SHFE" else set())
        for r in payload["o_curinstrument"]:
            month = str(r.get("DELIVERYMONTH", "")).strip()
            product_id = r["PRODUCTID"].strip()
            # 期货品种 ID 以 _f 结尾（cu_f）；sc_tas 是原油 TAS 指令，价格恒为 0，不是独立合约。
            # 不用 PRODUCTCLASS 判断：这个字段 2023 年才出现
            if not product_id.endswith("_f") or not month.isdigit() or product_id in skip:
                continue
            rows.append({"symbol": product_id[:-2] + month,
                         "product": r["PRODUCTNAME"].strip(),
                         "open": _fut_price(r["OPENPRICE"]), "high": _fut_price(r["HIGHESTPRICE"]),
                         "low": _fut_price(r["LOWESTPRICE"]), "close": _fut_price(r["CLOSEPRICE"]),
                         "settle": _fut_price(r["SETTLEMENTPRICE"]),
                         "pre_settle": _fut_price(r["PRESETTLEMENTPRICE"]),
                         "volume": _v39_num(r["VOLUME"]), "open_interest": _v39_num(r["OPENINTEREST"]),
                         "oi_change": _v39_num(r["OPENINTERESTCHG"]),
                         "turnover_10k": _v39_num(r.get("TURNOVER"))})   # 2021 年及以前的文件没有成交额，为 None
    elif exchange == "CZCE":
        text, url = _czce_text("Future", "FutureDataDaily", ymd)
        _, table = _czce_table(text, "合约代码")
        for r in table:
            rows.append({"symbol": r["合约代码"], "product": _fut_product(r["合约代码"]),
                         "open": _fut_price(r["今开盘"]), "high": _fut_price(r["最高价"]),
                         "low": _fut_price(r["最低价"]), "close": _fut_price(r["今收盘"]),
                         "settle": _fut_price(r["今结算"]), "pre_settle": _fut_price(r["昨结算"]),
                         "volume": _v39_num(r["成交量(手)"]), "open_interest": _v39_num(r["持仓量"]),
                         "oi_change": _v39_num(r["增减量"]), "turnover_10k": _v39_num(r["成交额(万元)"])})
    elif exchange == "CFFEX":
        table, url = _cffex_daily_table(ymd)
        for rec in table[1:]:
            code = rec[0].strip()
            if not code or code in ("小计", "合计", "总计") or "-C-" in code or "-P-" in code:
                continue
            r = dict(zip(table[0], rec))
            rows.append({"symbol": code, "product": _fut_product(code),
                         "open": _fut_price(r["今开盘"]), "high": _fut_price(r["最高价"]),
                         "low": _fut_price(r["最低价"]), "close": _fut_price(r["今收盘"]),
                         "settle": _fut_price(r["今结算"]), "pre_settle": _fut_price(r["前结算"]),
                         "volume": _v39_num(r["成交量"]), "open_interest": _v39_num(r["持仓量"]),
                         "oi_change": _v39_num(r["持仓变化"]), "turnover_10k": _v39_num(r["成交金额"])})
    else:
        data, url = _gfex_rows(ymd, 0)
        for r in data:
            rows.append({"symbol": r["varietyOrder"] + r["delivMonth"], "product": r["variety"],
                         "open": _fut_price(r["open"]), "high": _fut_price(r["high"]),
                         "low": _fut_price(r["low"]), "close": _fut_price(r["close"]),
                         "settle": _fut_price(r["clearPrice"]), "pre_settle": _fut_price(r["lastClear"]),
                         "volume": _v39_num(r["volumn"]), "open_interest": _v39_num(r["openInterest"]),
                         "oi_change": _v39_num(r["diffI"]), "turnover_10k": _v39_num(r["turnover"])})
    if not rows:
        raise RuntimeError(f"{exchange} {day} 解析出 0 个期货合约，格式可能已变")
    for row in rows:
        row["date"], row["exchange"] = day, exchange
    frame = _v39_frame(rows, exchange.lower(), url, _FUT_COLUMNS)
    if frame.duplicated(["symbol"]).any():
        raise RuntimeError(f"{exchange} {day} 期货合约代码重复")
    return frame


# 郑商所 CF/RM/OI/SR 另有带两位字母后缀的系列（如 CF701MSC14400），后缀并入 series 原样保留
_OPTION_CODE = re.compile(r"^([A-Za-z]+\d{3,4}(?:[A-Z]{2})?)-?([CP])-?(\d+(?:\.\d+)?)$")


@_v39_contract
def options_daily(date, exchange):
    """商品期权 / 股指期权日行情（交易所官方）— 上期所 / 上期能源 / 郑商所 / 中金所 / 广期所。

    series：期权系列（商品期权 = 标的期货合约，如 cu2610；中金所 = HO/IO/MO + 月份；
    郑商所部分品种另有 CF701MS 这类带后缀的系列，按官方代码原样保留，与 CF701 分开）。
    delta：交易所公布值（中金所不公布，为 None）。
    iv_pct：逐合约隐含波动率 %（郑商所、广期所公布）；series_iv_pct：上期所/能源中心按系列公布的
    隐含波动率（官方 SIGMA × 100）。ETF 期权不在这里，见 Layer 9。非交易日抛 ValueError。
    各所期权上市时间不同（上期所铜期权 2018-09、郑商所白糖期权 2017-04），之前的日期抛 ValueError。
    """
    exchange = _fut_exchange(exchange)
    day = _v39_date(date)
    ymd = day.replace("-", "")
    rows = []
    if exchange in ("SHFE", "INE"):
        payload, url = _shfe_json(exchange, "option/dailydata/kx", ymd, "o_curinstrument")
        skip = (_shfe_ine_ids("option/dailydata/kx", ymd, "o_curinstrument", "PRODUCTID")
                if exchange == "SHFE" else set())
        sigma_rows = _v39_rows(payload.get("o_cursigma"), f"{exchange} {url} 的 o_cursigma")
        if not all("INSTRUMENTID" in r for r in sigma_rows):
            raise RuntimeError(f"{exchange} {url} 的 o_cursigma 行没有 INSTRUMENTID")
        # 字典推导会让重复的 INSTRUMENTID 静默相互覆盖，返回错误的隐含波动率。
        # 2026-09-20 抽查 10 个交易日（2018-09-21 期权首日起）：只有按品种汇总的「小计」行
        # 重复且 SIGMA 恒为空，真实系列 ID 不重复、SIGMA 无空值。
        sigma = {}
        for r in sigma_rows:
            series_id = str(r["INSTRUMENTID"]).strip()
            if series_id in ("小计", "合计", "总计"):
                continue
            if not series_id:
                raise RuntimeError(f"{exchange} {url} 的 o_cursigma 有空的 INSTRUMENTID")
            if series_id in sigma:
                raise RuntimeError(f"{exchange} {url} 的 o_cursigma 里 {series_id} 出现两次，"
                                   "隐含波动率会互相覆盖")
            sigma[series_id] = _v39_req_num(r.get("SIGMA"), f"{exchange} {series_id} 的 SIGMA")
        for r in payload["o_curinstrument"]:
            code = str(r.get("INSTRUMENTID", "")).strip()
            kind = {"1": "C", "2": "P"}.get(str(r.get("OPTIONSTYPE")))
            if kind is None or r["PRODUCTID"].strip() in skip:
                continue                        # 小计 / 总计 行 OPTIONSTYPE 为空
            parsed = _OPTION_CODE.match(code)
            if not parsed or parsed.group(2) != kind:
                raise RuntimeError(f"{exchange} 期权 {code} 的代码与 OPTIONSTYPE={r.get('OPTIONSTYPE')} 不一致")
            series = str(r["UNDERLYINGINSTRID"]).strip()
            if series not in sigma:     # 实测 10 个交易日里主表每个系列都有 sigma 行
                raise RuntimeError(f"{exchange} {url} 的 o_cursigma 里没有系列 {series}，结果不完整")
            iv = sigma[series]
            rows.append({"symbol": code, "series": series, "option_type": kind,
                         "strike": _v39_num(r["STRIKEPRICE"]),
                         "open": _fut_price(r["OPENPRICE"]), "high": _fut_price(r["HIGHESTPRICE"]),
                         "low": _fut_price(r["LOWESTPRICE"]), "close": _fut_price(r["CLOSEPRICE"]),
                         "settle": _fut_price(r["SETTLEMENTPRICE"]),
                         "pre_settle": _fut_price(r["PRESETTLEMENTPRICE"]),
                         "volume": _v39_num(r["VOLUME"]), "open_interest": _v39_num(r["OPENINTEREST"]),
                         "oi_change": _v39_num(r["OPENINTERESTCHG"]), "turnover_10k": _v39_num(r["TURNOVER"]),
                         "delta": _v39_num(r.get("DELTA")), "iv_pct": None,
                         "series_iv_pct": round(iv * 100, 4) if iv is not None else None})
    elif exchange == "CZCE":
        text, url = _czce_text("Option", "OptionDataDaily", ymd)
        if "无交易记录" in text:
            raise ValueError(f"郑商所 {ymd} 没有期权成交记录（郑商所期权 2017-04-19 起上市）")
        _, table = _czce_table(text, "合约代码")
        for r in table:
            parsed = _OPTION_CODE.match(r["合约代码"])
            if not parsed:
                raise RuntimeError(f"郑商所期权代码无法解析: {r['合约代码']}")
            rows.append({"symbol": r["合约代码"], "series": parsed.group(1), "option_type": parsed.group(2),
                         "strike": _v39_num(parsed.group(3)),
                         "open": _fut_price(r["今开盘"]), "high": _fut_price(r["最高价"]),
                         "low": _fut_price(r["最低价"]), "close": _fut_price(r["今收盘"]),
                         "settle": _fut_price(r["今结算"]), "pre_settle": _fut_price(r["昨结算"]),
                         "volume": _v39_num(r["成交量(手)"]), "open_interest": _v39_num(r["持仓量"]),
                         "oi_change": _v39_num(r["增减量"]), "turnover_10k": _v39_num(r["成交额(万元)"]),
                         "delta": _v39_num(r["DELTA"]), "iv_pct": _v39_num(r["隐含波动率"]),
                         "series_iv_pct": None})
    elif exchange == "CFFEX":
        table, url = _cffex_daily_table(ymd)
        for rec in table[1:]:
            code = rec[0].strip()
            if "-C-" not in code and "-P-" not in code:
                continue
            parsed = _OPTION_CODE.match(code)
            if not parsed:
                raise RuntimeError(f"中金所期权代码无法解析: {code}")
            r = dict(zip(table[0], rec))
            rows.append({"symbol": code, "series": parsed.group(1), "option_type": parsed.group(2),
                         "strike": _v39_num(parsed.group(3)),
                         "open": _fut_price(r["今开盘"]), "high": _fut_price(r["最高价"]),
                         "low": _fut_price(r["最低价"]), "close": _fut_price(r["今收盘"]),
                         "settle": _fut_price(r["今结算"]), "pre_settle": _fut_price(r["前结算"]),
                         "volume": _v39_num(r["成交量"]), "open_interest": _v39_num(r["持仓量"]),
                         "oi_change": _v39_num(r["持仓变化"]), "turnover_10k": _v39_num(r["成交金额"]),
                         "delta": None, "iv_pct": None, "series_iv_pct": None})
    else:
        data, url = _gfex_rows(ymd, 1)
        for r in data:
            parsed = _OPTION_CODE.match(r["delivMonth"])
            if not parsed:
                raise RuntimeError(f"广期所期权代码无法解析: {r['delivMonth']}")
            rows.append({"symbol": r["delivMonth"], "series": parsed.group(1), "option_type": parsed.group(2),
                         "strike": _v39_num(parsed.group(3)),
                         "open": _fut_price(r["open"]), "high": _fut_price(r["high"]),
                         "low": _fut_price(r["low"]), "close": _fut_price(r["close"]),
                         "settle": _fut_price(r["clearPrice"]), "pre_settle": _fut_price(r["lastClear"]),
                         "volume": _v39_num(r["volumn"]), "open_interest": _v39_num(r["openInterest"]),
                         "oi_change": _v39_num(r["diffI"]), "turnover_10k": _v39_num(r["turnover"]),
                         "delta": _v39_num(r["delta"]), "iv_pct": _v39_num(r["impliedVolatility"]),
                         "series_iv_pct": None})
    if not rows:
        raise RuntimeError(f"{exchange} {day} 解析出 0 个期权合约，格式可能已变")
    for row in rows:
        row["date"], row["exchange"] = day, exchange
    frame = _v39_frame(rows, exchange.lower(), url, _OPT_COLUMNS)
    if frame.duplicated(["symbol"]).any():
        raise RuntimeError(f"{exchange} {day} 期权合约代码重复")
    return frame


def _rank_member(value):
    value = str(value or "").strip()
    return value if value and value != "-" else None


@_v39_contract
def futures_position_rank(date, exchange, symbol=None):
    """期货会员成交量 / 持买单 / 持卖单前 20 名（交易所官方持仓排名）。

    exchange: 'SHFE' / 'INE' / 'CZCE' / 'CFFEX'（广期所、大商所未接入）。
    level='contract' 为单个合约；level='product' 为品种合计，只有郑商所公布（symbol 为品种字母，如 AP）。
    上期所 / 能源中心文件里的 cuall 行是按会员类型的汇总、没有名次，已剔除。
    symbol 可选，按合约或品种过滤（不区分大小写）。中金所按 IF/IH/IC/IM/TS/TF/T/TL 各取一个文件，
    当天已上市的品种缺任何一个都抛 RuntimeError（不返回部分品种）；source_url 列出实际读取的文件。
    上期能源 2019 年的排名文件是空的（抛 ValueError），实测 2021 年起有数据。
    """
    exchange = _fut_exchange(exchange)
    if exchange == "GFEX":
        raise ValueError("广期所持仓排名未接入")
    day = _v39_date(date)
    ymd = day.replace("-", "")
    rows = []
    if exchange in ("SHFE", "INE"):
        payload, url = _shfe_json(exchange, "future/dailydata/pm", ymd, "o_cursor")
        skip = (_shfe_ine_ids("future/dailydata/pm", ymd, "o_cursor", "INSTRUMENTID")
                if exchange == "SHFE" else set())
        for r in payload["o_cursor"]:
            code = str(r["INSTRUMENTID"]).strip()
            rank = _v39_num(r["RANK"])
            if rank is None:
                raise RuntimeError(f"{exchange} {code} 持仓排名缺名次字段")
            # RANK 1–20 为会员名次；999 = 该合约合计，-1 / 0 = 按会员类型汇总
            if not 1 <= rank <= 20 or code in skip:
                continue
            rows.append({"level": "contract", "symbol": code,
                         "rank": int(rank),
                         "volume_member": _rank_member(r["PARTICIPANTABBR1"]), "volume": _v39_num(r["CJ1"]),
                         "volume_chg": _v39_num(r["CJ1_CHG"]),
                         "long_member": _rank_member(r["PARTICIPANTABBR2"]), "long_oi": _v39_num(r["CJ2"]),
                         "long_chg": _v39_num(r["CJ2_CHG"]),
                         "short_member": _rank_member(r["PARTICIPANTABBR3"]), "short_oi": _v39_num(r["CJ3"]),
                         "short_chg": _v39_num(r["CJ3_CHG"])})
    elif exchange == "CZCE":
        text, url = _czce_text("Future", "FutureDataHolding", ymd)
        level = code = None
        for line in text.splitlines():
            head = re.match(r"^(品种|合约)：\s*(\S+)\s+日期：", line)
            if head:
                level = "product" if head.group(1) == "品种" else "contract"
                found = re.search(r"[A-Za-z]+\d*$", head.group(2))
                if not found:
                    raise RuntimeError(f"郑商所持仓排名表头认不出品种 / 合约: {line[:60]}")
                code = found.group(0)
                continue
            cells = [c.strip().replace(",", "") for c in line.split("|")]
            if len(cells) < 10 or not cells[0].isdigit():
                continue                        # 表头、合计行
            if code is None:
                raise RuntimeError("郑商所持仓排名在品种/合约标题之前出现数据行")
            rows.append({"level": level, "symbol": code, "rank": int(cells[0]),
                         "volume_member": _rank_member(cells[1]), "volume": _v39_num(cells[2]),
                         "volume_chg": _v39_num(cells[3]),
                         "long_member": _rank_member(cells[4]), "long_oi": _v39_num(cells[5]),
                         "long_chg": _v39_num(cells[6]),
                         "short_member": _rank_member(cells[7]), "short_oi": _v39_num(cells[8]),
                         "short_chg": _v39_num(cells[9])})
    else:
        expected = [p for p, first in CFFEX_RANK_FIRST_DAY.items() if ymd >= first]
        if not expected:
            raise ValueError("中金所持仓排名从 2010-04-16（沪深300 期货上市）起才有")
        urls, missing = [], []
        for product in expected:                # 当时还没上市的品种（如 2022 年前的 IM）不请求
            file_url = CFFEX_RANK_URL.format(ym=ymd[:6], dd=ymd[6:], product=product)
            table = _cffex_csv(file_url, allow_missing=True)
            if table is None:
                missing.append(product)
                continue
            urls.append(file_url)
            parsed = _cffex_rank_rows(table, product, ymd)
            rows.extend(parsed)
            if not parsed:
                raise RuntimeError(f"中金所 {product} {day} 持仓排名文件解析出 0 行，格式可能已变")
        if len(missing) == len(expected):
            raise ValueError(f"中金所 {day} 没有持仓排名：非交易日或尚未发布")
        if missing:
            raise RuntimeError(f"中金所 {day} 缺少已上市品种 {'/'.join(missing)} 的持仓排名，结果不完整"
                               "（可能尚未全部发布，稍后重试）")
        url = " | ".join(urls)
    if not rows:
        raise RuntimeError(f"{exchange} {day} 持仓排名解析出 0 行，格式可能已变")
    for row in rows:
        row["date"], row["exchange"] = day, exchange
    frame = _v39_frame(rows, exchange.lower(), url, _RANK_COLUMNS)
    if frame.duplicated(["level", "symbol", "rank"]).any():
        raise RuntimeError(f"{exchange} {day} 持仓排名 合约+名次 重复")
    if symbol:
        frame = frame[frame["symbol"].str.upper() == str(symbol).upper()].reset_index(drop=True)
    return frame


def _sina_hq(codes):
    response = _v39_http(SINA_HQ_URL + ",".join(codes), headers={"Referer": "https://finance.sina.com.cn/"})
    out = {}
    for key, body in re.findall(r'var hq_str_([^=]+)="([^"]*)"', response.content.decode("gbk", "replace")):
        out[key] = body.split(",") if body else []
    # 代码不存在时新浪照样回一个空内容的变量（实测 nf_ZZ9999）；一个变量都没有说明页面变了，
    # 否则会把格式改变报成「代码不存在或已摘牌」
    if not out:
        raise RuntimeError(f"新浪行情页没有 hq_str 变量（{response.url}），格式可能已变")
    return out, response.url


@_v39_contract
def futures_realtime(symbols):
    """国内期货实时行情（新浪）— 覆盖全部六家交易所，大商所品种只能走这里。

    symbols: 'RB0'（主力连续）/ 'CU2610' / 'IF2609' / 'M0' 等，可传列表；带不带 'nf_' 前缀都行。
    中金所品种（IF/IH/IC/IM/TS/TF/T/TL）另有 pre_close / 涨跌停价。无效代码抛 ValueError。
    盘中是实时价；收盘后是当日收盘快照，结算价以 futures_daily 为准。
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    if not isinstance(symbols, (list, tuple, set)) or not symbols:
        raise ValueError("symbols 需为非空的代码或代码列表（例 'RB0' / ['RB0', 'IF2609']）")
    codes = []
    for raw in symbols:
        code = str(raw).strip()
        code = code[3:] if code.lower().startswith("nf_") else code
        if not re.fullmatch(r"[A-Za-z]{1,2}\d{1,4}", code):
            raise ValueError(f"期货代码格式不对: {raw}（例 RB0 / CU2610 / IF2609）")
        codes.append(code.upper())
    data, url = _sina_hq(["nf_" + c for c in codes])
    rows = []
    for code in codes:
        fields = data.get("nf_" + code)
        if not fields:
            raise ValueError(f"新浪没有期货 {code} 的行情（代码不存在或已摘牌）")
        product = _fut_product(code)
        if product in _CFFEX_SINA_PRODUCTS:
            if len(fields) < 50:
                raise RuntimeError(f"新浪中金所期货 {code} 字段数 {len(fields)}，格式可能已变")
            rows.append({"symbol": code, "name": fields[49], "datetime": f"{fields[36]} {fields[37]}",
                         "open": _fut_price(fields[0]), "high": _fut_price(fields[1]),
                         "low": _fut_price(fields[2]), "last": _fut_price(fields[3]),
                         "bid": _fut_price(fields[16]), "ask": _fut_price(fields[26]),
                         "bid_vol": _v39_num(fields[17]), "ask_vol": _v39_num(fields[27]),
                         "volume": _v39_num(fields[4]), "open_interest": _v39_num(fields[6]),
                         "pre_settle": _fut_price(fields[14]), "pre_close": _fut_price(fields[13]),
                         "upper_limit": _fut_price(fields[9]), "lower_limit": _fut_price(fields[10]),
                         "avg_price": _fut_price(fields[48])})
        else:
            if len(fields) < 28:
                raise RuntimeError(f"新浪商品期货 {code} 字段数 {len(fields)}，格式可能已变")
            clock = fields[1].zfill(6)
            rows.append({"symbol": code, "name": fields[0],
                         "datetime": f"{fields[17]} {clock[:2]}:{clock[2:4]}:{clock[4:]}",
                         "open": _fut_price(fields[2]), "high": _fut_price(fields[3]),
                         "low": _fut_price(fields[4]), "last": _fut_price(fields[8]),
                         "bid": _fut_price(fields[6]), "ask": _fut_price(fields[7]),
                         "bid_vol": _v39_num(fields[11]), "ask_vol": _v39_num(fields[12]),
                         "volume": _v39_num(fields[14]), "open_interest": _v39_num(fields[13]),
                         "pre_settle": _fut_price(fields[10]), "pre_close": None,
                         "upper_limit": None, "lower_limit": None, "avg_price": _fut_price(fields[27])})
    return _v39_frame(rows, "sina", url)


@_v39_contract
def futures_kline(symbol, start=None, end=None):
    """国内期货日 K 线（新浪）— 单个合约或主力连续的逐日序列，覆盖全部六家交易所（含大商所）。

    symbol: 'RB0' / 'M0'（主力连续）或 'RB2601' / 'M2601' / 'IF2612'；郑商所也写 4 位年月（'MA2601'），带不带 'nf_' 前缀都行。
    start/end: 'YYYY-MM-DD'，可只给一端。主力连续换月当天会跳空，未做复权。
    实测（2026-09-22）价格与交易所官方 futures_daily 逐日一致，成交量 / 持仓偶有 ≤0.1% 的出入，精确值以 futures_daily 为准。
    结算价新浪给得不全（给 0 的统一成 None）：中金所品种基本没有；主力连续早年缺得多（CU0 5285 根缺 1364 根，
    最晚缺到 2024-09-25），需要结算价用 futures_daily。具体合约只能取到约 2022 年起到期的，更早的新浪返回空；
    中金所主力连续（IF0 / IC0 / IH0 / T0 / TF0）只从 2017-01-17 起，更早的用 futures_daily。
    新浪每次都返回该代码的全部历史（RB0 约 0.5 MB），start / end 是取回后本地筛选。
    代码不存在 / 太老、区间内没有 K 线抛 ValueError；返回格式改变抛 RuntimeError。
    """
    code = str(symbol).strip()
    code = code[3:] if code.lower().startswith("nf_") else code
    if not re.fullmatch(r"[A-Za-z]{1,2}\d{1,4}", code):
        raise ValueError(f"期货代码格式不对: {symbol}（例 RB0 / RB2601 / MA2601）")
    code = code.upper()
    lo = _v39_date(start) if start else None
    hi = _v39_date(end) if end else None
    if lo and hi and lo > hi:
        raise ValueError(f"start {lo} 晚于 end {hi}")
    response = _v39_http(SINA_FUT_KLINE_URL.format(code=code), params={"symbol": code}, timeout=(5, 20),
                         headers={"Referer": "https://finance.sina.com.cn/"}, session=V39_RETRY_SESSION)
    text = response.content.decode("gbk", "replace")
    match = re.search(rf"var _{re.escape(code)}=\((.*)\);?\s*$", text, re.S)
    if not match:
        raise RuntimeError(f"新浪期货日 K {code} 的返回不是预期的 JSONP（{response.url}），格式可能已变")
    body = match.group(1).strip()
    if body == "null":
        raise ValueError(f"新浪没有期货 {code} 的日 K：代码不存在，或是约 2022 年以前到期的老合约"
                         "（郑商所也要写 4 位年月，如 MA2601）")
    try:
        items = json.loads(body)
    except ValueError as exc:
        raise RuntimeError(f"新浪期货日 K {code} 的返回不是 JSON，格式可能已变") from exc
    rows, seen = [], set()
    for r in _v39_rows(items, f"新浪期货日 K {code}"):
        day = _v39_src_date(r["d"])
        if day in seen:
            raise RuntimeError(f"新浪期货日 K {code} 同一天 {day} 出现两次，结果不可信")
        seen.add(day)
        if (lo and day < lo) or (hi and day > hi):
            continue
        rows.append({"date": day, "symbol": code,
                     "open": _fut_price(r["o"]), "high": _fut_price(r["h"]),
                     "low": _fut_price(r["l"]), "close": _fut_price(r["c"]),
                     "settle": _fut_price(r["s"]),       # 新浪没有结算价时给 0 → None
                     "volume": _v39_num(r["v"]), "open_interest": _v39_num(r["p"])})
    if not rows:
        raise ValueError(f"新浪期货 {code} 在所给区间内没有日 K（合约当时未上市或已到期）")
    rows.sort(key=lambda row: row["date"])
    return _v39_frame(rows, "sina", response.url,
                      ["date", "symbol", "open", "high", "low", "close", "settle", "volume", "open_interest"])


@_v39_contract
def a50_futures():
    """富时中国 A50 期指（新浪 hf_CHA50CFD，连续合约报价）— 盘前/夜盘看 A 股外资情绪。"""
    data, url = _sina_hq(["hf_CHA50CFD"])
    fields = data.get("hf_CHA50CFD")
    if not fields or len(fields) < 14:
        raise RuntimeError("新浪 A50 期指行情为空或字段数不对")
    row = {"name": fields[13], "datetime": f"{fields[12]} {fields[6]}",
           "last": _fut_price(fields[0]), "open": _fut_price(fields[8]),
           "high": _fut_price(fields[4]), "low": _fut_price(fields[5]),
           "bid": _fut_price(fields[2]), "ask": _fut_price(fields[3]),
           "pre_settle": _fut_price(fields[7])}
    if row["last"] is None:
        raise RuntimeError("新浪 A50 期指最新价为空")
    return _v39_frame([row], "sina", url)


@_v39_contract
def sge_spot(instrument="Au99.99"):
    """上海黄金交易所现货日线（官方）— 2016-12 至今。

    instrument 例: 'Au99.99' / 'Au(T+D)' / 'mAu(T+D)' / 'Ag(T+D)' / 'Ag99.99' / 'Pt99.95'。
    黄金单位 元/克，白银 元/千克。无成交日交易所填 0，这里剔除；代码不存在时上金所返回全 0，抛 ValueError。
    少数日子收盘价略超出高低区间是原始数据如此，日期列在 attrs['ohlc_anomaly_dates']。
    """
    response = _v39_http(SGE_DAILY_URL, method="POST", data={"instid": instrument},
                         headers={"Referer": "https://www.sge.com.cn/"})
    payload = _v39_json(response)
    series = payload.get("time") if isinstance(payload, dict) else None
    if not isinstance(series, list):
        raise RuntimeError("上金所日线返回结构改变")
    rows = []
    for rec in series:
        if not isinstance(rec, list) or len(rec) != 5:
            raise RuntimeError(f"上金所日线字段数不对: {rec}")
        prices = [_v39_num(v) for v in rec[1:]]
        # 实测 7 个品种约 1.5 万条全是数字，无成交填 0；空值 / NaN 说明格式变了，不能当无成交跳过
        if None in prices:
            raise RuntimeError(f"上金所日线出现空值或非有限数值: {rec}")
        if not all(prices):
            continue
        open_, close, low, high = prices
        rows.append({"date": _v39_src_date(rec[0]), "instrument": instrument, "open": open_,
                     "high": high, "low": low, "close": close})
    if not rows:
        raise ValueError(f"上金所没有 {instrument} 的有效行情（代码不存在时返回全 0）")
    # 实测 Au99.99 在 2017–2018 年有 17 天收盘价略超出当日高低区间（上金所原始数据如此，
    # 不是高低列颠倒）。个别日子保留原值、记在 attrs；大面积不成立才说明字段顺序变了。
    bad = [r["date"] for r in rows if not r["low"] <= min(r["open"], r["close"]) <= max(r["open"], r["close"]) <= r["high"]]
    if len(bad) > 0.05 * len(rows):
        raise RuntimeError(f"上金所日线 {len(bad)}/{len(rows)} 天高低开收关系不成立，字段顺序可能已变")
    frame = _v39_frame(rows, "sge", response.url)
    frame.attrs["ohlc_anomaly_dates"] = bad
    return frame
