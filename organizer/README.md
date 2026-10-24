# Photo organizer (Windows)

Five small scripts that sort a messy photo/video folder into `yyyy-mm-dd`
folders by **real capture date** (never the file's modified/created date), then
find and remove duplicates. Every step is a dry run until you pass `--apply`,
and every real change is written to a manifest CSV so it can be undone.

## One-time setup (PowerShell)

```powershell
winget install Python.Python.3.12
winget install OliverBetz.ExifTool      # reads capture dates (JPG, HEIC, CR3, MTS...)
winget install Gyan.FFmpeg              # step 2 only
winget install Ollama.Ollama            # step 3 burst check only
ollama pull qwen2.5vl:7b
pip install -r organizer\requirements-organizer.txt
```

If a tool isn't on `PATH`, pass `--exiftool C:\path\exiftool.exe` (or set the
`EXIFTOOL` / `FFMPEG` / `FFPROBE` environment variables).

## Workflow

Try it on a **small copy** of your photos first.

```powershell
# 1. Organize. Review DEST\_reports\step1_plan_*.csv, then run with --apply.
python organizer\step1_organize.py E:\Photos\Inbox E:\Photos\Organized
python organizer\step1_organize.py E:\Photos\Inbox E:\Photos\Organized --apply
#    Changed your mind?  python organizer\step1_organize.py --undo E:\Photos\Organized\_reports\step1_manifest_<ts>.csv --apply

# 2. (optional) Convert camcorder .MTS/.M2TS/.MOD to .mp4
python organizer\step2_convert_videos.py E:\Photos\Organized --apply

# 3. Find duplicates (read-only). Open the .html contact sheet; edit the CSV `role` column if you disagree.
python organizer\step3_find_duplicates.py E:\Photos\Organized

# 4. Move duplicates to DEST\_duplicates (undoable)
python organizer\step4_quarantine.py E:\Photos\Organized\_reports\duplicates_<ts>.csv --apply

# 5. After reviewing _duplicates, delete for good (asks you to type DELETE)
python organizer\step5_purge.py E:\Photos\Organized
```

## How the capture date is chosen (step 1)

First valid value wins: EXIF `DateTimeOriginal` -> QuickTime/iPhone `CreationDate`
-> `CreateDate` -> XMP/IPTC dates -> QuickTime `CreateDate` (UTC) -> the Sony
`.modd` sidecar -> a date inside an `.xmp` sidecar -> a date in the file name
(`IMG_20230514_...`, `PXL_...`, `VID-20230514-WA...`). Dates before 1990, in the
future, or "clock never set" values like 2000-01-01 are ignored. Files with
no trustworthy date go to `_undated\<original sub-path>`. The plan CSV shows
the ignored filesystem date in `fs_mtime_ignored` for reference only.

Sidecars (`.xmp`, `.acr`, `.aae`, `.thm`, `.modd`, `.moff`, `.mdi`, `.moi`) move with
their photo/video and are renamed to match. If a name already exists, the new
file gets ` (1)`, ` (2)`...; if it is byte-identical it goes to `_duplicates`.
AVCHD clips (`00001.MTS`) are renamed `yyyy-mm-dd_HHMMSS.mts` so they don't collide.
Leftover AVCHD structure files (`.CPI`, `.MPL`, `.BDM`) are not moved.

## Sony camcorder videos (step 2)

Recommended: organize first (step 1), then convert. H.264 AVCHD clips are
**remuxed** to MP4 with no quality loss (audio becomes AAC); MPEG-2 `.MOD/.TOD`
files are re-encoded to H.264 and deinterlaced. The MP4 is checked against the
source duration before the original (plus `.modd/.moff/.mdi`) is moved to
`_video_originals\<date>\`. Nothing is deleted. Use `--keep-originals` to leave them.

## Duplicate detection (step 3)

Per date folder only:

1. **exact** - identical bytes (SHA-256), photos and videos.
2. **visual** - same picture re-saved/resized (perceptual hash). A RAW and its JPG
   are *not* treated as duplicates unless you pass `--collapse-raw-jpg`.
3. **burst** - shots within `--burst-seconds` (default 3) that look alike go to
   Ollama, which decides whether they are the same moment and picks the best.
   Skip with `--no-ollama`.

Photos with an XMP rating >= 1 or a Pick flag are never marked as duplicates.
Results of the slow parts are cached in `_reports\.cache`, so re-runs are quick.

## Tests

```powershell
python -m unittest discover -s organizer\tests
```
