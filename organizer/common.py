"""
Shared helpers for the photo organizer scripts (step1..step5).

Written for Windows first (pathlib, no shell=True, long-path prefix, argfile for
exiftool) but has no OS-specific dependencies, so it also runs on macOS/Linux.

Python 3.10+. Third-party packages are only needed by step 3 (Pillow, pillow-heif).
"""

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff",
              ".gif", ".bmp", ".webp"}
RAW_EXTS = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".dng", ".orf",
            ".rw2", ".raf", ".pef", ".srw"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".3gp", ".mts", ".m2ts", ".mod",
              ".tod", ".mpg", ".mpeg", ".wmv", ".mkv"}
# Files that travel with a primary file (matched by stem or full file name).
SIDECAR_EXTS = {".xmp", ".acr", ".aae", ".thm", ".modd", ".moff", ".mdi",
                ".moi", ".pp3", ".dop"}
# AVCHD folder-structure files: not needed once the clips are extracted.
AVCHD_STRUCTURE_EXTS = {".cpi", ".mpl", ".bdm", ".tdt", ".tid", ".pmf", ".bdmv"}

JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}
SKIP_DIR_NAMES = {"$recycle.bin", "system volume information", ".cache",
                  "__pycache__", ".git"}

# Folders the scripts create inside DEST.
REPORTS_DIR = "_reports"
DUPLICATES_DIR = "_duplicates"
UNDATED_DIR = "_undated"
VIDEO_ORIGINALS_DIR = "_video_originals"
RESERVED_DIRS = {REPORTS_DIR, DUPLICATES_DIR, UNDATED_DIR, VIDEO_ORIGINALS_DIR}

DATE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def classify(path):
    """'raw' | 'image' | 'video' | 'sidecar' | 'structure' | None."""
    ext = Path(path).suffix.lower()
    if ext in RAW_EXTS:
        return "raw"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in SIDECAR_EXTS:
        return "sidecar"
    if ext in AVCHD_STRUCTURE_EXTS:
        return "structure"
    return None


def is_junk(name):
    return name.lower() in JUNK_NAMES


# ---------------------------------------------------------------------------
# Windows-safe paths and file operations
# ---------------------------------------------------------------------------

