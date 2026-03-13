#!/usr/bin/env python3
"""
analyze_cycle_time.py
Phân tích cycle time (thời gian xử lý) từ Sepsis Cases Event Log.

Usage:
    python analyze_cycle_time.py "Sepsis Cases - Event Log.xes"
    python analyze_cycle_time.py "Sepsis Cases - Event Log.xes" --output-dir output
"""

import argparse
import csv
import json
import os
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
#  ARGUMENT PARSING
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze cycle time statistics from an XES event log."
    )
    parser.add_argument("input", help="Path to .xes file")
    parser.add_argument("--output-dir", default="output",
                        help="Directory to write outputs (default: output)")
    parser.add_argument("--max-traces", type=int, default=None,
                        help="Limit number of traces to parse")
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  XES PARSING
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ts(value: str) -> datetime:
    """Parse ISO 8601 timestamp from XES, returning a timezone-aware datetime."""
    # Python 3.11+ fromisoformat handles offset-aware timestamps
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # Fallback: strip milliseconds if weird format
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)


def iter_traces_with_timestamps(xes_path: str, max_traces: int | None = None):
    """
    Yield (trace_id, events) where events is a list of (activity_name, timestamp).
    """
    context = ET.iterparse(xes_path, events=("start", "end"))
    in_trace = False
    in_event = False
    current_events: list[tuple[str, datetime]] = []
    current_name: str | None = None
    current_ts:   datetime | None = None
    trace_id: str | None = None
    trace_count = 0

    for xml_event, elem in context:
        tag = elem.tag

        # ── Trace boundaries ────────────────────────────────────────────────
        if xml_event == "start" and tag == "trace":
            in_trace = True
            current_events = []
            trace_id = None

        elif xml_event == "end" and tag == "string" and in_trace and not in_event:
            if elem.attrib.get("key") == "concept:name":
                trace_id = elem.attrib.get("value")

        elif xml_event == "end" and tag == "trace":
            if current_events:
                yield trace_id or f"trace_{trace_count}", current_events
            trace_count += 1
            in_trace = False
            elem.clear()
            if max_traces is not None and trace_count >= max_traces:
                break

        # ── Event boundaries ────────────────────────────────────────────────
        elif xml_event == "start" and tag == "event" and in_trace:
            in_event = True
            current_name = None
            current_ts   = None

        elif xml_event == "end" and tag == "string" and in_event:
            if elem.attrib.get("key") == "concept:name":
                current_name = elem.attrib.get("value")

        elif xml_event == "end" and tag == "date" and in_event:
            if elem.attrib.get("key") == "time:timestamp":
                try:
                    current_ts = _parse_ts(elem.attrib.get("value", ""))
                except Exception:
                    current_ts = None

        elif xml_event == "end" and tag == "event" and in_trace:
            if current_name and current_ts:
                current_events.append((current_name, current_ts))
            in_event = False
            elem.clear()


# ─────────────────────────────────────────────────────────────────────────────
#  CYCLE TIME COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_cycle_times(xes_path: str, max_traces: int | None = None):
    """
    Returns:
        trace_durations : list of (trace_id, duration_hours)
        transition_times: dict[(src, tgt)] = list of wait times in hours
    """
    trace_durations: list[tuple[str, float]] = []
    transition_times: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
    variants_count: defaultdict[tuple[str, ...], list[str]] = defaultdict(list)

    for trace_id, events in iter_traces_with_timestamps(xes_path, max_traces):
        if len(events) < 2:
            continue

        # Make all timestamps timezone-aware (UTC) for safe arithmetic
        def to_utc(ts: datetime) -> datetime:
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(timezone.utc)

        events_utc = [(name, to_utc(ts)) for name, ts in events]
        
        # Track variants (sequence of activities)
        path_tuple = tuple(name for name, _ in events_utc)
        variants_count[path_tuple].append(trace_id)

        first_ts = events_utc[0][1]
        last_ts  = events_utc[-1][1]
        duration_hours = (last_ts - first_ts).total_seconds() / 3600.0
        trace_durations.append((trace_id, duration_hours))

        # Pairwise transition times
        for (src_name, src_ts), (tgt_name, tgt_ts) in zip(events_utc, events_utc[1:]):
            delta_h = (tgt_ts - src_ts).total_seconds() / 3600.0
            if delta_h >= 0:   # skip negative (possible timestamp jitter)
                transition_times[(src_name, tgt_name)].append(delta_h)

    return trace_durations, dict(transition_times), variants_count


def summarize(values: list[float]) -> dict:
    if not values:
        return {}
    return {
        "count":      len(values),
        "mean_h":     statistics.mean(values),
        "median_h":   statistics.median(values),
        "std_h":      statistics.stdev(values) if len(values) > 1 else 0.0,
        "min_h":      min(values),
        "max_h":      max(values),
        "p25_h":      sorted(values)[int(0.25 * len(values))],
        "p75_h":      sorted(values)[int(0.75 * len(values))],
        "p90_h":      sorted(values)[int(0.90 * len(values))],
    }


def hours_to_str(h: float) -> str:
    """Human-friendly duration string."""
    if h < 1:
        return f"{h * 60:.1f} min"
    if h < 48:
        return f"{h:.2f} h"
    return f"{h / 24:.2f} days"


