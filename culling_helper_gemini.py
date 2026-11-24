#!/usr/bin/env python3
import argparse
import io
import json
import os
import glob
from datetime import datetime
from google import genai
from PIL import Image
from dotenv import load_dotenv

# ==========================================
# Configuration
# ==========================================
# Install dependencies:
#   pip install google-genai pillow

# API key: env var GEMINI_API_KEY takes priority; hardcoded value is the fallback.
# Best practice: set GEMINI_API_KEY in your environment and leave the line below as None.
API_KEY = None
MODEL_NAME = "gemini-3.8-flash"  # or "gemini-3.8-flash-image" for image-specific tasks
BATCH_SIZE = 10                  # images per API call

# Token optimisation: Gemini bills 258 tokens per 768×768 tile.
# Capping the long edge at 1024 px keeps each photo to ~2 tiles (~516 tokens)
# instead of 20–50 tiles for a full-resolution image — a 10–25× saving.
MAX_IMAGE_PX = 1024   # long-edge cap before sending to the API (pixels)
JPEG_QUALITY = 85     # JPEG quality used when re-encoding for the API (75–90 is fine)

DEFAULT_PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt_gemini.txt")


# ==========================================
# Helpers
# ==========================================

def load_system_prompt(path):
    """Reads the system prompt text from an external file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def get_image_files(folder_path):
    """Finds all common image files in the given directory."""
    extensions = ('*.jpg', '*.jpeg', '*.png')
    files = []
    for ext in extensions:
        files.extend(glob.glob(os.path.join(folder_path, ext)))
        files.extend(glob.glob(os.path.join(folder_path, ext.upper())))
    return sorted(set(files))   # set() de-dupes if both cases matched on a case-insensitive FS


def prepare_image_for_api(filepath):
    """
    Opens an image, resizes it so the long edge is at most MAX_IMAGE_PX,
    and returns a PIL Image object ready to pass to generate_content().

    Why resize?
      Gemini Vision charges 258 tokens per 768×768 tile.
      A 6000×4000 photo → ~44 tiles → ~11,400 tokens.
      The same photo capped at 1024 px → 2 tiles → ~516 tokens.
      With 10 images per batch that's the difference between ~114k and ~5k input tokens.
    """
    with Image.open(filepath) as img:
        img.load()   # force eager load so the file handle can close

    # Convert to RGB so JPEG encoding always works (handles RGBA/P-mode PNGs)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Resize only if larger than the cap
    w, h = img.size
    if max(w, h) > MAX_IMAGE_PX:
        scale = MAX_IMAGE_PX / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)

    # Re-encode as JPEG in memory so Gemini receives a compact, well-defined format
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    buf.seek(0)
    return Image.open(buf)


# ==========================================
# Core batch processing
# ==========================================

def parse_json_response(text):
    """Extracts and parses a JSON array from the model's response text."""
    # Strip markdown code fences if the model wrapped the JSON anyway
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def render_report(results, batch_num, folder_path, date_str, model_name):
    """Converts the parsed JSON results into a readable markdown report and saves it."""
    keeps    = [r for r in results if r.get("keep")]
    discards = [r for r in results if not r.get("keep")]

    lines = []
    lines.append(f"# Photography Culling Report — Batch {batch_num}")
    lines.append(f"**Model:** `{model_name}`  |  **Date:** {date_str}")
    lines.append(f"**Folder:** `{folder_path}`\n")
    lines.append(f"**Summary:** {len(keeps)} keep(s), {len(discards)} discard(s) "
                 f"({len(keeps) + len(discards)} evaluated)\n")
    lines.append("---\n")

    lines.append("# Keeps\n")
    for i, r in enumerate(keeps, 1):
        pr = "✅ Portfolio ready" if r.get("portfolio_ready") else "🔧 Needs edits"
        lines.append(f"## {i}. {r['filename']}  —  {pr}")
        lines.append(f"- **Scores:** Technical {r['technical_score']}/5  |  "
                     f"Composition {r['composition_score']}/5  |  "
                     f"Artistic {r['artistic_score']}/5")
        lines.append(f"- **Category:** {r.get('category', '—')}")
        lines.append(f"- **Title:** {r.get('title', '—')}")
        lines.append(f"- **Description:** {r.get('description', '—')}")
        lines.append(f"- **Editing Suggestions:** {r.get('editing_suggestions', '—')}")
        lines.append(f"- **Develop Adjustments:** `{r.get('develop_adjustments', '—')}`\n")

    if not keeps:
        lines.append("*No keeps in this batch.*\n")

    lines.append("---\n")
    lines.append("# Discards\n")
    for i, r in enumerate(discards, 1):
        lines.append(f"## {i}. {r['filename']}")
        lines.append(f"- **Scores:** Technical {r['technical_score']}/5  |  "
                     f"Composition {r['composition_score']}/5  |  "
                     f"Artistic {r['artistic_score']}/5")
        lines.append(f"- **Reason:** {r.get('reason', '—')}\n")

    if not discards:
        lines.append("*No discards in this batch.*\n")

    report_filename = f"culling_report_gemini_{date_str}_batch_{batch_num}.md"
    report_filepath = os.path.join(folder_path, report_filename)
    with open(report_filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  Report saved: {report_filepath}")
    print(f"  Keeps: {len(keeps)}  |  Discards: {len(discards)}")
    return report_filepath


def process_batch(batch_files, batch_num, folder_path, date_str, client, model_name, system_prompt):
    """Prepares a batch of images, calls Gemini, parses JSON, and saves the report."""
    print(f"\nProcessing Batch {batch_num} ({len(batch_files)} images)...")

    filenames = [os.path.basename(f) for f in batch_files]

    # Build contents: [system_prompt, img1, img2, ..., filename_mapping]
    contents = [system_prompt]
    for file in batch_files:
        contents.append(prepare_image_for_api(file))
    contents.append(
        f"The images provided above are in the following order: {', '.join(filenames)}."
    )

    print("Waiting for Gemini to analyze images...")
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents
        )
        raw_text = response.text
    except Exception as e:
        print(f"  Error communicating with Gemini: {e}")
        return

    # Parse JSON response
    try:
        results = parse_json_response(raw_text)
    except json.JSONDecodeError as e:
        print(f"  Warning: Could not parse JSON response ({e}). Saving raw response instead.")
        # Fallback: save the raw text so nothing is lost
        fallback = os.path.join(folder_path,
                                f"culling_report_gemini_{date_str}_batch_{batch_num}_raw.txt")
        with open(fallback, "w", encoding="utf-8") as f:
            f.write(raw_text)
        print(f"  Raw response saved: {fallback}")
        return

    render_report(results, batch_num, folder_path, date_str, model_name)


