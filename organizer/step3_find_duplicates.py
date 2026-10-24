"""
Step 3 - find duplicates inside each DEST\\yyyy-mm-dd\\ folder (never across folders).

    python step3_find_duplicates.py DEST [--folder 2023-05-14] [--no-ollama]

Three passes, all read-only:
  exact   byte-identical files (SHA-256)
  visual  same picture re-saved / resized / re-exported (perceptual dHash)
  burst   near-identical shots taken within a few seconds; an Ollama vision
          model decides whether they show the same moment and picks the best

Writes DEST\\_reports\\duplicates_<ts>.csv (edit the `role` column by hand if you
disagree) and a .html contact sheet. Nothing is moved - step 4 does that.
Photos with an XMP rating >= 1 or Pick flag are never marked as duplicates.
"""

import argparse
import base64
import html
import io
import json
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

DEFAULT_MODEL = "qwen2.5vl:7b"
OLLAMA_URL = "http://localhost:11434"
HASH_SIZE = 16  # 16x16 -> 256-bit dHash

BURST_SCHEMA = {
    "type": "object",
    "properties": {
        "same_moment": {"type": "boolean"},
        "best_index": {"type": "integer"},
        "near_duplicate_indexes": {"type": "array", "items": {"type": "integer"}},
        "reason": {"type": "string"},
    },
    "required": ["same_moment", "best_index", "near_duplicate_indexes", "reason"],
}

BURST_PROMPT = (
    "You are shown {n} photos taken within a few seconds of each other, in order "
    "(zero-indexed: first image is 0). Decide whether they show the SAME moment, "
    "i.e. the same subjects in the same pose, expression and framing, differing only "
    "in tiny ways (slight blur, tiny shift). If anyone's pose, expression, eyes "
    "(open/closed), position or the framing differs meaningfully, they are NOT the "
    "same moment. If they are the same moment, pick the best one (sharpest, eyes open, "
    "best exposure) as best_index and list ALL other images that are redundant "
    "in near_duplicate_indexes. If not the same moment, set same_moment=false and "
    "near_duplicate_indexes=[]. Keep the reason to one sentence.")


# ---------------------------------------------------------------------------
# Small JSON cache so reruns are cheap
# ---------------------------------------------------------------------------

class Cache:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")


def file_key(path):
    st = Path(path).stat()
    return f"{Path(path).name}|{st.st_size}|{st.st_mtime_ns}"


# ---------------------------------------------------------------------------
# Image helpers (Pillow only; imported lazily so --help works without it)
# ---------------------------------------------------------------------------

def _pil():
    try:
        from PIL import Image, ImageFilter, ImageOps, ImageStat
    except ImportError:
        print("ERROR: Pillow is required: pip install -r organizer\\requirements-organizer.txt")
        sys.exit(2)
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return Image, ImageFilter, ImageOps, ImageStat


def load_preview(path, kind, exiftool, max_side=1024):
    """Open a displayable RGB image of the file; RAW files via their embedded JPEG."""
    Image, _f, ImageOps, _s = _pil()
    try:
        if kind == "raw":
            if not exiftool:
                return None
            import subprocess
            data = b""
            for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
                proc = subprocess.run([exiftool, "-b", tag, str(path)],
                                      capture_output=True, timeout=120)
                if proc.stdout:
                    data = proc.stdout
                    break
            if not data:
                return None
            img = Image.open(io.BytesIO(data))
        else:
            img = Image.open(c.lp(path))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        return img
    except Exception as ex:
        print(f"  warning: cannot read image {Path(path).name}: {ex}")
        return None


def dhash(img):
    Image, _f, _o, _s = _pil()
    g = img.convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS)
    px = g.tobytes()
    bits = 0
    for row in range(HASH_SIZE):
        base = row * (HASH_SIZE + 1)
        for col in range(HASH_SIZE):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def hamming(a, b):
    return (a ^ b).bit_count()


def sharpness(img):
    _I, ImageFilter, _o, ImageStat = _pil()
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0]


