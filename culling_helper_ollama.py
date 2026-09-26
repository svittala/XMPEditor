#!/usr/bin/env python3
import argparse
import os
import glob
import base64
import json
import urllib.request
import urllib.error
from datetime import datetime

# ==========================================
# Configuration
# ==========================================
# Ensure you have a vision-capable model pulled in Ollama, e.g., 'ollama pull llava'
OLLAMA_MODEL = "qwen2.5vl:7b"
# older version - llava
OLLAMA_URL = "http://localhost:11434/api/generate"
BATCH_SIZE = 5
OLLAMA_NUM_CTX = 32768  # Increased context size to avoid context limit errors

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

def process_batch(batch_files, batch_num, folder_path, date_str, system_prompt):
    """Sends a batch of images to Ollama and writes the report."""
    print(f"\nProcessing Batch {batch_num} ({len(batch_files)} images)...")

    # 1. Prepare images and prompt
    base64_images = []
    filenames = []
    for file in batch_files:
        base64_images.append(get_base64_image(file))
        filenames.append(os.path.basename(file))

    prompt = f"{system_prompt}\n\nHere are the images for this batch. The filenames in order are: {', '.join(filenames)}."
    
    # 2. Build the Ollama API payload
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "images": base64_images,
        "stream": False,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX
        }
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(OLLAMA_URL, data=data, headers={'Content-Type': 'application/json'})
    
    # 3. Call the Ollama API
    print("Waiting for Ollama to analyze images (this may take a while depending on your GPU)...")
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            report_content = result.get('response', '')
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8')
        print(f"HTTP Error communicating with Ollama: {e.code} {e.reason}")
        print(f"Details from Ollama: {error_msg}")
        return
    except urllib.error.URLError as e:
        print(f"Error communicating with Ollama: {e.reason}")
        return
    except json.JSONDecodeError:
        print("Error: Received invalid JSON response from Ollama.")
        return

    # 4. Format and save the report
    report_filename = f"culling_report_{date_str}_batch_{batch_num}.md"
    report_filepath = os.path.join(folder_path, report_filename)
    
    header = f"# Photography Culling Report - Batch {batch_num}\n"
    header += f"**Folder:** `{folder_path}`\n"
    header += f"**Date:** {date_str}\n"
    header += f"**Images Reviewed:** `{filenames[0]}` to `{filenames[-1]}`\n\n---\n\n"
    
    with open(report_filepath, "w", encoding="utf-8") as f:
        f.write(header + report_content)
        
    print(f"Report saved to: {report_filepath}")

def main():
    parser = argparse.ArgumentParser(
        description="Batch photo culling helper using a local Ollama vision model.")
    parser.add_argument("folder", nargs="?",
                         help="folder containing images to review (prompted for if omitted)")
    parser.add_argument("--prompt-file", "-p", default=DEFAULT_PROMPT_FILE,
                         help=f"text file with the system prompt to use "
                              f"(default: {DEFAULT_PROMPT_FILE})")
    args = parser.parse_args()

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
        process_batch(batch, i, folder_path, date_str, system_prompt)

    print("\nAll batches processed successfully!")

if __name__ == "__main__":
    main()
