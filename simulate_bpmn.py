#!/usr/bin/env python3
"""
simulate_bpmn.py
Giả lập quy trình BPMN Sepsis với thời gian tuỳ chỉnh.

Usage:
    # Chạy với baseline từ event log thực tế
    python simulate_bpmn.py

    # Chạy với config YAML tuỳ chỉnh
    python simulate_bpmn.py --config my_scenario.yaml

    # Chạy override inline
    python simulate_bpmn.py --set "ER Triage=0.05h" --set "IV Antibiotics=1h"

    # Đặt tên scenario
    python simulate_bpmn.py --config optimized.yaml --scenario-name "Optimized v1"
"""

import argparse
import json
import os
import re
import sys
from copy import deepcopy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS — BPMN process paths (from data analysis)
# ─────────────────────────────────────────────────────────────────────────────

# The Sepsis BPMN has two main parallel paths after triage:
#   Path A (Lab route):   ER Reg → ER Triage → ER Sepsis Triage → Lab Tests → Admission NC → Release
#   Path B (IV route):    ER Reg → ER Triage → ER Sepsis Triage → IV Liquid → IV Antibiotics → Admission NC → Release
# Gateway split weights (from real data proportions):
#   Lab route: ~55% of cases reach lab tests first
#   IV route:  ~72% get IV Liquid → IV Antibiotics
#   Admission NC vs IC: ~91% vs ~9%
#   Release A: ~82%, Release B: ~7%, Release C: ~3%, Release D: ~3%, Release E: ~1%
# After Release A, 41% go to Return ER.

GATEWAY_WEIGHTS = {
    "lab_vs_iv":       {"Lab Tests": 0.55, "IV Liquid": 0.45},
    "admission":       {"Admission NC": 0.91, "Admission IC": 0.09},
    "release":         {"Release A": 0.82, "Release B": 0.07,
                        "Release C": 0.03, "Release D": 0.07, "Release E": 0.01},
    "return_er_prob":  0.41,
}

# Ordered steps for a single "happy path" simulation
# Each dict: name, type (task|gateway|loop), branches (for gateway)
PROCESS_STEPS = [
    {"name": "ER Registration",    "type": "task"},
    {"name": "ER Triage",          "type": "task"},
    {"name": "ER Sepsis Triage",   "type": "task"},
    # XOR gateway: Lab Tests OR IV
    {"name": "Lab / IV Gateway",   "type": "gateway",
     "branches": ["Lab Tests", "IV Liquid"],
     "weights":  [GATEWAY_WEIGHTS["lab_vs_iv"]["Lab Tests"],
                  GATEWAY_WEIGHTS["lab_vs_iv"]["IV Liquid"]]},
    {"name": "Lab Tests",          "type": "task"},
    {"name": "IV Liquid",          "type": "task"},
    {"name": "IV Antibiotics",     "type": "task"},
    # XOR gateway: Admission NC OR IC
    {"name": "Admission Gateway",  "type": "gateway",
     "branches": ["Admission NC", "Admission IC"],
     "weights":  [GATEWAY_WEIGHTS["admission"]["Admission NC"],
                  GATEWAY_WEIGHTS["admission"]["Admission IC"]]},
    {"name": "Admission NC",       "type": "task"},
    {"name": "Admission IC",       "type": "task"},
    # Release gateway
    {"name": "Release Gateway",    "type": "gateway",
     "branches": list(GATEWAY_WEIGHTS["release"].keys()),
     "weights":  list(GATEWAY_WEIGHTS["release"].values())},
    {"name": "Release A",          "type": "task"},
    {"name": "Release B",          "type": "task"},
    {"name": "Release C",          "type": "task"},
    {"name": "Release D",          "type": "task"},
    {"name": "Release E",          "type": "task"},
    # Return ER (conditional)
    {"name": "Return ER",          "type": "task"},
]

# Only task-type steps are time-bearing
TASK_STEPS = [s["name"] for s in PROCESS_STEPS if s["type"] == "task"]

# Default baseline times (hours) — will be loaded from activity_transitions.csv
# These hardcoded values are fallback if no CSV found
DEFAULT_BASELINE_HOURS: dict[str, float] = {
    "ER Registration":  0.0,
    "ER Triage":        0.30,
    "ER Sepsis Triage": 0.01,
    "Lab Tests":        48.0,
    "IV Liquid":        2.50,
    "IV Antibiotics":   0.01,
    "Admission NC":     9.00,
    "Admission IC":     72.0,
    "Release A":        0.10,
    "Release B":        0.10,
    "Release C":        0.10,
    "Release D":        0.10,
    "Release E":        0.10,
    "Return ER":        24.0,
}