def jpeg_bytes(img, max_side=768):
    im = img.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Item:
    def __init__(self, path):
        self.path = Path(path)
        self.kind = c.classify(path)
        self.size = self.path.stat().st_size
        self.sha = ""
        self.hash = None
        self.w = self.h = 0
        self.sharp = 0.0
        self.when = None
        self.xmp = None
        self.protected = False
        self.img = None  # transient preview, dropped after use

    @property
    def stem(self):
        return self.path.stem.lower()

    @property
    def pixels(self):
        return self.w * self.h


def ensure_sha(item):
    if not item.sha:
        item.sha = c.sha256_file(item.path)
    return item.sha


def choose_keeper(items):
    """Best candidate first: protected > RAW > pixels > file size > has xmp > short name."""
    def score(i):
        return (i.protected, i.kind == "raw", i.pixels, i.size, bool(i.xmp),
                -len(i.path.name))
    return sorted(items, key=score, reverse=True)


class UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)

    def groups(self):
        out = defaultdict(list)
        for x in list(self.p):
            out[self.find(x)].append(x)
        return [g for g in out.values() if len(g) > 1]


# ---------------------------------------------------------------------------
# Detection passes
# ---------------------------------------------------------------------------

def find_exact(items):
    by_size = defaultdict(list)
    for i in items:
        by_size[i.size].append(i)
    groups = []
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        by_sha = defaultdict(list)
        for i in same_size:
            by_sha[ensure_sha(i)].append(i)
        groups += [g for g in by_sha.values() if len(g) > 1]
    return groups


def find_visual(images, threshold, collapse_raw):
    uf = UnionFind()
    for a_idx in range(len(images)):
        for b_idx in range(a_idx + 1, len(images)):
            a, b = images[a_idx], images[b_idx]
            if (a.kind == "raw") != (b.kind == "raw") and not collapse_raw:
                continue  # RAW vs JPG: camera pairs / edits are not duplicates
            if hamming(a.hash, b.hash) <= threshold:
                uf.union(a, b)
    return uf.groups()


def burst_candidates(images, burst_seconds, burst_distance, max_group=6):
    timed = sorted((i for i in images if i.when), key=lambda i: i.when)
    groups, cur = [], []
    for i in timed:
        if cur and (i.when - cur[-1].when).total_seconds() <= burst_seconds \
                and hamming(i.hash, cur[-1].hash) <= burst_distance:
            cur.append(i)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur = [i]
    if len(cur) > 1:
        groups.append(cur)
    out = []
    for g in groups:
        for k in range(0, len(g), max_group):
            part = g[k:k + max_group]
            if len(part) > 1:
                out.append(part)
    return out


def ollama_ready(model, base=OLLAMA_URL):
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    except Exception as ex:
        print(f"  Ollama not reachable at {base} ({ex}); skipping burst pass.")
        return False
    if not any(n == model or n.startswith(model) for n in names):
        print(f"  Warning: model '{model}' not found. Run: ollama pull {model}")
        return False
    return True


