"""
Step 1 - organize photos/videos into DEST\\yyyy-mm-dd\\ by real capture date.

    python step1_organize.py SRC DEST            # dry run: writes a plan CSV only
    python step1_organize.py SRC DEST --apply    # actually move the files
    python step1_organize.py SRC DEST --apply --copy   # copy instead of move
    python step1_organize.py --undo DEST\\_reports\\step1_manifest_<ts>.csv [--apply]

The capture date comes from file metadata (EXIF / QuickTime / AVCHD stream /
XMP sidecar / date in the file name). Only when a file has none of those is its
filesystem modified date used (date_source "fs_mtime"; disable with
--no-fs-date). Files still without a trustworthy date go to DEST\\_undated.
"""

import argparse
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402


class Entry:
    def __init__(self, src, kind, dt, source, dst_dir, new_name, sidecars):
        self.src = src
        self.kind = kind
        self.dt = dt
        self.source = source
        self.dst_dir = dst_dir
        self.new_name = new_name
        self.sidecars = sidecars


def _is_avchd_clip(path):
    return path.suffix.lower() in (".mts", ".m2ts") and path.stem.isdigit()


def new_name_for(path, dt):
    """AVCHD clips are all called 00000.MTS, 00001.MTS... on every card, so give
    them a unique, sortable, date-based name. Everything else keeps its name."""
    if dt is not None and _is_avchd_clip(path):
        return dt.strftime("%Y-%m-%d_%H%M%S") + path.suffix.lower()
    return path.name


def build_plan(src, dest, exiftool_fn, min_date=c.MIN_DATE, fs_date_fallback=True):
    """Scan src and return (entries, stats). `exiftool_fn(paths, ee)` returns
    {normcase(abspath): record}; injected so tests don't need exiftool."""
    src, dest = Path(src), Path(dest)
    exclude = [dest]
    stats = Counter()
    ignored = Counter()

    by_dir = {}
    for p in c.walk_files(src, exclude=exclude):
        by_dir.setdefault(p.parent, []).append(p)

    primaries = []  # (path, kind, sidecars)
    for directory, files in by_dir.items():
        siblings = {f.name.lower(): f for f in files}
        order = {"raw": 0, "image": 1, "video": 2}
        prim = sorted((f for f in files if c.classify(f) in order),
                      key=lambda f: (order[c.classify(f)], f.name.lower()))
        claimed = set()
        for f in prim:
            scs = [s for s in c.sidecars_for(f, siblings) if s not in claimed]
            claimed.update(scs)
            primaries.append((f, c.classify(f), scs))
        for f in files:
            kind = c.classify(f)
            if kind == "sidecar" and f not in claimed:
                stats["orphan_sidecars"] += 1
            elif kind == "structure":
                stats["avchd_structure_files_left"] += 1
            elif kind is None:
                ignored[f.suffix.lower() or "(none)"] += 1

    # Read metadata: stills without -ee, videos with it (pulls the date out of
    # the AVCHD H.264 stream). MODD sidecars are read too as a video fallback.
    stills = [p for p, k, _ in primaries if k in ("raw", "image")]
    videos = [p for p, k, _ in primaries if k == "video"]
    modd = [s for _, k, scs in primaries if k == "video" for s in scs
            if s.suffix.lower() == ".modd"]
    print(f"Found {len(stills)} photo(s), {len(videos)} video(s). Reading metadata...")
    records = {}
    if stills:
        records.update(exiftool_fn(stills, False))
    if videos or modd:
        records.update(exiftool_fn(videos + modd, True))

    entries = []
    for path, kind, scs in sorted(primaries, key=lambda t: str(t[0]).lower()):
        dt, source = c.date_from_record(c.record_for(records, path), min_date)
        if dt is None:
            for s in scs:
                if s.suffix.lower() == ".modd":
                    dt, source = c.date_from_record(c.record_for(records, s), min_date)
                    if dt:
                        source = "modd:" + source
                        break
        if dt is None:
            for s in scs:
                if s.suffix.lower() == ".xmp":
                    dt, source = c.date_from_xmp(s, min_date)
                    if dt:
                        break
        if dt is None:
            fdt = c.date_from_filename(path.name)
            if c.is_valid_date(fdt, min_date):
                dt, source = fdt, "filename"

        if dt is None and fs_date_fallback:
            # Last resort: no embedded metadata at all, so use the file's
            # modified time rather than leaving it in a source-shaped folder.
            fdt = _fs_datetime(path)
            if c.is_valid_date(fdt, min_date):
                dt, source = fdt, "fs_mtime"

        if dt is not None:
            dst_dir = dest / dt.strftime("%Y-%m-%d")
            new_name = new_name_for(path, dt)
        else:
            dst_dir = dest / c.UNDATED_DIR
            new_name = path.name
            source = "none"
        stats[f"date_source:{source.split(':')[0] if source != 'none' else 'undated'}"] += 1
        entries.append(Entry(path, kind, dt, source, dst_dir, new_name, scs))

    return entries, stats, ignored


