"""
==============================================================================
  VECTOR DATABASE VISUALISER
  Plots both ChromaDB collections in 2D using UMAP dimensionality reduction
==============================================================================

  HOW TO RUN:
      py -3.11 visualise_db.py

  WHAT IT DOES:
  1. Pulls all vectors from buffett_philosophy and company_financials
  2. Compresses 384 dimensions down to 2 using UMAP
  3. Plots everything in an interactive Plotly chart
  4. Colour coded by collection and document source
  5. Hover over any point to see the actual chunk text

==============================================================================
"""

import chromadb
import numpy as np
import umap
import plotly.graph_objects as go

# ==============================================================================
# CONFIGURATION
# ==============================================================================

CHROMA_PATH = "./chroma_db"

# ==============================================================================
# STEP 1 — PULL ALL VECTORS FROM BOTH COLLECTIONS
# ==============================================================================

print("=" * 60)
print("  Vector Database Visualiser")
print("=" * 60)

client = chromadb.PersistentClient(path=CHROMA_PATH)

def pull_collection(name):
    print(f"\n  Pulling '{name}'...")
    col = client.get_collection(name)
    count = col.count()
    print(f"  Total chunks: {count:,}")

    # Pull everything — embeddings, documents, metadata
    results = col.get(
        include=["embeddings", "documents", "metadatas"],
        limit=count
    )

    embeddings = np.array(results["embeddings"])
    documents  = results["documents"]
    metadatas  = results["metadatas"]

    print(f"  Embeddings shape: {embeddings.shape}")
    return embeddings, documents, metadatas

buffett_emb, buffett_docs, buffett_meta = pull_collection("buffett_philosophy")
financial_emb, financial_docs, financial_meta = pull_collection("company_financials")

# ==============================================================================
# STEP 2 — COMBINE AND REDUCE TO 2D WITH UMAP
# ==============================================================================

print("\n  Combining collections...")
all_embeddings = np.vstack([buffett_emb, financial_emb])
print(f"  Total vectors: {len(all_embeddings):,}")

print("\n  Running UMAP dimensionality reduction...")
print("  (This takes 1-3 minutes for ~4,000 vectors)")

reducer = umap.UMAP(
    n_components=2,
    n_neighbors=15,
    min_dist=0.1,
    metric="cosine",
    random_state=42
)

coords_2d = reducer.fit_transform(all_embeddings)
print(f"  Done. Output shape: {coords_2d.shape}")

# Split coordinates back into their collections
n_buffett   = len(buffett_emb)
buffett_coords   = coords_2d[:n_buffett]
financial_coords = coords_2d[n_buffett:]

# ==============================================================================
# STEP 3 — BUILD PLOTLY CHART
# ==============================================================================

print("\n  Building interactive chart...")

fig = go.Figure()

# ── BUFFETT PHILOSOPHY — coloured by decade ──
decade_colours = {
    "1970s": "#C4B5FD",  # light purple
    "1980s": "#818CF8",  # medium purple
    "1990s": "#6366F1",  # purple
    "2000s": "#4338CA",  # dark purple
    "2010s": "#3730A3",  # darker purple
    "2020s": "#1E1B4B",  # darkest purple
}

def get_decade(meta):
    year = meta.get("year", 0)
    if isinstance(year, int):
        if year < 1980: return "1970s"
        elif year < 1990: return "1980s"
        elif year < 2000: return "1990s"
        elif year < 2010: return "2000s"
        elif year < 2020: return "2010s"
        else: return "2020s"
    return "Unknown"

# Group Buffett chunks by decade
decade_groups = {}
for i, (x, y) in enumerate(buffett_coords):
    decade = get_decade(buffett_meta[i])
    if decade not in decade_groups:
        decade_groups[decade] = {"x": [], "y": [], "text": []}
    decade_groups[decade]["x"].append(x)
    decade_groups[decade]["y"].append(y)
    # Truncate hover text to 200 chars
    preview = buffett_docs[i][:200].replace("<", "&lt;").replace(">", "&gt;")
    source  = buffett_meta[i].get("source", "Unknown")
    decade_groups[decade]["text"].append(f"<b>{source}</b><br><br>{preview}...")

for decade, data in sorted(decade_groups.items()):
    colour = decade_colours.get(decade, "#A5B4FC")
    fig.add_trace(go.Scatter(
        x=data["x"],
        y=data["y"],
        mode="markers",
        name=f"Buffett — {decade}",
        marker=dict(
            color=colour,
            size=5,
            opacity=0.75,
            symbol="circle"
        ),
        text=data["text"],
        hovertemplate="%{text}<extra></extra>",
        legendgroup="buffett"
    ))

# ── COMPANY FINANCIALS — coloured by document type ──
doc_type_colours = {
    "Earnings Call Transcript": "#F97316",  # orange
    "Quarterly Report":         "#EF4444",  # red
    "Annual Report":            "#DC2626",  # dark red
}

doc_groups = {}
for i, (x, y) in enumerate(financial_coords):
    doc_type = financial_meta[i].get("doc_type", "Unknown")
    quarter  = financial_meta[i].get("quarter", "")
    year     = financial_meta[i].get("year", "")
    label    = f"{doc_type}"

    if label not in doc_groups:
        doc_groups[label] = {"x": [], "y": [], "text": [], "doc_type": doc_type}
    doc_groups[label]["x"].append(x)
    doc_groups[label]["y"].append(y)

    preview = financial_docs[i][:200].replace("<", "&lt;").replace(">", "&gt;")
    source  = financial_meta[i].get("source", "Unknown")
    doc_groups[label]["text"].append(f"<b>{source}</b><br><br>{preview}...")

for label, data in sorted(doc_groups.items()):
    colour = doc_type_colours.get(data["doc_type"], "#FB923C")
    fig.add_trace(go.Scatter(
        x=data["x"],
        y=data["y"],
        mode="markers",
        name=f"Datadog — {label}",
        marker=dict(
            color=colour,
            size=6,
            opacity=0.85,
            symbol="diamond"
        ),
        text=data["text"],
        hovertemplate="%{text}<extra></extra>",
        legendgroup="financial"
    ))

# ==============================================================================
# STEP 4 — LAYOUT AND EXPORT
# ==============================================================================

fig.update_layout(
    title=dict(
        text="Vector Space — Buffett Philosophy vs Datadog Financials",
        font=dict(size=18),
        x=0.5
    ),
    xaxis=dict(title="UMAP Dimension 1", showgrid=False, zeroline=False),
    yaxis=dict(title="UMAP Dimension 2", showgrid=False, zeroline=False),
    plot_bgcolor="#0F172A",
    paper_bgcolor="#0F172A",
    font=dict(color="#E2E8F0"),
    legend=dict(
        bgcolor="#1E293B",
        bordercolor="#334155",
        borderwidth=1,
        font=dict(size=11)
    ),
    width=1200,
    height=800,
    hovermode="closest"
)

# Save as interactive HTML
output_file = "vector_visualisation.html"
fig.write_html(output_file)

print()
print("=" * 60)
print(f"  VISUALISATION COMPLETE")
print(f"  Saved to: {output_file}")
print(f"  Open it in your browser — every dot is hoverable")
print()
print(f"  Purple dots  = Buffett philosophy (by decade)")
print(f"  Orange/Red dots = Datadog financials (by document type)")
print()
print(f"  What to look for:")
print(f"  - Purple and orange should form separate clusters")
print(f"  - Within purple, decades may cluster by era of thinking")
print(f"  - Within orange, transcripts vs reports may separate")
print("=" * 60)