def lp(path):
    """Return a string path usable for file operations. On Windows, paths of
    240+ chars get the \\\\?\\ prefix so they don't hit MAX_PATH."""
    s = str(path)
    if os.name != "nt":
        return s
    s = os.path.abspath(s)
    if len(s) >= 240 and not s.startswith("\\\\?\\"):
        if s.startswith("\\\\"):
            return "\\\\?\\UNC\\" + s[2:]
        return "\\\\?\\" + s
    return s


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(lp(path), "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def same_content(a, b):
    try:
        if os.path.getsize(lp(a)) != os.path.getsize(lp(b)):
            return False
    except OSError:
        return False
    return sha256_file(a) == sha256_file(b)


def unique_path(dst, taken=None):
    """dst, or 'name (1).ext', 'name (2).ext'... if it already exists on disk
    or `taken(path)` says it's reserved."""
    dst = Path(dst)

    def busy(p):
        return os.path.exists(lp(p)) or (taken is not None and taken(p))

    if not busy(dst):
        return dst
    n = 1
    while True:
        cand = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
        if not busy(cand):
            return cand
        n += 1


def safe_move(src, dst, copy=False):
    """Move (or copy) src to dst without ever overwriting. Same-volume moves
    are a rename. Cross-volume: copy, verify size + SHA-256, then delete the
    source. Returns the SHA-256 when it was computed, else ''."""
    src_l, dst_l = lp(src), lp(dst)
    if os.path.exists(dst_l):
        raise FileExistsError(f"destination exists: {dst}")
    os.makedirs(os.path.dirname(dst_l), exist_ok=True)

    if not copy:
        try:
            os.rename(src_l, dst_l)
            return ""
        except FileExistsError:
            raise
        except OSError:
            pass  # different volume (or similar) -> copy path below

    tmp = dst_l + ".part"
    shutil.copy2(src_l, tmp)
    try:
        if os.path.getsize(src_l) != os.path.getsize(tmp):
            raise OSError("size mismatch after copy")
        digest = sha256_file(src_l)
        if sha256_file(tmp) != digest:
            raise OSError("checksum mismatch after copy")
        os.rename(tmp, dst_l)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if not copy:
        os.remove(src_l)
    return digest


def remove_empty_dirs(root, keep_root=True):
    """Delete empty directories under root (bottom-up)."""
    for dirpath, _dirs, _files in os.walk(lp(root), topdown=False):
        if keep_root and Path(dirpath) == Path(lp(root)):
            continue
        try:
            os.rmdir(dirpath)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Manifest (resume / undo)
# ---------------------------------------------------------------------------

MANIFEST_FIELDS = ["ts", "op", "src", "dst", "sha256", "size", "reason"]


class Manifest:
    """Append-only CSV of everything a script actually did. Flushed per row so
    a crash or unplugged drive leaves an accurate record."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self._f = open(self.path, "a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._f, fieldnames=MANIFEST_FIELDS)
        if new:
            self._w.writeheader()

    def add(self, op, src, dst, sha256="", size=0, reason=""):
        self._w.writerow({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "op": op, "src": str(src), "dst": str(dst),
            "sha256": sha256, "size": size, "reason": reason,
        })
        self._f.flush()

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_manifest(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def undo_manifest(path, apply):
    """Reverse a manifest, newest row first. op=move -> move dst back to src;
    op=copy/create -> delete dst. Never overwrites an existing src."""
    rows = read_manifest(path)
    done = skipped = 0
    for row in reversed(rows):
        op, src, dst = row["op"], row["src"], row["dst"]
        if not os.path.exists(lp(dst)):
            print(f"  skip (missing): {dst}")
            skipped += 1
            continue
        if op == "move":
            if os.path.exists(lp(src)):
                print(f"  skip (original path occupied): {src}")
                skipped += 1
                continue
            print(f"  {'restore' if apply else 'would restore'}: {dst} -> {src}")
            if apply:
                safe_move(dst, src)
        elif op in ("copy", "create"):
            print(f"  {'delete' if apply else 'would delete'}: {dst}")
            if apply:
                os.remove(lp(dst))
        else:
            print(f"  skip (unknown op {op!r}): {dst}")
            skipped += 1
            continue
        done += 1
    print(f"Undo {'done' if apply else 'plan'}: {done} item(s), {skipped} skipped.")
    if apply:
        parents = {Path(r["dst"]).parent for r in rows}
        for p in parents:
            try:
                os.rmdir(lp(p))
            except OSError:
                pass


def write_csv(path, fieldnames, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def timestamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# Directory walking and sidecars
# ---------------------------------------------------------------------------

def walk_files(root, exclude=()):
    """Yield every file under root, skipping junk, system folders and any
    directory in `exclude` (e.g. DEST when it sits inside the source)."""
    exclude = {os.path.normcase(os.path.abspath(str(e))) for e in exclude}
    for dirpath, dirs, files in os.walk(lp(root)):
        dirs[:] = [
            d for d in dirs
            if d.lower() not in SKIP_DIR_NAMES
            and os.path.normcase(os.path.abspath(os.path.join(dirpath, d))) not in exclude
        ]
        dirs.sort()
        for name in sorted(files):
            if is_junk(name):
                continue
            yield Path(dirpath) / name


def sidecars_for(primary, siblings):
    """Sidecars of `primary`. `siblings` maps lowercase filename -> Path for
    the primary's directory. Matches `IMG_1.xmp` and `IMG_1.CR3.xmp` forms."""
    primary = Path(primary)
    name, stem = primary.name.lower(), primary.stem.lower()
    out = []
    for lname, p in siblings.items():
        if p == primary or p.suffix.lower() not in SIDECAR_EXTS:
            continue
        if p.stem.lower() in (stem, name):
            out.append(p)
    return sorted(out)


def sidecar_new_name(primary_name, sidecar_name, new_primary_name):
    sp = Path(sidecar_name)
    if sp.stem.lower() == primary_name.lower():
        return new_primary_name + sp.suffix           # 00012.MTS.modd
    return Path(new_primary_name).stem + sp.suffix    # 00012.modd


def siblings_of(directory):
    out = {}
    try:
        for name in os.listdir(lp(directory)):
            out[name.lower()] = Path(directory) / name
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------
# Capture-date extraction
# ---------------------------------------------------------------------------

MIN_DATE = datetime(1990, 1, 1)
# Typical "clock was never set" values.
SUSPECT_DATES = {date(2000, 1, 1), date(1980, 1, 1), date(1970, 1, 1),
                 date(2002, 1, 1), date(2004, 1, 1)}

_DT_RE = re.compile(
    r"^\s*(\d{4})[:\-/](\d{2})[:\-/](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")


def parse_dt(value):
    """Parse '2023:05:14 10:22:31[.12][+02:00]' as a naive local datetime.
    The offset is ignored on purpose: we want the date the photographer saw."""
    if not isinstance(value, str):
        return None
    m = _DT_RE.match(value)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None


def is_valid_date(dt, min_date=MIN_DATE):
    if dt is None:
        return False
    if dt < min_date or dt > datetime.now() + timedelta(days=1):
        return False
    return dt.date() not in SUSPECT_DATES


_QT_GROUPS = ("quicktime", "track", "media")

# (tag, groups to exclude) in priority order; first valid value wins.
DATE_TAG_ORDER = [
    ("SubSecDateTimeOriginal", ()),
    ("DateTimeOriginal", ()),
    ("CreationDate", ()),                # QuickTime/Keys: local time with offset
    ("SubSecCreateDate", ()),
    ("CreateDate", _QT_GROUPS),          # EXIF/XMP
    ("DateCreated", ()),                 # XMP photoshop / IPTC
    ("CreateDate", ()),                  # QuickTime/Track (UTC) as a late resort
    ("MediaCreateDate", ()),
]

# Filename patterns: IMG_20230514_102231, PXL_20230514_..., VID-20230514-WA0001,
# 2023-05-14 10.22.31, 20230514_102231
_FN_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)"
    r"(?:[ _T-]?([01]\d|2[0-3])[.:]?([0-5]\d)[.:]?([0-5]\d))?")


