# XMP metadata tools

Two small, dependency-free Python 3 scripts for working with Adobe XMP sidecar
files (`.xmp`) that sit next to raw photos (e.g. `OV7A2905.CR3` +
`OV7A2905.xmp`). Runs on macOS and Windows with any Python 3.8+ — stdlib only,
nothing to `pip install`. `.acr` files (Adobe's binary Camera Raw cache) are
not handled by these tools.

- **`xmpread.py`** — view an image's metadata as a tree.
- **`xmpwrite.py`** — set Title, Description, Tags, Rating, and Pick.
- **`xmp_common.py`** — shared parsing/formatting code used by both; not run directly.

## Reading metadata

```
python3 xmpread.py PATH [PATH ...] [--json]
```

`PATH` can be an image file (its sibling `.xmp` is found automatically), a
`.xmp` file directly, or a directory (every `*.xmp` in it is shown).

```
$ python3 xmpread.py OV7A2905/OV7A2905.CR3
OV7A2905/OV7A2905.xmp
├─ File
│  ├─ Sidecar: OV7A2905/OV7A2905.xmp
│  ├─ Source Image: OV7A2905.CR3
│  └─ Dimensions: 6000×4000
├─ Camera & Lens
│  ├─ Make: Canon
│  ├─ Model: Canon EOS R6m2
│  ...
├─ Exposure
│  ├─ Shutter: 1/200 s
│  ├─ Aperture: f/7.1
│  ...
├─ Rating & Tags
│  ├─ Rating: — (not rated)
│  ├─ Title: —
│  ├─ Keywords: —
│  ...
└─ Develop (Camera Raw)
   ├─ Version: 18.5.1 (Process 15.4)
   ...
```

Add `--json` to get the same data (raw attributes plus parsed title,
description, creator, tags, etc.) as JSON for scripting.

## Updating metadata

```
python3 xmpwrite.py PATH [options]
```

`PATH` is a single image or `.xmp` file (not a directory). At least one of:

| Flag | Sets | Notes |
|---|---|---|
| `--title TEXT` | `dc:title` | short string |
| `--description TEXT` | `dc:description` | a longer paragraph |
| `--tags "a,b,c"` | `dc:subject` | **replaces** the keyword/category list |
| `--add-tags "a,b,c"` | `dc:subject` | **merges** into the existing list, no duplicates |
| `--rating 0-5` | `xmp:Rating` | star rating |
| `--pick -1\|0\|1` | `xmpDM:pick` | -1 reject, 0 unflagged, 1 pick |

Other options: `--out FILE` (write elsewhere, leave the original untouched),
`--no-backup` (skip the `.bak` safety copy), `--dry-run` (print the intended
diff, write nothing).

```
python3 xmpwrite.py OV7A2905/OV7A2905.CR3 \
  --title "Lake Reflection" \
  --description "A calm lake at sunrise with mirrored clouds." \
  --tags "Landscape,Water" \
  --rating 4 --pick 1
```

### How writes are done safely

- Before overwriting, a backup is written next to the original
  (`OV7A2905.xmp.bak`; timestamped if a `.bak` already exists). Use
  `--no-backup` to skip this.
- Edits are applied as **surgical text patches**, not a full XML rewrite — the
  file's attribute order, indentation, and every Camera Raw develop setting
  (`crs:*`) are left untouched; only the fields you asked to change (plus
  `xmp:MetadataDate`, refreshed like Adobe apps do) are modified.
- `--dry-run` shows a unified diff of exactly what would change before you
  commit to it.

After writing, run `xmpread.py` on the same file to confirm the change.

## Semantic search over culling reports (`search/`)

The `culling_helper_*.py` scripts produce Markdown reports (see `testresults/`)
that list kept images with a Category, Title, Description, and Editing
Suggestions. When you have hundreds of these, `search/` lets you find the right
image by meaning — either a ranked search or a natural-language chat — all
locally via Ollama.

Unlike the core tools above, this feature has third-party dependencies and
needs a running [Ollama](https://ollama.com). It is isolated under `search/`
and the vector store it builds (`chroma_db/`) is gitignored.

### Architecture

```
        ┌──────────────────────┐        ┌───────────────────────────────┐
        │  testresults/*.md     │        │  Ollama  (localhost:11434)    │
        │  culling reports      │        │  ┌─────────────┐ ┌──────────┐ │
        └──────────┬───────────┘        │  │nomic-embed- │ │ llama3.1 │ │
                   │                     │  │text (embed) │ │  (chat)  │ │
    parse_report() │ (reused, no         │  └──────▲──────┘ └────▲─────┘ │
    resolve_reports│  new markdown        └─────────┼─────────────┼───────┘
    _clean_field   │  parsing)                      │ embeddings   │ RAG answers
                   ▼                                │              │
        ┌──────────────────────┐  one doc/kept img │              │
        │  build_index.py       ├───────────────────┘              │
        │  (ingest / indexing)  │                                  │
        └──────────┬───────────┘                                  │
                   │ add_documents(ids=report:filename)            │
                   ▼                                               │
        ┌──────────────────────┐                                  │
        │  chroma_db/  (Chroma  │◄───── similarity_search ─────────┤
        │  persistent vectors)  │       (+ category filter)        │
        └──────────┬───────────┘                                  │
                   │  vectorstore + helpers                        │
                   ▼                                               │
        ┌──────────────────────┐                                  │
        │  search_core.py       │  semantic_search / list_          │
        │  (shared retrieval)   │  categories / answer_question ────┘
        └──────────┬───────────┘
                   │ imported by
                   ▼
        ┌──────────────────────┐
        │  app.py (Streamlit)   │   🔎 Search tab  →  ranked cards
        │  localhost:8501       │   💬 Ask tab     →  RAG chat + cited sources
        └──────────────────────┘
```

### Modules

| File | Role |
|---|---|
| `search/build_index.py` | Ingestion. Reuses `apply_culling_report.parse_report` (and `resolve_reports`, `_clean_field`) to turn each **kept** image into one Chroma document — no separate markdown parsing. Stable id `report:filename` so re-runs upsert. CLI: `--reports`, `--db`, `--rebuild`. |
| `search/search_core.py` | Shared, UI-free retrieval layer: `get_vectorstore`, `semantic_search` (with optional category filter), `list_categories`, and `answer_question` (RAG chain over `ChatOllama`). Model/path constants overridable via env vars. Imported by both `build_index.py` and `app.py` so config stays in one place. |
| `search/app.py` | Streamlit UI. **Search** tab runs `semantic_search`; **Ask** tab runs `answer_question` and lists the source images that grounded the answer. Sidebar: category filter, top-k, DB path, chat model. |
| `requirements-search.txt` | The feature's pip dependencies (`langchain`, `langchain-chroma`, `langchain-ollama`, `chromadb`, `streamlit`). Kept separate so the core XMP tools remain stdlib-only. |

Reused unchanged from the repo root: `apply_culling_report.py` (the report
parser) and `xmp_common.read_text`.

### One-time setup

```
pip install -r requirements-search.txt
ollama pull nomic-embed-text     # embeddings
ollama pull llama3.1             # chat / RAG model (default, heaviest)
```

`llama3.1` is the default but is heavy. For a lighter machine, pull one or both
of these instead (you only need the chat models you plan to use):

```
ollama pull qwen3:1.7b           # small, fast
ollama pull phi4-mini            # small, good quality for its size
ollama list                      # confirm what's installed
```

Ollama must be running at `http://localhost:11434` (the same instance the
culling helpers use).

### 1. Build the index

Each **kept** image becomes one searchable record (filename + category + title
+ description + editing suggestions). The existing report parser is reused, so
what gets indexed is exactly what the helpers wrote.

```
python3 search/build_index.py --reports testresults --rebuild
```

`--reports` takes a report file or a directory (repeatable); `--rebuild` clears
the collection first. Re-running without `--rebuild` upserts by a stable id
(`report:filename`), so growing the report set never creates duplicates.

### 2. Search

```
streamlit run search/app.py        # opens http://localhost:8501
```

- **Search tab** — type a query (e.g. "serene fjord with towering cliffs") and
  get ranked cards showing filename, description, and editing suggestions.
- **Ask tab** — ask a question (e.g. "which nature images need more contrast?")
  and the local LLM answers over the retrieved images, citing them by filename.
- The sidebar has a category filter, a top-k slider, and lets you point at a
  different Chroma path, and a **Chat model** dropdown (`llama3.1`,
  `qwen3:1.7b`, `phi4-mini`). The choice applies to the Ask tab and can be
  changed at any time; the model must already be pulled in Ollama.

#### Choosing the chat model

- **Per session:** use the sidebar dropdown.
- **Default model:** set `XMP_SEARCH_CHAT_MODEL`, e.g.
  `XMP_SEARCH_CHAT_MODEL=phi4-mini streamlit run search/app.py`.
- **Dropdown contents:** set `XMP_SEARCH_CHAT_MODELS` to a comma-separated list
  of Ollama tags, e.g. `XMP_SEARCH_CHAT_MODELS=phi4-mini,qwen3:4b,llama3.1`.
  The default model is always included.
- Reasoning models such as qwen3 may emit `<think>…</think>` text; it is
  stripped from answers automatically.

The embedding model and DB path can be overridden with `XMP_SEARCH_EMBED_MODEL`
and `XMP_SEARCH_DB` (see `search/search_core.py`). Changing the embedding model
requires rebuilding the index with `--rebuild`. Note the culling helper's `qwen2.5vl:7b` is a vision
model; RAG chat uses a text model (`llama3.1` by default).
