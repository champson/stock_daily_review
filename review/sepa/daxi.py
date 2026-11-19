"""Strict, optional Daxi VCP candidate input; credentials never enter source data.

Protocol evidence (static inspection, no publisher CLI execution):
- https://daxiapi.com/article/help/2026-06-18-daxiapi-cli-guide/
- https://registry.npmjs.org/daxiapi-cli/3.3.1 and its pinned dist/index.js:
  webpack module 53613 uses POST /coze/get_pattern_stocks, body {pattern:'vcp'},
  Bearer authentication and an errCode==0/data envelope. Module 53346 exposes
  `daxiapi stock pattern vcp`, optionally --mode, not --date. Module 65652
  prints data with console.log; there is no --json/--format switch. The bundled
  HTTP client disables proxies, so it must not be executed in this Cloud path.
- https://daxiapi.com/article/help/2026-06-18-sql-screener-guide/ documents a
  separate dated SQL command, limited to ten days; this adapter does not call it.

The success envelope is confirmed by the published client. The INNER schema
below is deliberately a CANDIDATE CONTRACT UNDER REVIEW, not a verified live
response example: data={date:'YYYY-MM-DD', rows:[{stockId, name?, signal_date?,
 snapshot_date?, date?}]}. No authenticated response has been observed. Unknown
text/TOON, arrays without a publisher snapshot date, and other shapes fail
closed. A raw row's generic `date` is retained as publisher_date, never silently
reinterpreted as a signal date. as_of asserts the response snapshot date; it is
not sent as a fabricated historical parameter to this latest-only endpoint.
"""
import http.client
import json
import os
import urllib.error
import urllib.request

from ..mcp_client import NoRedirect
from .sources import _day, _now_shanghai, normalize_symbol

ENDPOINT = "https://daxiapi.com/coze/get_pattern_stocks"
CLIENT_PACKAGE = "daxiapi-cli"
CLIENT_VERSION = "3.3.1"
CLIENT_INTEGRITY = "sha512-mcFP5dWfXy+huwyVoIJEhcZ/0zxEVQjMGUFUweQAhzbpaZ2rxRFjOYvy9hZnya2LcP5tPvHMksRF+Ys2KeFNhA=="
SCHEMA_STATUS = "candidate_under_review_no_authenticated_sample"
MAX_RESPONSE_BYTES = 2_000_000
REQUEST_TIMEOUT = 30


class DaxiDataError(ValueError):
    """Unavailable source or unsupported protocol, distinct from a proven empty pool."""

    def __init__(self, message, code="source_unavailable"):
        super().__init__(message)
        self.code = code


def _date(value, label):
    try:
        return _day(value, label)
    except (TypeError, ValueError):
        raise DaxiDataError(f"{label}缺失或不是有效的 YYYY-MM-DD", "invalid_date") from None


def _provider_failure(code):
    if code in (401, 403, "401", "403", "MISSING_API_KEY", "INVALID_TOKEN", "TOKEN_EXPIRED"):
        return DaxiDataError("大虾皮认证或访问权限不可用；请检查 DAXIAPI_TOKEN 与账户权限", "authentication_failed")
    if code in (429, "429"):
        return DaxiDataError("大虾皮调用受限；请按服务配额稍后重试", "rate_limited")
    return DaxiDataError("大虾皮接口未成功；请检查服务、凭据、权限或额度", "publisher_error")


def _parse_candidates(payload, day):
    """Validate the explicitly provisional inner contract; never infer a data date."""
    if not isinstance(payload, dict) or "errCode" not in payload:
        raise DaxiDataError("大虾皮响应缺少已核验的成功状态字段", "unsupported_schema")
    code = payload["errCode"]
    # The actual client uses JavaScript strict comparison with numeric zero.
    if isinstance(code, bool) or not isinstance(code, int) or code != 0:
        raise _provider_failure(code)
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise DaxiDataError("大虾皮候选内层格式尚未核验，不能把未知响应当成空名单", "unsupported_schema")
    snapshot = _date(data.get("date"), "供应商候选快照日期")
    if snapshot != day:
        raise DaxiDataError("大虾皮候选快照日期与复盘日不一致，拒绝使用过期或未来名单", "snapshot_date_mismatch")
    if len(data["rows"]) > 20000:
        raise DaxiDataError("大虾皮候选行数超过验证上限", "unsupported_schema")
    unique = {}
    for raw_row in data["rows"]:
        if not isinstance(raw_row, dict):
            raise DaxiDataError("大虾皮候选记录不是对象", "invalid_candidate")
        try:
            symbol = normalize_symbol(raw_row.get("stockId"))
        except (TypeError, ValueError):
            raise DaxiDataError("大虾皮候选缺少无歧义的 A 股 stockId 字符串", "invalid_candidate") from None
        row = {"symbol": symbol, "source": "daxiapi", "snapshot_date": snapshot}
        name = raw_row.get("name")
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise DaxiDataError("大虾皮候选名称格式无效", "invalid_candidate")
            row["name"] = name.strip()
        if raw_row.get("snapshot_date") is not None:
            if _date(raw_row["snapshot_date"], "候选记录快照日期") != snapshot:
                raise DaxiDataError("候选记录快照日期与名单快照不一致", "snapshot_date_mismatch")
        for input_field, output_field in (("signal_date", "signal_date"), ("date", "publisher_date")):
            if raw_row.get(input_field) is not None:
                published = _date(raw_row[input_field], "候选记录日期")
                if published > snapshot:
                    raise DaxiDataError("候选记录日期超过供应商快照日期", "invalid_candidate")
                row[output_field] = published
        if symbol in unique and unique[symbol] != row:
            raise DaxiDataError("大虾皮重复候选的名称或日期存在冲突", "invalid_candidate")
        unique.setdefault(symbol, row)
    return [unique[symbol] for symbol in sorted(unique)]


