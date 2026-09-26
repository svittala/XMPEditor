#!/usr/bin/env python3
"""
xmpwrite.py — update Title, Description, Tags, Rating, and Pick in an Adobe
XMP sidecar file, in place.

Edits are made as surgical text patches (not a full XML re-serialization), so
every other field in the file — all the Camera Raw "crs:" develop settings,
attribute order, indentation — is left byte-for-byte untouched.

Usage:
    python3 xmpwrite.py PATH [options]

Options:
    --title TEXT           Set dc:title
    --description TEXT     Set dc:description (can be a long paragraph)
    --tags "a,b,c"          Replace dc:subject (keywords/categories) entirely
    --add-tags "a,b,c"      Merge into existing dc:subject (no duplicates)
    --rating {0,1,2,3,4,5}  Set xmp:Rating
    --pick {-1,0,1}         Set xmpDM:pick (-1 reject, 0 unflagged, 1 pick)
    --out FILE              Write the result to FILE instead of overwriting PATH
    --no-backup             Skip writing a PATH.bak backup before overwriting
    --dry-run               Show what would change; write nothing

Examples:
    python3 xmpwrite.py photos/OV7A2905.xmp --title "Lake Reflection" \\
        --description "A calm lake at sunrise with mirrored clouds." \\
        --tags "Landscape,Water" --rating 4 --pick 1

    python3 xmpwrite.py photos/OV7A2905.CR3 --add-tags "Sunset" --dry-run
"""

import argparse
import datetime
import difflib
import re
import shutil
import sys
from pathlib import Path

import xmp_common as xc

# ---------------------------------------------------------------------------
# Low-level text surgery
#
# XMP files contain many nested <rdf:Description> elements (used for
# develop-setting structs like crs:CorrectionMasks / crs:MapPolynomial), not
# just the top-level one that holds the image's own attributes and dc:/xmp:
# children. Every edit below is scoped to that top-level element via
# xc.find_root_description_span() so it never touches the nested structs.
# ---------------------------------------------------------------------------

def set_attribute(text, name, value):
    """Set an attribute on the top-level <rdf:Description> open tag,
    replacing it if present, else appending a new indented attribute line
    before the '>'."""
    start, end, _, _ = xc.find_root_description_span(text)
    open_tag = text[start:end]

    escaped = xc.esc_attr(str(value))
    pattern = re.compile(r"(?<=[\s])" + re.escape(name) + r'\s*=\s*"[^"]*"')
    if pattern.search(open_tag):
        new_open_tag = pattern.sub(f'{name}="{escaped}"', open_tag, count=1)
    else:
        assert open_tag.endswith(">")
        new_open_tag = open_tag[:-1] + f'\n   {name}="{escaped}">'

    return text[:start] + new_open_tag + text[end:]


def _replace_or_insert_element(text, tag, new_block):
    """Replace an existing <tag>...</tag> block that is a direct child of the
    top-level <rdf:Description> (any indentation), or, if absent, insert
    new_block on its own line just before that element's closing tag."""
    _, open_end, close_start, _ = xc.find_root_description_span(text)
    inner = text[open_end:close_start]

    pattern = re.compile(
        r"[ \t]*<" + re.escape(tag) + r">.*?</" + re.escape(tag) + r">\n?",
        re.DOTALL,
    )
    m = pattern.search(inner)
    if m:
        abs_start, abs_end = open_end + m.start(), open_end + m.end()
        return text[:abs_start] + new_block + "\n" + text[abs_end:]

    line_start = text.rfind("\n", 0, close_start) + 1
    return text[:line_start] + new_block + "\n" + text[line_start:]


def set_title_or_description(text, tag, value):
    block = (
        f"   <{tag}>\n"
        f"    <rdf:Alt>\n"
        f'     <rdf:li xml:lang="x-default">{xc.esc_text(value)}</rdf:li>\n'
        f"    </rdf:Alt>\n"
        f"   </{tag}>"
    )
    return _replace_or_insert_element(text, tag, block)


def set_tags(text, tags):
    items = "\n".join(f"     <rdf:li>{xc.esc_text(t)}</rdf:li>" for t in tags)
    block = (
        "   <dc:subject>\n"
        "    <rdf:Bag>\n"
        f"{items}\n"
        "    </rdf:Bag>\n"
        "   </dc:subject>"
    )
    return _replace_or_insert_element(text, "dc:subject", block)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def apply_updates(text, title=None, description=None, tags=None,
                   rating=None, pick=None, touch_metadata_date=True):
    if title is not None:
        text = set_title_or_description(text, "dc:title", title)
    if description is not None:
        text = set_title_or_description(text, "dc:description", description)
    if tags is not None:
        text = set_tags(text, tags)
    if rating is not None:
        text = set_attribute(text, "xmp:Rating", rating)
    if pick is not None:
        text = set_attribute(text, "xmpDM:pick", pick)
    if touch_metadata_date and any(v is not None for v in (title, description, tags, rating, pick)):
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        text = set_attribute(text, "xmp:MetadataDate", now)
    return text