# ─────────────────────────────────────────────────────────────────────────────
#  ARGUMENT PARSING
# ─────────────────────────────────────────────────────────────────────────────

def parse_time_override(s: str) -> tuple[str, float]:
    """Parse 'Activity Name=1.5h' or 'Activity Name=90m' or 'Activity Name=2d'."""
    m = re.match(r"^(.+?)=([0-9.]+)(h|m|d)?$", s.strip())
    if not m:
        raise argparse.ArgumentTypeError(
            f"Invalid format '{s}'. Use: \"Activity Name=1.5h\" (h=hours, m=minutes, d=days)"
        )
    name, val, unit = m.group(1).strip(), float(m.group(2)), (m.group(3) or "h")
    if unit == "m":
        val /= 60.0
    elif unit == "d":
        val *= 24.0
    return name, val


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate Sepsis BPMN process with custom activity durations."
    )
    parser.add_argument("--config", metavar="FILE",
                        help="YAML config file with activity durations (optional)")
    parser.add_argument("--set", dest="overrides", metavar="KEY=VALUE",
                        action="append", default=[],
                        help="Override activity duration, e.g. --set \"ER Triage=0.5h\"")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--transitions-csv", default="output/activity_transitions.csv",
                        help="CSV from analyze_cycle_time.py for baseline times")
    parser.add_argument("--scenario-name", default="Scenario",
                        help="Name for the simulation scenario (for chart title)")
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  BASELINE LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_baseline_from_csv(csv_path: str) -> dict[str, float]:
    """Load median transition times from activity_transitions.csv.
    For each target activity, compute weighted average of all incoming median times.
    """
    import csv as csvmodule
    times: dict[str, list[float]] = {}
    if not os.path.exists(csv_path):
        print(f"  [warn] {csv_path} not found, using hardcoded defaults.", file=sys.stderr)
        return deepcopy(DEFAULT_BASELINE_HOURS)

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csvmodule.DictReader(f)
        for row in reader:
            tgt = row["target"].strip()
            try:
                med = float(row["median_h"])
                cnt = int(row["count"])
            except (ValueError, KeyError):
                continue
            if tgt not in times:
                times[tgt] = []
            # weighted by count
            times[tgt].extend([med] * cnt)

    result = deepcopy(DEFAULT_BASELINE_HOURS)
    for act, vals in times.items():
        # Map "Lab Tests" = merge of CRP, Leucocytes, LacticAcid
        if act in result:
            result[act] = sum(vals) / len(vals)

    # Lab Tests: merge CRP / Leucocytes / LacticAcid (all are lab activities)
    lab_keys = ["CRP", "Leucocytes", "LacticAcid"]
    lab_vals = [times[k] for k in lab_keys if k in times]
    if lab_vals:
        flat = [v for sub in lab_vals for v in sub]
        result["Lab Tests"] = sum(flat) / len(flat)

    return result


def load_config_yaml(path: str) -> dict[str, float]:
    """Load YAML config (requires PyYAML if available, else fallback to basic parse)."""
    try:
        import yaml  # type: ignore
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return {k: float(v) for k, v in data.get("activities", {}).items()}
    except ImportError:
        # Manual basic YAML parse (key: value lines under activities:)
        result = {}
        in_activities = False
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue
                if stripped == "activities:":
                    in_activities = True
                    continue
                if in_activities:
                    if stripped[0].isalpha() and not stripped.startswith(" ") and ":" in stripped and not stripped.endswith(":"):
                        # top-level key, not under activities
                        in_activities = False
                        continue
                    m = re.match(r"^\s+([^:]+):\s*([0-9.]+)", line)
                    if m:
                        result[m.group(1).strip()] = float(m.group(2))
        return result


