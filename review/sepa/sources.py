"""Manual SEPA candidates and explicitly dated, reproducible adjusted daily bars.

Candidate dates are user claims, never inferred from file timestamps. Tencent
daily volume is shares for 688/689 and lots for other supported equities;
all downstream volume is shares. Source payloads can be retained under work/.
"""
import csv
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def _now_shanghai():
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _day(value, label):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label}必须显式使用 YYYY-MM-DD 日期")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label}日期无效") from None
    return parsed.isoformat()


def normalize_symbol(value):
    """Canonical A-share equity code; numeric coercion and index codes are refused."""
    if not isinstance(value, str):
        raise ValueError("证券代码必须为保留前导零的六位字符串")
    text = value.strip().lower()
    match = re.fullmatch(r"(?:(sh|sz|bj)(\d{6})|(\d{6})(?:\.(sh|sz|bj|xshg|xshe))?)", text)
    if not match:
        raise ValueError(f"证券代码格式无效：{value!r}")
    code = match.group(2) or match.group(3)
    explicit = match.group(1) or match.group(4)
    explicit = {"xshg": "sh", "xshe": "sz"}.get(explicit, explicit)
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        market = "sh"
    elif code.startswith(("000", "001", "002", "003", "300", "301")):
        market = "sz"
    elif code.startswith(("43", "83", "87", "88", "920")):
        market = "bj"
    else:
        raise ValueError(f"不支持的 A 股股票号段：{code}")
    if explicit and explicit != market:
        raise ValueError(f"市场标识与股票号段不符：{value!r}")
    return market + code


def load_candidates(symbols=(), csv_path=None, as_of=None):
    """Load a sorted unique manual pool. First CSV occurrence wins conflicts.

    CSV accepts symbol/code, optional name/source/signal_date (Chinese code,
    name and source headings also accepted). A duplicate explicit symbol can
    supplement the CSV pool but cannot overwrite its source metadata.
    """
    day = _day(as_of, "复盘日")
    candidates = {}
    aliases = {"代码": "code", "股票代码": "code", "证券代码": "code",
               "名称": "name", "股票名称": "name", "证券名称": "name",
               "来源": "source", "信号日期": "signal_date"}
    if csv_path is not None:
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError("候选 CSV 缺少表头")
            fields = [aliases.get(f.strip().lower(), f.strip().lower()) for f in reader.fieldnames]
            if len(set(fields)) != len(fields) or not any(f in fields for f in ("symbol", "code")):
                raise ValueError("候选 CSV 需要唯一的 symbol 或 code 列")
            reader.fieldnames = fields
            for line, raw in enumerate(reader, start=2):
                if None in raw:
                    raise ValueError(f"候选 CSV 第 {line} 行列数超过表头")
                row = {key: (value or "").strip() for key, value in raw.items()}
                if not any(row.values()):
                    continue
                first, second = row.get("symbol"), row.get("code")
                if not first and not second:
                    raise ValueError(f"候选 CSV 第 {line} 行缺少证券代码")
                symbol = normalize_symbol(first or second)
                if first and second and normalize_symbol(second) != symbol:
                    raise ValueError(f"候选 CSV 第 {line} 行 symbol 与 code 矛盾")
                item = {"symbol": symbol, "source": row.get("source") or "manual_csv"}
                if row.get("name"):
                    item["name"] = row["name"]
                if row.get("signal_date"):
                    signal = _day(row["signal_date"], "信号日")
                    if signal > day:
                        raise ValueError(f"候选 CSV 第 {line} 行信号日超过复盘日")
                    item["signal_date"] = signal
                candidates.setdefault(symbol, item)
    if isinstance(symbols, str):
        symbols = (symbols,)
    for value in symbols:
        symbol = normalize_symbol(value)
        candidates.setdefault(symbol, {"symbol": symbol, "source": "manual"})
    return [candidates[symbol] for symbol in sorted(candidates)]


def _positive(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field}必须为有限正数")
    return value


