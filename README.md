# Citation Network Graph

Interactive citation network explorer built on the [OpenAlex API](https://openalex.org).

**Live app:** https://patisonkindle-commits.github.io/citation-network/

- `index.html` — web app (pure client-side JS + vis-network). Type an English topic → fetches 15–20 seed articles, expands citations & references, renders an interactive graph. Node size = citation count, arrows = citation direction, hover = title/year/source. Works anywhere, no backend.
- `citation_graph.py` — Python pipeline (requests + networkx + pyvis): `python3 citation_graph.py "your topic"` → writes `hybrid_research_graph.html`.