def ask_ollama(group, model, cache, base=OLLAMA_URL):
    """Returns parsed verdict dict or None. Cached by the group's file hashes."""
    key = "ollama|" + model + "|" + ",".join(ensure_sha(i)[:16] for i in group)
    hit = cache.get(key)
    if hit is not None:
        return hit
    images = [base64.b64encode(jpeg_bytes(i.img)).decode("ascii") for i in group]
    payload = {
        "model": model, "stream": False, "format": BURST_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 8192},
        "messages": [{"role": "user", "content": BURST_PROMPT.format(n=len(group)),
                      "images": images}],
    }
    req = urllib.request.Request(base + "/api/chat", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            body = json.loads(r.read().decode("utf-8"))
        verdict = json.loads(body["message"]["content"])
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as ex:
        print(f"  warning: Ollama request failed: {ex}")
        return None
    cache.set(key, verdict)
    return verdict


# ---------------------------------------------------------------------------
# Per-folder pipeline
# ---------------------------------------------------------------------------

def process_folder(folder, args, exiftool, cache, use_ollama):
    """Returns report rows for one folder."""
    items = []
    siblings = c.siblings_of(folder)
    for f in sorted(folder.iterdir()):
        if not f.is_file() or c.is_junk(f.name):
            continue
        if c.classify(f) not in ("raw", "image", "video"):
            continue
        it = Item(f)
        for s in c.sidecars_for(f, siblings):
            if s.suffix.lower() == ".xmp":
                it.xmp = s
                rating, pick = c.xmp_protection(s)
                it.protected = rating >= 1 or pick == 1
        items.append(it)
    if len(items) < 2:
        return []

    roles = {}      # Item -> (group_id, method, role, distance, reason)
    gcount = [0]

    def record(group, method, keeper_order, reason="", dist_ref=None):
        gcount[0] += 1
        gid = f"{folder.name}#{gcount[0]}"
        keep = keeper_order[0]
        for it in group:
            is_dup = it is not keep and not it.protected
            d = hamming(it.hash, keep.hash) if (it.hash is not None and keep.hash is not None) else ""
            roles[it] = (gid, method, "dup" if is_dup else "keep", d, reason)

    removed = set()

    # 1. exact
    for g in find_exact(items):
        order = choose_keeper(g)
        record(g, "exact", order)
        removed.update(i for i in g if roles[i][2] == "dup")

    # 2. hashes for the remaining images
    images = [i for i in items if i.kind in ("raw", "image") and i not in removed]
    for i in images:
        key = "img|" + file_key(i.path)
        hit = cache.get(key)
        if hit:
            i.hash, i.w, i.h, i.sharp = int(hit["hash"], 16), hit["w"], hit["h"], hit["sharp"]
        else:
            img = load_preview(i.path, i.kind, exiftool)
            if img is None:
                continue
            i.hash, (i.w, i.h), i.sharp = dhash(img), img.size, sharpness(img)
            cache.set(key, {"hash": format(i.hash, "x"), "w": i.w, "h": i.h, "sharp": i.sharp})
    hashed = [i for i in images if i.hash is not None]

    # 3. visual
    for g in find_visual(hashed, args.visual_threshold, args.collapse_raw_jpg):
        record(g, "visual", choose_keeper(g))
        removed.update(i for i in g if roles[i][2] == "dup")

    # 4. burst (Ollama)
    if use_ollama:
        survivors = [i for i in hashed if i not in removed]
        if exiftool:
            recs = c.exiftool_batch([i.path for i in survivors], exiftool)
            for i in survivors:
                dt, _ = c.date_from_record(c.record_for(recs, i.path))
                i.when = dt
        for g in burst_candidates(survivors, args.burst_seconds, args.burst_distance):
            for i in g:
                i.img = load_preview(i.path, i.kind, exiftool, 768)
            if any(i.img is None for i in g):
                continue
            verdict = ask_ollama(g, args.model, cache)
            if not verdict or not verdict.get("same_moment"):
                continue
            best = verdict.get("best_index")
            if not isinstance(best, int) or not 0 <= best < len(g):
                best = max(range(len(g)), key=lambda k: g[k].sharp)
            dups = {k for k in verdict.get("near_duplicate_indexes", [])
                    if isinstance(k, int) and 0 <= k < len(g) and k != best}
            if not dups:
                continue
            members = [g[best]] + [g[k] for k in sorted(dups)]
            record(members, "burst", members, verdict.get("reason", ""))
            removed.update(i for i in members if roles[i][2] == "dup")
            for i in g:
                i.img = None
        cache.save()

    for i in roles:
        ensure_sha(i)
    cache.save()

    rows = []
    for it, (gid, method, role, dist, reason) in sorted(
            roles.items(), key=lambda kv: (kv[1][0], kv[1][2] != "keep", kv[0].path.name)):
        rows.append({
            "folder": folder.name, "group_id": gid, "method": method, "role": role,
            "path": str(it.path), "sha256": it.sha, "width": it.w, "height": it.h,
            "size": it.size, "sharpness": f"{it.sharp:.1f}", "distance": dist,
            "protected": "yes" if it.protected else "", "ollama_reason": reason,
        })
    return rows


FIELDS = ["folder", "group_id", "method", "role", "path", "sha256", "width", "height",
          "size", "sharpness", "distance", "protected", "ollama_reason"]


def write_html(rows, path, exiftool):
    """Contact sheet: one block per duplicate group with embedded thumbnails."""
    groups = defaultdict(list)
    for r in rows:
        groups[r["group_id"]].append(r)
    out = ["<html><meta charset='utf-8'><title>Duplicates</title><style>"
           "body{font-family:sans-serif}.g{border:1px solid #ccc;margin:10px;padding:8px}"
           ".r{display:inline-block;margin:6px;width:220px;vertical-align:top;font-size:12px}"
           ".keep{border:3px solid #2a2}.dup{border:3px solid #c22}"
           "img{max-width:214px;max-height:160px}</style><body>"
           f"<h2>Duplicate groups: {len(groups)}</h2>"]
    for gid, members in groups.items():
        out.append(f"<div class='g'><b>{html.escape(gid)}</b> ({members[0]['method']})")
        if members[0]["ollama_reason"]:
            out.append(f" - {html.escape(members[0]['ollama_reason'])}")
        out.append("<br>")
        for r in members:
            p = Path(r["path"])
            kind = c.classify(p)
            img = load_preview(p, kind, exiftool, 220) if kind in ("raw", "image") else None
            thumb = ""
            if img is not None:
                thumb = ("<img src='data:image/jpeg;base64,"
                         + base64.b64encode(jpeg_bytes(img, 220)).decode("ascii") + "'><br>")
            out.append(f"<div class='r {r['role']}'>{thumb}<b>{r['role'].upper()}</b> "
                       f"{html.escape(p.name)}<br>{r['width']}x{r['height']}, "
                       f"{c.human_size(int(r['size']))}</div>")
        out.append("</div>")
    out.append("</body></html>")
    Path(path).write_text("".join(out), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dest")
    ap.add_argument("--folder", help="only this folder name (e.g. 2023-05-14)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-ollama", action="store_true", help="skip the burst/similar pass")
    ap.add_argument("--burst-seconds", type=float, default=3.0)
    ap.add_argument("--burst-distance", type=int, default=40,
                    help="max dHash distance (of 256 bits) for burst candidates")
    ap.add_argument("--visual-threshold", type=int, default=10,
                    help="max dHash distance (of 256 bits) to call two images the same picture")
    ap.add_argument("--collapse-raw-jpg", action="store_true",
                    help="treat a RAW and its JPG as duplicates (keeps the RAW)")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--exiftool")
    args = ap.parse_args(argv)

    dest = Path(args.dest)
    if not dest.is_dir():
        ap.error(f"not a folder: {dest}")
    exiftool = c.find_tool("exiftool", args.exiftool, "EXIFTOOL")
    if not exiftool:
        print("Note: exiftool not found - RAW previews and burst timing are unavailable.")

    use_ollama = not args.no_ollama
    if use_ollama:
        if not exiftool:
            print("  Burst pass needs exiftool for capture times; skipping it.")
            use_ollama = False
        else:
            use_ollama = ollama_ready(args.model)

    folders = [p for p in sorted(dest.iterdir())
               if p.is_dir() and (p.name == args.folder if args.folder
                                  else c.DATE_FOLDER_RE.match(p.name))]
    print(f"Scanning {len(folders)} folder(s)...")
    cache = Cache(dest / c.REPORTS_DIR / ".cache" / "step3.json")
    rows = []
    for n, folder in enumerate(folders, 1):
        found = process_folder(folder, args, exiftool, cache, use_ollama)
        if found:
            print(f"[{n}/{len(folders)}] {folder.name}: "
                  f"{sum(1 for r in found if r['role'] == 'dup')} duplicate(s)")
        rows += found

    ts = c.timestamp()
    csv_path = dest / c.REPORTS_DIR / f"duplicates_{ts}.csv"
    c.write_csv(csv_path, FIELDS, rows)
    dups = [r for r in rows if r["role"] == "dup"]
    print(f"\n{len(dups)} duplicate file(s) in {len({r['group_id'] for r in rows})} group(s), "
          f"{c.human_size(sum(int(r['size']) for r in dups))} reclaimable.")
    print(f"Report: {csv_path}")
    if rows and not args.no_html:
        html_path = csv_path.with_suffix(".html")
        write_html(rows, html_path, exiftool)
        print(f"Contact sheet: {html_path}")
    print("Review it, then run step4_quarantine.py with the CSV.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