def _validate_bars(rows, as_of, volume_unit):
    if not isinstance(rows, list) or not rows:
        raise ValueError("没有可验证的日线")
    bars, previous = [], ""
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("日线记录必须为对象")
        day = _day(row.get("date"), "行情日")
        if day <= previous or day > as_of:
            raise ValueError("日线日期重复、乱序或超过复盘日")
        previous = day
        bar = {"date": day, **{key: _positive(row.get(key), key) for key in ("open", "high", "low", "close")}}
        if bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"]):
            raise ValueError("OHLC 高低价相互矛盾")
        volume = row.get("volume")
        if volume is not None:
            if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0:
                raise ValueError("成交量必须为非负有限数或 null")
            volume *= 100 if volume_unit == "lots" else 1
            if not math.isfinite(volume):
                raise ValueError("成交量换算后超出有限数值范围")
        bar["volume"] = volume
        bars.append(bar)
    if bars[-1]["date"] != as_of:
        raise ValueError("行情末日不是复盘日，拒绝使用过期收盘价")
    return bars


def _metadata(payload):
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("日线必须注明真实数据源 source")
    result = {"source": source}
    for field in ("source_url", "fetched_at"):
        value = payload.get(field)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"来源元数据 {field} 无效")
            if field == "fetched_at":
                try:
                    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError("来源抓取时间必须为带时区的 ISO 时间") from None
                if "T" not in value or stamp.tzinfo is None:
                    raise ValueError("来源抓取时间必须为带时区的 ISO 时间")
            result[field] = value
    return result


def _live_payload(symbol, as_of, min_bars):
    now = _now_shanghai()
    if as_of > now.date().isoformat():
        raise ValueError("复盘日超过北京时间今天，不能请求未来收盘日线")
    if as_of == now.date().isoformat() and (now.hour, now.minute) < (15, 15):
        raise ValueError("今日尚未到北京时间15:15，拒绝将未完成日线作为收盘行情")
    # Lazy import keeps manual/CSV/offline use independent of network libraries.
    from ..vendor import astock as api
    start = (date.fromisoformat(as_of) - timedelta(days=max(365, math.ceil(min_bars * 1.8) + 30))).isoformat()
    frame = api.tencent_kline(symbol, period="day", adjust="qfq", start=start, end=as_of, count=min(min_bars, 640))
    rows = frame.to_dict(orient="records")
    if not rows:
        raise ValueError("腾讯未返回日线")
    # The pinned provider's explicit adjust/code columns prevent accidental
    # mixing with unadjusted prices or a same-numbered index.
    if any(row.get("code") != symbol or row.get("adjust") != "qfq" for row in rows):
        raise ValueError("腾讯日线代码或前复权标记不匹配")
    metadata = {}
    for field in ("source", "source_url", "fetched_at"):
        values = {row.get(field) for row in rows}
        if len(values) != 1:
            raise ValueError("腾讯日线来源元数据不一致")
        metadata[field] = values.pop()
    if metadata["source"] != "tencent":
        raise ValueError("腾讯日线 source 不匹配")
    for row in rows:
        # Pandas uses NaN/NA for missing volume; preserve missing as null.
        volume = row.get("volume")
        if volume is not None and (type(volume).__name__ == "NAType" or
                                   isinstance(volume, float) and math.isnan(volume)):
            row["volume"] = None
    return {"symbol": symbol, "date": as_of, "adjustment": "qfq",
            "volume_unit": "shares" if symbol.startswith(("sh688", "sh689")) else "lots",
            **metadata, "bars": rows, "provider_attrs": dict(frame.attrs),
            "request": {"symbol": symbol, "period": "day", "adjust": "qfq", "start": start, "end": as_of}}


