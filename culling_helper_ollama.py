#!/usr/bin/env python3
import argparse
import os
import glob
import base64
import json
import urllib.request
import urllib.error
import concurrent.futures
from datetime import datetime

# ==========================================
# Configuration
# ==========================================
# Ensure you have a vision-capable model pulled in Ollama, e.g., 'ollama pull llava'
OLLAMA_MODEL = "qwen2.5vl:7b"
# older version - llava
OLLAMA_URL = "http://localhost:11434/api/generate"
BATCH_SIZE = 10  # Images per markdown report
# On 8GB RAM a 7B vision model fits only one inference at a time, so keep this
# low: extra workers just overlap encoding/setup, they don't run models in
# parallel. 2 is a safe default; raise via --workers only if you have headroom.
MAX_WORKERS = 2
# One image per request now, so we need far less context than a 5-image batch.
# Smaller context = less memory pressure, which matters on 8GB.
OLLAMA_NUM_CTX = 8192

# The persona/instructions for the LLM live in an external text file so you
# can run different batches with different prompts via --prompt-file.
DEFAULT_PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt")


def load_system_prompt(path):
    """Reads the system prompt text from an external file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

def get_base64_image(image_path):
    """Reads an image file and returns its base64 encoded string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def check_ollama_ready():
    """Checks if Ollama is running and warms up the model."""
    print("Checking if Ollama is ready...")
    try:
        # Check if the service is up and list models
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req) as response:
            tags_data = json.loads(response.read().decode('utf-8'))
            
        models = [model['name'] for model in tags_data.get('models', [])]
        if not any(OLLAMA_MODEL in m or m.startswith(OLLAMA_MODEL) for m in models):
            print(f"Warning: Model '{OLLAMA_MODEL}' might not be pulled. You may need to run 'ollama pull {OLLAMA_MODEL}'")
            
        print("Sending a warm-up request to load the model into memory (this may take a moment)...")
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": "Hello",
            "stream": False,
            "options": {
                "num_ctx": OLLAMA_NUM_CTX
            }
        }
        data = json.dumps(payload).encode('utf-8')
        warmup_req = urllib.request.Request(OLLAMA_URL, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(warmup_req) as warmup_resp:
            pass # Model is now loaded
            
        print("Ollama is ready and model is loaded!")
        return True
    except urllib.error.HTTPError as e:
        print(f"Ollama HTTP Error during readiness check: {e.code} {e.reason}")
        print(f"Details: {e.read().decode('utf-8')}")
        return False
    except urllib.error.URLError as e:
        print(f"Error: Could not connect to Ollama. Is it running? Details: {e.reason}")
        return False

def get_image_files(folder_path):
    """Finds all common image files in the given directory."""
    extensions = ('*.jpg', '*.jpeg', '*.png')
    files = set()
    for ext in extensions:
        files.update(glob.glob(os.path.join(folder_path, ext)))
        # Also check uppercase extensions
        files.update(glob.glob(os.path.join(folder_path, ext.upper())))
    # Sort files alphabetically to ensure consistent batching
    return sorted(list(files))

# JSON schema forcing a single, self-contained verdict per image. Sending one
# image per request (below) is what actually prevents cross-image bleed; the
# schema just keeps the output structured so we can merge it ourselves.
IMAGE_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "portfolio_ready": {"type": "boolean"},
        "technical_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "composition_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "artistic_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "category": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "editing_suggestions": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["keep", "technical_score", "composition_score", "artistic_score"],
}


