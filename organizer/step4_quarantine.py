"""
Step 4 - move the duplicates listed in a step 3 report into DEST\\_duplicates\\.

    python step4_quarantine.py DEST\\_reports\\duplicates_<ts>.csv           # dry run
    python step4_quarantine.py DEST\\_reports\\duplicates_<ts>.csv --apply
    python step4_quarantine.py --undo DEST\\_reports\\step4_manifest_<ts>.csv --apply

Only rows with role=dup are moved (with their sidecars). A file is skipped if
it changed since the report (SHA-256 mismatch) or if its group has no surviving
keeper on disk. Edit the `role` column in the CSV to override any decision.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", nargs="?")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dest", help="library root (default: parent of the report's _reports folder)")
    ap.add_argument("--undo", metavar="MANIFEST")
    args = ap.parse_args(argv)

    if args.undo:
        c.undo_manifest(args.undo, args.apply)
        return 0
    if not args.report:
        ap.error("REPORT.csv is required")
    report = Path(args.report)
    dest = Path(args.dest) if args.dest else report.resolve().parent.parent

    with open(report, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    keepers = defaultdict(int)
    for r in rows:
        if r["role"].strip().lower() == "keep" and os.path.exists(c.lp(r["path"])):
            keepers[r["group_id"]] += 1

    manifest = c.Manifest(dest / c.REPORTS_DIR / f"step4_manifest_{c.timestamp()}.csv") \
        if args.apply else None
    moved = skipped = freed = 0
    try:
        for r in rows:
            if r["role"].strip().lower() != "dup":
                continue
            src = Path(r["path"])
            if not os.path.exists(c.lp(src)):
                print(f"  skip (missing): {src}")
                skipped += 1
                continue
            if keepers[r["group_id"]] == 0:
                print(f"  skip (no keeper left in {r['group_id']}): {src.name}")
                skipped += 1
                continue
            if r.get("sha256") and c.sha256_file(src) != r["sha256"]:
                print(f"  skip (changed since report): {src}")
                skipped += 1
                continue

            target_dir = dest / c.DUPLICATES_DIR / r["folder"]
            siblings = c.siblings_of(src.parent)
            target = c.unique_path(target_dir / src.name)
            jobs = [(src, target)] + [
                (s, target.parent / c.sidecar_new_name(src.name, s.name, target.name))
                for s in c.sidecars_for(src, siblings)]
            for s, t in jobs:
                print(f"  {'move' if args.apply else 'would move'}: {s.name} -> {t}")
                if args.apply:
                    size = os.path.getsize(c.lp(s))
                    digest = c.safe_move(s, t)
                    manifest.add("move", s, t, digest, size,
                                 f"{r['method']} {r['group_id']}")
                    freed += size
            moved += 1
    finally:
        if manifest:
            manifest.close()

    print(f"\n{moved} duplicate(s) {'quarantined' if args.apply else 'would be quarantined'}, "
          f"{skipped} skipped.")
    if args.apply:
        print(f"Review {dest / c.DUPLICATES_DIR}, then run step5_purge.py to delete for good.")
    else:
        print("DRY RUN - re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
