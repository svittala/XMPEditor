"""
Step 1c - remove source folders that are empty after steps 1 / 1b moved their files.

    python step1c_remove_empty_dirs.py REPORT.csv [REPORT.csv ...]            # lists only
    python step1c_remove_empty_dirs.py REPORT.csv [REPORT.csv ...] --apply    # asks to confirm, then removes

REPORT is a step1 / step1b manifest or plan CSV. The folders that held the
`src` files (and their parent folders, bottom-up) are candidates. A folder is
removed only if it is truly empty - or contains nothing but candidate folders
that are themselves being removed. A folder with any file left in it (even
Thumbs.db) is kept and reported. The top-level source folder (the common parent
of everything in the CSV, or --src) is never removed.

Nothing is deleted without you typing DELETE at the prompt. Removed folders are
logged to DEST\\_reports\\step1c_removed_<ts>.csv (empty folders have no contents
to restore, so there is no undo).
"""

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402


def source_files(reports):
    out = []
    for report in reports:
        with open(report, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("op") not in (None, "", "move", "copy"):
                    continue
                out.append(Path(r["src"]))
    return out


def common_root(dirs):
    return Path(os.path.commonpath([str(d) for d in dirs]))


def candidates(files, root=None, protect=()):
    """Folders to consider: each file's parent and its ancestors, below `root`."""
    parents = {f.parent for f in files}
    if not parents:
        return set(), None
    root = Path(root) if root else common_root(parents)
    protect = [Path(p) for p in protect]
    cands = set()
    for d in parents:
        while d != root and root in d.parents:
            cands.add(d)
            d = d.parent
    # never touch the library itself or anything that contains it
    cands = {d for d in cands
             if not any(p == d or d in p.parents for p in protect)}
    return cands, root


def plan(cands):
    """Return (removable, kept). `removable` is ordered deepest-first so each
    folder is removed after its children. kept: {folder: reason}."""
    removable, kept = [], {}
    removable_set = set()
    for d in sorted(cands, key=lambda p: len(p.parts), reverse=True):
        try:
            entries = os.listdir(c.lp(d))
        except OSError:
            continue  # already gone / unreadable
        blockers = [e for e in entries if (d / e) not in removable_set]
        if blockers:
            n = len(blockers)
            kept[d] = f"{n} item(s) left (e.g. {blockers[0]})"
        else:
            removable.append(d)
            removable_set.add(d)
    return removable, kept


def run(reports, apply=False, src_root=None, dest=None, confirm=input, quiet=False):
    files = source_files(reports)
    protect = [dest] if dest else []
    cands, root = candidates(files, src_root, protect)
    removable, kept = plan(cands)
    if not quiet:
        print(f"Source root (kept): {root}")
        for d in removable:
            print(f"  empty: {d}")
        for d, why in sorted(kept.items()):
            print(f"  kept:  {d}  - {why}")
        print(f"\n{len(removable)} empty folder(s) can be removed; {len(kept)} kept.")
    if not apply:
        if not quiet:
            print("DRY RUN - nothing was removed. Re-run with --apply to remove them.")
        return []
    if not removable:
        return []
    if confirm(f"Type DELETE to remove these {len(removable)} empty folder(s): ").strip() != "DELETE":
        print("Cancelled.")
        return []
    removed, rows = [], []
    for d in removable:
        try:
            os.rmdir(c.lp(d))  # refuses non-empty folders
            removed.append(d)
            rows.append({"folder": d})
        except OSError as ex:
            print(f"  skipped {d}: {ex}")
    if dest:
        log = Path(dest) / c.REPORTS_DIR / f"step1c_removed_{c.timestamp()}.csv"
        c.write_csv(log, ["folder"], rows)
        print(f"Log: {log}")
    print(f"Removed {len(removed)} folder(s).")
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reports", nargs="+", help="step1 / step1b manifest or plan CSV(s)")
    ap.add_argument("--apply", action="store_true", help="remove the folders (asks first)")
    ap.add_argument("--src", help="source root to keep (default: common parent of all sources)")
    ap.add_argument("--dest", help="library root (default: parent of the first report's _reports folder)")
    args = ap.parse_args(argv)
    for r in args.reports:
        if not Path(r).is_file():
            ap.error(f"report not found: {r}")
    dest = Path(args.dest) if args.dest else Path(args.reports[0]).resolve().parent.parent
    run(args.reports, apply=args.apply, src_root=args.src, dest=dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
