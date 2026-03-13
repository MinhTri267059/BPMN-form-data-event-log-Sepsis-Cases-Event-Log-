#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import networkx as nx
import pandas as pd
from itertools import islice


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert XES event log into a directed activity graph."
    )
    parser.add_argument(
        "input",
        help="Path to .xes file",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to write outputs (default: output)",
    )
    parser.add_argument(
        "--max-traces",
        type=int,
        default=None,
        help="Limit number of traces to parse (default: all)",
    )
    parser.add_argument(
        "--min-edge-count",
        type=int,
        default=1,
        help="Filter edges with weight below this value (default: 1)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Random seed for layout (default: 7)",
    )
    return parser.parse_args()


def iter_traces(xes_path: str, max_traces: int | None = None):
    context = ET.iterparse(xes_path, events=("start", "end"))
    in_trace = False
    in_event = False
    current_events: list[str] = []
    current_event_name: str | None = None
    trace_count = 0

    for event, elem in context:
        tag = elem.tag

        if event == "start" and tag == "trace":
            in_trace = True
            current_events = []

        elif event == "start" and tag == "event" and in_trace:
            in_event = True
            current_event_name = None

        elif event == "end" and tag == "string" and in_event:
            if elem.attrib.get("key") == "concept:name":
                current_event_name = elem.attrib.get("value")

        elif event == "end" and tag == "event" and in_trace:
            if current_event_name:
                current_events.append(current_event_name)
            in_event = False
            elem.clear()

        elif event == "end" and tag == "trace":
            if current_events:
                yield current_events
            trace_count += 1
            in_trace = False
            elem.clear()

            if max_traces is not None and trace_count >= max_traces:
                break


def build_graph(xes_path: str, max_traces: int | None = None):
    edge_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    activity_counts: Counter[str] = Counter()
    trace_count = 0
    event_count = 0

    for events in iter_traces(xes_path, max_traces=max_traces):
        trace_count += 1
        event_count += len(events)
        activity_counts.update(events)
        for source, target in zip(events, events[1:]):
            edge_counts[(source, target)] += 1

    return trace_count, event_count, activity_counts, edge_counts


def write_edges_csv(path: str, edge_counts: dict[tuple[str, str], int]):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "weight"])
        for (source, target), weight in sorted(edge_counts.items()):
            writer.writerow([source, target, weight])


def write_summary(path: str, trace_count: int, event_count: int, activity_counts: Counter):
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "traces": trace_count,
        "events": event_count,
        "unique_activities": len(activity_counts),
        "top_activities": activity_counts.most_common(10),
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)


def compute_centrality(graph: nx.DiGraph) -> pd.DataFrame:
    """Compute multiple centrality metrics and return a ranked DataFrame."""
    in_deg  = nx.in_degree_centrality(graph)
    out_deg = nx.out_degree_centrality(graph)
    betw    = nx.betweenness_centrality(graph, weight="weight", normalized=True)
    # PageRank: treats each node as a 'web page'; high score = many high-weight predecessors
    pr      = nx.pagerank(graph, weight="weight")

    # raw event count stored on each node
    freq = {n: graph.nodes[n].get("count", 0) for n in graph.nodes}

    rows = []
    for node in graph.nodes:
        rows.append({
            "activity":           node,
            "event_count":        freq[node],
            "in_degree_centrality":  round(in_deg[node],  4),
            "out_degree_centrality": round(out_deg[node], 4),
            "betweenness_centrality": round(betw[node],  4),
            "pagerank":           round(pr[node],         4),
        })

    df = pd.DataFrame(rows)
    df["importance_score"] = (
        0.35 * df["betweenness_centrality"] / (df["betweenness_centrality"].max() or 1) +
        0.35 * df["pagerank"]              / (df["pagerank"].max()              or 1) +
        0.30 * df["event_count"]           / (df["event_count"].max()           or 1)
    )
    df.sort_values("importance_score", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)
    df.index += 1   # rank starts at 1
    return df


