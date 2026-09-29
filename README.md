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

### One-time setup

```
pip install -r requirements-search.txt
ollama pull nomic-embed-text     # embeddings
ollama pull llama3.1             # chat / RAG model
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
  different Chroma path or chat model.

Models and paths can be overridden with the `XMP_SEARCH_EMBED_MODEL`,
`XMP_SEARCH_CHAT_MODEL`, and `XMP_SEARCH_DB` environment variables (see
`search/search_core.py`). Note the culling helper's `qwen2.5vl:7b` is a vision
model; RAG chat uses a text model (`llama3.1` by default).