# ─────────────────────────────────────────────────────────────────────────────
#  SIMULATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def simulate(activity_times: dict[str, float]) -> dict:
    """
    Compute weighted-average total process time by traversing the BPMN.
    Returns a dict of {step_name: cumulative_time_hours} and total.
    """
    gw = GATEWAY_WEIGHTS

    # Compute expected time at each task (weighted by gateway probabilities)
    step_times: dict[str, float] = {}

    def t(name: str) -> float:
        return activity_times.get(name, 0.0)

    # Sequential mandatory steps
    step_times["ER Registration"]  = t("ER Registration")
    step_times["ER Triage"]        = t("ER Triage")
    step_times["ER Sepsis Triage"] = t("ER Sepsis Triage")

    # Gateway 1: Lab OR IV (weighted average)
    p_lab = gw["lab_vs_iv"]["Lab Tests"]
    p_iv  = gw["lab_vs_iv"]["IV Liquid"]
    step_times["Lab / IV (weighted)"] = (
        p_lab * t("Lab Tests") +
        p_iv  * (t("IV Liquid") + t("IV Antibiotics"))
    )

    # Gateway 2: Admission NC or IC (weighted average)
    p_nc = gw["admission"]["Admission NC"]
    p_ic = gw["admission"]["Admission IC"]
    step_times["Admission (weighted)"] = (
        p_nc * t("Admission NC") +
        p_ic * t("Admission IC")
    )

    # Gateway 3: Release (weighted average)
    step_times["Release (weighted)"] = sum(
        prob * t(rel) for rel, prob in gw["release"].items()
    )

    # Return ER (conditional, weighted by probability)
    p_return = gw["return_er_prob"]
    step_times["Return ER (conditional)"] = p_return * t("Return ER")

    # Cumulative timeline
    ordered_keys = [
        "ER Registration",
        "ER Triage",
        "ER Sepsis Triage",
        "Lab / IV (weighted)",
        "Admission (weighted)",
        "Release (weighted)",
        "Return ER (conditional)",
    ]
    timeline: dict[str, float] = {}
    cumulative = 0.0
    for key in ordered_keys:
        val = step_times[key]
        cumulative += val
        timeline[key] = val

    return {
        "steps": timeline,
        "total_hours": cumulative,
        "total_days": cumulative / 24.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  CONSOLE OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

def hours_to_str(h: float) -> str:
    if h < 0.017:   return f"{h * 60:.1f} min"
    if h < 48:      return f"{h:.2f} h"
    return f"{h / 24:.2f} days"


def print_comparison(baseline_result: dict, scenario_result: dict, scenario_name: str):
    b_steps = baseline_result["steps"]
    s_steps = scenario_result["steps"]

    print()
    print("═" * 75)
    print(f"  BPMN SIMULATION — Baseline vs {scenario_name}")
    print("═" * 75)
    print(f"  {'Step':<28} {'Baseline':>12} {'Scenario':>12} {'Δ':>12}")
    print("─" * 75)
    for key in b_steps:
        b = b_steps[key]
        s = s_steps.get(key, 0.0)
        delta = s - b
        delta_str = ("+" if delta > 0 else "") + hours_to_str(abs(delta))
        if delta > 0:
            delta_str = "▲ " + delta_str
        elif delta < 0:
            delta_str = "▼ " + delta_str
        else:
            delta_str = "  " + delta_str
        print(f"  {key:<28} {hours_to_str(b):>12} {hours_to_str(s):>12}  {delta_str:>12}")
    print("─" * 75)
    b_total = baseline_result["total_hours"]
    s_total = scenario_result["total_hours"]
    delta_total = s_total - b_total
    pct = (delta_total / b_total * 100) if b_total else 0
    sign = "+" if pct > 0 else ""
    print(f"  {'TOTAL':<28} {hours_to_str(b_total):>12} {hours_to_str(s_total):>12}"
          f"  {'▲' if delta_total > 0 else '▼'} {hours_to_str(abs(delta_total)):>10}")
    print(f"  {'':28} {'':>12} {'':>12}  ({sign}{pct:.1f}%)")
    print("═" * 75)


# ─────────────────────────────────────────────────────────────────────────────
#  CHART
# ─────────────────────────────────────────────────────────────────────────────

def draw_simulation_chart(baseline_result: dict, scenario_result: dict,
                          scenario_name: str, output_path: str):
    b_steps = baseline_result["steps"]
    s_steps = scenario_result["steps"]
    labels  = list(b_steps.keys())
    b_vals  = [b_steps[k] for k in labels]
    s_vals  = [s_steps.get(k, 0.0) for k in labels]

    # Shorten labels for display
    short_labels = [
        l.replace("(weighted)", "(w)").replace("(conditional)", "(~)")
        for l in labels
    ]

    x  = np.arange(len(labels))
    w  = 0.35
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10),
                                    gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#F0F4F8")

    # ── Bar chart ──
    ax1.set_facecolor("#FAFCFF")
    bars_b = ax1.bar(x - w/2, b_vals, w, label="Baseline (actual data)",
                     color="#3498DB", alpha=0.85, edgecolor="white", linewidth=0.8)
    bars_s = ax1.bar(x + w/2, s_vals, w, label=f"Scenario: {scenario_name}",
                     color="#E67E22", alpha=0.85, edgecolor="white", linewidth=0.8)

    # Value labels on bars
    for bar in bars_b:
        h = bar.get_height()
        if h > 0.01:
            ax1.text(bar.get_x() + bar.get_width()/2, h + 0.3,
                     hours_to_str(h), ha="center", va="bottom",
                     fontsize=7, color="#2C3E50")
    for bar in bars_s:
        h = bar.get_height()
        if h > 0.01:
            ax1.text(bar.get_x() + bar.get_width()/2, h + 0.3,
                     hours_to_str(h), ha="center", va="bottom",
                     fontsize=7, color="#7D3C00")

    ax1.set_xticks(x)
    ax1.set_xticklabels(short_labels, rotation=20, ha="right", fontsize=9)
    ax1.set_ylabel("Time (hours)", fontsize=10)
    ax1.set_title(
        f"BPMN Simulation — Sepsis Process\nBaseline vs {scenario_name}",
        fontsize=13, fontweight="bold", pad=10, color="#1A252F"
    )
    ax1.legend(fontsize=9, framealpha=0.9)
    ax1.grid(axis="y", alpha=0.3, linestyle="--")
    ax1.spines[["top", "right"]].set_visible(False)

    # ── Delta chart ──
    deltas = [s - b for s, b in zip(s_vals, b_vals)]
    colors_delta = ["#E74C3C" if d > 0 else "#27AE60" for d in deltas]
    ax2.set_facecolor("#FAFCFF")
    ax2.bar(x, deltas, color=colors_delta, alpha=0.8, edgecolor="white")
    ax2.axhline(0, color="#555", linewidth=0.8, linestyle="-")
    ax2.set_xticks(x)
    ax2.set_xticklabels(short_labels, rotation=20, ha="right", fontsize=9)
    ax2.set_ylabel("Δ (hours)", fontsize=9)
    ax2.set_title("Difference (Scenario − Baseline)  ▲red=slower · ▼green=faster",
                  fontsize=9, color="#555")
    ax2.grid(axis="y", alpha=0.3, linestyle="--")
    ax2.spines[["top", "right"]].set_visible(False)

    # Summary box
    b_total = baseline_result["total_hours"]
    s_total = scenario_result["total_hours"]
    delta_t = s_total - b_total
    pct     = (delta_t / b_total * 100) if b_total else 0
    sign    = "+" if pct > 0 else ""
    summary_text = (
        f"Total  Baseline: {hours_to_str(b_total)}\n"
        f"Total  Scenario: {hours_to_str(s_total)}\n"
        f"Change: {sign}{pct:.1f}%  ({hours_to_str(abs(delta_t))} {'longer' if delta_t > 0 else 'shorter'})"
    )
    ax1.text(0.99, 0.97, summary_text, transform=ax1.transAxes,
             fontsize=8.5, va="top", ha="right",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#EBF5FB",
                       edgecolor="#2980B9", alpha=0.9))

    fig.tight_layout(pad=2.0)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"  Chart saved: {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    # 1. Load baseline from CSV (generated by analyze_cycle_time.py)
    print("Loading baseline times …")
    baseline_times = load_baseline_from_csv(args.transitions_csv)

    # 2. Build scenario times = baseline + overrides
    scenario_times = deepcopy(baseline_times)

    if args.config:
        if not os.path.exists(args.config):
            print(f"Config file not found: {args.config}", file=sys.stderr)
            return 1
        print(f"Loading config: {args.config}")
        yaml_overrides = load_config_yaml(args.config)
        scenario_times.update(yaml_overrides)

    for raw in args.overrides:
        try:
            name, val = parse_time_override(raw)
        except argparse.ArgumentTypeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        if name not in scenario_times:
            print(f"  [warn] Unknown activity '{name}', adding anyway.")
        scenario_times[name] = val
        print(f"  Override: {name} = {hours_to_str(val)}")

    # 3. Simulate
    baseline_result = simulate(baseline_times)
    scenario_result = simulate(scenario_times)

    # 4. Print comparison
    print_comparison(baseline_result, scenario_result, args.scenario_name)

    # 5. Save outputs
    result_json = os.path.join(args.output_dir, "simulation_result.json")
    with open(result_json, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at":    __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "scenario_name":   args.scenario_name,
            "baseline_times":  {k: round(v, 4) for k, v in baseline_times.items()},
            "scenario_times":  {k: round(v, 4) for k, v in scenario_times.items()},
            "baseline_result": baseline_result,
            "scenario_result": scenario_result,
        }, f, ensure_ascii=False, indent=2)

    chart_path = os.path.join(args.output_dir, "simulation_chart.png")
    draw_simulation_chart(baseline_result, scenario_result, args.scenario_name, chart_path)

    print()
    print(f"  Results saved: {result_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
