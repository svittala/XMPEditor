#!/usr/bin/env python3
import argparse
import os
import glob
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# ==========================================
# Configuration
# ==========================================
# Ensure you have installed the Google Generative AI SDK:
# pip install google-generativeai pillow

# Set your API key here, or leave as None to pull from the GEMINI_API_KEY environment variable.
API_KEY = None
MODEL_NAME = "gemini-1.5-pro" # Highly recommended for advanced vision and reasoning tasks
BATCH_SIZE = 10

# The persona/instructions for the LLM live in an external text file so you
# can run different batches with different prompts via --prompt-file.
DEFAULT_PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt_gemini.txt")


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
        # Also check uppercase extensions
        files.extend(glob.glob(os.path.join(folder_path, ext.upper())))
    # Sort files alphabetically to ensure consistent batching
    return sorted(files)

def process_batch(batch_files, batch_num, folder_path, date_str, model, system_prompt):
    """Sends a batch of images to Gemini and writes the report."""
    print(f"\nProcessing Batch {batch_num} ({len(batch_files)} images)...")

    # 1. Prepare images and prompt
    content = []
    filenames = []

    # Append the prompt first
    content.append(system_prompt)

    # Then load and append all images
    for file in batch_files:
        filenames.append(os.path.basename(file))
        img = Image.open(file)
        content.append(img)
        
    # Append the filename mapping at the end so the model knows which is which
    content.append(f"The images provided above are in the following order: {', '.join(filenames)}.")
    
    # 2. Call the Gemini API
    print("Waiting for Gemini to analyze images...")
    try:
        response = model.generate_content(content)
        report_content = response.text
    except Exception as e:
        print(f"Error communicating with Gemini: {e}")
        return

    # 3. Format and save the report
    report_filename = f"culling_report_gemini_{date_str}_batch_{batch_num}.md"
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
        description="Batch photo culling helper using the Gemini vision model.")
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

    # Setup API Key
    api_key = API_KEY or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: No Gemini API key found.")
        print("Please set the GEMINI_API_KEY environment variable or edit the script to include your API_KEY.")
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL_NAME)

    # Get folder
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
        process_batch(batch, i, folder_path, date_str, model, system_prompt)

    print("\nAll batches processed successfully!")

if __name__ == "__main__":
    main()