def write_centrality(path: str, df: pd.DataFrame):
    df.to_csv(path, index_label="rank", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
#  LOOP DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_loops(graph: nx.DiGraph, max_cycles: int = 200) -> list[dict]:
    """Detect all simple cycles in the graph (capped at max_cycles for performance).

    Returns a list of dicts with keys:
      - loop_path  : " → " joined string of nodes in the cycle
      - length     : number of nodes in the cycle
      - min_edge_weight : minimum edge weight along the cycle (proxy for frequency)
      - nodes      : list of node names (for downstream use)
    """
    results = []
    for cycle in islice(nx.simple_cycles(graph), max_cycles):
        if len(cycle) < 1:
            continue
        # compute min weight around the cycle
        edges_in_cycle = [(cycle[i], cycle[(i + 1) % len(cycle)]) for i in range(len(cycle))]
        weights = [graph.edges[e].get("weight", 1) for e in edges_in_cycle if graph.has_edge(*e)]
        min_w = min(weights) if weights else 0
        results.append({
            "loop_path":       " → ".join(cycle) + " → " + cycle[0],
            "length":          len(cycle),
            "min_edge_weight": min_w,
            "nodes":           cycle,
        })
    # sort by length then by frequency desc
    results.sort(key=lambda r: (r["length"], -r["min_edge_weight"]))
    return results


def write_loops_csv(path: str, loops: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "loop_path", "length", "min_edge_weight"])
        writer.writeheader()
        for rank, loop in enumerate(loops, 1):
            writer.writerow({
                "rank":            rank,
                "loop_path":       loop["loop_path"],
                "length":          loop["length"],
                "min_edge_weight": loop["min_edge_weight"],
            })


def print_loops_table(loops: list[dict]):
    if not loops:
        print("No loops (cycles) detected in the process graph.")
        return
    print()
    print("═" * 90)
    print(f"  LOOP DETECTION  —  {len(loops)} cycle(s) found")
    print("═" * 90)
    print(f"{'Rank':<5} {'Len':<5} {'Freq (min weight)':<20} {'Loop Path'}")
    print("─" * 90)
    for rank, loop in enumerate(loops, 1):
        label = loop["loop_path"]
        if len(label) > 55:
            label = label[:52] + "..."
        print(f"{rank:<5} {loop['length']:<5} {loop['min_edge_weight']:<20} {label}")
    print("═" * 90)


def get_loop_edges(loops: list[dict]) -> set[tuple[str, str]]:
    """Return the set of (source, target) edges that participate in any cycle."""
    edge_set: set[tuple[str, str]] = set()
    for loop in loops:
        cycle = loop["nodes"]
        for i in range(len(cycle)):
            edge_set.add((cycle[i], cycle[(i + 1) % len(cycle)]))
    return edge_set


