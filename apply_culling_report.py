#!/usr/bin/env python3
"""
apply_culling_report.py — apply a culling_helper_*.py markdown report to the
corresponding .xmp sidecars.

For each image listed in a report:
  - Keep  -> merge the Category and the three scores (as "Tech:N"/"Comp:N"/
             "Art:N" keywords) into dc:subject; set xmp:Rating from the scores
             (all three >=4 -> 3 stars, exactly two >=4 -> 2, else 1); set
             xmp:Label (Portfolio-ready -> Green, Needs edits -> Yellow); write
             Title, Description, and the Suggested Edits into
             photoshop:Instructions.
  - Discard -> set xmp:Rating = 0 and xmp:Label = Red; still record the score
             keywords and the Reason (into photoshop:Instructions).

Existing tags are preserved (Category/scores are merged in, like xmpwrite's
--add-tags; stale Tech:/Comp:/Art: keywords are refreshed in place), and every
other field in the .xmp (Camera Raw develop settings, etc.) is left untouched,
same as xmpwrite.py.

Usage:
    python3 apply_culling_report.py REPORT [REPORT ...] --images-dir DIR [options]
    python3 apply_culling_report.py testresults --images-dir testdata

REPORT may be a specific .md file or a directory (every *.md in it is used).
--images-dir is the local folder containing the images/.xmp sidecars — NOT
the "Folder:" path recorded in the report header, which reflects wherever
the report was originally generated (e.g. a different machine/drive).

Options:
    --images-dir DIR       folder containing the .xmp sidecars (required)
    --no-backup            skip writing a .bak backup before overwriting
    --dry-run              show what would change; write nothing
    --apply-develop-edits  also write the report's suggested Lightroom
                           Develop slider values (from a "Develop
                           Adjustments" field, e.g. "Contrast2012=+15,
                           Dehaze=+10") into each keep's .xmp. Off by
                           default — without this flag, develop values in
                           a report are ignored.
"""

import argparse
import re
import sys
from pathlib import Path

import xmp_common as xc
import xmpwrite as xw

DISCARD_RATING = 0

# Status text (from the report's "Status" field) -> Lightroom color label.
KEEP_LABELS = {"portfolio-ready": "Green", "needs edits": "Yellow"}
DISCARD_LABEL = "Red"

# Keyword prefixes used to store the three 1-5 scores, e.g. "Tech:4". Existing
# keywords with these prefixes are stripped before re-adding so re-running a
# report updates the scores in place instead of accumulating stale pairs.
SCORE_KEYWORD_PREFIXES = ("Tech:", "Comp:", "Art:")

# Camera Raw develop sliders the LLM is allowed to suggest values for (see
# "Develop Adjustments" in system_prompt*.txt), and each one's valid range.
# Anything outside this allowlist is ignored rather than written verbatim,
# so a report can never inject an arbitrary crs: (or other) attribute.
DEVELOP_SLIDERS = {
    "Exposure2012": (-5, 5),
    "Contrast2012": (-100, 100),
    "Highlights2012": (-100, 100),
    "Shadows2012": (-100, 100),
    "Whites2012": (-100, 100),
    "Blacks2012": (-100, 100),
    "Texture": (-100, 100),
    "Clarity2012": (-100, 100),
    "Dehaze": (-100, 100),
    "Vibrance": (-100, 100),
    "Saturation": (-100, 100),
}

_SECTION_RE = re.compile(r"^#+\s*(Keeps?|Discards?)\b", re.IGNORECASE)
_ITEM_RE = re.compile(r"^(?:\d+\.|[-*])\s*\*\*(.+?)\*\*:?\s*(.*)$")
_FIELD_RE = re.compile(r"^\s*-\s*\*\*(.+?)\*\*:?\s*(.*)$")
_INLINE_CATEGORY_RE = re.compile(r"\*\*([^*]+)\*\*\s+categor", re.IGNORECASE)
_DEVELOP_PAIR_RE = re.compile(r"([A-Za-z0-9]+)\s*=\s*([+-]?[0-9]*\.?[0-9]+)")
# Pulls the three 1-5 scores out of a Score field value like
# "Technical: 4/5, Composition: 3/5, Artistic: 3/5".
_SCORE_RE = re.compile(
    r"technical\s*:\s*(\d+).*?composition\s*:\s*(\d+).*?artistic\s*:\s*(\d+)",
    re.IGNORECASE | re.DOTALL,
)


def resolve_reports(path_str):
    p = Path(path_str)
    if p.is_dir():
        return sorted(p.glob("*.md"))
    if p.exists():
        return [p]
    return []


def _clean_field(value):
    """Return value with surrounding quotes stripped, or None if it's
    missing/empty/a placeholder like "N/A" (older reports use this for
    fields that don't apply, e.g. a discard's Title)."""
    if not value:
        return None
    value = value.strip().strip('"').strip("'").strip()
    if not value or value.lower() == "n/a":
        return None
    return value


