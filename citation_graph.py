#!/usr/bin/env python3
"""
Citation Network Builder via OpenAlex API + pyvis
Author: patisonkindle-commits
Generates: hybrid_research_graph.html
"""
from __future__ import annotations
import math, time, re
from collections import defaultdict

import requests
import networkx as nx
from pyvis.network import Network

# ─── Config ───────────────────────────────────────────────────────────────────
OPENALEX_BASE = "https://api.openalex.org"
MAX_SEEDS     = 18        # top-N seed articles per query
MAX_REFS      = 20        # references per seed
MAX_CITING    = 3         # citing works per seed
MAX_DETAILS   = 200      # articles to enrich with full metadata
SLEEP         = 0.5      # seconds between API calls
SEED_COLOR    = "#f59e0b" # amber for original seed articles
EDGE_BASE     = (88, 166, 255)

# Community palette (ColorBrewer Set3 + grey fallback)
_COMMUNITY_PALETTE = [
    "#8dd3c7","#ffffb3","#bebada","#fb8072","#80b1d3",
    "#fdb462","#b3de69","#fccde5","#d9d9d9","#bc80bd",
    "#ccebc5","#ffed6f",
]

# Year-bucket thresholds
_YEAR_BUCKETS = [
    ("recent",  2023, "#22d3ee"),  # cyan  – 2023+
    ("current", 2020, "#58a6ff"),  # blue  – 2020–22
    ("mid",     2015, "#a78bfa"),  # purple– 2015–19
    ("classic", 1900, "#94a3b8"),  # grey  – <2015
]
_YEAR_PALETTE = {k: c for k, _, c in _YEAR_BUCKETS}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _year_bucket(year: int | None) -> tuple[str, str]:
    if not year:
        return "unknown", "#6b7280"
    for key, threshold, _ in _YEAR_BUCKETS:
        if year >= threshold:
            return key, _YEAR_PALETTE[key]
    return "classic", _YEAR_PALETTE["classic"]

def _slug(work_id: str) -> str:
    return work_id.rstrip("/").split("/")[-1]

