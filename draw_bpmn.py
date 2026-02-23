#!/usr/bin/env python3
"""
draw_bpmn.py
Vẽ BPMN-style process diagram cho Sepsis Cases Event Log.

Output:
  output/bpmn_diagram.png   – ảnh BPMN
  output/bpmn_diagram.xml   – BPMN 2.0 XML (mở được bằng Camunda Modeler / draw.io)
"""

import os
import textwrap
import xml.dom.minidom as md
import xml.etree.ElementTree as ET

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Circle, RegularPolygon

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# 1.  LAYOUT DEFINITION
#     Posisi setiap elemen dalam koordinat grid (x, y).
#     Tipe: "start", "end", "task", "xor", "and_split", "and_join"
# ──────────────────────────────────────────────────────────────────────────────
NODES = {
    # id              : (x,   y,  type,           label)
    "start":            (0,   0,  "start",         "Start"),
    "er_reg":           (2,   0,  "task",          "ER\nRegistration"),
    "er_triage":        (4,   0,  "task",          "ER\nTriage"),
    "er_sepsis":        (6,   0,  "task",          "ER Sepsis\nTriage"),
    "gw_split1":        (8,   0,  "xor",           ""),          # XOR split: lab vs IV
    "lab_loop":         (10,  1.8,"task",          "Lab Tests\n(CRP / Leucocytes\n/ LacticAcid)"),
    "iv_liquid":        (10, -1.8,"task",          "IV\nLiquid"),
    "iv_antibiotics":   (12, -1.8,"task",          "IV\nAntibiotics"),
    "gw_join1":         (14,  0,  "xor",           ""),          # XOR join
    "adm_nc":           (16,  1,  "task",          "Admission\nNC"),
    "adm_ic":           (16, -1,  "task",          "Admission\nIC"),
    "gw_join2":         (18,  0,  "xor",           ""),          # XOR join
    "gw_release":       (20,  0,  "xor",           ""),          # XOR split release
    "rel_a":            (22,  2,  "task",          "Release A"),
    "rel_b":            (22,  0.7,"task",          "Release B"),
    "rel_c":            (22, -0.7,"task",          "Release C"),
    "rel_d":            (22, -2,  "task",          "Release D"),
    "return_er":        (24,  2,  "task",          "Return\nER"),
    "gw_end":           (26,  0,  "xor",           ""),
    "end":              (28,  0,  "end",           "End"),
}