def parse_develop_adjustments(s):
    """Parse a "SliderName=value, SliderName=value" string into a dict of
    known Camera Raw slider name -> string value, clamped to that slider's
    valid range. Unknown slider names or unparseable values are skipped
    (with a warning printed) rather than passed through."""
    result = {}
    if not s:
        return result
    for name, raw_value in _DEVELOP_PAIR_RE.findall(s):
        bounds = DEVELOP_SLIDERS.get(name)
        if bounds is None:
            print(f"    warning: ignoring unknown develop slider {name!r}")
            continue
        value = float(raw_value)
        lo, hi = bounds
        if value < lo or value > hi:
            print(f"    warning: clamping {name}={value} to [{lo}, {hi}]")
            value = max(lo, min(hi, value))
        result[name] = f"{value:.2f}" if name == "Exposure2012" else str(int(round(value)))
    return result


def parse_scores(s):
    """Parse a Score field value into (technical, composition, artistic) ints,
    or None if it can't be read (e.g. an older report with no Score line)."""
    if not s:
        return None
    m = _SCORE_RE.search(s)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def rating_from_scores(scores):
    """Map the three scores to a star rating: all three >=4 -> 3 stars,
    exactly two >=4 -> 2 stars, otherwise 1 star. A keep with no parseable
    scores counts as zero >=4, i.e. 1 star."""
    n = sum(1 for s in (scores or ()) if s >= 4)
    if n == 3:
        return 3
    if n == 2:
        return 2
    return 1


def score_keywords(scores):
    """Turn (t, c, a) into ["Tech:t", "Comp:c", "Art:a"]."""
    if not scores:
        return []
    t, c, a = scores
    return [f"Tech:{t}", f"Comp:{c}", f"Art:{a}"]


def strip_score_keywords(tags):
    """Drop any existing Tech:/Comp:/Art: keywords so re-running a report
    updates the scores in place rather than piling up stale values."""
    return [t for t in tags if not t.startswith(SCORE_KEYWORD_PREFIXES)]


def label_for(status, status_field):
    """Lightroom color label: discards are Red; keeps are Green when the
    report's Status is "Portfolio-ready", else Yellow."""
    if status == "discard":
        return DISCARD_LABEL
    key = (status_field or "").strip().lower()
    return KEEP_LABELS.get(key, "Yellow")


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


def apply_entry(entry, images_dir, no_backup, dry_run, apply_develop_edits):
    filename = entry["filename"]
    sidecars = xc.resolve_sidecars(str(Path(images_dir) / filename))
    if not sidecars:
        print(f"  skip {filename}: no .xmp sidecar found in {images_dir}")
        return False
    sidecar = sidecars[0]

    old_text = xc.read_text(sidecar)
    old_data = xc.parse_xmp(sidecar)

    scores = parse_scores(entry["fields"].get("score"))
    label = label_for(entry["status"], entry["fields"].get("status"))

    # Start from existing keywords minus any stale score keywords, then add
    # the fresh scores (for keeps and discards alike, so nothing is lost).
    tags = strip_score_keywords(list(old_data["subject"]))
    for t in score_keywords(scores):
        if t not in tags:
            tags.append(t)

    title = description = develop = None
    if entry["status"] == "keep":
        rating = rating_from_scores(scores)
        category = entry["fields"].get("category", "")
        for t in (xw.parse_tag_list(category) if category else []):
            if t not in tags:
                tags.append(t)
        title = _clean_field(entry["fields"].get("title"))
        description = _clean_field(entry["fields"].get("description"))
        instructions = _clean_field(entry["fields"].get("suggested edits"))
        if apply_develop_edits:
            develop = parse_develop_adjustments(
                entry["fields"].get("develop adjustments", "")) or None
    else:
        rating = DISCARD_RATING
        instructions = _clean_field(entry["fields"].get("reason"))

    new_text = xw.apply_updates(old_text, title=title, description=description,
                                 tags=tags, rating=rating, develop=develop,
                                 label=label, instructions=instructions)

    old_rating = old_data["attrs"].get("xmp:Rating", "—")
    old_label = old_data["attrs"].get("xmp:Label", "—")
    old_tags = ", ".join(old_data["subject"]) or "—"
    new_tags_display = ", ".join(tags) if tags is not None else old_tags
    print(f"  {filename} ({entry['status']}) -> {sidecar.name}: "
          f"rating {old_rating!r} -> {rating}, label {old_label!r} -> {label!r}, "
          f"tags '{old_tags}' -> '{new_tags_display}'")
    if title is not None:
        print(f"    title -> {title!r}")
    if description is not None:
        print(f"    description -> {description!r}")
    if instructions is not None:
        print(f"    instructions -> {instructions!r}")
    if develop:
        print(f"    develop -> {develop}")

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
    parser.add_argument("--apply-develop-edits", action="store_true",
                         help="also apply the report's suggested Lightroom "
                              "Develop slider adjustments (Contrast, "
                              "Highlights, Dehaze, etc.) to each keep's "
                              ".xmp; off by default")
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
            if apply_entry(entry, args.images_dir, args.no_backup, args.dry_run,
                            args.apply_develop_edits):
                updated += 1
            else:
                skipped += 1

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {updated} file(s); "
          f"skipped {skipped} (no matching sidecar).")


if __name__ == "__main__":
    main()
