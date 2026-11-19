"""Bridge verified public MCP monthly calendars to the official adapter.

The connected ChatGPT app is not a hidden HTTP endpoint. Its public factual
snapshots can be imported explicitly; remote HTTP is used only for an explicitly
configured Streamable HTTP MCP server exposing the reviewed calendar schema.
"""
import calendar
import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from ..mcp_client import MCPClient, MCPError

OFFICIAL_URL = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
ARCHIVE_SOURCE = "深交所（官方月历归档）"
MAX_JSON_BYTES = 262_144


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("月历 JSON 包含重复字段")
        result[key] = value
    return result


def _json_bytes(raw):
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("月历 JSON 超过大小限制")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys,
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError("月历 JSON 含非有限数值")))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("月历不是有效 UTF-8 JSON") from None


def _read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    return _json_bytes(raw), raw


def _month_args(year, month):
    if type(year) is not int or type(month) is not int or not 1 <= year <= 9999 or not 1 <= month <= 12:
        raise ValueError("月历 year/month 必须为有效整数")
    return f"{year:04d}-{month:02d}"


def _iso_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("月历日期必须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("月历包含不存在的日期") from None


def _timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("月历来源获取时间必须为带时区 ISO 时间")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("月历来源获取时间无效") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("月历来源获取时间必须含时区")
    return value


def _official_url(value, year, month):
    if not isinstance(value, str):
        raise ValueError("月历必须提供官方来源 URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "www.szse.cn" or parsed.fragment
            or parsed.path != "/api/report/exchange/onepersistenthour/monthList"):
        raise ValueError("月历来源 URL 不是经核验的深交所月历接口")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) != 1 or query[0][0] != "month" or not re.fullmatch(r"\d{4}-\d{1,2}", query[0][1]):
        raise ValueError("官方月历 URL 必须包含唯一月份")
    actual_year, actual_month = map(int, query[0][1].split("-"))
    if (actual_year, actual_month) != (year, month):
        raise ValueError("官方月历 URL 月份与请求不符")
    return value


def _unwrap(payload):
    if not isinstance(payload, dict):
        raise ValueError("MCP 月历必须为对象")
    if payload.get("isError"):
        raise ValueError("MCP 工具返回错误月历")
    if "structuredContent" in payload:
        payload = payload["structuredContent"]
    elif "content" in payload and "ok" not in payload:
        blocks = payload["content"]
        if (not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict)
                or blocks[0].get("type") != "text" or not isinstance(blocks[0].get("text"), str)):
            raise ValueError("MCP 月历不是单一结构化 JSON")
        payload = _json_bytes(blocks[0]["text"].encode())
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("operation") != "trading_calendar":
        raise ValueError("MCP 月历操作或成功标记不匹配")
    return payload


def validate_mcp_month(payload, year, month):
    """Validate all calendar dates and their identical original SZSE provenance."""
    label = _month_args(year, month)
    payload = _unwrap(payload)
    if payload.get("timezone") not in (None, "Asia/Shanghai"):
        raise ValueError("MCP 月历时区不是上海")
    data = payload.get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    expected = {date(year, month, n).isoformat() for n in range(1, calendar.monthrange(year, month)[1] + 1)}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("MCP 月历缺日或记录数不完整")
    seen, metadata, normalized = set(), None, []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("MCP 月历记录必须为对象")
        day = _iso_day(row.get("date")).isoformat()
        if day not in expected or day in seen or type(row.get("is_open")) is not bool:
            raise ValueError("MCP 月历日期重复、错月或开市标记无效")
        if row.get("source") != "szse":
            raise ValueError("MCP 月历不是深交所来源")
        current = (_official_url(row.get("source_url"), year, month), _timestamp(row.get("fetched_at")))
        if metadata is not None and metadata != current:
            raise ValueError("MCP 月历来源 URL 或获取时间不一致")
        metadata = current
        seen.add(day)
        normalized.append({"date": day, "is_open": row["is_open"]})
    if seen != expected:
        raise ValueError("MCP 月历未覆盖完整自然月")
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise ValueError("MCP 月历 warnings 不是文本数组")
    return {"rows": sorted(normalized, key=lambda row: row["date"]),
            "source": "A 股 MCP / 深交所", "source_url": metadata[0], "fetched_at": metadata[1],
            "warning": "；".join(warnings) or None,
            "provenance": {"operation": "trading_calendar", "provider": "szse", "calendar_month": label,
                           "source_url": metadata[0], "fetched_at": metadata[1]}}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".calendar-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _archive_month(value, year, month, directory, raw_hash):
    label = _month_args(year, month)
    archive = {"version": 1, "calendar_month": label, "source": ARCHIVE_SOURCE,
               "source_url": value["source_url"],
               "fetched_at": value["fetched_at"], "rows": value["rows"]}
    path = Path(directory) / f"{label}.json"
    _write_json(path, archive)
    # Retain the provider's exact original URL and capture hash separately.
    _write_json(path.with_suffix(".source.json"), {"version": 1, "calendar_month": label,
                "operation": "trading_calendar", "provider": "szse", "source": "A 股 MCP",
                "original_source_url": value["source_url"], "fetched_at": value["fetched_at"],
                "snapshot_sha256": raw_hash})
    return path


