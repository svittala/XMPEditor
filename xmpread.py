#!/usr/bin/env python3
"""
xmpread.py — view the key metadata inside an Adobe XMP sidecar file as a tree.

Usage:
    python3 xmpread.py PATH [PATH ...] [--json]

PATH may be:
  - an image file (e.g. OV7A2905.CR3) — its sibling OV7A2905.xmp is used
  - a .xmp file directly
  - a directory — every *.xmp file in it is shown

Examples:
    python3 xmpread.py photos/OV7A2905.CR3
    python3 xmpread.py photos/OV7A2905.xmp --json
    python3 xmpread.py photos/
"""

import argparse
import json
import sys

import xmp_common as xc


def build_groups(data):
    attrs = data["attrs"]

    file_group = [
        ("Sidecar", data["path"]),
        ("Source Image", attrs.get("xmpMM:PreservedFileName")
            or (f"(*.{attrs['photoshop:SidecarForExtension']})"
                if attrs.get("photoshop:SidecarForExtension") else None)),
        ("Dimensions", f"{attrs['tiff:ImageWidth']}×{attrs['tiff:ImageLength']}"
            if attrs.get("tiff:ImageWidth") and attrs.get("tiff:ImageLength") else None),
    ]

    camera_group = [
        ("Make", attrs.get("tiff:Make")),
        ("Model", attrs.get("tiff:Model")),
        ("Lens", attrs.get("aux:Lens") or attrs.get("exifEX:LensModel")),
        ("Serial", attrs.get("aux:SerialNumber")),
        ("Firmware", attrs.get("aux:Firmware")),
    ]

    exposure_group = [
        ("Shutter", xc.shutter(attrs)),
        ("Aperture", xc.f_number(attrs)),
        ("ISO", xc.iso_value(data)),
        ("Focal Length", xc.focal_length(attrs)),
        ("Exposure Bias", xc.exposure_bias(attrs)),
        ("Metering Mode", xc.metering_mode(attrs)),
        ("White Balance", xc.white_balance_exif(attrs)),
        ("Flash", xc.flash_label(data)),
    ]

    dates_group = [
        ("Date Taken", attrs.get("exif:DateTimeOriginal") or attrs.get("xmp:CreateDate")),
        ("Metadata Modified", attrs.get("xmp:MetadataDate")),
    ]

    rating_group = [
        ("Rating", xc.stars(attrs)),
        ("Pick", xc.pick_label(attrs)),
        ("Title", data["title"]),
        ("Description", data["description"]),
        ("Creator", ", ".join(data["creator"]) if data["creator"] else None),
        ("Copyright", data["rights"]),
        ("Keywords", ", ".join(data["subject"]) if data["subject"] else None),
    ]

    develop_group = [
        ("Version", f"{attrs['crs:Version']} (Process {attrs['crs:ProcessVersion']})"
            if attrs.get("crs:Version") else None),
        ("White Balance", attrs.get("crs:WhiteBalance")),
        ("Exposure", attrs.get("crs:Exposure2012")),
        ("Contrast", attrs.get("crs:Contrast2012")),
        ("Highlights", attrs.get("crs:Highlights2012")),
        ("Shadows", attrs.get("crs:Shadows2012")),
        ("Whites", attrs.get("crs:Whites2012")),
        ("Blacks", attrs.get("crs:Blacks2012")),
        ("Texture", attrs.get("crs:Texture")),
        ("Clarity", attrs.get("crs:Clarity2012")),
        ("Dehaze", attrs.get("crs:Dehaze")),
        ("Vibrance", attrs.get("crs:Vibrance")),
        ("Saturation", attrs.get("crs:Saturation")),
    ]

    return [
        ("File", file_group),
        ("Camera & Lens", camera_group),
        ("Exposure", exposure_group),
        ("Dates", dates_group),
        ("Rating & Tags", rating_group),
        ("Develop (Camera Raw)", develop_group),
    ]


def render_tree(path, groups):
    lines = [str(path)]
    for gi, (group_name, items) in enumerate(groups):
        last_group = gi == len(groups) - 1
        branch = "└─" if last_group else "├─"
        lines.append(f"{branch} {group_name}")
        cont = "   " if last_group else "│  "
        for ii, (label, value) in enumerate(items):
            last_item = ii == len(items) - 1
            item_branch = "└─" if last_item else "├─"
            shown = value if value not in (None, "") else "—"
            lines.append(f"{cont}{item_branch} {label}: {shown}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="View key metadata from Adobe .xmp sidecar files as a tree.")
    parser.add_argument("paths", nargs="+", metavar="PATH",
                         help="image file, .xmp file, or directory")
    parser.add_argument("--json", action="store_true",
                         help="print raw + derived metadata as JSON instead of a tree")
    args = parser.parse_args()

    results = []
    exit_code = 0
    for path_str in args.paths:
        sidecars = xc.resolve_sidecars(path_str)
        if not sidecars:
            print(f"error: no .xmp sidecar found for '{path_str}'", file=sys.stderr)
            exit_code = 1
            continue
        for sidecar in sidecars:
            try:
                data = xc.parse_xmp(sidecar)
            except Exception as e:
                print(f"error: failed to parse '{sidecar}': {e}", file=sys.stderr)
                exit_code = 1
                continue
            results.append(data)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for i, data in enumerate(results):
            if i:
                print()
            print(render_tree(data["path"], build_groups(data)))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