def date_from_filename(name):
    m = _FN_RE.search(name)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None


def _tag_values(record, tag, exclude_groups):
    for key, val in record.items():
        group, _, name = key.rpartition(":")
        if name != tag:
            continue
        if group and group.lower().startswith(exclude_groups) and exclude_groups:
            continue
        yield key, val


def date_from_record(record, min_date=MIN_DATE):
    """First valid capture date from one exiftool JSON record (-G1 keys).
    Returns (datetime, 'Group:Tag') or (None, '')."""
    if not record:
        return None, ""
    for tag, excl in DATE_TAG_ORDER:
        for key, val in _tag_values(record, tag, tuple(excl)):
            dt = parse_dt(val if isinstance(val, str) else str(val))
            if is_valid_date(dt, min_date):
                return dt, key
    return None, ""


def date_from_xmp(xmp_path, min_date=MIN_DATE):
    """Capture date written in an XMP sidecar, via the repo's xmp_common."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import xmp_common
        attrs = xmp_common.parse_xmp(xmp_path)["attrs"]
    except Exception:
        return None, ""
    for key in ("exif:DateTimeOriginal", "photoshop:DateCreated", "xmp:CreateDate"):
        dt = parse_dt(attrs.get(key))
        if is_valid_date(dt, min_date):
            return dt, f"xmp:{key}"
    return None, ""


def xmp_protection(xmp_path):
    """(rating, pick) from an XMP sidecar; (0, 0) if unreadable."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import xmp_common
        attrs = xmp_common.parse_xmp(xmp_path)["attrs"]
        rating = int(float(attrs.get("xmp:Rating", 0)))
        pick = int(attrs.get("xmpDM:pick", 0))
        return max(rating, 0), pick
    except Exception:
        return 0, 0


# ---------------------------------------------------------------------------
# External tools
# ---------------------------------------------------------------------------

def find_tool(name, override=None, env_var=None):
    """Locate an executable: explicit path -> env var -> PATH (adds .exe on
    Windows automatically via shutil.which)."""
    for cand in (override, os.environ.get(env_var) if env_var else None):
        if cand:
            p = shutil.which(cand) or (cand if os.path.isfile(cand) else None)
            if p:
                return p
    return shutil.which(name)


def exiftool_batch(paths, exiftool, ee=False, batch_size=500):
    """Run exiftool over many files. Paths go through a UTF-8 argfile (no
    command-line length limit, no Windows code-page trouble). Returns
    {normcase(abspath): record}."""
    result = {}
    paths = [str(p) for p in paths]
    for i in range(0, len(paths), batch_size):
        chunk = paths[i:i + batch_size]
        with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False,
                                         encoding="utf-8") as af:
            af.write("\n".join(chunk) + "\n")
            argfile = af.name
        try:
            cmd = [exiftool, "-j", "-G1", "-n", "-m", "-charset", "filename=utf8"]
            if ee:
                cmd.append("-ee")
            cmd += ["-@", argfile]
            proc = subprocess.run(cmd, capture_output=True, timeout=1800)
        finally:
            os.remove(argfile)
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            continue
        try:
            records = json.loads(out)
        except json.JSONDecodeError:
            print(f"  warning: could not parse exiftool output for batch at {i}")
            continue
        for rec in records:
            src = rec.get("SourceFile")
            if src:
                result[os.path.normcase(os.path.abspath(src))] = rec
    return result


def record_for(records, path):
    return records.get(os.path.normcase(os.path.abspath(str(path))))


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