def load_daxi_candidates(as_of, *, command="daxiapi"):
    """Return {date, source, rows, raw}, or raise ValueError when unverified.

    `command` is retained for the caller contract only: no CLI is executed and
    overrides are refused. A successful return with rows=[] means a numeric
    errCode=0 plus an explicit, matching publisher snapshot and empty rows. It
    never means authentication, missing data, or an unknown response was ignored.
    `source.schema_status` continues to disclose the lack of a live schema sample.
    """
    day = _date(as_of, "复盘日")
    if day > _now_shanghai().date().isoformat():
        raise DaxiDataError("复盘日超过北京时间今天，不能请求未来候选", "future_date")
    if command != "daxiapi":
        raise DaxiDataError("大虾皮适配器仅使用已固定的第一方 HTTPS 端点，不执行自定义命令", "unsupported_command")
    token = os.environ.get("DAXIAPI_TOKEN")
    if not isinstance(token, str) or not token.strip():
        raise DaxiDataError("缺少 DAXIAPI_TOKEN；不会发起需要认证的数据请求", "missing_credentials")
    if not token.isascii() or token != token.strip() or any(ord(character) < 32 or ord(character) == 127 for character in token):
        raise DaxiDataError("DAXIAPI_TOKEN 格式无效；不会发送凭据", "invalid_credentials")
    request = urllib.request.Request(
        ENDPOINT, json.dumps({"pattern": "vcp"}, separators=(",", ":")).encode("utf-8"),
        {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    # Default opener settings retain inherited proxy variables and CA trust.
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            status = response.status
            if status != 200:
                raise _provider_failure(status)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise DaxiDataError("大虾皮未返回可验证的 JSON 响应", "unsupported_schema")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if 300 <= code < 400:
            raise DaxiDataError("大虾皮接口发生重定向，已拒绝转发凭据", "redirect_refused") from None
        raise _provider_failure(code) from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        raise DaxiDataError("大虾皮连接失败或超时；请检查服务与 Cloud 网络策略", "source_unavailable") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise DaxiDataError("大虾皮响应超过验证大小上限", "unsupported_schema")
    try:
        def invalid_constant(_value):
            raise ValueError("non-finite JSON")
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        payload = json.loads(body.decode("utf-8-sig"), parse_constant=invalid_constant, object_pairs_hook=unique_object)
        normalized_raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (UnicodeError, ValueError, RecursionError):
        raise DaxiDataError("大虾皮响应不是单一有效 JSON 对象", "unsupported_schema") from None
    if token in normalized_raw:
        raise DaxiDataError("大虾皮响应包含凭据回显，已拒绝保留原文", "credential_reflection")
    rows = _parse_candidates(payload, day)
    source = {
        "provider": "daxiapi", "endpoint": ENDPOINT, "transport": "https_post",
        "client_package": CLIENT_PACKAGE, "client_version": CLIENT_VERSION,
        "client_integrity": CLIENT_INTEGRITY, "schema_status": SCHEMA_STATUS,
        "snapshot_date": day, "fetched_at": _now_shanghai().isoformat(),
        "verified_empty": not rows, "raw_row_count": len(payload["data"]["rows"]),
        "observations": [
            "端点与认证封装来自第一方 daxiapi-cli@3.3.1 静态代码；未安装或执行该 CLI。",
            "data.date/rows 为待真实认证响应核验的候选协议；未知结构失败，不宣称已验证 live 兼容。",
            "as_of 只核对供应商返回的快照日期，不是历史查询参数；未添加未公开的 --date 或 HTTP 日期字段。",
            "供应商 VCP 名单可包含较早形态；signal_date 只保留显式字段，通用 date 不冒充信号日。",
            "名单仅作为外部候选，不代表本地 SEPA 趋势模板或 VCP 检测已经通过。",
        ],
    }
    return {"date": day, "source": source, "rows": rows, "raw": payload}
