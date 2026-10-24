"""
Step 5 - permanently delete DEST\\_duplicates\\ (after you have reviewed it).

    python step5_purge.py DEST                       # lists what would go, asks you to type DELETE
    python step5_purge.py DEST --older-than 30       # only files quarantined 30+ days ago

"Quarantined N days ago" comes from the step 1 / step 4 manifests in
DEST\\_reports, not from file dates. Files without a manifest record are
left alone when --older-than is used. This cannot be undone.
"""

import argparse
import glob
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402


def quarantine_times(dest):
    times = {}
    pattern = str(Path(dest) / c.REPORTS_DIR / "step*_manifest_*.csv")
    for mf in glob.glob(pattern):
        for row in c.read_manifest(mf):
            if row["op"] == "move":
                try:
                    times[os.path.normcase(row["dst"])] = datetime.fromisoformat(row["ts"])
                except ValueError:
                    pass
    return times


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dest")
    ap.add_argument("--older-than", type=int, metavar="DAYS")
    args = ap.parse_args(argv)

    root = Path(args.dest) / c.DUPLICATES_DIR
    if not root.is_dir():
        print(f"Nothing to purge: {root} does not exist.")
        return 0

    files = [p for p in c.walk_files(root)]
    if args.older_than is not None:
        cutoff = datetime.now() - timedelta(days=args.older_than)
        times = quarantine_times(args.dest)
        files = [p for p in files
                 if times.get(os.path.normcase(str(p))) and times[os.path.normcase(str(p))] < cutoff]

    total = sum(os.path.getsize(c.lp(p)) for p in files)
    for p in files[:20]:
        print(f"  {p}")
    if len(files) > 20:
        print(f"  ... and {len(files) - 20} more")
    print(f"\n{len(files)} file(s), {c.human_size(total)} will be PERMANENTLY deleted.")
    if not files:
        return 0
    if input("Type DELETE to confirm: ").strip() != "DELETE":
        print("Cancelled.")
        return 1

    deleted = 0
    for p in files:
        try:
            try:
                os.remove(c.lp(p))
            except PermissionError:
                os.chmod(c.lp(p), 0o666)  # clear read-only flag on Windows
                os.remove(c.lp(p))
            deleted += 1
        except OSError as ex:
            print(f"  could not delete {p}: {ex}")
    c.remove_empty_dirs(root)
    print(f"Deleted {deleted} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
