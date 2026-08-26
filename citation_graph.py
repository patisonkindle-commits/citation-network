#!/usr/bin/env python3
"""
Citation Network Graph builder.

Pipeline:
  1. Search OpenAlex for 15-20 seed articles matching an English query.
  2. Pull citations (who cites them) & references (what they cite) via DOI/OpenAlex ID.
  3. Build a networkx DiGraph (node = article sized by citation count, edge = citation).
  4. Render an interactive pyvis graph -> hybrid_research_graph.html
     Hover a node to see title, publication year and source venue.

Usage:
    python3 citation_graph.py "your english search query"

Requires: pip install requests networkx pyvis
"""
import math
import sys
import time

import requests

import networkx as nx
from pyvis.network import Network

# --- config ---------------------------------------------------------------
MAILTO = "patison.kindle@gmail.com"   # polite pool = better rate limits
API = "https://api.openalex.org/works"
N_SEEDS = 18              # seed articles (requirement: 15-20)
MAX_REFS_PER_SEED = 20    # references kept per seed (first 20 listed)
MAX_CITING_BATCH = 60     # citing works kept per batch of seeds
DETAIL_BATCH = 50         # works fetched per detail call
SLEEP = 0.5               # time.sleep between API hits (free tier politeness)


def get(url, params):
    """GET with polite delay; returns parsed JSON."""
    time.sleep(SLEEP)
    r = requests.get(url, params=params, timeout=60,
                     headers={"User-Agent": f"citation-graph-demo (mailto:{MAILTO})"})
    r.raise_for_status()
    return r.json()


def short_id(openalex_url):
    """'https://openalex.org/W2100837269' -> 'W2100837269'"""
    return openalex_url.rsplit("/", 1)[-1]


def search_seeds(query):
    """Step 1: find seed articles matching the query."""
    print(f"[1/4] Searching OpenAlex for {N_SEEDS} articles: {query!r}")
    data = get(API, {"search": query, "per-page": N_SEEDS, "mailto": MAILTO})
    return data.get("results", [])


def fetch_citing(seed_ids):
    """Step 2a: works that cite any of the given seed IDs (batched OR filter)."""
    citing = {}
    for i in range(0, len(seed_ids), 10):
        chunk = "|".join(seed_ids[i:i + 10])
        print(f"      fetching citing works for seeds {i + 1}-{min(i + 10, len(seed_ids))}")
        data = get(API, {"filter": f"cites:{chunk}", "per-page": MAX_CITING_BATCH,
                         "sort": "cited_by_count:desc", "mailto": MAILTO})
        for w in data.get("results", []):
            citing[w["id"]] = w
    return citing


def fetch_details(work_urls):
    """Step 2b: metadata (title/year/source/citations) for referenced-work URLs."""
    meta = {}
    ids = [short_id(u) for u in work_urls]
    for i in range(0, len(ids), DETAIL_BATCH):
        chunk = "|".join(ids[i:i + DETAIL_BATCH])
        print(f"      fetching details {i + 1}-{min(i + DETAIL_BATCH, len(ids))}/{len(ids)}")
        data = get(API, {"filter": f"ids.openalex:{chunk}", "per-page": DETAIL_BATCH,
                         "mailto": MAILTO})
        for w in data.get("results", []):
            meta[w["id"]] = w
    return meta


def node_attrs(w, is_seed):
    """Map an OpenAlex work record -> graph node attributes."""
    src = ""
    loc = w.get("primary_location") or {}
    if loc and loc.get("source"):
        src = loc["source"].get("display_name") or ""
    year = w.get("publication_year") or "?"
    cites = w.get("cited_by_count", 0)
    title = w.get("display_name") or "(untitled)"
    hover = (
        f"<div style='max-width:320px'>"
        f"<b>{title}</b><br>"
        f"Year: {year}<br>"
        f"Source: {src or 'n/a'}<br>"
        f"Citations: {cites}"
        f"</div>"
    )
    return {"label": title[:38] + ("…" if len(title) > 38 else ""),
            "title": hover, "group": "seed" if is_seed else "related",
            "_year": year, "_src": src, "_cites": cites}


def main():
    query = " ".join(sys.argv[1:]) or "large language models"
    G = nx.DiGraph()
    records = {}   # openalex url -> work record
    seed_set = {}  # short id -> openalex url

    # Step 1: seeds ---------------------------------------------------------
    seeds = search_seeds(query)
    if len(seeds) < N_SEEDS:
        print(f"      note: only {len(seeds)} results found")
    for w in seeds:
        records[w["id"]] = w
        seed_set[short_id(w["id"])] = w["id"]
    seed_urls = [w["id"] for w in seeds]

    # Step 2: references + citing works -------------------------------------
    print("[2/4] Collecting references & citations (sleep 0.5s per request)")
    ref_urls = []
    for w in seeds:
        ref_urls.extend((w.get("referenced_works") or [])[:MAX_REFS_PER_SEED])

    citing_map = fetch_citing([short_id(u) for u in seed_urls])
    records.update(citing_map)

    ref_meta = fetch_details(ref_urls) if ref_urls else {}
    records.update(ref_meta)

    # Step 3: build networkx DiGraph ----------------------------------------
    print("[3/4] Building networkx DiGraph")
    def add(w, is_seed=False):
        wid = short_id(w["id"])
        if wid not in G:
            G.add_node(wid, **node_attrs(w, is_seed))
        return wid

    for w in seeds:
        add(w, is_seed=True)
    for w in citing_map.values():
        add(w)
    for w in ref_meta.values():
        add(w)

    # Edges from real referenced_works lists:
    #   - seed -> its references
    #   - citing work -> whichever seeds it actually references
    for url, w in records.items():
        src_id = short_id(url)
        if not G.has_node(src_id):
            continue
        for ref in (w.get("referenced_works") or []):
            rid = short_id(ref)
            if G.has_node(rid):
                G.add_edge(src_id, rid)

    print(f"      nodes={G.number_of_nodes()} edges={G.number_of_edges()}")

    # Step 4: render with pyvis ---------------------------------------------
    print("[4/4] Rendering pyvis graph -> hybrid_research_graph.html")
    net = Network(height="92vh", width="100%", bgcolor="#0d1117",
                  font_color="#e6edf3", directed=True, select_menu=False,
                  filter_menu=False, cdn_resources="remote")
    net.barnes_hut(gravity=-6000, spring_length=140)

    max_c = max((d["_cites"] for _, d in G.nodes(data=True)), default=1) or 1
    palette = {"seed": "#f59e0b", "related": "#58a6ff"}
    for nid, d in G.nodes(data=True):
        size = 8 + 34 * math.sqrt(d["_cites"] / max_c)
        net.add_node(nid, label=d["label"], title=d["title"], size=size,
                     color=palette[d["group"]])
    for u, v in G.edges():
        net.add_edge(u, v, arrows="to", color="#30363d")

    net.write_html("hybrid_research_graph.html", notebook=False)
    print(f"DONE: hybrid_research_graph.html "
          f"({G.number_of_nodes()} nodes / {G.number_of_edges()} edges)")


if __name__ == "__main__":
    main()
