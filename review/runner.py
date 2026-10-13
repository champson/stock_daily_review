import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from .mcp_client import MCPClient, MCPError
from .analysis import LABELS, validate_rows, analyze


def pointer(data, path):
    if not path:
        return data
    if not path.startswith("/"):
        raise ValueError("字段路径必须使用 JSON Pointer")
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def arguments(value, trading_date):
    if isinstance(value, str):
        return value.replace("$date", trading_date)
    if isinstance(value, list):
        return [arguments(v, trading_date) for v in value]
    if isinstance(value, dict):
        return {k: arguments(v, trading_date) for k, v in value.items()}
    return value


def collect(client, spec, trading_date):
    if not spec or not spec.get("tool"):
        raise ValueError("未配置已验证的 MCP 工具映射")
    result = client.call_tool(spec["tool"], arguments(spec.get("arguments", {}), trading_date))
    rows = pointer(result, spec.get("rows_path", ""))
    if not isinstance(rows, list):
        raise ValueError("rows_path 未指向记录数组")
    normalized = []
    for row in rows:
        item = {}
        for field, mapping in spec["fields"].items():
            path = mapping if isinstance(mapping, str) else mapping["path"]
            try:
                value = pointer(row, path)
            except (KeyError, IndexError, TypeError, ValueError):
                value = None
            if value is not None and isinstance(mapping, dict):
                if "values" in mapping:
                    value = mapping["values"].get(str(value))
                if "scale" in mapping and value is not None:
                    value = float(value) * mapping["scale"]
            item[field] = value
        normalized.append(item)
    return normalized


def build_report(client, config, trading_date):
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    calendar = config.get("calendar")
    if not calendar:
        raise ValueError("先配置交易日历；不能用周一至周五替代交易日历")
    raw = client.call_tool(calendar["tool"], arguments(calendar.get("arguments", {}), trading_date))
    if pointer(raw, calendar["date_path"]) != trading_date:
        raise ValueError("交易日历返回日期不匹配")
    open_day = pointer(raw, calendar["open_path"])
    if not isinstance(open_day, bool):
        raise ValueError("交易日历 open_path 必须返回布尔值")
    if not open_day:
        return {"skipped": True, "date": trading_date, "reason": "交易所休市"}
    sections, provenance, errors = {}, [], []
    for kind in LABELS:
        spec = config.get("datasets", {}).get(kind)
        try:
            rows = collect(client, spec, trading_date)
            valid, rejected = validate_rows(rows, trading_date, kind)
            section = analyze(kind, valid, bool(spec.get("universe_complete", False)) and not rejected)
            section["rejected_count"] = len(rejected)
            if rejected:
                section["status"] = "partial" if valid else "unavailable"
                section["quality_issues"] = rejected
            sections[kind] = section
            provenance.append({"dataset": kind, "tool": spec["tool"], "source": "通达信 MCP", "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()})
        except (MCPError, ValueError, KeyError, TypeError, OverflowError) as exc:
            sections[kind] = {"label": LABELS[kind], "status": "unavailable", "sample_size": 0, "universe_complete": False, "metrics": {}, "rows": [], "observations": [str(exc)]}
            errors.append({"dataset": kind, "message": str(exc)})
    available = sum(s["status"] == "available" for s in sections.values())
    return {"schema_version": 1, "date": trading_date, "generated_at": now, "timezone": "Asia/Shanghai", "source": "通达信 MCP", "status": "complete" if available == len(LABELS) else "partial" if any(s["sample_size"] for s in sections.values()) else "unavailable", "sections": sections, "provenance": provenance, "errors": errors}


def configured_client(config):
    url = config.get("mcp_url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("尚未配置通达信官方 MCP 服务 URL；请以官方接入页为准")
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host == "tdx.com.cn" or host.endswith(".tdx.com.cn")) or parsed.username or parsed.password:
        raise ValueError("仅允许通达信官方 tdx.com.cn 域名下的 HTTPS MCP 服务")
    token = os.environ.get(config.get("token_env", "TDX_MCP_TOKEN"))
    auth = config.get("auth")
    if token and (not isinstance(auth, dict) or not auth.get("header")):
        raise ValueError("请根据官方接入说明配置认证请求头与前缀")
    return MCPClient(url, token, auth_header=auth["header"] if token else "Authorization", auth_prefix=auth.get("prefix", "") if token else "Bearer ")


def main():
    parser = argparse.ArgumentParser(description="通达信 MCP 股票复盘")
    parser.add_argument("action", choices=["discover", "run"])
    parser.add_argument("--config", default="config/review.json")
    parser.add_argument("--date")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    try:
        client = configured_client(config)
        client.initialize()
        if args.action == "discover":
            result = {"server": config["mcp_url"], "tools": client.list_tools()}
        else:
            trading_date = args.date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            result = build_report(client, config, trading_date)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        print(json.dumps({"output": str(path), "status": result.get("status", "skipped" if result.get("skipped") else "discovered")}, ensure_ascii=False))
        return 2 if result.get("status") == "unavailable" else 0
    except (MCPError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
