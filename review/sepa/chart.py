"""Deterministic, headless PNGs for a single dated SEPA/VCP analysis.

Matplotlib is loaded only by ``write_chart``.  The plot uses a non-interactive
FigureCanvasAgg directly, so importing this module does not select a GUI backend
or register pyplot figures.  Missing volume and unavailable moving averages are
shown explicitly; neither is replaced with zero.
"""
from __future__ import annotations

import math
import os
import tempfile
import textwrap
from collections.abc import Mapping
from datetime import date
from numbers import Real
from pathlib import Path


_UP, _DOWN = "#c64c54", "#168578"
_MA_COLORS = {50: "#b58a21", 150: "#4879ad", 200: "#76808c"}


def _number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value):
    return _number(value) and value > 0


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _text(value, default="unknown", limit=70):
    if value is None or value == "":
        return default
    return str(value).replace("\n", " ").encode("ascii", "backslashreplace").decode()[:limit]


def _state(value):
    if isinstance(value, Mapping):
        for key in ("state", "status", "passed", "passes", "template_passed"):
            if key in value:
                return _state(value[key])
        return "unknown"
    if isinstance(value, bool):
        return "pass" if value else "fail"
    return _text(value, limit=40)


def _moving_average(values, period):
    """A trailing mean on the complete input, with NaN before enough history."""
    result = [math.nan] * len(values)
    for end in range(period, len(values) + 1):
        result[end - 1] = math.fsum(values[end - period:end]) / period
    return result


def _bars(instrument):
    source = instrument.get("bars")
    if not isinstance(source, (list, tuple)) or not source:
        raise ValueError("instrument.bars must contain dated OHLC records")
    rows, previous = [], ""
    for index, row in enumerate(source):
        if not isinstance(row, Mapping) or not _date(row.get("date")) or row["date"] <= previous:
            raise ValueError(f"bar {index}: dates must be valid, unique and sorted")
        if any(not _positive(row.get(key)) for key in ("open", "high", "low", "close")):
            raise ValueError(f"bar {index}: OHLC prices must be finite and positive")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            raise ValueError(f"bar {index}: inconsistent OHLC range")
        rows.append(row)
        previous = row["date"]
    as_of = instrument.get("date")
    if not _date(as_of) or rows[-1]["date"] > as_of:
        raise ValueError("instrument.date must be valid and not precede its bars")
    return rows, as_of


def _known(record, as_of, fields):
    """Do not label geometry that was not yet observable on the chart date."""
    return all(record.get(key) is None or (_date(record[key]) and record[key] <= as_of)
               for key in fields)


def _position_labels(canvas, axis, labels, occupied):
    """Move point labels among bounded lanes without moving their data anchors."""
    canvas.draw()
    renderer = canvas.get_renderer()
    bounds = axis.get_window_extent(renderer)
    occupied = [item.get_window_extent(renderer).expanded(1.02, 1.08) for item in occupied]
    for annotation, direction in labels:
        candidates = [(dx, direction * dy) for dy in (12, 26, 40, 54)
                      for dx in (0, -28, 28, -60, 60)]
        best, best_score = candidates[0], math.inf
        for offset in candidates:
            annotation.set_position(offset)
            annotation.update_positions(renderer)
            annotation.update_bbox_position_size(renderer)
            box = annotation.get_bbox_patch().get_window_extent(renderer).expanded(1.03, 1.12)
            outside = box.x0 < bounds.x0 or box.x1 > bounds.x1 or box.y0 < bounds.y0 or box.y1 > bounds.y1
            score = 100 * outside + sum(box.overlaps(other) for other in occupied)
            if score < best_score:
                best, best_score = offset, score
            if score == 0:
                break
        annotation.set_position(best)
        annotation.update_positions(renderer)
        annotation.update_bbox_position_size(renderer)
        occupied.append(annotation.get_bbox_patch().get_window_extent(renderer).expanded(1.03, 1.12))