def load_bars(symbol, as_of, price_dir=None, min_bars=320, *, capture_dir=None):
    """Return validated qfq OHLCV in shares, retaining short histories as short.

    Offline <symbol>.json must explicitly state symbol, date (or as_of),
    adjustment='qfq', volume_unit in {'shares','lots'}, source and bars. Unknown
    unit/basis, stale closes and malformed data fail before trend evaluation.
    Offline input declares completed closing bars; its capture time alone does
    not prove an exchange-certified close. Today's live requests require China
    time >=15:15. Neither input path certifies point-in-time qfq for backtests.
    capture_dir optionally retains the exact offline payload or original live
    provider rows (before volume normalization), addressed by its SHA-256.
    """
    symbol, day = normalize_symbol(symbol), _day(as_of, "复盘日")
    if day > _now_shanghai().date().isoformat():
        raise ValueError("复盘日超过北京时间今天，不能读取未来收盘日线")
    if isinstance(min_bars, bool) or not isinstance(min_bars, int) or not 1 <= min_bars <= 2000:
        raise ValueError("min_bars 必须为 1 至 2000 的整数")
    if price_dir is None:
        payload = _live_payload(symbol, day, min_bars)
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        provenance = {"kind": "tencent_kline", "provider_revision": "29b07d2e37005b9fd568bca1262f6e572b5f9407"}
    else:
        path = Path(price_dir) / f"{symbol}.json"
        raw = path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"非法 JSON 数值：{v}")))
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("日线文件不是有效 UTF-8 JSON") from None
        provenance = {"kind": "json", "input_path": str(path.resolve())}
    if not isinstance(payload, dict):
        raise ValueError("日线输入必须为带来源元数据的对象")
    if normalize_symbol(payload.get("symbol")) != symbol:
        raise ValueError("日线文件证券代码不匹配")
    if _day(payload.get("date", payload.get("as_of")), "文件日期") != day:
        raise ValueError("日线文件日期不是复盘日")
    if payload.get("as_of") is not None and _day(payload["as_of"], "文件 as_of") != day:
        raise ValueError("日线文件 date 与 as_of 日期不一致")
    if payload.get("adjustment") != "qfq":
        raise ValueError("日线必须明确为同一前复权口径 qfq")
    unit = payload.get("volume_unit")
    if unit not in ("shares", "lots"):
        raise ValueError("日线成交量单位必须明确为 shares 或 lots")
    metadata = _metadata(payload)
    bars = _validate_bars(payload.get("bars"), day, unit)
    observations = payload.get("observations", [])
    if not isinstance(observations, list) or any(not isinstance(item, str) for item in observations):
        raise ValueError("日线 observations 必须为文本数组")
    observations = list(observations)
    observations.append("前复权序列可能包含复盘日之后的公司行为调整；保留原始快照，仅供研究，不认证历史时点可得性。")
    if price_dir is not None:
        observations.append("离线日线按提供者声明的完整收盘快照使用；未由文件时间认证盘后完成状态。")
    if len(bars) < min_bars:
        observations.append(f"历史仅 {len(bars)} 根，少于请求的 {min_bars} 根；保留真实短历史，不填充交易日。")
    missing = sum(bar["volume"] is None for bar in bars)
    if missing:
        observations.append(f"{missing} 根成交量缺失，保留 null；量能条件不可将其视为零。")
    if unit == "lots":
        observations.append("源成交量单位为手，按每手100股换算为股。")
    digest = hashlib.sha256(raw).hexdigest()
    provenance.update(point_in_time_certified=False, adjustment_basis="provider_current_qfq" if price_dir is None else "declared_qfq")
    if payload.get("adjust_as_of") is not None:
        provenance["declared_adjust_as_of"] = _day(payload["adjust_as_of"], "声明复权基准日")
    result = {"symbol": symbol, "date": day, "adjustment": "qfq", "volume_unit": "shares", **metadata,
              "bars": bars, "observations": observations, "history_sufficient": len(bars) >= min_bars,
              "provenance": {**provenance, "input_sha256": digest, "raw_volume_unit": unit}}
    if capture_dir is not None:
        directory = Path(capture_dir)
        directory.mkdir(parents=True, exist_ok=True)
        capture = directory / f"{symbol}-{day}-{digest[:12]}.json"
        capture.write_bytes(raw)
        result["raw_capture_path"] = str(capture.resolve())
        result["raw_capture_sha256"] = digest
    return result