def _fetch_json(path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{OPENALEX_BASE}{path}", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()

def _inverted_index_to_text(inverted_index: dict) -> str:
    """Expand OpenAlex inverted_index dict -> plain text."""
    if not inverted_index:
        return ""
    items = sorted(inverted_index.items(), key=lambda kv: kv[1][0] if kv[1] else 0)
    return " ".join(word for word, _ in items)

def _abstract_snippet(work: dict, max_chars: int = 200) -> str:
    abstract = work.get("abstract_inverted_index")
    if not abstract:
        return ""
    text = _inverted_index_to_text(abstract)
    return text[:max_chars] + ("…" if len(text) > max_chars else "")

def _enrich_work(w: dict) -> dict:
    w = dict(w)
    w["slug"]         = _slug(w.get("id", ""))
    w["authors_short"] = ", ".join(
        a["author"].get("display_name", "?") for a in w.get("authorships", [])[:2]
    ) + (" et al." if len(w.get("authorships", [])) > 2 else "")
    yb, yc            = _year_bucket(w.get("publication_year"))
    w["_year_bucket"] = yb
    w["_year_color"]  = yc
    w["_cites"]       = w.get("cited_by_count", 0)
    w["_snippet"]     = _abstract_snippet(w)
    return w

# ─── OpenAlex API ─────────────────────────────────────────────────────────────

def search_works(q: str, per_page: int = MAX_SEEDS) -> list[dict]:
    r = _fetch_json("/works", {
        "search": q,
        "per-page": per_page,
        "mailto": "patison.kindle@gmail.com",
    })
    return [dict(w, is_seed=True) for w in r.get("results", [])]

def fetch_references(work_id: str) -> list[str]:
    """Return list of referenced work IDs."""
    try:
        # Get the work object to find its referenced_works field
        r = _fetch_json(f"/works/{_slug(work_id)}")
        refs = [w["id"] for w in r.get("referenced_works", []) if w.get("id")]
        return refs
    except Exception:
        return []

def fetch_citing_works(work_id: str, per_page: int = MAX_CITING) -> list[str]:
    """Return list of citing work IDs."""
    try:
        r = _fetch_json("/works", {
            "filter": f"cited_by:{_slug(work_id)}",
            "per-page": per_page,
            "mailto": "patison.kindle@gmail.com",
        })
        return [w["id"] for w in r.get("results", []) if w.get("id")]
    except Exception:
        return []

def fetch_work_details(ids: list[str]) -> dict[str, dict]:
    """Fetch full metadata for a batch of work IDs, return {slug: work}."""
    if not ids:
        return {}
    id_param = "|".join(f"https://openalex.org/{i}" for i in ids)
    try:
        r = _fetch_json("/works", {
            "filter": f"ids.openalex:{id_param}",
            "per-page": min(len(ids), 200),
            "mailto": "patison.kindle@gmail.com",
        })
        return {_slug(w["id"]): w for w in r.get("results", [])}
    except Exception:
        return {}

# ─── Legend / stats injection ───────────────────────────────────────────────

def _build_legend_html() -> str:
    return """
    <style>
    #graph-legend, #graph-stats {
        display: none;
    }
    #graph-legend {
        position: absolute; top: 10px; right: 10px; z-index: 1000;
        background: rgba(13,17,23,0.92); border: 1px solid #30363d;
        border-radius: 8px; padding: 12px 14px; font-family: system-ui;
        font-size: 12px; color: #c9d1d9; min-width: 180px;
    }
    #graph-legend h4 { margin: 0 0 8px; font-size: 13px; color: #f0f6fc; }
    .legend-section { margin-bottom: 8px; }
    .legend-section b { display: block; margin-bottom: 4px; color: #8b949e; }
    .legend-row { display: flex; align-items: center; gap: 6px; margin: 3px 0; }
    .legend-swatch {
        width: 12px; height: 12px; border-radius: 3px; flex-shrink: 0;
    }
    .legend-edge {
        width: 22px; height: 2px;
        background: rgba(88,166,255,1);
        flex-shrink: 0;
    }
    #graph-stats {
        position: absolute; bottom: 10px; left: 10px; z-index: 1000;
        background: rgba(13,17,23,0.85); border: 1px solid #30363d;
        border-radius: 6px; padding: 8px 12px; font-family: monospace;
        font-size: 11px; color: #8b949e; line-height: 1.6;
    }
    #export-btn {
        position: absolute; bottom: 10px; right: 10px; z-index: 1000;
        background: #238636; color: #fff; border: none;
        border-radius: 6px; padding: 6px 12px; font-size: 12px;
        cursor: pointer; font-family: system-ui;
    }
    #export-btn:hover { background: #2ea043; }
    </style>
    <div id="graph-legend">
      <h4>🔗 Citation Network</h4>
      <div class="legend-section">
        <b style="color:#8b949e;font-size:11px">PUBLICATION YEAR</b>
        <div class="legend-row"><div class="legend-swatch" style="background:#22d3ee"></div>2023+ (recent)</div>
        <div class="legend-row"><div class="legend-swatch" style="background:#58a6ff"></div>2020–22 (current)</div>
        <div class="legend-row"><div class="legend-swatch" style="background:#a78bfa"></div>2015–19 (mid)</div>
        <div class="legend-row"><div class="legend-swatch" style="background:#f59e0b"></div>Seed &lt;2015</div>
        <div class="legend-row"><div class="legend-swatch" style="background:#475569"></div>Related &lt;2015</div>
      </div>
      <div class="legend-section">
        <b style="color:#8b949e;font-size:11px">EDGE</b>
        <div class="legend-row">
          <div class="legend-edge" style="opacity:0.9"></div>Highly cited target
        </div>
        <div class="legend-row">
          <div class="legend-edge" style="opacity:0.2"></div>Lowly cited target
        </div>
      </div>
      <div class="legend-section">
        <b style="color:#8b949e;font-size:11px">NODE SIZE</b>
        <div class="legend-row">∝ √(citation count)</div>
      </div>
      <div class="legend-section">
        <b style="color:#8b949e;font-size:11px">COMMUNITY</b>
        <div class="legend-row">Colored by Louvain cluster</div>
      </div>
    </div>
    <div id="graph-stats">
      nodes: 0 &nbsp;|&nbsp; edges: 0 &nbsp;|&nbsp; communities: 0<br>
      avg cites: 0.0 &nbsp;|&nbsp; max cites: 0
    </div>
    <button id="export-btn" onclick="(function(){
        var canvas = document.querySelector('canvas');
        if (!canvas) { alert('Canvas not found.'); return; }
        var url = canvas.toDataURL('image/png');
        var a = document.createElement('a'); a.href = url;
        a.download = 'citation-network.png'; a.click();
    })()">📷 Export PNG</button>
    """

_LEGEND_INJECTED = False

def _inject_legend_stats(html: str, n_nodes: int, n_edges: int,
                         n_comm: int, avg_cites: float, max_cites: int) -> str:
    global _LEGEND_INJECTED
    if _LEGEND_INJECTED:
        return html
    _LEGEND_INJECTED = True
    injected = _build_legend_html()
    injected = (injected
        .replace("nodes: 0", f"nodes: {n_nodes}")
        .replace("edges: 0", f"edges: {n_edges}")
        .replace("communities: 0", f"communities: {n_comm}")
        .replace("avg cites: 0.0", f"avg cites: {avg_cites:.1f}")
        .replace("max cites: 0", f"max cites: {max_cites}"))
    # inject once, after <body> or before </body>
    marker = "<!-- citation-network-injected -->"
    if marker not in html:
        html = html.replace("<body>", "<body>\n" + marker + "\n" + injected, 1)
    return html

# ─── Louvain community ───────────────────────────────────────────────────────

def _louvain_communities(G: nx.DiGraph) -> dict:
    try:
        import community as community_louvain
    except Exception:
        return {}
    ug = G.to_undirected()
    parts = community_louvain.best_partition(ug, resolution=1.0, randomize=True)
    return parts

# ─── Main pipeline ───────────────────────────────────────────────────────────

def main():
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "machine learning"

    # 1. Search seeds
    print(f"[1/5] Searching OpenAlex for {MAX_SEEDS} articles: '{query}'")
    seeds_raw = search_works(query, per_page=MAX_SEEDS)
    seeds = [_enrich_work(w) for w in seeds_raw]
    if not seeds:
        print("ERROR: no results from OpenAlex"); return
    print(f"      Got {len(seeds)} seeds")

    # 2. Collect references + citing works
    print("[2/5] Collecting references & citations (sleep 0.5s per request)")
    ref_map: dict[str, list[str]]  = defaultdict(list)
    cite_map: dict[str, list[str]] = defaultdict(list)

    seed_slugs = [_slug(s["id"]) for s in seeds]
    for i, seed in enumerate(seeds):
        if i > 0 and i % 10 == 0:
            print(f"      fetching refs/cites for seeds {i+1}-{min(i+10, len(seeds))}")
        time.sleep(SLEEP)
        seed_slug = seed_slugs[i]
        # refs: seed_slug -> reference_id (works the seed cites)
        refs  = fetch_references(seed_slug)
        # cites: citer_id -> seed_slug (works that cite the seed)
        cites = fetch_citing_works(seed_slug)
        ref_map[seed_slug]  = refs
        cite_map[seed_slug] = cites
        # normalize to slugs for graph matching
        ref_map[seed_slug]  = [_slug(r) for r in refs]
        cite_map[seed_slug] = [_slug(c) for c in cites]

    # collect IDs needing detail fetch
    all_refs  = set()
    all_cites = set()
    for s in seed_slugs:
        all_refs.update(ref_map[s])
        all_cites.update(cite_map[s])
    related_ids = all_refs | all_cites
    print(f"      {len(related_ids)} related IDs (refs + citing)")

    # batch-fetch details for seeds + related
    all_ids = seed_slugs + list(related_ids)
    all_ids = list(dict.fromkeys(all_ids))[:MAX_DETAILS]
    print(f"      fetching details 1-{min(50,len(all_ids))}/{len(all_ids)}")
    details = fetch_work_details(all_ids[:50])
    for batch_start in range(50, len(all_ids), 50):
        batch_end = min(batch_start + 50, len(all_ids))
        print(f"      fetching details {batch_start+1}-{batch_end}/{len(all_ids)}")
        time.sleep(SLEEP)
        details.update(fetch_work_details(all_ids[batch_start:batch_end]))

    # Build works dict (all nodes)
    works: dict[str, dict] = {}
    for s in seed_slugs:
        # Use full details if available, else fallback to search result
        w = details.get(s)
        if w:
            w["is_seed"] = True
            works[s] = _enrich_work(w)
        else:
            # search result has no full details; still add but mark seed
            seed = next((x for x in seeds if _slug(x["id"]) == s), None)
            if seed:
                works[s] = seed  # already enriched
    for rid in related_ids:
        if rid not in works and rid in details:
            w = details[rid]
            w["is_seed"] = False
            works[rid] = _enrich_work(w)

    # 3. Build graph
    print("[3/5] Building networkx DiGraph")
    G = nx.DiGraph()
    for slug, w in works.items():
        G.add_node(slug, **w)

    for seed_slug in seed_slugs:
        # refs: edge seed -> reference (seed cites ref)
        for ref in ref_map.get(seed_slug, []):
            if ref in works:
                G.add_edge(seed_slug, ref)
        # cites: edge citer -> seed (citer cites seed)
        for cit in cite_map.get(seed_slug, []):
            if cit in works:
                G.add_edge(cit, seed_slug)

    print(f"      nodes={G.number_of_nodes()} edges={G.number_of_edges()}")

    # 4. Louvain communities
    print("[4/5] Running Louvain community detection")
    parts = _louvain_communities(G)
    n_comm = len(set(parts.values())) if parts else 0
    print(f"      {n_comm} communities detected")

    cites = [G.nodes[n]["_cites"] for n in G.nodes]
    avg_cites = sum(cites) / len(cites) if cites else 0
    max_cites = max(cites) if cites else 0
    print(f"  Stats: avg citations={avg_cites:.1f}, max={max_cites}")
    top = sorted(cites, reverse=True)[:3]
    if top:
        top_slug = [n for n in G.nodes if G.nodes[n]["_cites"] == top[0]][0]
        print(f"  Most cited: {works.get(top_slug,{}).get('title','?')[:60]} ({top[0]} cites)")

    # Smart label threshold: top 20% by citation count (show only top 20%)
    if cites:
        cite_threshold = sorted(cites, reverse=True)[int(len(cites) * 0.2)]
    else:
        cite_threshold = 0
    print(f"  Label threshold: >={cite_threshold} cites (top 20%)")

    # 5. pyvis network
    print("[5/5] Rendering pyvis graph -> hybrid_research_graph.html")
    net = Network(height="800px", width="100%", directed=True,
                 bgcolor="#0d1117", font_color="#c9d1d9",
                 notebook=False, select_menu=True)
    # pyvis 0.3.2 caches node colors; we track per-id here for the patch below
    _NODE_COLOR_BY_ID = {}

    wmax = max_cites or 1
    for nid, d in G.nodes(data=True):
        node_cites = d["_cites"]
        size  = 8 + 34 * math.sqrt(node_cites / wmax)
        yb, yc = d["_year_bucket"], d["_year_color"]
        is_seed = d.get("is_seed", False)

        # Coloring: community first, else year color, else seed amber
        if parts and nid in parts:
            cidx = parts[nid] % len(_COMMUNITY_PALETTE)
            node_color = _COMMUNITY_PALETTE[cidx]
        elif not is_seed:
            node_color = yc
        else:
            node_color = SEED_COLOR

        label = (d.get("title") or "")[:45]
        # Smart label: show for highly cited nodes (top 20%)
        cite_threshold = sorted(cites, reverse=True)[int(len(cites) * 0.2)] if cites else 0
        show_label = is_seed or node_cites >= cite_threshold
        if show_label:
            label = f"{d.get('authors_short','?')} ({d.get('publication_year','?')})\n{label}"

        title = (f"<b>{d.get('title','?')}</b><br>"
                 f"Year: {d.get('publication_year','?')} &nbsp;|&nbsp; "
                 f"Cited by: {node_cites:,}<br>"
                 f"DOI: {d.get('doi','N/A')}<br>"
                 f"{d.get('_snippet','')}")
        group = (f"seed_{yb}" if is_seed else f"related_{yb}")
        _NODE_COLOR_BY_ID[nid] = node_color

        net.add_node(nid, label=label if show_label else "",
                     title=title, size=size, group=group)
    # patch pyvis 0.3.2: add_node(color=...,group=...) drops color,
    # so set it directly on the node options dict.
    for opt in net.nodes:
        ncolor = _NODE_COLOR_BY_ID.get(opt["id"])
        if ncolor:
            opt["color"] = {"background": ncolor, "border": ncolor}

    # Edge opacity based on target node's citation count
    for u, v in G.edges():
        target_cites = G.nodes[v]["_cites"] if v in G.nodes else 0
        opacity = 0.08 + 0.65 * (target_cites / wmax)
        width   = 0.4  + 2.5 * (target_cites / wmax)
        net.add_edge(u, v, arrows="to",
                     color=f"rgba(88,166,255,{opacity:.3f})",
                     width=width)

    net.toggle_physics(True)
    net.set_options("""
    {
      "nodes": { "borderWidth": 1, "borderWidthSelected": 2, "font": { "size": 11, "face": "monospace" } },
      "edges": { "smooth": { "type": "curvedCW", "roundness": 0.15 } },
      "interaction": { "hover": true, "navigationButtons": true, "keyboard": true, "tooltipDelay": 100 },
      "physics": { "barnesHut": { "gravitationalConstant": -4000, "centralGravity": 0.3, "springLength": 120, "avoidOverlap": 0.5 } }
    }
    """)

    # Generate + inject legend/stats (idempotent)
    base_html = net.generate_html(notebook=False)
    final_html = _inject_legend_stats(base_html,
                                       G.number_of_nodes(), G.number_of_edges(),
                                       n_comm, avg_cites, max_cites)
    with open("hybrid_research_graph.html", "w", encoding="utf-8") as f:
        f.write(final_html)

    print(f"DONE: hybrid_research_graph.html "
          f"({G.number_of_nodes()} nodes / {G.number_of_edges()} edges / {n_comm} communities)")


if __name__ == "__main__":
    main()