def parse_tag_list(s):
    return [t.strip() for t in s.split(",") if t.strip()]


def backup_path_for(path):
    path = Path(path)
    candidate = path.with_name(path.name + ".bak")
    if not candidate.exists():
        return candidate
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.name}.{stamp}.bak")


def main():
    parser = argparse.ArgumentParser(
        description="Update Title, Description, Tags, Rating, and Pick in an "
                     "Adobe .xmp sidecar file.")
    parser.add_argument("path", metavar="PATH",
                         help="image file or .xmp file (a single sidecar)")
    parser.add_argument("--title")
    parser.add_argument("--description")
    parser.add_argument("--tags", metavar="a,b,c",
                         help="replace keywords entirely, comma-separated")
    parser.add_argument("--add-tags", metavar="a,b,c",
                         help="merge these keywords into the existing set")
    parser.add_argument("--rating", type=int, choices=[0, 1, 2, 3, 4, 5])
    parser.add_argument("--pick", type=int, choices=[-1, 0, 1])
    parser.add_argument("--out", metavar="FILE",
                         help="write result to FILE instead of overwriting PATH")
    parser.add_argument("--no-backup", action="store_true",
                         help="don't write a .bak backup before overwriting")
    parser.add_argument("--dry-run", action="store_true",
                         help="show the changes without writing anything")
    args = parser.parse_args()

    sidecars = xc.resolve_sidecars(args.path)
    if not sidecars:
        print(f"error: no .xmp sidecar found for '{args.path}'", file=sys.stderr)
        sys.exit(1)
    if len(sidecars) > 1:
        print(f"error: '{args.path}' is a directory with multiple .xmp files; "
              f"pass a single image or .xmp file", file=sys.stderr)
        sys.exit(1)
    sidecar = sidecars[0]

    if all(v is None for v in (args.title, args.description, args.tags,
                                args.add_tags, args.rating, args.pick)):
        print("error: no changes requested (use --title/--description/--tags/"
              "--add-tags/--rating/--pick)", file=sys.stderr)
        sys.exit(1)

    old_text = xc.read_text(sidecar)
    old_data = xc.parse_xmp(sidecar)

    tags = None
    if args.tags is not None:
        tags = parse_tag_list(args.tags)
    elif args.add_tags is not None:
        existing = old_data["subject"]
        merged = list(existing)
        for t in parse_tag_list(args.add_tags):
            if t not in merged:
                merged.append(t)
        tags = merged

    new_text = apply_updates(
        old_text,
        title=args.title,
        description=args.description,
        tags=tags,
        rating=args.rating,
        pick=args.pick,
    )

    changes = []
    if args.title is not None:
        changes.append(("Title", old_data["title"], args.title))
    if args.description is not None:
        changes.append(("Description", old_data["description"], args.description))
    if tags is not None:
        changes.append(("Tags", ", ".join(old_data["subject"]) or "—", ", ".join(tags) or "—"))
    if args.rating is not None:
        changes.append(("Rating", old_data["attrs"].get("xmp:Rating", "—"), args.rating))
    if args.pick is not None:
        changes.append(("Pick", old_data["attrs"].get("xmpDM:pick", "—"), args.pick))

    print(f"{sidecar}:")
    for label, before, after in changes:
        print(f"  {label}: {before!r} -> {after!r}")

    if args.dry_run:
        print("\n--- dry run: no files written ---")
        diff = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(sidecar), tofile=str(sidecar),
        )
        sys.stdout.writelines(diff)
        return

    save_result(sidecar, new_text, out=args.out, no_backup=args.no_backup)


def save_result(sidecar, new_text, out=None, no_backup=False):
    """Write new_text for sidecar, backing up the original first unless
    told not to (or unless writing to a different --out path). Used by
    xmpwrite's own CLI and by other scripts (e.g. apply_culling_report.py)
    that batch-update sidecars."""
    sidecar = Path(sidecar)
    target = Path(out) if out else sidecar
    if target == sidecar and not no_backup:
        backup = backup_path_for(sidecar)
        shutil.copy2(sidecar, backup)
        print(f"  backup written to {backup}")

    xc.write_text_atomic(target, new_text)
    print(f"  wrote {target}")
    return target


if __name__ == "__main__":
    main()
