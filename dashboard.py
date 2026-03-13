#!/usr/bin/env python3
"""
dashboard.py  —  Web dashboard cho Sepsis Process Mining
Chạy: python dashboard.py   →  mở trình duyệt tại http://127.0.0.1:5050
"""

import csv
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, send_file, abort

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
XES_FILE   = BASE_DIR / "Sepsis Cases - Event Log.xes"

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def read_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_csv_as_list(path: Path, max_rows=500) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            rows.append(dict(row))
    return rows


def run_script(cmd: list[str]) -> dict:
    """Run a script in a subprocess, return {ok, stdout, stderr}."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(BASE_DIR),
            timeout=120
        )
        return {
            "ok":     result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "Timeout (120 s)"}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def python_exe() -> str:
    """Return venv python or current interpreter."""
    venv_py = BASE_DIR / ".venv" / "bin" / "python"
    return str(venv_py) if venv_py.exists() else sys.executable


# ─────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/summary")
def api_summary():
    data = read_json(OUTPUT_DIR / "cycle_time_summary.json")
    if data is None:
        return jsonify({"error": "File not found. Run analyze_cycle_time.py first."}), 404
    return jsonify(data)


@app.route("/api/loops")
def api_loops():
    rows = read_csv_as_list(OUTPUT_DIR / "loops.csv", max_rows=200)
    if not rows:
        return jsonify({"error": "File not found. Run build_graph.py first."}), 404
    return jsonify(rows)


@app.route("/api/transitions")
def api_transitions():
    rows = read_csv_as_list(OUTPUT_DIR / "activity_transitions.csv", max_rows=300)
    if not rows:
        return jsonify({"error": "File not found. Run analyze_cycle_time.py first."}), 404
    return jsonify(rows)


@app.route("/api/trace-durations")
def api_trace_durations():
    rows = read_csv_as_list(OUTPUT_DIR / "trace_durations.csv", max_rows=1100)
    return jsonify(rows)


@app.route("/api/simulation")
def api_simulation():
    data = read_json(OUTPUT_DIR / "simulation_result.json")
    if data is None:
        return jsonify({"error": "No simulation result yet."}), 404
    return jsonify(data)


@app.route("/api/centrality")
def api_centrality():
    rows = read_csv_as_list(OUTPUT_DIR / "centrality.csv")
    return jsonify(rows)


@app.route("/api/variants")
def api_variants():
    data = read_json(OUTPUT_DIR / "variants.json")
    if data is None:
        return jsonify([])
    return jsonify(data)


@app.route("/api/image/<name>")
def api_image(name: str):
    allowed = {"graph.png", "bpmn_diagram.png", "simulation_chart.png"}
    if name not in allowed:
        abort(404)
    path = OUTPUT_DIR / name
    if not path.exists():
        abort(404)
    return send_file(str(path), mimetype="image/png")


# ── Run scripts ───────────────────────────────────────────────────────────────

@app.route("/api/run/build-graph", methods=["POST"])
def run_build_graph():
    py = python_exe()
    result = run_script([py, "build_graph.py", str(XES_FILE)])
    return jsonify(result)


@app.route("/api/run/analyze", methods=["POST"])
def run_analyze():
    py = python_exe()
    result = run_script([py, "analyze_cycle_time.py", str(XES_FILE)])
    return jsonify(result)


@app.route("/api/run/simulate", methods=["POST"])
def run_simulate():
    """
    POST body: {
      "scenario_name": "...",
      "overrides": {"ER Triage": 0.5, "IV Antibiotics": 1.0, ...}  # hours
    }
    """
    body = request.get_json(silent=True) or {}
    scenario_name = body.get("scenario_name", "Web Scenario")
    overrides = body.get("overrides", {})

    py = python_exe()
    cmd = [py, "simulate_bpmn.py", "--scenario-name", scenario_name]
    for activity, hours in overrides.items():
        cmd += ["--set", f"{activity}={hours}h"]

    result = run_script(cmd)
    # reload fresh simulation result
    if result["ok"]:
        sim_data = read_json(OUTPUT_DIR / "simulation_result.json")
        result["simulation"] = sim_data
    return jsonify(result)


@app.route("/api/baseline-times")
def api_baseline_times():
    """Return per-activity baseline hours from activity_transitions.csv."""
    import sys as _sys
    # Import the helper from simulate_bpmn.py
    try:
        _sys.path.insert(0, str(BASE_DIR))
        import importlib
        sb = importlib.import_module("simulate_bpmn")
        times = sb.load_baseline_from_csv(str(OUTPUT_DIR / "activity_transitions.csv"))
        return jsonify({k: round(v, 4) for k, v in times.items()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# MAIN HTML TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────

HTML = r"""
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sepsis Process Mining — Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --card2: #22263a;
    --border: #2e3250;
    --accent: #4f6ef7;
    --accent2: #38d9a9;
    --accent3: #f06060;
    --accent4: #f7b731;
    --text: #e4e8f5;
    --muted: #8891b6;
    --pill-ok: #1a3a2a;
    --pill-ok-text: #38d9a9;
    --radius: 14px;
    --shadow: 0 4px 24px #0008;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Inter', 'Segoe UI', sans-serif; min-height: 100vh; }

  /* ── SIDEBAR ─────────────────────────── */
  #sidebar {
    position: fixed; left: 0; top: 0; bottom: 0; width: 230px;
    background: var(--card); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; padding: 24px 0; z-index: 100;
  }
  .sidebar-logo {
    padding: 0 22px 28px;
    font-weight: 800; font-size: 15px; color: var(--accent);
    letter-spacing: .3px; line-height: 1.35;
  }
  .sidebar-logo span { color: var(--text); font-weight: 400; display: block; font-size: 11px; margin-top: 3px; color: var(--muted); }
  .nav-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; padding: 0 22px 8px; margin-top: 12px; }
  .nav-item {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 22px; cursor: pointer; border-radius: 0;
    font-size: 13px; color: var(--muted); transition: all .2s;
    border-left: 3px solid transparent;
  }
  .nav-item:hover { color: var(--text); background: #ffffff08; }
  .nav-item.active { color: var(--accent); border-left-color: var(--accent); background: #4f6ef710; font-weight: 600; }
  .nav-icon { font-size: 16px; width: 20px; text-align: center; }

  /* ── MAIN ────────────────────────────── */
  #main { margin-left: 230px; padding: 32px 36px; min-height: 100vh; }
  .page { display: none; }
  .page.active { display: block; }
  .page-title { font-size: 24px; font-weight: 800; margin-bottom: 6px; }
  .page-sub   { font-size: 13px; color: var(--muted); margin-bottom: 28px; }

  /* ── CARDS ───────────────────────────── */
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 22px 24px;
    box-shadow: var(--shadow);
  }
  .card-title { font-size: 13px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: .7px; margin-bottom: 14px; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; margin-bottom: 24px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 24px; }
  .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-bottom: 24px; }
  @media(max-width:1200px) { .grid-4 { grid-template-columns: repeat(2,1fr); } }

  /* ── STAT CARD ───────────────────────── */
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px 22px; }
  .stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .7px; }
  .stat-value { font-size: 28px; font-weight: 800; margin: 6px 0 4px; color: var(--text); }
  .stat-sub   { font-size: 12px; color: var(--muted); }
  .stat-card.accent  { border-color: var(--accent);  }
  .stat-card.accent2 { border-color: var(--accent2); }
  .stat-card.accent3 { border-color: var(--accent3); }
  .stat-card.accent4 { border-color: var(--accent4); }
  .stat-value.blue   { color: var(--accent); }
  .stat-value.green  { color: var(--accent2); }
  .stat-value.red    { color: var(--accent3); }
  .stat-value.yellow { color: var(--accent4); }

  /* ── CHART WRAPPER ───────────────────── */
  .chart-wrap { position: relative; height: 280px; }
  .chart-wrap.tall { height: 360px; }

  /* ── TABLE ───────────────────────────── */
  .tbl-wrap { overflow-x: auto; max-height: 400px; overflow-y: auto; border-radius: 10px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead { position: sticky; top: 0; background: var(--card2); z-index: 2; }
  th { padding: 10px 14px; text-align: left; color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .6px; border-bottom: 1px solid var(--border); }
  td { padding: 9px 14px; border-bottom: 1px solid #ffffff08; color: var(--text); vertical-align: middle; }
  tr:hover td { background: #ffffff06; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; }
  .badge-blue   { background: #4f6ef720; color: var(--accent); }
  .badge-green  { background: #38d9a920; color: var(--accent2); }
  .badge-red    { background: #f0606020; color: var(--accent3); }
  .badge-yellow { background: #f7b73120; color: var(--accent4); }

  /* ── BUTTONS ─────────────────────────── */
  .btn { display: inline-flex; align-items: center; gap: 7px; padding: 9px 18px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: all .2s; }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-primary:hover { background: #3d5ce0; }
  .btn-success { background: #1a4a3a; color: var(--accent2); border: 1px solid var(--accent2); }
  .btn-success:hover { background: #2a6a52; }
  .btn-danger  { background: #3a1a1a; color: var(--accent3); border: 1px solid var(--accent3); }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .btn-group { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }

  /* ── RUN LOG ─────────────────────────── */
  .log-box {
    background: #090b12; border: 1px solid var(--border); border-radius: 10px;
    padding: 14px; font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 12px; color: #a9b7d6; max-height: 260px; overflow-y: auto;
    white-space: pre-wrap; word-break: break-all; line-height: 1.6; margin-top: 16px;
  }
  .log-box .ok   { color: var(--accent2); }
  .log-box .err  { color: var(--accent3); }

  /* ── SIMULATION FORM (REDESIGNED) ──── */
  .sim-layout { display: grid; grid-template-columns: 42% 1fr; gap: 20px; margin-bottom: 24px; }
  @media(max-width:1100px) { .sim-layout { grid-template-columns: 1fr; } }
  .bpmn-inline { width: 100%; border-radius: 10px; border: 1px solid var(--border); cursor: zoom-in; transition: opacity .2s; }
  .bpmn-inline:hover { opacity: .85; }
  .scenario-name-row { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
  .scenario-name-row label { font-size: 11px; color: var(--muted); font-weight: 700; text-transform: uppercase; white-space: nowrap; }
  .scenario-name-row input[type=text] {
    flex: 1; background: #090b12; border: 1px solid var(--border); border-radius: 8px;
    color: var(--text); padding: 8px 12px; font-size: 14px; outline: none;
  }
  /* slider rows */
  .slider-row { margin-bottom: 14px; }
  .slider-row-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 5px; }
  .slider-label { font-size: 12px; font-weight: 700; color: var(--text); }
  .slider-hint  { font-size: 10px; color: var(--muted); }
  .baseline-chip { font-size: 10px; background: #38d9a920; color: var(--accent2); padding: 2px 8px; border-radius: 10px; }
  .slider-controls { display: flex; align-items: center; gap: 10px; }
  .slider-controls input[type=range] {
    flex: 1; accent-color: var(--accent); height: 4px; cursor: pointer;
  }
  .slider-num {
    width: 70px; background: #090b12; border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); padding: 5px 8px; font-size: 13px; text-align: right; outline: none;
  }
  .slider-num:focus { border-color: var(--accent); }
  .slider-unit { font-size: 11px; color: var(--muted); width: 12px; }
  .slider-reset { font-size: 10px; color: var(--muted); cursor: pointer; padding: 3px 7px; border-radius: 5px; background: #ffffff10; border: none; color: var(--muted); }
  .slider-reset:hover { color: var(--text); background: #ffffff20; }
  /* history section */
  .history-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
  .history-tbl td:first-child { width: 40px; text-align: center; }
  .history-tbl .best-row td { background: #38d9a910; }
  .sim-compare { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 20px; }
  @media(max-width:900px) { .sim-compare { grid-template-columns: 1fr; } }
  .compare-col { background: var(--card2); border-radius: 10px; padding: 16px 18px; }
  .compare-col h4 { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .6px; margin-bottom: 12px; }
  .compare-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid #ffffff08; font-size: 13px; }
  .compare-row:last-child { border: none; }
  .compare-row .step-name { color: var(--muted); }
  .compare-row .step-val  { font-weight: 700; }
  .section-gap { margin-bottom: 24px; }
  
  /* ── DIAGRAM TABS & VIS NETWORK ─────── */
  .img-tabs { display: flex; gap: 10px; margin-bottom: 18px; flex-wrap: wrap; }
  .img-tab { padding: 8px 16px; border-radius: 8px; font-size: 13px; cursor: pointer; border: 1px solid var(--border); color: var(--muted); transition: all .2s; }
  .img-tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .img-viewer img { width: 100%; border-radius: 10px; border: 1px solid var(--border); }
  
  .diagram-tab { display: none; }
  .diagram-tab.active { display: block; }
  
  .variant-row { padding: 12px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background .2s; }
  .variant-row:hover { background: #ffffff08; }
  .variant-row.active { background: #4f6ef720; border-left: 3px solid var(--accent); }
  .variant-title { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
  .variant-sub { font-size: 11px; color: var(--muted); line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  
  #mynetwork { width: 100%; height: 450px; border-radius: 12px; background: #090b12; border: 1px solid var(--border); }
</style>
</head>
<body>

<!-- SIDEBAR -->
<nav id="sidebar">
  <div class="sidebar-logo">🏥 Sepsis Mining<span>Process Analytics Dashboard</span></div>
  <div class="nav-label">Analysis</div>
  <div class="nav-item active" onclick="goPage('overview')" id="nav-overview">
    <span class="nav-icon">📊</span> Overview
  </div>
  <div class="nav-item" onclick="goPage('cycletime')" id="nav-cycletime">
    <span class="nav-icon">⏱️</span> Cycle Time
  </div>
  <div class="nav-item" onclick="goPage('loops')" id="nav-loops">
    <span class="nav-icon">🔄</span> Loop Detection
  </div>
  <div class="nav-label">Tools</div>
  <div class="nav-item" onclick="goPage('simulate')" id="nav-simulate">
    <span class="nav-icon">🎮</span> Simulation
  </div>
  <div class="nav-item" onclick="goPage('diagrams')" id="nav-diagrams">
    <span class="nav-icon">🗺️</span> Diagrams
  </div>
  <div class="nav-item" onclick="goPage('runner')" id="nav-runner">
    <span class="nav-icon">▶️</span> Run Scripts
  </div>
</nav>

<!-- MAIN -->
<main id="main">

  <!-- ══ OVERVIEW ══════════════════════════════════════════════ -->
  <div class="page active" id="page-overview">
    <div class="page-title">📊 Overview</div>
    <div class="page-sub">Tổng quan quy trình điều trị Sepsis — 1,050 traces · 15,214 events · 16 activities</div>

    <div class="grid-4" id="stats-grid">
      <div class="loading">Đang tải…</div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-title">Phân phối Cycle Time (trace durations)</div>
        <div class="chart-wrap tall"><canvas id="chartDuration"></canvas></div>
      </div>
      <div class="card">
        <div class="card-title">Top 10 Activity — Importance Score</div>
        <div class="chart-wrap tall"><canvas id="chartImportance"></canvas></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Top 10 Transitions chậm nhất (median, giờ)</div>
      <div class="chart-wrap"><canvas id="chartTransitions"></canvas></div>
    </div>
  </div>

  <!-- ══ CYCLE TIME ══════════════════════════════════════════════ -->
  <div class="page" id="page-cycletime">
    <div class="page-title">⏱️ Cycle Time Analysis</div>
    <div class="page-sub">Phân tích thời gian xử lý từng trace và từng cặp activity</div>

    <div class="grid-4 section-gap" id="ct-stats-grid">
      <div class="loading">Đang tải…</div>
    </div>

    <div class="grid-2 section-gap">
      <div class="card">
        <div class="card-title">Percentile Distribution (days)</div>
        <div class="chart-wrap"><canvas id="chartPercentile"></canvas></div>
      </div>
      <div class="card">
        <div class="card-title">Histogram — Trace Duration (≤ 60 ngày)</div>
        <div class="chart-wrap"><canvas id="chartHist"></canvas></div>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-title">Activity Transitions — Thời gian chờ trung bình giữa các bước</div>
      <div class="tbl-wrap" id="transitions-table"><div class="loading">Đang tải…</div></div>
    </div>
  </div>

  <!-- ══ LOOPS ══════════════════════════════════════════════ -->
  <div class="page" id="page-loops">
    <div class="page-title">🔄 Loop Detection</div>
    <div class="page-sub">Các vòng lặp được phát hiện trong quy trình (200 cycles đầu)</div>

    <div class="grid-3 section-gap" id="loop-stats">
      <div class="loading">Đang tải…</div>
    </div>

    <div class="grid-2 section-gap">
      <div class="card">
        <div class="card-title">Phân bố độ dài vòng lặp</div>
        <div class="chart-wrap"><canvas id="chartLoopLen"></canvas></div>
      </div>
      <div class="card">
        <div class="card-title">Top 10 vòng lặp theo tần suất</div>
        <div class="chart-wrap"><canvas id="chartLoopFreq"></canvas></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Danh sách tất cả vòng lặp</div>
      <div class="tbl-wrap" id="loops-table"><div class="loading">Đang tải…</div></div>
    </div>
  </div>

  <!-- ══ SIMULATION ══════════════════════════════════════════════ -->
  <div class="page" id="page-simulate">
    <div class="page-title">🎮 BPMN Simulation</div>
    <div class="page-sub">Điều chỉnh slider để thay đổi thời gian từng bước — số trung bình thực tế hiển thị sẵn làm baseline</div>

    <!-- ── Top: BPMN + Config form ── -->
    <div class="sim-layout">

      <!-- LEFT: BPMN diagram -->
      <div class="card" style="padding:16px;">
        <div class="card-title">📍 BPMN — Tham khảo tên node</div>
        <img src="/api/image/bpmn_diagram.png" class="bpmn-inline"
             alt="BPMN Diagram" title="Click để xem lớn"
             onclick="window.open('/api/image/bpmn_diagram.png','_blank')">
        <p style="font-size:10px;color:var(--muted);margin-top:8px;text-align:center;">Click ảnh để phóng to</p>
      </div>

      <!-- RIGHT: sliders -->
      <div class="card">
        <div class="card-title">⚙️ Cấu hình kịch bản</div>
        <div class="scenario-name-row">
          <label>Tên kịch bản</label>
          <input type="text" id="sim-name" value="Kịch bản 1" placeholder="Nhập tên…">
          <button class="btn btn-success" onclick="runSimulation()" id="sim-run-btn" style="white-space:nowrap;">▶ Chạy</button>
        </div>
        <div id="sim-form-sliders">
          <div class="loading" style="padding:20px 0;">Đang tải giá trị baseline…</div>
        </div>
        <div id="sim-log" style="display:none;"></div>
      </div>
    </div>

    <!-- ── History & Multi-scenario comparison ── -->
    <div id="sim-history-section" style="display:none;" class="section-gap">
      <div class="card section-gap">
        <div class="history-header">
          <div class="card-title" style="margin:0;">📊 So sánh tất cả kịch bản (lịch sử)</div>
          <button class="btn" style="background:#3a1a1a;color:var(--accent3);border:1px solid var(--accent3);font-size:12px;padding:6px 12px;" onclick="clearHistory()">🗑 Xoá lịch sử</button>
        </div>
        <div class="chart-wrap tall"><canvas id="chartHistory"></canvas></div>
      </div>

      <div class="card section-gap">
        <div class="card-title">📋 Bảng lịch sử kịch bản</div>
        <div class="tbl-wrap">
          <table class="history-tbl">
            <thead><tr>
              <th>#</th><th>Tên kịch bản</th><th>Thời gian</th>
              <th>Tổng (days)</th><th>vs Baseline</th><th>Ghi chú</th>
            </tr></thead>
            <tbody id="history-tbody"></tbody>
          </table>
        </div>
      </div>

      <!-- Last run detail -->
      <div class="sim-compare" id="sim-compare-cards"></div>
    </div>
  </div>

  <!-- ══ DIAGRAMS ══════════════════════════════════════════════ -->
  <div class="page" id="page-diagrams">
    <div class="page-title">🗺️ Sơ đồ & Mô phỏng Quy trình</div>
    <div class="page-sub">Theo dõi các đường đi thực tế của bệnh nhân (Trace Animation) hoặc xem sơ đồ tĩnh</div>
    
    <div class="img-tabs">
      <div class="img-tab active" onclick="showDiagTab('tab-animation', this)">Trace Animation</div>
      <div class="img-tab" onclick="showDiagTab('tab-bpmn', this)">BPMN Diagram</div>
      <div class="img-tab" onclick="showDiagTab('tab-graph', this)">Activity Graph</div>
      <div class="img-tab" onclick="showDiagTab('tab-sim', this)">Simulation Chart</div>
    </div>
    
    <!-- 1) TRACE ANIMATION TAB -->
    <div id="tab-animation" class="diagram-tab active">
      <div class="grid-2" style="grid-template-columns: 320px 1fr; align-items: start;">
        <div class="card" style="padding:0; overflow:hidden;">
          <div class="card-title" style="padding: 16px 20px 10px; margin:0; border-bottom:1px solid var(--border);">
            Top Các Đường Đi Phổ Biến
          </div>
          <div id="variants-list" class="tbl-wrap" style="height:450px; overflow-y:auto;">
            <div class="loading">Đang tải variants...</div>
          </div>
        </div>
        
        <div class="card" style="padding:0; position:relative; display:flex; flex-direction:column; background:transparent; border:none; box-shadow:none;">
          <div id="mynetwork"></div>
          <div id="anim-controls" style="position:absolute; bottom:16px; left:16px; background:#1a1d27ee; padding:12px 18px; border-radius:10px; border:1px solid var(--border); display:none; align-items:center; gap:16px; box-shadow:0 4px 12px #000a;">
            <button class="btn btn-primary" onclick="replayCurrentVariant()">▶ Phát Lại Trace</button>
            <span id="anim-status" style="font-size:13px; font-weight:600; color:var(--accent2);"></span>
          </div>
        </div>
      </div>
    </div>

    <!-- 2) STATIC IMAGE TABS -->
    <div id="tab-bpmn" class="card img-viewer diagram-tab">
      <img src="/api/image/bpmn_diagram.png" alt="BPMN Diagram" onerror="this.alt='Chưa có ảnh'">
    </div>
    <div id="tab-graph" class="card img-viewer diagram-tab">
      <img src="/api/image/graph.png?t=1" alt="Activity Graph" onerror="this.alt='Chưa có ảnh'">
    </div>
    <div id="tab-sim" class="card img-viewer diagram-tab">
      <img src="/api/image/simulation_chart.png?t=1" alt="Simulation Chart" onerror="this.alt='Chưa có ảnh'">
    </div>
  </div>

  <!-- ══ RUN SCRIPTS ══════════════════════════════════════════════ -->
  <div class="page" id="page-runner">
    <div class="page-title">▶️ Run Scripts</div>
    <div class="page-sub">Chạy lại các script phân tích và xem output trực tiếp</div>

    <div class="card section-gap">
      <div class="card-title">Script 1 — build_graph.py (Loop Detection + Graph)</div>
      <p style="font-size:12px;color:var(--muted);margin-bottom:14px;">Phân tích graph, tính centrality, phát hiện vòng lặp. Output: <code>graph.png</code>, <code>loops.csv</code>, <code>centrality.csv</code></p>
      <button class="btn btn-success" onclick="runScript('build-graph', 'log-bg')">▶ Chạy build_graph.py</button>
      <div id="log-bg" class="log-box" style="display:none"></div>
    </div>

    <div class="card section-gap">
      <div class="card-title">Script 2 — analyze_cycle_time.py (Cycle Time)</div>
      <p style="font-size:12px;color:var(--muted);margin-bottom:14px;">Phân tích timestamp, tính cycle time. Output: <code>trace_durations.csv</code>, <code>activity_transitions.csv</code>, <code>cycle_time_summary.json</code></p>
      <button class="btn btn-success" onclick="runScript('analyze', 'log-ct')">▶ Chạy analyze_cycle_time.py</button>
      <div id="log-ct" class="log-box" style="display:none"></div>
    </div>
  </div>

</main>

<script>
// ── NAVIGATION ──────────────────────────────────────────────────────────────
function goPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
}

// ── UTILS ────────────────────────────────────────────────────────────────────
async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) return null;
  return r.json();
}

function fmt(h) {
  if (h == null || isNaN(h)) return '—';
  h = parseFloat(h);
  if (h < 0.017) return (h * 60).toFixed(1) + ' min';
  if (h < 48)    return h.toFixed(2) + ' h';
  return (h / 24).toFixed(2) + ' days';
}

let simChart = null, simDeltaChart = null;
let durationChart = null, importanceChart = null, transitionChart = null;
let percentileChart = null, histChart = null;
let loopLenChart = null, loopFreqChart = null;

const CHART_DEFAULTS = {
  plugins: { legend: { labels: { color: '#8891b6', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#8891b6', font: { size: 10 } }, grid: { color: '#2e325008' } },
    y: { ticks: { color: '#8891b6', font: { size: 10 } }, grid: { color: '#2e325040' } }
  }
};

function mkChart(id, type, data, options = {}) {
  const ctx = document.getElementById(id);
  if (!ctx) return null;
  return new Chart(ctx, {
    type, data,
    options: {
      responsive: true, maintainAspectRatio: false,
      ...CHART_DEFAULTS, ...options,
      plugins: { ...CHART_DEFAULTS.plugins, ...options.plugins },
      scales: { ...CHART_DEFAULTS.scales, ...options.scales }
    }
  });
}

// ── OVERVIEW ─────────────────────────────────────────────────────────────────
async function loadOverview() {
  const [sum, centrality, transitions, traces] = await Promise.all([
    fetchJSON('/api/summary'),
    fetchJSON('/api/centrality'),
    fetchJSON('/api/transitions'),
    fetchJSON('/api/trace-durations')
  ]);

  // Stat cards
  if (sum && sum.overall) {
    const o = sum.overall;
    document.getElementById('stats-grid').innerHTML = `
      <div class="stat-card accent2"><div class="stat-label">Median Cycle Time</div>
        <div class="stat-value green">${fmt(o.median_h)}</div><div class="stat-sub">Đại diện tốt nhất</div></div>
      <div class="stat-card accent"><div class="stat-label">Mean Cycle Time</div>
        <div class="stat-value blue">${fmt(o.mean_h)}</div><div class="stat-sub">Bị kéo bởi ca phức tạp</div></div>
      <div class="stat-card accent3"><div class="stat-label">Max Cycle Time</div>
        <div class="stat-value red">${fmt(o.max_h)}</div><div class="stat-sub">Ca dài nhất</div></div>
      <div class="stat-card accent4"><div class="stat-label">Độ lệch chuẩn</div>
        <div class="stat-value yellow">${fmt(o.std_h)}</div><div class="stat-sub">Mức độ biến động</div></div>
    `;
  }

  // Duration histogram (bucket by day ranges)
  if (traces && traces.length) {
    const durations = traces.map(r => parseFloat(r.duration_days)).filter(d => d <= 60);
    const buckets = Array(12).fill(0);
    const labels = ['0-1d','1-2d','2-5d','5-10d','10-15d','15-20d','20-30d','30-40d','40-50d','50-60d','60+d','outlier'];
    const ranges = [[0,1],[1,2],[2,5],[5,10],[10,15],[15,20],[20,30],[30,40],[40,50],[50,60],[60,200],[200,99999]];
    traces.forEach(r => {
      const d = parseFloat(r.duration_days);
      for (let i = 0; i < ranges.length; i++) {
        if (d >= ranges[i][0] && d < ranges[i][1]) { buckets[i]++; break; }
      }
    });
    if (durationChart) durationChart.destroy();
    durationChart = mkChart('chartDuration', 'bar', {
      labels: ['0-1d','1-2d','2-5d','5-10d','10-15d','15-20d','20-30d','30-40d','40-50d','50-60d','60d+','outlier'],
      datasets: [{ label: 'Số trace', data: buckets, backgroundColor: '#4f6ef780', borderColor: '#4f6ef7', borderWidth: 1 }]
    });
  }

  // Importance score chart
  if (centrality && centrality.length) {
    const top = centrality.slice(0, 10);
    if (importanceChart) importanceChart.destroy();
    importanceChart = mkChart('chartImportance', 'bar', {
      labels: top.map(r => r.activity),
      datasets: [{
        label: 'Importance Score', data: top.map(r => parseFloat(r.importance_score).toFixed(3)),
        backgroundColor: top.map((_, i) => `hsl(${220 + i * 12}, 70%, 55%)`), borderRadius: 6
      }]
    }, { indexAxis: 'y' });
  }

  // Top transitions chart
  if (transitions && transitions.length) {
    const top10 = transitions.slice(0, 10);
    if (transitionChart) transitionChart.destroy();
    transitionChart = mkChart('chartTransitions', 'bar', {
      labels: top10.map(r => r.source + ' → ' + r.target),
      datasets: [{
        label: 'Median wait (h)', data: top10.map(r => parseFloat(r.median_h).toFixed(2)),
        backgroundColor: '#38d9a960', borderColor: '#38d9a9', borderWidth: 1, borderRadius: 6
      }]
    }, { indexAxis: 'y' });
  }
}

// ── CYCLE TIME ────────────────────────────────────────────────────────────────
async function loadCycleTime() {
  const [sum, transitions, traces] = await Promise.all([
    fetchJSON('/api/summary'), fetchJSON('/api/transitions'), fetchJSON('/api/trace-durations')
  ]);

  if (sum && sum.overall) {
    const o = sum.overall;
    document.getElementById('ct-stats-grid').innerHTML = `
      <div class="stat-card accent2"><div class="stat-label">Median</div>
        <div class="stat-value green">${fmt(o.median_h)}</div></div>
      <div class="stat-card accent"><div class="stat-label">Mean</div>
        <div class="stat-value blue">${fmt(o.mean_h)}</div></div>
      <div class="stat-card"><div class="stat-label">P25 / P75</div>
        <div class="stat-value" style="font-size:20px">${fmt(o.p25_h)} / ${fmt(o.p75_h)}</div></div>
      <div class="stat-card accent3"><div class="stat-label">P90</div>
        <div class="stat-value red">${fmt(o.p90_h)}</div></div>
    `;
    // Percentile chart
    const labels = ['Min','P25','Median','P75','P90','Max'];
    const vals   = [o.min_h, o.p25_h, o.median_h, o.p75_h, o.p90_h, o.max_h].map(v => (v/24).toFixed(2));
    if (percentileChart) percentileChart.destroy();
    percentileChart = mkChart('chartPercentile', 'bar', {
      labels,
      datasets: [{
        label: 'Days', data: vals,
        backgroundColor: ['#38d9a960','#4f6ef750','#f7b73160','#f7b73180','#f0606060','#f0606090'],
        borderRadius: 8, borderWidth: 0
      }]
    });
  }

  // Histogram (≤ 60 days)
  if (traces && traces.length) {
    const durations = traces.map(r => parseFloat(r.duration_days)).filter(d => !isNaN(d) && d <= 60);
    const nbuckets = 20;
    const max60 = 60;
    const bsize = max60 / nbuckets;
    const buckets = Array(nbuckets).fill(0);
    durations.forEach(d => {
      const i = Math.min(Math.floor(d / bsize), nbuckets - 1);
      buckets[i]++;
    });
    const lbls = buckets.map((_, i) => (i * bsize).toFixed(0) + 'd');
    if (histChart) histChart.destroy();
    histChart = mkChart('chartHist', 'bar', {
      labels: lbls,
      datasets: [{ label: 'Số trace (≤60 ngày)', data: buckets, backgroundColor: '#4f6ef760', borderColor: '#4f6ef7', borderRadius: 4 }]
    }, { plugins: { legend: { display: false } } });
  }

  // Transitions table
  if (transitions && transitions.length) {
    const rows = transitions.slice(0, 100).map(r => `
      <tr>
        <td>${r.source}</td>
        <td>${r.target}</td>
        <td><span class="badge badge-blue">${r.count}</span></td>
        <td>${fmt(parseFloat(r.mean_h))}</td>
        <td><strong>${fmt(parseFloat(r.median_h))}</strong></td>
        <td>${fmt(parseFloat(r.std_h))}</td>
        <td>${fmt(parseFloat(r.min_h))}</td>
        <td>${fmt(parseFloat(r.max_h))}</td>
      </tr>`).join('');
    document.getElementById('transitions-table').innerHTML = `
      <table><thead><tr>
        <th>Source</th><th>Target</th><th>Count</th>
        <th>Mean</th><th>Median</th><th>Std</th><th>Min</th><th>Max</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  }
}

// ── LOOPS ─────────────────────────────────────────────────────────────────────
async function loadLoops() {
  const loops = await fetchJSON('/api/loops');
  if (!loops) {
    document.getElementById('loop-stats').innerHTML = '<div class="empty">Chưa có dữ liệu. Chạy build_graph.py trước.</div>';
    return;
  }
  const self_loops = loops.filter(l => l.length === '1' || parseInt(l.length) === 1);
  const short_loops = loops.filter(l => parseInt(l.length) <= 3 && parseInt(l.length) > 1);
  const long_loops  = loops.filter(l => parseInt(l.length) > 3);
  document.getElementById('loop-stats').innerHTML = `
    <div class="stat-card accent3"><div class="stat-label">Self-loops</div>
      <div class="stat-value red">${self_loops.length}</div><div class="stat-sub">Vòng 1 node</div></div>
    <div class="stat-card accent4"><div class="stat-label">Short loops (≤3)</div>
      <div class="stat-value yellow">${short_loops.length}</div><div class="stat-sub">Vòng 2-3 node</div></div>
    <div class="stat-card accent"><div class="stat-label">Long loops (>3)</div>
      <div class="stat-value blue">${long_loops.length}</div><div class="stat-sub">Vòng ≥4 node</div></div>
  `;

  // Length distribution
  const lenCount = {};
  loops.forEach(l => { const k = l.length; lenCount[k] = (lenCount[k] || 0) + 1; });
  const lens = Object.keys(lenCount).sort((a,b) => parseInt(a)-parseInt(b));
  if (loopLenChart) loopLenChart.destroy();
  loopLenChart = mkChart('chartLoopLen', 'bar', {
    labels: lens.map(l => l + ' node'),
    datasets: [{ label: 'Số vòng lặp', data: lens.map(l => lenCount[l]),
      backgroundColor: '#f7b73170', borderColor: '#f7b731', borderRadius: 6 }]
  });

  // Top 10 by min_edge_weight
  const top10 = [...loops].sort((a,b) => parseInt(b.min_edge_weight) - parseInt(a.min_edge_weight)).slice(0, 10);
  if (loopFreqChart) loopFreqChart.destroy();
  loopFreqChart = mkChart('chartLoopFreq', 'bar', {
    labels: top10.map(l => l.loop_path.length > 40 ? l.loop_path.slice(0,37)+'…' : l.loop_path),
    datasets: [{ label: 'Min Edge Weight', data: top10.map(l => parseInt(l.min_edge_weight)),
      backgroundColor: '#f0606060', borderColor: '#f06060', borderRadius: 6 }]
  }, { indexAxis: 'y' });

  // Table
  const rows = loops.map((l, i) => `
    <tr>
      <td>${i + 1}</td>
      <td><span class="badge ${parseInt(l.length)===1?'badge-red':parseInt(l.length)<=3?'badge-yellow':'badge-blue'}">${l.length}</span></td>
      <td><span class="badge badge-green">${l.min_edge_weight}</span></td>
      <td style="max-width:500px;word-break:break-all;font-size:11px;">${l.loop_path}</td>
    </tr>`).join('');
  document.getElementById('loops-table').innerHTML = `
    <table><thead><tr><th>#</th><th>Độ dài</th><th>Tần suất</th><th>Đường đi</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

// ── SIMULATION (REDESIGNED) ──────────────────────────────────────────────────
const SIM_ACTIVITIES = [
  { key: 'ER Registration',  label: 'ER Registration',  hint: 'Đăng ký cấp cứu',       max: 10 },
  { key: 'ER Triage',        label: 'ER Triage',        hint: 'Phân loại cấp cứu',      max: 5  },
  { key: 'ER Sepsis Triage', label: 'ER Sepsis Triage', hint: 'Xác nhận Sepsis',         max: 2  },
  { key: 'Lab Tests',        label: 'Lab Tests',        hint: 'CRP/Leucocytes/LacticAcid', max: 200 },
  { key: 'IV Liquid',        label: 'IV Liquid',        hint: 'Truyền dịch',             max: 24 },
  { key: 'IV Antibiotics',   label: 'IV Antibiotics',   hint: 'Truyền kháng sinh',       max: 24 },
  { key: 'Admission NC',     label: 'Admission NC',     hint: 'Nhập khoa thường',        max: 100 },
  { key: 'Admission IC',     label: 'Admission IC',     hint: 'Nhập ICU',                max: 300 },
  { key: 'Release A',        label: 'Release A',        hint: 'Xuất viện A',             max: 50 },
  { key: 'Release B',        label: 'Release B',        hint: 'Xuất viện B',             max: 50 },
  { key: 'Return ER',        label: 'Return ER',        hint: 'Quay lại cấp cứu',       max: 600 },
];

let baselineTimes  = {};   // loaded from /api/baseline-times
let scenarioHistory = [];  // [{name, timestamp, total_hours, total_days, steps, pct}]
let historyChart   = null;

async function initSimPage() {
  const data = await fetchJSON('/api/baseline-times');
  if (!data || data.error) {
    document.getElementById('sim-form-sliders').innerHTML =
      '<div class="empty">Chưa có baseline. Hãy chạy analyze_cycle_time.py trước.</div>';
    return;
  }
  baselineTimes = data;
  buildSliders();
}

function sliderKey(key) { return 'sl-' + key.replace(/\s/g, '_'); }
function numKey(key)    { return 'sn-' + key.replace(/\s/g, '_'); }

function buildSliders() {
  document.getElementById('sim-form-sliders').innerHTML = SIM_ACTIVITIES.map(a => {
    const bv = parseFloat(baselineTimes[a.key] || 0);
    const maxV = Math.max(a.max, bv * 2, 1);
    const step = maxV <= 10 ? 0.1 : maxV <= 100 ? 0.5 : 1;
    return `
    <div class="slider-row">
      <div class="slider-row-header">
        <span class="slider-label">${a.label}</span>
        <span><span class="baseline-chip">baseline: ${fmtH(bv)}</span>
          <span class="slider-hint" style="margin-left:6px">${a.hint}</span></span>
      </div>
      <div class="slider-controls">
        <input type="range" id="${sliderKey(a.key)}" min="0" max="${maxV.toFixed(1)}" step="${step}" value="${bv.toFixed(2)}"
          oninput="syncNum('${a.key}', this.value)">
        <input type="number" id="${numKey(a.key)}" class="slider-num" value="${bv.toFixed(2)}" min="0" max="${maxV}" step="${step}"
          oninput="syncSlider('${a.key}', this.value)">
        <span class="slider-unit">h</span>
        <button class="slider-reset" onclick="resetSlider('${a.key}', ${bv.toFixed(2)})" title="Reset về baseline">↺</button>
      </div>
    </div>`;
  }).join('');
}

function fmtH(h) {
  if (h == null || isNaN(h)) return '—';
  h = parseFloat(h);
  if (h < 0.017) return (h * 60).toFixed(0) + 'min';
  if (h < 48)    return h.toFixed(2) + 'h';
  return (h / 24).toFixed(1) + 'd';
}

function syncNum(key, val) {
  const n = document.getElementById(numKey(key));
  if (n) n.value = parseFloat(val).toFixed(2);
}
function syncSlider(key, val) {
  const s = document.getElementById(sliderKey(key));
  if (s) s.value = val;
}
function resetSlider(key, baseline) {
  const s = document.getElementById(sliderKey(key));
  const n = document.getElementById(numKey(key));
  if (s) s.value = baseline;
  if (n) n.value = parseFloat(baseline).toFixed(2);
}

async function runSimulation() {
  const name = document.getElementById('sim-name').value || 'Kịch bản';
  const overrides = {};
  SIM_ACTIVITIES.forEach(a => {
    const n = document.getElementById(numKey(a.key));
    if (n) overrides[a.key] = parseFloat(n.value);
  });

  const btn = document.getElementById('sim-run-btn');
  btn.disabled = true; btn.textContent = '⏳ Đang chạy…';
  const log = document.getElementById('sim-log');
  log.style.display = 'block'; log.className = 'log-box';
  log.textContent = '⏳ Đang chạy simulation…';

  const resp = await fetch('/api/run/simulate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ scenario_name: name, overrides })
  });
  const data = await resp.json();
  btn.disabled = false; btn.textContent = '▶ Chạy';

  log.innerHTML = `<span class="${data.ok ? 'ok' : 'err'}">${data.ok ? '✅ Thành công' : '❌ Lỗi'}</span>\n${data.stdout || ''}${data.stderr ? '\n'+data.stderr : ''}`;

  if (data.ok && data.simulation) {
    const sim = data.simulation;
    const pct = ((sim.scenario_result.total_hours - sim.baseline_result.total_hours)
                  / sim.baseline_result.total_hours * 100);
    scenarioHistory.push({
      name:        name,
      timestamp:   new Date().toLocaleTimeString('vi-VN', {hour:'2-digit', minute:'2-digit'}),
      total_hours: sim.scenario_result.total_hours,
      total_days:  sim.scenario_result.total_days,
      steps:       sim.scenario_result.steps,
      baseline:    sim.baseline_result,
      pct:         pct,
    });
    renderHistory(sim.baseline_result);
    renderCompareCards(sim);
    document.getElementById('sim-history-section').style.display = 'block';
    // auto-increment scenario name
    const m = name.match(/(.*?)(\d+)$/);
    if (m) document.getElementById('sim-name').value = m[1] + (parseInt(m[2]) + 1);
  }
}

const SCENARIO_COLORS = [
  '#4f6ef7','#38d9a9','#f7b731','#f06060','#a855f7',
  '#22d3ee','#fb923c','#84cc16','#f472b6','#60a5fa'
];

function renderHistory(baselineResult) {
  // Build multi-dataset chart: baseline + all scenarios
  const stepKeys = Object.keys(baselineResult.steps);
  const shortLabels = stepKeys.map(k =>
    k.replace('(weighted)','(w)').replace('(conditional)','(~)')
  );

  const datasets = [
    {
      label: '🔵 Baseline',
      data: stepKeys.map(k => parseFloat(baselineResult.steps[k]).toFixed(3)),
      backgroundColor: '#3b82f640',
      borderColor: '#3b82f6',
      borderWidth: 2,
      borderRadius: 5,
    },
    ...scenarioHistory.map((sc, i) => ({
      label: sc.pct <= 0
        ? `✅ ${sc.name} (${sc.pct.toFixed(1)}%)`
        : `⚠️ ${sc.name} (+${sc.pct.toFixed(1)}%)`,
      data: stepKeys.map(k => parseFloat(sc.steps[k] || 0).toFixed(3)),
      backgroundColor: SCENARIO_COLORS[i % SCENARIO_COLORS.length] + '60',
      borderColor:     SCENARIO_COLORS[i % SCENARIO_COLORS.length],
      borderWidth: 2,
      borderRadius: 5,
    }))
  ];

  if (historyChart) historyChart.destroy();
  historyChart = mkChart('chartHistory', 'bar', { labels: shortLabels, datasets },
    { plugins: { legend: { labels: { color: '#8891b6', font: { size: 11 }, padding: 16 } } } });

  // Table
  const best = scenarioHistory.reduce((a, b) => a.pct < b.pct ? a : b, scenarioHistory[0]);
  const rows = scenarioHistory.map((sc, i) => {
    const isBest = sc === best;
    const pctStr = sc.pct <= 0
      ? `<span class="delta-neg">▼ ${Math.abs(sc.pct).toFixed(1)}%</span>`
      : `<span class="delta-pos">▲ +${sc.pct.toFixed(1)}%</span>`;
    return `<tr class="${isBest ? 'best-row' : ''}">
      <td>${i + 1}</td>
      <td><strong>${sc.name}</strong>${isBest ? ' 🏆' : ''}</td>
      <td style="color:var(--muted);font-size:12px;">${sc.timestamp}</td>
      <td><strong>${fmt(sc.total_hours)}</strong></td>
      <td>${pctStr}</td>
      <td style="font-size:11px;color:var(--muted);">vs baseline: ${fmt(parseFloat(baselineResult.total_hours))}</td>
    </tr>`;
  }).join('');
  document.getElementById('history-tbody').innerHTML = rows;
}

function renderCompareCards(sim) {
  const b = sim.baseline_result;
  const s = sim.scenario_result;
  const stepKeys = Object.keys(b.steps);
  const bCard = stepKeys.map(k => `
    <div class="compare-row"><span class="step-name">${k}</span>
    <span class="step-val">${fmt(b.steps[k])}</span></div>`).join('');
  const sCard = stepKeys.map(k => {
    const d = (s.steps[k] || 0) - b.steps[k];
    const cls = d > 0.001 ? 'delta-pos' : d < -0.001 ? 'delta-neg' : 'delta-neu';
    const ds = d > 0.001 ? '▲ +'+fmt(d) : d < -0.001 ? '▼ '+fmt(Math.abs(d)) : '—';
    return `<div class="compare-row"><span class="step-name">${k}</span>
      <span class="step-val">${fmt(s.steps[k] || 0)} <span class="${cls}" style="font-size:11px">${ds}</span></span></div>`;
  }).join('');
  const pct = ((s.total_hours - b.total_hours) / b.total_hours * 100).toFixed(1);
  const sign = parseFloat(pct) > 0 ? '+' : '';
  document.getElementById('sim-compare-cards').innerHTML = `
    <div class="compare-col"><h4>🔵 Baseline</h4>${bCard}
      <div class="compare-row" style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px">
        <strong>TOTAL</strong><strong>${fmt(b.total_hours)}</strong></div></div>
    <div class="compare-col"><h4>🟠 ${sim.scenario_name}</h4>${sCard}
      <div class="compare-row" style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px">
        <strong>TOTAL</strong><strong>${fmt(s.total_hours)}
        <span class="${parseFloat(pct)>0?'delta-pos':'delta-neg'}" style="font-size:12px"> (${sign}${pct}%)</span></strong></div></div>
  `;
}

function clearHistory() {
  if (!confirm('Xoá toàn bộ lịch sử kịch bản?')) return;
  scenarioHistory = [];
  document.getElementById('sim-history-section').style.display = 'none';
  if (historyChart) { historyChart.destroy(); historyChart = null; }
}

// ── DIAGRAMS & ANIMATION ──────────────────────────────────────────────────────
function showDiagTab(tabId, btn) {
  document.querySelectorAll('.img-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.diagram-tab').forEach(t => t.classList.remove('active'));
  document.getElementById(tabId).classList.add('active');
  
  if (tabId === 'tab-animation' && !network) {
    initNetwork();
  }
}

let network = null;
let nodes = null;
let edges = null;
let allVariants = [];
let currentVariantIndex = -1;
let animTimer = null;

async function initNetwork() {
  const transitions = await fetchJSON('/api/transitions');
  const variants = await fetchJSON('/api/variants');
  
  if (!transitions || !variants || variants.length === 0) {
    document.getElementById('variants-list').innerHTML = '<div class="empty" style="padding:20px">Chưa có dữ liệu variants.<br>Hãy chạy analyze_cycle_time.py</div>';
    return;
  }
  allVariants = variants;
  
  // Render variant list
  document.getElementById('variants-list').innerHTML = variants.slice(0, 50).map((v, i) => `
    <div class="variant-row" id="var-row-${i}" onclick="selectVariant(${i})">
      <div class="variant-title">Variant ${i+1} <span class="badge badge-blue" style="float:right">${v.count} traces</span></div>
      <div class="variant-sub">${v.path.join(' ➔ ')}</div>
    </div>
  `).join('');
  
  // Build Nodes & Edges from transitions
  const nodeSet = new Set();
  transitions.forEach(t => { nodeSet.add(t.source); nodeSet.add(t.target); });
  
  const nodesArr = Array.from(nodeSet).map(n => ({
    id: n, label: n, 
    shape: 'box', 
    color: { background: '#22263a', border: '#4f6ef7' },
    font: { color: '#e4e8f5', face: 'Inter' },
    borderWidth: 1, borderRadius: 6, margin: 10
  }));
  
  const edgesArr = transitions.map(t => ({
    from: t.source, to: t.target,
    id: t.source + '->' + t.target,
    arrows: 'to', color: { color: '#2e3250', opacity: 0.5 },
    width: 1
  }));
  
  nodes = new vis.DataSet(nodesArr);
  edges = new vis.DataSet(edgesArr);
  
  const container = document.getElementById('mynetwork');
  const data = { nodes, edges };
  const options = {
    physics: { barnesHut: { gravitationalConstant: -3000, centralGravity: 0.3, springLength: 120 } },
    interaction: { hover: true, tooltipDelay: 200 }
  };
  
  network = new vis.Network(container, data, options);
  
  // auto select top 1
  selectVariant(0);
}

function selectVariant(idx) {
  currentVariantIndex = idx;
  document.querySelectorAll('.variant-row').forEach(el => el.classList.remove('active'));
  const row = document.getElementById('var-row-' + idx);
  if (row) row.classList.add('active');
  
  replayCurrentVariant();
}

function resetGraphStyle() {
  nodes.forEach(n => nodes.update({id: n.id, color: { background: '#22263a', border: '#4f6ef7' }, borderWidth: 1}));
  edges.forEach(e => edges.update({id: e.id, color: { color: '#2e3250', opacity: 0.5 }, width: 1}));
}

async function replayCurrentVariant() {
  if (animTimer) clearTimeout(animTimer);
  resetGraphStyle();
  const v = allVariants[currentVariantIndex];
  if (!v) return;
  
  document.getElementById('anim-controls').style.display = 'flex';
  const st = document.getElementById('anim-status');
  st.textContent = `Chuẩn bị chạy Variant ${currentVariantIndex+1}...`;
  
  const path = v.path;
  let i = 0;
  
  function step() {
    if (i > 0) {
      // Highlight edge from previous to current
      const from = path[i-1];
      const to = path[i];
      const edgeId = from + '->' + to;
      if (edges.get(edgeId)) {
        edges.update({id: edgeId, color: { color: '#f7b731', opacity: 1 }, width: 3});
      }
    }
    
    // Highlight current node
    const cur = path[i];
    if (nodes.get(cur)) {
      nodes.update({id: cur, color: { background: '#f7b731', border: '#fff' }, borderWidth: 2});
      st.textContent = `Đang ở: ${cur} (${i+1}/${path.length})`;
    }
    
    i++;
    if (i < path.length) {
      animTimer = setTimeout(step, 800);
    } else {
      st.textContent = `Hoàn tất (${path.length} bước)`;
    }
  }
  
  setTimeout(step, 400);
}

// ── RUN SCRIPTS ───────────────────────────────────────────────────────────────
async function runScript(endpoint, logId) {
  const log = document.getElementById(logId);
  log.style.display = 'block';
  log.innerHTML = '⏳ Đang chạy…';
  const resp = await fetch('/api/run/' + endpoint, { method: 'POST' });
  const data = await resp.json();
  log.innerHTML = `<span class="${data.ok ? 'ok' : 'err'}">${data.ok ? '✅ Hoàn tất' : '❌ Lỗi'}</span>\n${data.stdout || ''}${data.stderr ? '\n[stderr]\n'+data.stderr : ''}`;
  if (data.ok) { loadOverview(); loadCycleTime(); loadLoops(); }
}

// ── INIT ─────────────────────────────────────────────────────────────────────
loadOverview();

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    const page = item.id.replace('nav-', '');
    if (page === 'cycletime') loadCycleTime();
    if (page === 'loops')     loadLoops();
    if (page === 'simulate')  initSimPage();
  });
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def open_browser(port: int):
    import time, webbrowser
    time.sleep(0.8)
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  🏥  Sepsis Dashboard đang chạy tại  http://127.0.0.1:{port}\n")
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False)

