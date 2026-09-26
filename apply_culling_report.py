#!/usr/bin/env python3
"""
apply_culling_report.py — apply a culling_helper_*.py markdown report to the
corresponding .xmp sidecars.

For each image listed in a report:
  - Keep  -> add the report's Category as a keyword tag, set xmp:Rating = 2
  - Discard -> set xmp:Rating = 1 (no tag; discards have no Category)

Existing tags are preserved (the Category is merged in, like xmpwrite's
--add-tags), and every other field in the .xmp (Camera Raw develop settings,
etc.) is left untouched, same as xmpwrite.py.

Usage:
    python3 apply_culling_report.py REPORT [REPORT ...] --images-dir DIR [options]
    python3 apply_culling_report.py testresults --images-dir testdata

REPORT may be a specific .md file or a directory (every *.md in it is used).
--images-dir is the local folder containing the images/.xmp sidecars — NOT
the "Folder:" path recorded in the report header, which reflects wherever
the report was originally generated (e.g. a different machine/drive).

Options:
    --images-dir DIR   folder containing the .xmp sidecars (required)
    --no-backup        skip writing a .bak backup before overwriting
    --dry-run          show what would change; write nothing
"""

import argparse
import re
import sys
from pathlib import Path

import xmp_common as xc
import xmpwrite as xw

KEEP_RATING = 2
DISCARD_RATING = 1

_SECTION_RE = re.compile(r"^#+\s*(Keeps?|Discards?)\b", re.IGNORECASE)
_ITEM_RE = re.compile(r"^(?:\d+\.|[-*])\s*\*\*(.+?)\*\*:?\s*(.*)$")
_FIELD_RE = re.compile(r"^\s*-\s*\*\*(.+?)\*\*:?\s*(.*)$")
_INLINE_CATEGORY_RE = re.compile(r"\*\*([^*]+)\*\*\s+categor", re.IGNORECASE)


def resolve_reports(path_str):
    p = Path(path_str)
    if p.is_dir():
        return sorted(p.glob("*.md"))
    if p.exists():
        return [p]
    return []


def parse_report(text):
    """Return a list of {filename, status, fields} dicts, status is
    'keep' or 'discard'. fields is a lowercased label -> value dict
    (e.g. {'category': 'Nature', 'editing suggestions': '...'})."""
    entries = []
    section = None
    current = None

    def flush():
        if current is not None:
            entries.append(current)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        sec_m = _SECTION_RE.match(line.strip())
        if sec_m:
            flush()
            current = None
            section = "keep" if sec_m.group(1).lower().startswith("keep") else "discard"
            continue

        # Item bullets/numbers start at column 0; field sub-bullets (e.g.
        # "  - **Category:** ...") are indented under them — match against
        # the unstripped line so indentation still disambiguates the two.
        item_m = _ITEM_RE.match(line)
        if item_m and section:
            flush()
            filename = item_m.group(1).strip()
            current = {"filename": filename, "status": section, "fields": {}}
            inline = item_m.group(2).strip()
            if inline:
                cat_m = _INLINE_CATEGORY_RE.search(inline)
                if cat_m:
                    current["fields"]["category"] = cat_m.group(1).strip()
            continue

        field_m = _FIELD_RE.match(line)
        if field_m and current is not None:
            label = field_m.group(1).rstrip(":").strip().lower()
            value = field_m.group(2).strip()
            current["fields"][label] = value

    flush()
    return entries


def apply_entry(entry, images_dir, no_backup, dry_run):
    filename = entry["filename"]
    sidecars = xc.resolve_sidecars(str(Path(images_dir) / filename))
    if not sidecars:
        print(f"  skip {filename}: no .xmp sidecar found in {images_dir}")
        return False
    sidecar = sidecars[0]

    old_text = xc.read_text(sidecar)
    old_data = xc.parse_xmp(sidecar)

    if entry["status"] == "keep":
        rating = KEEP_RATING
        category = entry["fields"].get("category", "")
        new_tags_list = xw.parse_tag_list(category) if category else []
        tags = list(old_data["subject"])
        for t in new_tags_list:
            if t not in tags:
                tags.append(t)
    else:
        rating = DISCARD_RATING
        tags = None

    new_text = xw.apply_updates(old_text, tags=tags, rating=rating)

    old_rating = old_data["attrs"].get("xmp:Rating", "—")
    old_tags = ", ".join(old_data["subject"]) or "—"
    new_tags_display = ", ".join(tags) if tags is not None else old_tags
    print(f"  {filename} ({entry['status']}) -> {sidecar.name}: "
          f"rating {old_rating!r} -> {rating}, tags '{old_tags}' -> '{new_tags_display}'")

    if dry_run:
        return True

    xw.save_result(sidecar, new_text, no_backup=no_backup)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Apply a culling report (Keeps/Discards + Category) to .xmp sidecars.")
    parser.add_argument("reports", nargs="+", metavar="REPORT",
                         help="a culling_report_*.md file, or a directory of them")
    parser.add_argument("--images-dir", required=True,
                         help="local folder containing the images/.xmp sidecars")
    parser.add_argument("--no-backup", action="store_true",
                         help="don't write a .bak backup before overwriting")
    parser.add_argument("--dry-run", action="store_true",
                         help="show the changes without writing anything")
    args = parser.parse_args()

    report_files = []
    for r in args.reports:
        found = resolve_reports(r)
        if not found:
            print(f"error: no report found for '{r}'", file=sys.stderr)
        report_files.extend(found)

    if not report_files:
        print("error: no report files to process", file=sys.stderr)
        sys.exit(1)

    updated, skipped = 0, 0
    for report_path in report_files:
        print(f"\n{report_path}:")
        entries = parse_report(xc.read_text(report_path))
        if not entries:
            print("  (no Keep/Discard entries found)")
            continue
        for entry in entries:
            if apply_entry(entry, args.images_dir, args.no_backup, args.dry_run):
                updated += 1
            else:
                skipped += 1

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {updated} file(s); "
          f"skipped {skipped} (no matching sidecar).")


if __name__ == "__main__":
    main()