def _fs_datetime(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _fs_mtime(path):
    dt = _fs_datetime(path)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def execute(entries, dest, apply, copy, manifest, plan_rows):
    """Resolve final names (never overwrite) and move/copy. In a dry run nothing
    touches disk; names are reserved in memory so the plan matches reality."""
    dest = Path(dest)
    reserved = set()

    def taken(p):
        return str(p).lower() in reserved

    moved = dupes = errors = 0
    for e in entries:
        target = e.dst_dir / e.new_name
        reason = ""
        exists = os.path.exists(c.lp(target)) or taken(target)
        if exists:
            identical = (os.path.exists(c.lp(target)) and c.same_content(e.src, target))
            if identical:
                folder = e.dst_dir.name
                target = c.unique_path(dest / c.DUPLICATES_DIR / folder / e.new_name, taken)
                reason = "identical-to-existing"
                dupes += 1
            else:
                target = c.unique_path(target, taken)
                reason = "name-collision"
        reserved.add(str(target).lower())

        jobs = [(e.src, target, "primary")]
        for s in e.sidecars:
            sname = c.sidecar_new_name(e.src.name, s.name, target.name)
            sdst = target.parent / sname
            if os.path.exists(c.lp(sdst)) or taken(sdst):
                sdst = c.unique_path(sdst, taken)
            reserved.add(str(sdst).lower())
            jobs.append((s, sdst, "sidecar"))

        for src, dst, role in jobs:
            plan_rows.append({
                "src": src, "dst": dst, "role": role, "kind": e.kind,
                "capture_date": e.dt.strftime("%Y-%m-%d %H:%M:%S") if e.dt else "",
                "date_source": e.source, "note": reason,
                "fs_mtime_ignored": _fs_mtime(src),
            })
            if not apply:
                continue
            try:
                size = os.path.getsize(c.lp(src))
                digest = c.safe_move(src, dst, copy=copy)
                manifest.add("copy" if copy else "move", src, dst, digest, size,
                             reason or role)
                moved += 1
            except Exception as ex:  # keep going; report at the end
                errors += 1
                print(f"  ERROR {src}: {ex}")
    return moved, dupes, errors


def organize(src, dest, apply=False, copy=False, exiftool_fn=None,
             min_date=c.MIN_DATE, quiet=False, fs_date_fallback=True):
    src, dest = Path(src), Path(dest)
    ts = c.timestamp()
    entries, stats, ignored = build_plan(src, dest, exiftool_fn, min_date, fs_date_fallback)

    plan_rows = []
    manifest = None
    if apply:
        manifest = c.Manifest(dest / c.REPORTS_DIR / f"step1_manifest_{ts}.csv")
    try:
        moved, dupes, errors = execute(entries, dest, apply, copy, manifest, plan_rows)
    finally:
        if manifest:
            manifest.close()

    plan_path = dest / c.REPORTS_DIR / f"step1_plan_{ts}.csv"
    c.write_csv(plan_path,
                ["src", "dst", "role", "kind", "capture_date", "date_source",
                 "note", "fs_mtime_ignored"], plan_rows)

    if not quiet:
        years = Counter(e.dt.year for e in entries if e.dt)
        print("\n=== Summary ===")
        print(f"Primary files: {len(entries)}   undated: {sum(1 for e in entries if not e.dt)}")
        for k, v in sorted(stats.items()):
            print(f"  {k}: {v}")
        if years:
            print("  per year: " + ", ".join(f"{y}={n}" for y, n in sorted(years.items())))
        if ignored:
            print("  ignored extensions: " + ", ".join(f"{k}={v}" for k, v in ignored.most_common(10)))
        print(f"Plan written to {plan_path}")
        if apply:
            print(f"Moved/copied {moved} file(s); {dupes} identical to an existing file "
                  f"(sent to {c.DUPLICATES_DIR}); {errors} error(s).")
            print(f"Manifest: {dest / c.REPORTS_DIR / f'step1_manifest_{ts}.csv'}")
        else:
            print("DRY RUN - nothing was moved. Re-run with --apply to execute.")
    return entries, plan_rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="?", help="folder to scan (recursively)")
    ap.add_argument("dest", nargs="?", help="destination root for yyyy-mm-dd folders")
    ap.add_argument("--apply", action="store_true", help="really move files (default: dry run)")
    ap.add_argument("--copy", action="store_true", help="copy instead of move")
    ap.add_argument("--exiftool", help="path to exiftool.exe (else EXIFTOOL env var, else PATH)")
    ap.add_argument("--min-year", type=int, default=1990,
                    help="ignore capture dates before this year (default 1990)")
    ap.add_argument("--no-fs-date", action="store_true",
                    help="don't fall back to the file's modified date when it has no "
                         "embedded capture date (those files go to _undated)")
    ap.add_argument("--undo", metavar="MANIFEST", help="reverse a step1 manifest CSV")
    args = ap.parse_args(argv)

    if args.undo:
        c.undo_manifest(args.undo, args.apply)
        if not args.apply:
            print("DRY RUN - add --apply to restore the files.")
        return 0
    if not args.src or not args.dest:
        ap.error("SRC and DEST are required")

    src, dest = Path(args.src), Path(args.dest)
    if not src.is_dir():
        ap.error(f"source folder not found: {src}")
    exiftool = c.find_tool("exiftool", args.exiftool, "EXIFTOOL")
    if not exiftool:
        print("ERROR: exiftool not found. Install it (winget install OliverBetz.ExifTool) "
              "or pass --exiftool PATH.")
        return 2

    def fn(paths, ee):
        return c.exiftool_batch(paths, exiftool, ee=ee)

    organize(src, dest, apply=args.apply, copy=args.copy, exiftool_fn=fn,
             min_date=datetime(args.min_year, 1, 1),
             fs_date_fallback=not args.no_fs_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