def import_snapshot(path, archive_directory, year=None, month=None):
    """Import public factual MCP rows to the existing strict official archive."""
    payload, raw = _read_json(path)
    data = _unwrap(payload).get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ValueError("MCP 快照没有可识别月份")
    first = _iso_day(rows[0].get("date"))
    year = first.year if year is None else year
    month = first.month if month is None else month
    value = validate_mcp_month(payload, year, month)
    return _archive_month(value, year, month, archive_directory, hashlib.sha256(raw).hexdigest())


def _manifest_entries(path):
    manifest, _ = _read_json(path)
    if (not isinstance(manifest, dict) or set(manifest) != {"version", "snapshots"}
            or type(manifest["version"]) is not int or manifest["version"] != 1
            or not isinstance(manifest["snapshots"], list)):
        raise ValueError("月历快照 manifest schema 无效")
    root = Path(path).resolve().parent
    result, seen = [], set()
    for entry in manifest["snapshots"]:
        if (not isinstance(entry, dict) or set(entry) != {"path", "sha256", "calendar_month"}
                or not isinstance(entry["path"], str) or not isinstance(entry["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                or not isinstance(entry["calendar_month"], str)
                or not re.fullmatch(r"\d{4}-\d{2}", entry["calendar_month"])):
            raise ValueError("月历 manifest 条目字段无效")
        relative = Path(entry["path"])
        target = (root / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not target.is_relative_to(root):
            raise ValueError("月历 manifest 路径越出快照目录")
        year, month = map(int, entry["calendar_month"].split("-"))
        label = _month_args(year, month)
        if label in seen:
            raise ValueError("月历 manifest 月份重复")
        seen.add(label)
        payload, raw = _read_json(target)
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("月历快照 SHA-256 与 manifest 不符")
        result.append((year, month, target, validate_mcp_month(payload, year, month), entry["sha256"]))
    return result


def import_manifest(path, archive_directory):
    # Verify every input before writing any archive.
    entries = _manifest_entries(path)
    return [_archive_month(value, year, month, archive_directory, digest)
            for year, month, _, value, digest in entries]


def _trusted_archive(path, year, month):
    """Read the explicitly selected official archive, never relabel it as live MCP."""
    label = _month_args(year, month)
    payload, raw = _read_json(path)
    fields = {"version", "calendar_month", "source", "source_url", "fetched_at", "rows"}
    if (not isinstance(payload, dict) or set(payload) != fields
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["calendar_month"] != label or payload["source"] != ARCHIVE_SOURCE):
        raise ValueError("官方归档 schema 或来源未通过核验")
    _official_url(payload["source_url"], year, month)
    _timestamp(payload["fetched_at"])
    expected = {date(year, month, n).isoformat() for n in range(1, calendar.monthrange(year, month)[1] + 1)}
    rows = payload["rows"]
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or any(not isinstance(row, dict) or set(row) != {"date", "is_open"}
                   or type(row["is_open"]) is not bool or row["date"] not in expected for row in rows)
            or {row["date"] for row in rows} != expected):
        raise ValueError("官方归档月历缺日、重复、错月或开市标记异常")
    return {"rows": sorted(rows, key=lambda row: row["date"]), "source": ARCHIVE_SOURCE,
            "source_url": payload["source_url"], "fetched_at": payload["fetched_at"],
            "warning": "使用已核验官方月历归档；此来源不是本次实时 MCP 获取。",
            "provenance": {"provider": "szse", "transport": "trusted_archive", "calendar_month": label,
                           "source_url": payload["source_url"], "fetched_at": payload["fetched_at"],
                           "archive_sha256": hashlib.sha256(raw).hexdigest()}}


def configured_calendar_client():
    """Only explicit user-configured remote MCP; no inferred connector URL."""
    url = os.environ.get("SEPA_CALENDAR_MCP_URL", "").strip()
    token = os.environ.get("SEPA_CALENDAR_MCP_TOKEN") or None
    if not url:
        if token:
            raise ValueError("配置了月历 MCP token，但缺少 SEPA_CALENDAR_MCP_URL")
        return None
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("SEPA_CALENDAR_MCP_URL 必须为不含凭据的显式 HTTPS 服务地址")
    return MCPClient(url, token)


def default_calendar_adapter(archive_directory=None):
    """Keep packaged verified months available when the operator adds new ones."""
    from ..a_stock_data import AStockData

    packaged = Path(__file__).resolve().parents[2] / "config" / "calendars"
    directory = Path(archive_directory) if archive_directory is not None else packaged
    adapter = AStockData({"tdx_official_calendar_fallback": True})
    if directory.resolve() != packaged:
        adapter = McpCalendarAdapter(adapter, archive_directory=packaged)
    return McpCalendarAdapter(adapter, client=configured_calendar_client(), archive_directory=directory)


class McpCalendarAdapter:
    """MCP-first calendar decoration with the existing official adapter fallback."""
    def __init__(self, fallback_adapter, *, snapshot_directory=None, manifest_path=None,
                 client=None, archive_directory=None, tool_name="trading_calendar"):
        if not callable(getattr(fallback_adapter, "calendar_month", None)):
            raise ValueError("月历备选 adapter 缺少 calendar_month")
        self.fallback = fallback_adapter
        self.snapshot_directory = Path(snapshot_directory) if snapshot_directory is not None else None
        self.manifest_path = Path(manifest_path) if manifest_path is not None else None
        self.client, self.archive_directory, self.tool_name = client, archive_directory, tool_name
        self._months, self._manifest, self._ready, self._setup_error = {}, None, False, None

    def __getattr__(self, name):
        return getattr(self.fallback, name)

    def _prepare_client(self):
        if self._setup_error:
            raise MCPError("显式 MCP 服务兼容性检查失败")
        if self._ready:
            return
        try:
            self.client.initialize()
            tools = self.client.list_tools()
            matches = [tool for tool in tools if isinstance(tool, dict) and tool.get("name") == self.tool_name]
            if len(matches) != 1:
                raise MCPError("显式 MCP 服务未提供唯一匹配的月历工具")
            schema = matches[0].get("inputSchema", {})
            properties = schema.get("properties", {})
            if (schema.get("type") != "object" or not isinstance(properties, dict)
                    or any(not isinstance(properties.get(key), dict) or properties[key].get("type") != "integer"
                           for key in ("year", "month"))
                    or any(key not in ("year", "month") for key in schema.get("required", []))):
                raise MCPError("显式 MCP 月历工具的 year/month schema 不兼容")
            self._ready = True
        except (RuntimeError, ValueError, KeyError, TypeError, OSError, AttributeError):
            self._setup_error = True
            raise MCPError("显式 MCP 服务兼容性检查失败") from None

    def _snapshot(self, year, month):
        label = _month_args(year, month)
        if self.manifest_path is not None:
            if self._manifest is None:
                self._manifest = {(y, m): (value, digest) for y, m, _, value, digest in _manifest_entries(self.manifest_path)}
            if (year, month) in self._manifest:
                return self._manifest[(year, month)]
        if self.snapshot_directory is not None:
            for name in (f"{label}.json", f"calendar-{label}.json"):
                path = self.snapshot_directory / name
                if path.exists():
                    payload, raw = _read_json(path)
                    return validate_mcp_month(payload, year, month), hashlib.sha256(raw).hexdigest()
        raise ValueError("没有对应月份的已验证 MCP 快照")

    def calendar_month(self, year, month):
        _month_args(year, month)
        if (year, month) in self._months:
            return self._months[(year, month)]
        failures = []
        value, digest = None, None
        if self.client is not None:
            try:
                self._prepare_client()
                payload = self.client.call_tool(self.tool_name, {"year": year, "month": month})
                value = validate_mcp_month(payload, year, month)
                digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()).hexdigest()
            except (RuntimeError, ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
                failures.append("远程 MCP 月历不可用（" + type(exc).__name__ + "）")
        if value is None and (self.snapshot_directory is not None or self.manifest_path is not None):
            try:
                value, digest = self._snapshot(year, month)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                failures.append("MCP 月历快照未通过核验（" + type(exc).__name__ + "）")
        archived = False
        if value is None and self.archive_directory is not None:
            path = Path(self.archive_directory) / f"{year:04d}-{month:02d}.json"
            if path.exists():
                try:
                    value = _trusted_archive(path, year, month)
                    archived = True
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    failures.append("官方月历归档未通过核验（" + type(exc).__name__ + "）")
        if value is not None:
            value = {**value}
            if self.archive_directory is not None and not archived:
                try:
                    _archive_month(value, year, month, self.archive_directory, digest)
                except (ValueError, OSError):
                    failures.append("已验证 MCP 月历的本地归档写入失败")
            value["warning"] = "；".join(failures + ([value["warning"]] if value["warning"] else [])) or None
        else:
            value = {**self.fallback.calendar_month(year, month)}
            if failures:
                value["warning"] = "；".join(failures + ["已采用官方备选月历"] +
                                            ([value.get("warning")] if value.get("warning") else []))
        self._months[(year, month)] = value
        return value

    def calendar(self, day):
        parsed = _iso_day(day)
        value = self.calendar_month(parsed.year, parsed.month)
        match = next(row for row in value["rows"] if row["date"] == day)
        return {"date": day, "open": match["is_open"],
                **{key: value.get(key) for key in ("source", "source_url", "fetched_at", "warning")}}
