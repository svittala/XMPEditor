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
