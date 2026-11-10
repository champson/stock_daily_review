"""主源优先、按模块降级；日历失效时不得推断开市。"""
from datetime import datetime
from zoneinfo import ZoneInfo
from .analysis import LABELS, analyze, validate_rows
from .mcp_client import MCPError


def build_report(config, day, primary=None, fallback_factory=None):
    from .runner import collect, configured_client, arguments, pointer
    if primary is None:
        from .a_stock_data import AStockData
        primary = AStockData(config["primary"])
    fallback = None

    def backup():
        nonlocal fallback
        if fallback is None:
            fallback = fallback_factory() if fallback_factory else configured_client(config)
            fallback.initialize()
        return fallback

    try:
        calendar = primary.calendar(day)
    except (RuntimeError, ValueError, OSError) as exc:
        spec = config.get("calendar")
        if not spec:
            raise ValueError(f"主源与备选交易日历均不可用，停止生成：{exc}") from None
        raw = backup().call_tool(spec["tool"], arguments(spec.get("arguments", {}), day))
        calendar = {"date": pointer(raw, spec["date_path"]), "open": pointer(raw, spec["open_path"]), "source": "通达信 MCP"}
    if calendar["date"] != day or not isinstance(calendar["open"], bool):
        raise ValueError("交易日历日期或类型不匹配")
    if not calendar["open"]:
        return {"skipped": True, "date": day, "reason": "交易所休市", "calendar": calendar}
    sections, errors, provenance, used = {}, [], [dict(calendar, dataset="calendar")], set()
    for kind in LABELS:
        issue = None
        try:
            data = primary.collect(kind, day)
            valid, rejected = validate_rows(data["rows"], day, kind)
            if not valid:
                raise ValueError("主源没有通过校验的当日收盘数据")
            provider = "a-stock-data"
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            issue = str(exc)
            spec = config.get("datasets", {}).get(kind)
            try:
                if not spec or not spec.get("tool"):
                    raise ValueError("通达信备选尚未配置经过验证的工具映射")
                rows = collect(backup(), spec, day)
                valid, rejected = validate_rows(rows, day, kind)
                if not valid:
                    raise ValueError("备选没有通过校验的当日收盘数据")
                provider = "通达信 MCP"
                data = {"rows": rows, "complete": spec.get("universe_complete", False),
                        "observations": ["主源不可用，已使用通达信官方 MCP 备选。"],
                        "provenance": [{"dataset": kind, "source": provider, "tool": spec["tool"]}]}
            except (MCPError, ValueError, KeyError, TypeError, OSError) as failure:
                errors.append({"dataset": kind, "message": issue + "；" + str(failure)})
                sections[kind] = {"label": LABELS[kind], "status": "unavailable", "sample_size": 0,
                                  "universe_complete": False, "metrics": {}, "rows": [], "observations": [errors[-1]["message"]]}
                continue
        section = analyze(kind, valid, bool(data.get("complete")) and not rejected)
        section["source"] = provider
        section["observations"].extend(data.get("observations", []))
        section["rejected_count"] = len(rejected)
        if rejected or data.get("partial"):
            section["status"] = "partial"
        if rejected:
            section["quality_issues"] = rejected
        if issue:
            errors.append({"dataset": kind, "message": issue, "recovered_by": provider})
        sections[kind] = section
        provenance.extend(data.get("provenance", []))
        used.add(provider)
    return {"schema_version": 1, "date": day, "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "timezone": "Asia/Shanghai", "source": "a-stock-data / 通达信 MCP" if len(used) > 1 else next(iter(used), "a-stock-data"),
            "source_priority": ["a-stock-data", "通达信 MCP"], "calendar": calendar,
            "status": "complete" if all(s["status"] == "available" for s in sections.values()) else "partial" if used else "unavailable",
            "sections": sections, "provenance": provenance, "errors": errors}