FLOWS = [
    # (source, target, label, weight_hint)
    ("start",         "er_reg",         "",                 "normal"),
    ("er_reg",        "er_triage",      "971",              "thick"),
    ("er_triage",     "er_sepsis",      "905",              "thick"),
    ("er_sepsis",     "gw_split1",      "",                 "normal"),
    # XOR split → lab tests OR IV
    ("gw_split1",     "lab_loop",       "lab route",        "normal"),
    ("gw_split1",     "iv_liquid",      "IV route",         "normal"),
    # lab loop back (simplified as self-arrow note)
    ("lab_loop",      "gw_join1",       "285 / 269",        "normal"),
    # IV path
    ("iv_liquid",     "iv_antibiotics", "501",              "thick"),
    ("iv_antibiotics","gw_join1",       "489",              "thick"),
    # join → admission
    ("gw_join1",      "adm_nc",         "NC",               "normal"),
    ("gw_join1",      "adm_ic",         "IC",               "dashed"),
    # admissions → join2
    ("adm_nc",        "gw_join2",       "",                 "normal"),
    ("adm_ic",        "gw_join2",       "",                 "dashed"),
    # → release gateway
    ("gw_join2",      "gw_release",     "",                 "normal"),
    # release branches
    ("gw_release",    "rel_a",          "A  (671)",         "thick"),
    ("gw_release",    "rel_b",          "B  (56)",          "normal"),
    ("gw_release",    "rel_c",          "C  (25)",          "normal"),
    ("gw_release",    "rel_d",          "D  (24)",          "normal"),
    # Release A → Return ER (276 times)
    ("rel_a",         "return_er",      "276",              "normal"),
    ("return_er",     "gw_end",         "",                 "normal"),
    # Other releases → end
    ("rel_b",         "gw_end",         "",                 "normal"),
    ("rel_c",         "gw_end",         "",                 "normal"),
    ("rel_d",         "gw_end",         "",                 "normal"),
    ("gw_end",        "end",            "",                 "normal"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 2.  DRAWING HELPERS
# ──────────────────────────────────────────────────────────────────────────────
COLORS = {
    "start":      "#27AE60",
    "end":        "#E74C3C",
    "task":       "#2980B9",
    "task_text":  "white",
    "xor":        "#F39C12",
    "and_split":  "#8E44AD",
    "and_join":   "#8E44AD",
    "flow":       "#555555",
    "flow_thick": "#2C3E50",
    "flow_dashed":"#95A5A6",
    "pool_bg":    "#EBF5FB",
    "grid":       "#D6EAF8",
}

TASK_W = 1.6
TASK_H = 0.9
GW_R   = 0.42
EV_R   = 0.38


def draw_task(ax, x, y, label, color=None):
    c = color or COLORS["task"]
    rect = FancyBboxPatch(
        (x - TASK_W / 2, y - TASK_H / 2), TASK_W, TASK_H,
        boxstyle="round,pad=0.08", linewidth=1.5,
        edgecolor="white", facecolor=c, zorder=3,
    )
    ax.add_patch(rect)
    ax.text(x, y, label, ha="center", va="center",
            fontsize=6.5, color=COLORS["task_text"],
            fontweight="bold", wrap=True, zorder=4,
            multialignment="center")


def draw_gateway(ax, x, y, gtype="xor"):
    c = COLORS.get(gtype, COLORS["xor"])
    diamond = mpatches.RegularPolygon(
        (x, y), 4, radius=GW_R, orientation=0,
        facecolor=c, edgecolor="white", linewidth=1.5, zorder=3,
    )
    ax.add_patch(diamond)
    if gtype == "xor":
        ax.text(x, y, "✕", ha="center", va="center",
                fontsize=10, color="white", fontweight="bold", zorder=4)
    elif gtype in ("and_split", "and_join"):
        ax.text(x, y, "+", ha="center", va="center",
                fontsize=12, color="white", fontweight="bold", zorder=4)


def draw_event(ax, x, y, etype="start"):
    c = COLORS[etype]
    circ = Circle((x, y), EV_R, facecolor=c, edgecolor="white",
                  linewidth=2, zorder=3)
    ax.add_patch(circ)
    sym = "▶" if etype == "start" else "■"
    ax.text(x, y, sym, ha="center", va="center",
            fontsize=9, color="white", zorder=4)


def node_anchor(node_id, direction="right"):
    x, y, ntype, _ = NODES[node_id]
    if ntype == "task":
        if direction == "right":  return (x + TASK_W / 2, y)
        if direction == "left":   return (x - TASK_W / 2, y)
        if direction == "top":    return (x, y + TASK_H / 2)
        if direction == "bottom": return (x, y - TASK_H / 2)
    return (x, y)   # events and gateways: centre


def draw_flow(ax, src_id, tgt_id, label="", weight="normal"):
    sx, sy, stype, _ = NODES[src_id]
    tx, ty, ttype, _ = NODES[tgt_id]

    # pick anchor sides
    if tx >= sx:
        sp = node_anchor(src_id, "right")
        tp = node_anchor(tgt_id, "left")
    else:
        sp = node_anchor(src_id, "left")
        tp = node_anchor(tgt_id, "right")

    lw      = 2.0 if weight == "thick" else 1.2
    ls      = (0, (4, 3)) if weight == "dashed" else "-"
    col     = COLORS["flow_thick"] if weight == "thick" else \
              COLORS["flow_dashed"] if weight == "dashed" else COLORS["flow"]

    ax.annotate("",
        xy=tp, xytext=sp,
        arrowprops=dict(
            arrowstyle="-|>", color=col, lw=lw,
            linestyle=ls,
            connectionstyle="arc3,rad=0.0",
        ),
        zorder=2,
    )
    if label:
        mx, my = (sp[0] + tp[0]) / 2, (sp[1] + tp[1]) / 2
        ax.text(mx, my + 0.12, label, ha="center", va="bottom",
                fontsize=5.5, color="#666666", zorder=5)


# ──────────────────────────────────────────────────────────────────────────────
# 3.  MAIN DRAW
# ──────────────────────────────────────────────────────────────────────────────
def draw_bpmn(output_path: str):
    fig, ax = plt.subplots(figsize=(32, 10))
    fig.patch.set_facecolor("#F8FBFF")
    ax.set_facecolor(COLORS["pool_bg"])

    # Pool border
    all_x = [v[0] for v in NODES.values()]
    all_y = [v[1] for v in NODES.values()]
    pad = 1.2
    pool_rect = FancyBboxPatch(
        (min(all_x) - pad, min(all_y) - pad),
        max(all_x) - min(all_x) + 2 * pad,
        max(all_y) - min(all_y) + 2 * pad,
        boxstyle="round,pad=0.2", linewidth=2.5,
        edgecolor="#2980B9", facecolor=COLORS["pool_bg"], zorder=0,
    )
    ax.add_patch(pool_rect)
    ax.text(min(all_x) - pad + 0.1, (min(all_y) + max(all_y)) / 2,
            "Sepsis Treatment Process",
            ha="left", va="center", fontsize=11, fontweight="bold",
            color="#2980B9", rotation=90, zorder=5)

    # Draw flows first (under nodes)
    for src, tgt, lbl, wt in FLOWS:
        draw_flow(ax, src, tgt, lbl, wt)

    # Draw nodes
    for nid, (x, y, ntype, label) in NODES.items():
        if ntype == "task":
            draw_task(ax, x, y, label)
        elif ntype in ("xor", "and_split", "and_join"):
            draw_gateway(ax, x, y, ntype)
        elif ntype in ("start", "end"):
            draw_event(ax, x, y, ntype)
        # label below events/gateways
        if ntype in ("start", "end"):
            ax.text(x, y - EV_R - 0.18, label,
                    ha="center", va="top", fontsize=7, color="#333")

    # Legend
    legend_items = [
        mpatches.Patch(color=COLORS["task"],  label="Task / Activity"),
        mpatches.Patch(color=COLORS["xor"],   label="XOR Gateway"),
        mpatches.Patch(color=COLORS["start"], label="Start Event"),
        mpatches.Patch(color=COLORS["end"],   label="End Event"),
        mpatches.Patch(color=COLORS["flow_thick"],  label="High-frequency flow"),
        mpatches.Patch(color=COLORS["flow_dashed"], label="Low-frequency flow"),
    ]
    ax.legend(handles=legend_items, loc="lower right", fontsize=8,
              framealpha=0.9, title="Legend", title_fontsize=8)

    ax.set_xlim(min(all_x) - pad - 0.3, max(all_x) + pad + 0.3)
    ax.set_ylim(min(all_y) - pad - 0.3, max(all_y) + pad + 0.3)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(
        "BPMN Process Diagram — Sepsis Cases Event Log\n"
        "(derived from 1,050 traces · 15,214 events · 16 activities)",
        fontsize=13, fontweight="bold", pad=14, color="#1A252F",
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    print(f"Saved: {output_path}")


# ──────────────────────────────────────────────────────────────────────────────
# 4.  BPMN 2.0 XML EXPORT
# ──────────────────────────────────────────────────────────────────────────────
def export_bpmn_xml(output_path: str):
    SCALE = 80   # pixels per grid unit
    TASK_PX_W, TASK_PX_H = int(TASK_W * SCALE), int(TASK_H * SCALE)
    GW_PX = int(GW_R * 2 * SCALE)
    EV_PX = int(EV_R * 2 * SCALE)

    ns = "http://www.omg.org/spec/BPMN/20100524/MODEL"
    di_ns = "http://www.omg.org/spec/BPMN/20100524/DI"
    dc_ns = "http://www.omg.org/spec/DD/20100524/DC"
    ET.register_namespace("", ns)
    ET.register_namespace("bpmndi", di_ns)
    ET.register_namespace("dc", dc_ns)

    root = ET.Element(f"{{{ns}}}definitions",
                      attrib={"id": "sepsis_bpmn",
                              "targetNamespace": "http://sepsis.example.org"})
    proc = ET.SubElement(root, f"{{{ns}}}process",
                         attrib={"id": "proc_sepsis", "isExecutable": "false"})

    bpmndi = ET.SubElement(root, f"{{{di_ns}}}BPMNDiagram", attrib={"id": "diagram1"})
    plane  = ET.SubElement(bpmndi, f"{{{di_ns}}}BPMNPlane",
                            attrib={"id": "plane1", "bpmnElement": "proc_sepsis"})

    def add_shape(elem_id, x, y, w, h):
        shape = ET.SubElement(plane, f"{{{di_ns}}}BPMNShape",
                              attrib={"id": f"shape_{elem_id}",
                                      "bpmnElement": elem_id})
        ET.SubElement(shape, f"{{{dc_ns}}}Bounds",
                      attrib={"x": str(x), "y": str(y),
                              "width": str(w), "height": str(h)})

    flow_idx = 0
    for nid, (gx, gy, ntype, label) in NODES.items():
        px = int(gx * SCALE)
        py = int(-gy * SCALE)   # flip y for screen coords
        clean = label.replace("\n", " ").strip()

        if ntype == "task":
            el = ET.SubElement(proc, f"{{{ns}}}task",
                               attrib={"id": nid, "name": clean})
            add_shape(nid, px - TASK_PX_W // 2, py - TASK_PX_H // 2,
                      TASK_PX_W, TASK_PX_H)
        elif ntype == "xor":
            el = ET.SubElement(proc, f"{{{ns}}}exclusiveGateway",
                               attrib={"id": nid, "name": clean})
            add_shape(nid, px - GW_PX // 2, py - GW_PX // 2, GW_PX, GW_PX)
        elif ntype in ("and_split", "and_join"):
            el = ET.SubElement(proc, f"{{{ns}}}parallelGateway",
                               attrib={"id": nid, "name": clean})
            add_shape(nid, px - GW_PX // 2, py - GW_PX // 2, GW_PX, GW_PX)
        elif ntype == "start":
            el = ET.SubElement(proc, f"{{{ns}}}startEvent",
                               attrib={"id": nid, "name": clean})
            add_shape(nid, px - EV_PX // 2, py - EV_PX // 2, EV_PX, EV_PX)
        elif ntype == "end":
            el = ET.SubElement(proc, f"{{{ns}}}endEvent",
                               attrib={"id": nid, "name": clean})
            add_shape(nid, px - EV_PX // 2, py - EV_PX // 2, EV_PX, EV_PX)

    for src, tgt, lbl, _ in FLOWS:
        flow_idx += 1
        fid = f"flow_{flow_idx}"
        ET.SubElement(proc, f"{{{ns}}}sequenceFlow",
                      attrib={"id": fid, "name": lbl,
                              "sourceRef": src, "targetRef": tgt})
        edge = ET.SubElement(plane, f"{{{di_ns}}}BPMNEdge",
                             attrib={"id": f"edge_{flow_idx}",
                                     "bpmnElement": fid})

    xml_str = ET.tostring(root, encoding="unicode")
    pretty  = md.parseString(xml_str).toprettyxml(indent="  ")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(pretty)
    print(f"Saved: {output_path}")


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    draw_bpmn(os.path.join(OUTPUT_DIR, "bpmn_diagram.png"))
    export_bpmn_xml(os.path.join(OUTPUT_DIR, "bpmn_diagram.bpmn"))
