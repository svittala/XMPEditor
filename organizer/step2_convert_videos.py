"""
Step 2 (optional) - convert camcorder videos in DEST\\yyyy-mm-dd\\ to MP4.

    python step2_convert_videos.py DEST             # dry run
    python step2_convert_videos.py DEST --apply

Handles AVCHD .mts/.m2ts (H.264) and older .mod/.tod (MPEG-2):
  * H.264 video is remuxed with no re-encoding (fast, lossless); audio becomes AAC.
  * MPEG-2 (or --reencode) is re-encoded to H.264 (CRF 18) and deinterlaced.
The MP4 is verified (duration within 1 s of the source) before the original and
its .modd/.moff/.mdi sidecars are moved to DEST\\_video_originals\\<date>\\.
Nothing is deleted. Requires ffmpeg + ffprobe (winget install Gyan.FFmpeg).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

CONVERT_EXTS = {".mts", ".m2ts", ".mod", ".tod"}
_NAME_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})$")


def probe(ffprobe, path):
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format",
         "-show_streams", str(path)], capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip())
    return json.loads(proc.stdout.decode("utf-8"))


def duration_of(info):
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return 0.0


def stream_codec(info, kind):
    for s in info.get("streams", []):
        if s.get("codec_type") == kind:
            return s.get("codec_name")
    return None


def capture_datetime(path):
    """Date from the folder name (set by step 1); time from the file name when
    step 1 renamed it to yyyy-mm-dd_HHMMSS."""
    m = _NAME_TIME_RE.match(path.stem)
    if m:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}",
                                 "%Y-%m-%d %H:%M:%S")
    try:
        return datetime.strptime(path.parent.name, "%Y-%m-%d")
    except ValueError:
        return None


def build_command(ffmpeg, src, tmp, info, reencode, creation):
    vcodec = stream_codec(info, "video")
    acodec = stream_codec(info, "audio")
    copy_video = (vcodec == "h264") and not reencode
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", str(src), "-map", "0:v:0", "-map", "0:a?"]
    if copy_video:
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "slow",
                "-vf", "yadif", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "copy" if acodec == "aac" else "aac"]
    if acodec != "aac":
        cmd += ["-b:a", "192k"]
    cmd += ["-movflags", "+faststart"]
    if creation:
        cmd += ["-metadata", "creation_time=" + creation.strftime("%Y-%m-%dT%H:%M:%S")]
    cmd += ["-f", "mp4", str(tmp)]
    return cmd, copy_video


def find_candidates(dest, only_folder=None):
    dest = Path(dest)
    for folder in sorted(p for p in dest.iterdir() if p.is_dir()):
        if folder.name in c.RESERVED_DIRS:
            continue
        if only_folder and folder.name != only_folder:
            continue
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in CONVERT_EXTS:
                yield f


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dest", nargs="?")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--reencode", action="store_true",
                    help="always re-encode to H.264 instead of copying the video stream")
    ap.add_argument("--keep-originals", action="store_true",
                    help="leave the originals where they are")
    ap.add_argument("--folder", help="only this yyyy-mm-dd folder")
    ap.add_argument("--ffmpeg")
    ap.add_argument("--ffprobe")
    ap.add_argument("--undo", metavar="MANIFEST")
    args = ap.parse_args(argv)

    if args.undo:
        c.undo_manifest(args.undo, args.apply)
        return 0
    if not args.dest:
        ap.error("DEST is required")
    dest = Path(args.dest)
    if not dest.is_dir():
        ap.error(f"not a folder: {dest}")

    ffmpeg = c.find_tool("ffmpeg", args.ffmpeg, "FFMPEG")
    ffprobe = c.find_tool("ffprobe", args.ffprobe, "FFPROBE")
    if not ffmpeg or not ffprobe:
        print("ERROR: ffmpeg/ffprobe not found. Install with: winget install Gyan.FFmpeg")
        return 2

    files = list(find_candidates(dest, args.folder))
    print(f"{len(files)} video(s) to convert.")
    manifest = None
    if args.apply:
        manifest = c.Manifest(dest / c.REPORTS_DIR / f"step2_manifest_{c.timestamp()}.csv")

    ok = failed = skipped = 0
    try:
        for src in files:
            out = src.with_suffix(".mp4")
            try:
                info = probe(ffprobe, src)
                src_dur = duration_of(info)
                if out.exists():
                    if abs(duration_of(probe(ffprobe, out)) - src_dur) <= 1.0:
                        print(f"  already converted: {src.name}")
                        skipped += 1
                        continue
                    out = c.unique_path(out)
                tmp = out.with_name(out.name + ".part")
                creation = capture_datetime(src)
                cmd, copied = build_command(ffmpeg, src, tmp, info, args.reencode, creation)
                print(f"  {'convert' if args.apply else 'would convert'} "
                      f"({'remux' if copied else 're-encode'}): {src.name} -> {out.name}")
                if not args.apply:
                    continue

                proc = subprocess.run(cmd, capture_output=True)
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[-500:])
                new_dur = duration_of(probe(ffprobe, tmp))
                if abs(new_dur - src_dur) > 1.0:
                    raise RuntimeError(f"duration mismatch: {src_dur:.1f}s vs {new_dur:.1f}s")
                os.replace(c.lp(tmp), c.lp(out))
                if creation:
                    ts = creation.timestamp()
                    os.utime(c.lp(out), (ts, ts))
                manifest.add("create", src, out, "", os.path.getsize(c.lp(out)),
                             "remux" if copied else "re-encode")

                if not args.keep_originals:
                    orig_dir = dest / c.VIDEO_ORIGINALS_DIR / src.parent.name
                    siblings = c.siblings_of(src.parent)
                    for f in [src] + c.sidecars_for(src, siblings):
                        target = c.unique_path(orig_dir / f.name)
                        size = os.path.getsize(c.lp(f))
                        manifest.add("move", f, target, c.safe_move(f, target), size,
                                     "original after mp4")
                ok += 1
            except Exception as ex:
                failed += 1
                print(f"  FAILED {src.name}: {ex}")
                tmp = src.with_suffix(".mp4.part")
                if tmp.exists():
                    tmp.unlink()
    finally:
        if manifest:
            manifest.close()

    print(f"\nConverted {ok}, already done {skipped}, failed {failed}.")
    if not args.apply:
        print("DRY RUN - re-run with --apply to convert.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