def analyze_image(image_path, system_prompt):
    """Sends ONE image to Ollama in its own request and returns the parsed verdict.

    Each call is fully independent: /api/generate keeps no state between requests
    (we never pass `context` back), so nothing from a previous image can leak in.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": system_prompt,
        "images": [get_base64_image(image_path)],
        "stream": False,
        "format": IMAGE_RESULT_SCHEMA,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,
            # A fresh, low-temperature pass per image; keeps verdicts deterministic.
            "temperature": 0,
        },
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(OLLAMA_URL, data=data, headers={'Content-Type': 'application/json'})

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8')
        print(f"HTTP Error communicating with Ollama: {e.code} {e.reason}")
        print(f"Details from Ollama: {error_msg}")
        return None
    except urllib.error.URLError as e:
        print(f"Error communicating with Ollama: {e.reason}")
        return None
    except json.JSONDecodeError:
        print("Error: Received invalid JSON response from Ollama.")
        return None

    try:
        return json.loads(result.get('response', '') or '{}')
    except json.JSONDecodeError:
        print(f"Warning: model returned non-JSON for {os.path.basename(image_path)}; skipping.")
        return None


def render_report(results, batch_num, folder_path, date_str, filenames):
    """Builds the Keeps/Discards markdown report from per-image verdicts."""
    def score_line(verdict):
        def s(key):
            val = verdict.get(key)
            return val if isinstance(val, int) else "?"
        return (
            f"   - **Score:** Technical: {s('technical_score')}/5, "
            f"Composition: {s('composition_score')}/5, "
            f"Artistic: {s('artistic_score')}/5\n"
        )

    keeps, discards = [], []
    for filename, verdict in results:
        if verdict is None:
            discards.append(f"1. **{filename}**\n   - **Reason:** analysis failed (no valid response)\n")
        elif verdict.get("keep"):
            status = "Portfolio-ready" if verdict.get("portfolio_ready") else "Needs edits"
            keeps.append(
                f"1. **{filename}**\n"
                + score_line(verdict)
                + f"   - **Status:** {status}\n"
                f"   - **Category:** {verdict.get('category', '').strip() or 'Uncategorized'}\n"
                f"   - **Title:** {verdict.get('title', '').strip()}\n"
                f"   - **Description:** {verdict.get('description', '').strip()}\n"
                f"   - **Suggested Edits:** {verdict.get('editing_suggestions', '').strip() or 'None'}\n"
            )
        else:
            discards.append(
                f"1. **{filename}**\n"
                + score_line(verdict)
                + f"   - **Reason:** {verdict.get('reason', '').strip() or 'No reason given'}\n"
            )

    header = f"# Photography Culling Report - Batch {batch_num}\n"
    header += f"**Folder:** `{folder_path}`\n"
    header += f"**Date:** {date_str}\n"
    header += f"**Images Reviewed:** `{filenames[0]}` to `{filenames[-1]}`\n\n---\n\n"

    body = "# Keeps\n" + ("".join(keeps) if keeps else "_None_\n")
    body += "\n# Discards\n" + ("".join(discards) if discards else "_None_\n")
    return header + body


def process_batch(batch_files, batch_num, folder_path, date_str, system_prompt, max_workers):
    """Analyzes each image in the batch individually, then writes one report.

    Images are dispatched concurrently (bounded by max_workers), but Ollama
    still serializes the actual inference on a memory-constrained machine.
    Results are reassembled in the original file order for the report.
    """
    print(f"\nProcessing Batch {batch_num} ({len(batch_files)} images, up to {max_workers} in flight)...")

    filenames = [os.path.basename(f) for f in batch_files]
    verdicts = [None] * len(batch_files)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(analyze_image, file, system_prompt): i
            for i, file in enumerate(batch_files)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            i = future_to_index[future]
            print(f"  Finished {filenames[i]}")
            verdicts[i] = future.result()

    results = list(zip(filenames, verdicts))
    report = render_report(results, batch_num, folder_path, date_str, filenames)

    report_filename = f"culling_report_{date_str}_batch_{batch_num}.md"
    report_filepath = os.path.join(folder_path, report_filename)
    with open(report_filepath, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Report saved to: {report_filepath}")

def main():
    parser = argparse.ArgumentParser(
        description="Batch photo culling helper using a local Ollama vision model.")
    parser.add_argument("folder", nargs="?",
                         help="folder containing images to review (prompted for if omitted)")
    parser.add_argument("--prompt-file", "-p", default=DEFAULT_PROMPT_FILE,
                         help=f"text file with the system prompt to use "
                              f"(default: {DEFAULT_PROMPT_FILE})")
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS,
                         help=f"max images analyzed concurrently (default: {MAX_WORKERS}; "
                              f"keep low on <=8GB RAM)")
    args = parser.parse_args()

    max_workers = max(1, args.workers)

    try:
        system_prompt = load_system_prompt(args.prompt_file)
    except OSError as e:
        print(f"Error: could not read prompt file '{args.prompt_file}': {e}")
        return

    if not check_ollama_ready():
        proceed = input("Ollama readiness check failed. Do you want to proceed anyway? (y/n): ")
        if proceed.lower() != 'y':
            return

    folder_path = args.folder or input("Enter the folder path containing the images: ").strip()

    # Strip quotes if dragged and dropped in terminal
    if folder_path.startswith('"') and folder_path.endswith('"'):
        folder_path = folder_path[1:-1]

    if not os.path.isdir(folder_path):
        print("Error: Invalid directory path.")
        return

    images = get_image_files(folder_path)
    if not images:
        print("No images (.jpg, .jpeg, .png) found in the specified folder.")
        return

    total_images = len(images)
    print(f"Found {total_images} images.")
    print(f"Using prompt file: {args.prompt_file}")

    date_str = datetime.now().strftime("%Y-%m-%d")

    # Split into batches
    batches = [images[i:i + BATCH_SIZE] for i in range(0, total_images, BATCH_SIZE)]

    for i, batch in enumerate(batches, start=1):
        process_batch(batch, i, folder_path, date_str, system_prompt, max_workers)

    print("\nAll batches processed successfully!")

if __name__ == "__main__":
    main()
