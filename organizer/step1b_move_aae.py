"""
Step 1b - move iPhone .aae edit files that were left behind in the source folders
next to the photos that step 1 moved.

    python step1b_move_aae.py DEST\\_reports\\step1_manifest_<ts>.csv           # dry run
    python step1b_move_aae.py DEST\\_reports\\step1_manifest_<ts>.csv --apply
    python step1b_move_aae.py --undo DEST\\_reports\\step1b_manifest_<ts>.csv --apply

Reads the step 1 manifest (or plan) CSV to learn where each photo came from and
where it went, looks in that original folder for leftover .aae files and moves
them into the photo's new folder. Matching is by file name:

    IMG_1234.JPG   <- IMG_1234.AAE          same name
    IMG_E1234.JPG  <- IMG_1234.AAE          iPhone's edited copy keeps the
                                            original's number in the .aae name

A same-name .aae is renamed to follow its photo if step 1 renamed it
(IMG_1234 (1).JPG). Nothing is ever overwritten.
"""

import argparse
import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

EDIT_RE = re.compile(r"^(?P<prefix>.*?)E(?P<num>\d+)$", re.IGNORECASE)


def original_stem(stem):
    """IMG_E1234 -> IMG_1234, else None."""
    m = EDIT_RE.match(stem)
    return f"{m['prefix']}{m['num']}" if m else None


def read_rows(report):
    with open(report, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        # manifest rows: op/src/dst/reason; plan rows: src/dst/role.
        # Only primaries (photos/videos) matter; skip failed/undone rows.
        if r.get("op") not in (None, "", "move", "copy"):
            continue
        if c.classify(r["src"]) in ("raw", "image", "video"):
            out.append((Path(r["src"]), Path(r["dst"])))
    return out


def plan_moves(pairs, claimed=None):
    """Return [(aae_path, dst_path, photo_dst)] for leftover .aae files."""
    claimed = set() if claimed is None else claimed
    cache = {}

    def aaes_in(directory):
        if directory not in cache:
            cache[directory] = {n: p for n, p in c.siblings_of(directory).items()
                                if p.suffix.lower() == ".aae"}
        return cache[directory]

    moves = []
    # exact-name matches first, so IMG_1234.AAE goes with the original and only
    # falls back to the edited IMG_E1234 when the original isn't around.
    for exact in (True, False):
        for src, dst in pairs:
            aaes = aaes_in(src.parent)
            stem = src.stem.lower()
            if exact:
                want, rename = stem, True
            else:
                want, rename = (original_stem(stem) or "").lower(), False
            if not want:
                continue
            aae = aaes.get(want + ".aae")
            if aae is None or aae in claimed or not os.path.exists(c.lp(aae)):
                continue
            claimed.add(aae)
            name = dst.stem + aae.suffix if rename else aae.name
            moves.append((aae, dst.parent / name, dst))
    return moves


def run(report, dest, apply=False, copy=False, quiet=False):
    pairs = read_rows(report)
    moves = plan_moves(pairs)
    ts = c.timestamp()
    reports = Path(dest) / c.REPORTS_DIR
    reserved = set()
    plan_rows = []
    manifest = c.Manifest(reports / f"step1b_manifest_{ts}.csv") if apply else None
    moved = errors = 0
    try:
        for aae, target, photo in moves:
            target = c.unique_path(target, lambda p: str(p).lower() in reserved)
            reserved.add(str(target).lower())
            plan_rows.append({"src": aae, "dst": target, "matched_photo": photo})
            if not apply:
                continue
            try:
                size = os.path.getsize(c.lp(aae))
                digest = c.safe_move(aae, target, copy=copy)
                manifest.add("copy" if copy else "move", aae, target, digest, size, "aae")
                moved += 1
            except Exception as ex:
                errors += 1
                print(f"  ERROR {aae}: {ex}")
    finally:
        if manifest:
            manifest.close()
    plan_path = reports / f"step1b_plan_{ts}.csv"
    c.write_csv(plan_path, ["src", "dst", "matched_photo"], plan_rows)
    if not quiet:
        print(f"Photos checked: {len(pairs)}   .aae files matched: {len(moves)}")
        print(f"Plan written to {plan_path}")
        if apply:
            print(f"Moved/copied {moved} file(s); {errors} error(s).")
            print(f"Manifest: {reports / f'step1b_manifest_{ts}.csv'}")
        else:
            print("DRY RUN - nothing was moved. Re-run with --apply to execute.")
    return plan_rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", nargs="?", help="step1_manifest_<ts>.csv (or step1_plan_<ts>.csv)")
    ap.add_argument("--apply", action="store_true", help="really move files (default: dry run)")
    ap.add_argument("--copy", action="store_true", help="copy instead of move")
    ap.add_argument("--dest", help="library root (default: parent of the report's _reports folder)")
    ap.add_argument("--undo", metavar="MANIFEST", help="reverse a step1b manifest CSV")
    args = ap.parse_args(argv)

    if args.undo:
        c.undo_manifest(args.undo, args.apply)
        if not args.apply:
            print("DRY RUN - add --apply to restore the files.")
        return 0
    if not args.report:
        ap.error("REPORT.csv is required")
    report = Path(args.report)
    if not report.is_file():
        ap.error(f"report not found: {report}")
    dest = Path(args.dest) if args.dest else report.resolve().parent.parent
    run(report, dest, apply=args.apply, copy=args.copy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