def write_chart(path, instrument, analysis):
    """Write one reproducible candlestick/volume PNG and return its ``Path``.

    ``instrument`` contains symbol, date, adjustment, volume_unit and sorted
    OHLCV bars. ``analysis`` contains trend and vcp dictionaries.  VCP geometry
    uses only supplied swing extrema, pivot/risk references and dated events;
    it is not reconstructed from later prices.  Moving averages use all input
    history before the visible window (at least the latest 120 sessions when
    available), and the window expands to retain supplied VCP anchors.

    Raises ValueError for invalid dated OHLC input. Missing/invalid volume is
    rendered as an unknown observation, rather than as a reported zero.
    Matplotlib is an optional dependency until this function is called.
    """
    if not isinstance(instrument, Mapping) or not isinstance(analysis, Mapping):
        raise ValueError("instrument and analysis must be mappings")
    output = Path(path)
    if output.suffix.lower() != ".png":
        raise ValueError("chart output path must end with .png")
    bars, as_of = _bars(instrument)
    vcp = analysis.get("vcp") if isinstance(analysis.get("vcp"), Mapping) else {}
    if vcp.get("as_of") is not None and vcp["as_of"] != as_of:
        raise ValueError("VCP analysis date must match instrument.date")
    index = {row["date"]: i for i, row in enumerate(bars)}
    notes = []
    swings = []
    for swing in vcp.get("swings", []) or []:
        if isinstance(swing, Mapping) and _known(swing, as_of, ("confirmed_at", "high_confirmed_at", "low_confirmed_at")):
            swings.append(swing)
        else:
            notes.append("Unconfirmed/future swing annotations omitted.")
    breakout = vcp.get("breakout")
    if not isinstance(breakout, Mapping):
        breakout = None
    elif not _known(breakout, as_of, ("date", "observed_at")):
        breakout = None
        notes.append("Future breakout observation omitted.")
    events = [event for event in vcp.get("events", []) or []
              if isinstance(event, Mapping)
              and event.get("type") == "state_changed"
              and event.get("state") in ("failed", "invalidated", "retest")
              and _known(event, as_of, ("date", "observed_at"))
              and (not vcp.get("setup_id") or event.get("setup_id") == vcp["setup_id"])]
    anchor_dates = [s.get(key) for s in swings for key in ("high_date", "low_date")]
    anchor_dates += [record.get("date") for record in ([breakout] if breakout else []) + events]
    start = max(0, len(bars) - 120)
    for anchor in anchor_dates:
        if anchor in index:
            start = min(start, max(0, index[anchor] - 6))
    shown = bars[start:]
    positions = list(range(len(shown)))
    closes = [float(row["close"]) for row in bars]
    averages = {period: _moving_average(closes, period)[start:] for period in _MA_COLORS}
    if len(bars) < 200:
        notes.append(f"History: {len(bars)} sessions; unavailable moving averages are not drawn.")
    if bars[-1]["date"] != as_of:
        notes.append("Last bar: " + bars[-1]["date"] + " (chart as-of " + as_of + ").")

    # The managed runtime's home can be read-only. Set a writable cache only
    # when the caller has not already configured Matplotlib's location.
    if "MPLCONFIGDIR" not in os.environ:
        cache = Path(tempfile.gettempdir()) / "stock-review-matplotlib"
        cache.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(cache)
    if "XDG_CACHE_HOME" not in os.environ:
        cache = Path(tempfile.gettempdir()) / "stock-review-matplotlib"
        cache.mkdir(parents=True, exist_ok=True)
        os.environ["XDG_CACHE_HOME"] = str(cache)
    try:
        from matplotlib import rc_context
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.patches import Rectangle
        from matplotlib.ticker import FuncFormatter, MaxNLocator
    except ImportError as exc:
        raise RuntimeError("PNG chart export requires the optional matplotlib dependency") from exc

    with rc_context({"font.family": "DejaVu Sans", "font.size": 9, "text.usetex": False, "axes.unicode_minus": False}):
        figure = Figure(figsize=(14, 8.5), dpi=140, facecolor="white")
        canvas = FigureCanvasAgg(figure)
        temporary = None
        try:
            price, volume = figure.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3.4, 1], "hspace": 0.08})
            figure.subplots_adjust(left=0.075, right=0.97, top=0.865, bottom=0.17)
            for axis in (price, volume):
                axis.set_facecolor("white")
                axis.grid(axis="y", color="#dce2e8", linewidth=0.6, alpha=0.8)
                axis.set_axisbelow(True)
                axis.spines[["top", "right"]].set_visible(False)
                axis.spines[["bottom", "left"]].set_color("#aab4bf")
                axis.tick_params(labelsize=8, colors="#546170", length=3)
            labels, occupied = [], []
            colors = [_UP if row["close"] >= row["open"] else _DOWN for row in shown]
            for x, row, color in zip(positions, shown, colors):
                price.vlines(x, row["low"], row["high"], color=color, linewidth=0.75, zorder=2)
                if row["open"] == row["close"]:
                    price.hlines(row["close"], x - 0.31, x + 0.31, color=color, linewidth=1.0, zorder=3)
                else:
                    price.add_patch(Rectangle((x - 0.31, min(row["open"], row["close"])), 0.62,
                                              abs(row["close"] - row["open"]), facecolor=color,
                                              edgecolor=color, linewidth=0.5, zorder=3))
            for period, values in averages.items():
                available = any(math.isfinite(value) for value in values)
                price.plot(positions if available else [], values if available else [],
                           color=_MA_COLORS[period], linewidth=1.15,
                           label=f"MA{period}" + ("" if available else " (unavailable)"))
            occupied.append(price.legend(loc="upper left", ncol=3, fontsize=8, frameon=False))

            def point_label(text, x, y, direction=1, marker="o", color="#74569b"):
                price.scatter([x], [y], s=26, marker=marker, facecolor="white", edgecolor=color, zorder=6)
                annotation = price.annotate(text, (x, y), xytext=(0, direction * 12),
                                            textcoords="offset points", ha="center", va="bottom" if direction > 0 else "top",
                                            fontsize=8, color=color, arrowprops={"arrowstyle": "-", "color": color, "lw": 0.6},
                                            bbox={"boxstyle": "round,pad=0.18", "fc": "white", "ec": "none", "alpha": 0.9}, zorder=7)
                labels.append((annotation, direction))

            for number, swing in enumerate(swings, 1):
                endpoints = []
                for key, field, direction in (("high_date", "high", 1), ("low_date", "low", -1)):
                    anchor, value = swing.get(key), swing.get(field)
                    if anchor not in index or not _positive(value):
                        notes.append(f"Swing {number} {field} anchor unavailable.")
                        continue
                    x = index[anchor] - start
                    label = ("H" if field == "high" else "L") + f"{number} {value:.2f}"
                    if field == "low" and _number(swing.get("drawdown_pct")):
                        label += f" ({abs(swing['drawdown_pct']):.1f}%)"
                    point_label(label, x, value, direction)
                    endpoints.append((x, value))
                if len(endpoints) == 2:
                    price.plot([p[0] for p in endpoints], [p[1] for p in endpoints],
                               color="#9a81b8", linestyle="--", linewidth=0.9, zorder=4)
            if not swings:
                notes.append("No confirmed VCP swing geometry supplied.")
            references = []
            if _known(vcp, as_of, ("pivot_known_at",)):
                for key, label, color in (("pivot", "Pivot", "#8765a8"), ("risk_level", "Risk reference", "#b16b38")):
                    value = vcp.get(key)
                    if _positive(value):
                        price.axhline(value, color=color, linestyle="--", linewidth=1, zorder=1)
                        occupied.append(price.text(0.99, value, f"{label} {value:.2f}", transform=price.get_yaxis_transform(),
                                                   ha="right", va="bottom", fontsize=8, color=color,
                                                   bbox={"fc": "white", "ec": "none", "alpha": 0.85, "pad": 1.5}))
                        references.append(value)
            else:
                notes.append("Future pivot/risk confirmation omitted.")
            for record, label, marker in ((breakout, "Breakout obs.", "^"),
                                           *((event, event["state"].capitalize(), "X") for event in events[-2:])):
                if record is None:
                    continue
                anchor = record.get("date")
                if anchor not in index:
                    notes.append(label + " anchor unavailable.")
                    continue
                row = bars[index[anchor]]
                value = record.get("close") if _positive(record.get("close")) else row["close"]
                point_label(label + " " + anchor, index[anchor] - start, value, 1, marker, "#375b8d")
            if breakout:
                confirmation = breakout.get("volume_confirmed")
                notes.append("Breakout volume: " + ("confirmed" if confirmation is True else "unconfirmed" if confirmation is False else "unknown") + ".")
            average_range = [value for series in averages.values() for value in series if math.isfinite(value)]
            lower = min([row["low"] for row in shown] + references + average_range)
            upper = max([row["high"] for row in shown] + references + average_range)
            span = max(upper - lower, shown[-1]["close"] * 0.04)
            price.set_ylim(max(0, lower - span * 0.18), upper + span * 0.20)
            price.set_ylabel("Price", fontsize=9, color="#546170")
            price.tick_params(axis="x", labelbottom=False)

            known_volume, missing_volume = [], []
            for x, row, color in zip(positions, shown, colors):
                value = row.get("volume")
                if _number(value) and value >= 0:
                    volume.bar(x, value, width=0.62, color=color, linewidth=0, zorder=2)
                    known_volume.append(value)
                else:
                    missing_volume.append(x)
            if missing_volume:
                volume.scatter(missing_volume, [0.04] * len(missing_volume), transform=volume.get_xaxis_transform(),
                               marker="x", s=18, color="#b78131", zorder=3)
                notes.append(f"Volume unknown: {len(missing_volume)}/{len(shown)} visible sessions (x); gaps are not zero.")
            if not known_volume:
                volume.set_ylim(0, 1)
                volume.set_yticks([])
                volume.text(0.5, 0.5, "Volume unavailable", transform=volume.transAxes,
                            ha="center", va="center", color="#8b7961", fontsize=10)
            else:
                volume.set_ylim(0, max(known_volume) * 1.15 if max(known_volume) else 1)
                volume.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1e6:g}M" if abs(value) >= 1e6 else f"{value / 1e3:g}K" if abs(value) >= 1e3 else f"{value:g}"))
            unit = {"股": "shares", "手": "lots", "张": "contracts", "份": "units"}.get(instrument.get("volume_unit"), instrument.get("volume_unit"))
            volume.set_ylabel("Volume\n(" + _text(unit, limit=24) + ")", fontsize=9, color="#546170")
            volume.xaxis.set_major_locator(MaxNLocator(nbins=9, integer=True))
            volume.xaxis.set_major_formatter(FuncFormatter(lambda value, _: shown[round(value)]["date"] if 0 <= round(value) < len(shown) else ""))
            volume.set_xlim(-1.2, len(shown) + 1.2)
            volume.set_xlabel("Trading sessions", fontsize=8, color="#687483")
            adjustment = {"前复权": "qfq", "后复权": "hfq", "不复权": "none"}.get(instrument.get("adjustment"), instrument.get("adjustment"))
            figure.text(0.075, 0.962, _text(instrument.get("symbol"), limit=40) + "  |  " + as_of,
                        fontsize=15, fontweight="bold", color="#25384a")
            figure.text(0.075, 0.927, "Adjustment: " + _text(adjustment, limit=28) + "  |  Trend: " + _state(analysis.get("trend"))
                        + "  |  VCP: " + _state(vcp.get("state")) + f"  |  Close: {shown[-1]['close']:.2f}",
                        fontsize=10, color="#536679")
            figure.text(0.075, 0.897, "Setup known: " + _text(vcp.get("setup_detected_at"), limit=20)
                        + "  |  Pivot known: " + _text(vcp.get("pivot_known_at"), limit=20)
                        + f"  |  Visible: {len(shown)} / {len(bars)} sessions", fontsize=8, color="#768392")
            notes.insert(0, "Swing labels mark extrema; confirmation dates above describe when the setup became known. Up candles red; down candles green.")
            footer = "\n".join(textwrap.wrap(" ".join(dict.fromkeys(notes)), width=170))
            figure.text(0.075, 0.035, footer, fontsize=8, color="#6c7886", va="bottom", linespacing=1.35)
            _position_labels(canvas, price, labels, occupied)
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="." + output.stem + "-", suffix=".png", dir=output.parent, delete=False) as handle:
                temporary = Path(handle.name)
            figure.savefig(temporary, format="png", dpi=140, facecolor="white",
                           metadata={"Software": "stock_daily_review SEPA/VCP chart"})
            os.replace(temporary, output)
            return output
        finally:
            # No pyplot figure manager is created; clear artists and break the
            # canvas/figure cycle explicitly for repeated batch exports.
            figure.clear()
            figure.set_canvas(None)
            canvas.figure = None
            if temporary is not None:
                temporary.unlink(missing_ok=True)