# ==========================================
# Entry point
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Batch photo culling helper using the Gemini vision model.")
    parser.add_argument("folder", nargs="?",
                        help="folder containing images to review (prompted if omitted)")
    parser.add_argument("--prompt-file", "-p", default=DEFAULT_PROMPT_FILE,
                        help=f"text file with the system prompt (default: {DEFAULT_PROMPT_FILE})")
    args = parser.parse_args()

    # Load system prompt
    try:
        system_prompt = load_system_prompt(args.prompt_file)
    except OSError as e:
        print(f"Error: could not read prompt file '{args.prompt_file}': {e}")
        return

        # Resolve API key — env var takes priority over the hardcoded fallback
    api_key = os.environ.get("GEMINI_API_KEY") or API_KEY
    if not api_key:
        print("Error: No Gemini API key found.")
        print("  Set the GEMINI_API_KEY environment variable, or edit API_KEY in this script.")
        print("  In PowerShell, run: $env:GEMINI_API_KEY=\"your_key_here\"")
        print("  In Command Prompt, run: set GEMINI_API_KEY=your_key_here")
        return

    client = genai.Client(api_key=api_key)

    # Get folder
    folder_path = args.folder or input("Enter the folder path containing the images: ").strip()
    if folder_path.startswith('"') and folder_path.endswith('"'):
        folder_path = folder_path[1:-1]

    if not os.path.isdir(folder_path):
        print(f"Error: '{folder_path}' is not a valid directory.")
        return

    images = get_image_files(folder_path)
    if not images:
        print("No images (.jpg, .jpeg, .png) found in the specified folder.")
        return

    print(f"Found {len(images)} images  |  Batch size: {BATCH_SIZE}  |  Model: {MODEL_NAME}")
    print(f"Images will be resized to max {MAX_IMAGE_PX}px before sending (token optimisation).")
    print(f"Prompt file: {args.prompt_file}\n")

    date_str = datetime.now().strftime("%Y-%m-%d")
    batches  = [images[i:i + BATCH_SIZE] for i in range(0, len(images), BATCH_SIZE)]

    for i, batch in enumerate(batches, start=1):
        process_batch(batch, i, folder_path, date_str, client, MODEL_NAME, system_prompt)

    print("\nAll batches processed successfully!")


if __name__ == "__main__":
    main()