def draw_graph(graph: nx.DiGraph, centrality_df: pd.DataFrame, output_path: str, seed: int,
              loop_edges: set[tuple[str, str]] | None = None):
    loop_edges = loop_edges or set()
    fig, ax = plt.subplots(figsize=(14, 9))
    pos = nx.spring_layout(graph, seed=seed, k=1.2 / (graph.number_of_nodes() ** 0.5))

    # map importance_score → colour (low=blue, high=red)
    score_map = dict(zip(centrality_df["activity"], centrality_df["importance_score"]))
    scores = [score_map.get(n, 0) for n in graph.nodes]
    cmap = plt.cm.RdYlBu_r
    norm = mcolors.Normalize(vmin=0, vmax=1)
    node_colors = [cmap(norm(s)) for s in scores]

    # node size proportional to event count
    counts = [graph.nodes[n].get("count", 1) for n in graph.nodes]
    max_count = max(counts) or 1
    node_sizes = [400 + 2000 * (c / max_count) for c in counts]

    # split edges into normal vs loop edges (loop edges drawn in red/orange)
    normal_edges = [e for e in graph.edges if e not in loop_edges]
    loop_edge_list = [e for e in graph.edges if e in loop_edges]

    weights_all = [graph.edges[e].get("weight", 1) for e in graph.edges]
    max_w = max(weights_all) or 1

    def edge_width(e):
        return 0.5 + 4.5 * (graph.edges[e].get("weight", 1) / max_w)

    normal_widths = [edge_width(e) for e in normal_edges]
    loop_widths   = [edge_width(e) for e in loop_edge_list]

    nx.draw_networkx_nodes(graph, pos, ax=ax, node_size=node_sizes,
                           node_color=node_colors, alpha=0.92)
    # normal edges
    nx.draw_networkx_edges(graph, pos, ax=ax, edgelist=normal_edges,
                           width=normal_widths,
                           alpha=0.55, edge_color="#444444",
                           arrows=True, arrowsize=15,
                           connectionstyle="arc3,rad=0.05")
    # loop edges — red + higher curvature so they don't overlap
    if loop_edge_list:
        nx.draw_networkx_edges(graph, pos, ax=ax, edgelist=loop_edge_list,
                               width=loop_widths,
                               alpha=0.85, edge_color="#E74C3C",
                               arrows=True, arrowsize=18,
                               connectionstyle="arc3,rad=0.30")
    nx.draw_networkx_labels(graph, pos, ax=ax, font_size=8, font_weight="bold")

    # colour bar legend
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Importance Score", fontsize=10)

    # loop edge legend patch
    import matplotlib.patches as mpatches
    legend_items = []
    if loop_edge_list:
        legend_items.append(mpatches.Patch(color="#E74C3C", label=f"Loop edge ({len(loop_edge_list)} edges)"))
    if legend_items:
        ax.legend(handles=legend_items, loc="lower left", fontsize=8)

    ax.set_title("Sepsis Event Log — Activity Graph\n(size = frequency | colour = importance | red = loop edge)",
                 fontsize=13, fontweight="bold")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)


def main() -> int:
    args = parse_args()
    if not os.path.exists(args.input):
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    os.makedirs(args.output_dir, exist_ok=True)

    trace_count, event_count, activity_counts, edge_counts = build_graph(
        args.input, max_traces=args.max_traces
    )

    filtered_edges = {
        edge: count
        for edge, count in edge_counts.items()
        if count >= args.min_edge_count
    }

    graph = nx.DiGraph()
    for activity, count in activity_counts.items():
        graph.add_node(activity, count=count)
    for (source, target), weight in filtered_edges.items():
        graph.add_edge(source, target, weight=weight)

    edges_csv        = os.path.join(args.output_dir, "edges.csv")
    graphml_path     = os.path.join(args.output_dir, "graph.graphml")
    png_path         = os.path.join(args.output_dir, "graph.png")
    summary_path     = os.path.join(args.output_dir, "summary.json")
    centrality_csv   = os.path.join(args.output_dir, "centrality.csv")
    loops_csv        = os.path.join(args.output_dir, "loops.csv")

    centrality_df = compute_centrality(graph)

    # ── Loop detection ──
    loops = detect_loops(graph)
    loop_edges = get_loop_edges(loops)
    write_loops_csv(loops_csv, loops)
    print_loops_table(loops)

    write_edges_csv(edges_csv, filtered_edges)
    nx.write_graphml(graph, graphml_path)
    write_summary(summary_path, trace_count, event_count, activity_counts)
    write_centrality(centrality_csv, centrality_df)
    draw_graph(graph, centrality_df, png_path, seed=args.seed, loop_edges=loop_edges)

    print("Graph generated:")
    print(f"  Traces:     {trace_count}")
    print(f"  Events:     {event_count}")
    print(f"  Activities: {len(activity_counts)}")
    print(f"  Edges:      {len(filtered_edges)}")
    print()
    print("─" * 80)
    print(f"{'Rank':<5} {'Activity':<22} {'Events':>8} {'Betweenness':>13} {'PageRank':>10} {'Score':>7}")
    print("─" * 80)
    for rank, row in centrality_df.iterrows():
        print(f"{rank:<5} {row['activity']:<22} {int(row['event_count']):>8} "
              f"{row['betweenness_centrality']:>13.4f} {row['pagerank']:>10.4f} "
              f"{row['importance_score']:>7.4f}")
    print("─" * 80)
    print(f"  Loops:      {len(loops)} cycle(s) detected")
    print(f"  Outputs: {args.output_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