# ─────────────────────────────────────────────────────────────────────────────
#  OUTPUT WRITERS
# ─────────────────────────────────────────────────────────────────────────────

def write_trace_durations_csv(path: str, trace_durations: list[tuple[str, float]]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["trace_id", "duration_hours", "duration_days"])
        for trace_id, dur_h in trace_durations:
            writer.writerow([trace_id, round(dur_h, 4), round(dur_h / 24, 4)])


def write_activity_transitions_csv(path: str,
                                   transition_times: dict[tuple[str, str], list[float]]):
    rows = []
    for (src, tgt), times in transition_times.items():
        s = summarize(times)
        rows.append({
            "source":     src,
            "target":     tgt,
            "count":      s["count"],
            "mean_h":     round(s["mean_h"],   4),
            "median_h":   round(s["median_h"], 4),
            "std_h":      round(s["std_h"],    4),
            "min_h":      round(s["min_h"],    4),
            "max_h":      round(s["max_h"],    4),
        })
    rows.sort(key=lambda r: -r["count"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "count",
                                               "mean_h", "median_h", "std_h",
                                               "min_h", "max_h"])
        writer.writeheader()
        writer.writerows(rows)


def write_summary_json(path: str, summary: dict, transition_times: dict):
    # Per-activity summary (group by target = when it was performed)
    activity_times: defaultdict[str, list[float]] = defaultdict(list)
    for (_, tgt), times in transition_times.items():
        activity_times[tgt].extend(times)

    act_summary = {}
    for act, times in activity_times.items():
        s = summarize(times)
        act_summary[act] = {k: round(v, 4) for k, v in s.items() if isinstance(v, float)}
        act_summary[act]["count"] = s["count"]

    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall": {k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in summary.items()},
        "per_activity_wait_hours": act_summary,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_variants_json(path: str, variants_count: dict[tuple, list[str]]):
    variants = []
    for path_tuple, trace_ids in variants_count.items():
        variants.append({
            "path": list(path_tuple),
            "count": len(trace_ids),
            "traces": trace_ids[:5] # keep it small, only top 5 examples
        })
    # Sort by frequency descending
    variants.sort(key=lambda v: -v["count"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(variants, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
#  CONSOLE OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(summary: dict, transition_times: dict):
    print()
    print("═" * 65)
    print("  CYCLE TIME ANALYSIS — Overall Process Duration")
    print("═" * 65)
    print(f"  Traces analysed : {summary['count']}")
    print(f"  Mean            : {hours_to_str(summary['mean_h'])}")
    print(f"  Median          : {hours_to_str(summary['median_h'])}")
    print(f"  Std Dev         : {hours_to_str(summary['std_h'])}")
    print(f"  Min             : {hours_to_str(summary['min_h'])}")
    print(f"  Max             : {hours_to_str(summary['max_h'])}")
    print(f"  P25             : {hours_to_str(summary['p25_h'])}")
    print(f"  P75             : {hours_to_str(summary['p75_h'])}")
    print(f"  P90             : {hours_to_str(summary['p90_h'])}")
    print("═" * 65)

    # Top 10 transitions by median wait time
    rows = []
    for (src, tgt), times in transition_times.items():
        med = statistics.median(times)
        rows.append((src, tgt, len(times), med))
    rows.sort(key=lambda r: -r[3])

    print()
    print("─" * 65)
    print("  Top 10 slowest transitions (by median wait)")
    print("─" * 65)
    print(f"  {'Source':<22} {'Target':<22} {'N':>5}  {'Median':>10}")
    print("─" * 65)
    for src, tgt, n, med in rows[:10]:
        print(f"  {src:<22} {tgt:<22} {n:>5}  {hours_to_str(med):>10}")
    print("─" * 65)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()
    if not os.path.exists(args.input):
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    os.makedirs(args.output_dir, exist_ok=True)
    print(f"Parsing {args.input} …")

    trace_durations, transition_times, variants_count = compute_cycle_times(
        args.input, max_traces=args.max_traces
    )

    if not trace_durations:
        print("No traces with timestamps found.", file=sys.stderr)
        return 1

    durations = [d for _, d in trace_durations]
    summary = summarize(durations)

    print_summary(summary, transition_times)

    # Write outputs
    traces_csv      = os.path.join(args.output_dir, "trace_durations.csv")
    transitions_csv = os.path.join(args.output_dir, "activity_transitions.csv")
    summary_json    = os.path.join(args.output_dir, "cycle_time_summary.json")
    variants_json   = os.path.join(args.output_dir, "variants.json")

    write_trace_durations_csv(traces_csv, trace_durations)
    write_activity_transitions_csv(transitions_csv, transition_times)
    write_summary_json(summary_json, summary, transition_times)
    write_variants_json(variants_json, variants_count)

    print()
    print(f"  Outputs written to {args.output_dir}/")
    print(f"    {os.path.basename(traces_csv)}")
    print(f"    {os.path.basename(transitions_csv)}")
    print(f"    {os.path.basename(summary_json)}")
    print(f"    {os.path.basename(variants_json)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
