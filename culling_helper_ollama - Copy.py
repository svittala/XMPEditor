#!/usr/bin/env python3
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
OLLAMA_MODEL = "llava" 
OLLAMA_URL = "http://localhost:11434/api/generate"
BATCH_SIZE = 10

# The persona and instructions for the LLM
SYSTEM_PROMPT = """You are an expert photography judge acting as a 'culling helper'.
I am going to provide you with a batch of images from a recent shoot.
Your job is to go through these images and determine the 'keeps' and 'discards'.

CRITERIA:
1. For any 'keeps', provide a category I can submit that image as a competition entry (e.g., Nature, Landscape, Portrait, etc.). Note: Strict 'Nature' categories cannot contain any man-made elements.
2. For any 'keeps', provide specific editing suggestions to make the image better (e.g., contrast, cropping, dehaze).
3. Ignore any images with people (do not suggest discarding them, just skip evaluating them for competition).
4. For 'discards', briefly explain why (e.g., weak composition, redundant, category violation).

Please output your evaluation in Markdown format. Format it with clear headers for Keeps and Discards, listing the filenames."""

def get_base64_image(image_path):
    """Reads an image file and returns its base64 encoded string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

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

def process_batch(batch_files, batch_num, folder_path, date_str):
    """Sends a batch of images to Ollama and writes the report."""
    print(f"\nProcessing Batch {batch_num} ({len(batch_files)} images)...")
    
    # 1. Prepare images and prompt
    base64_images = []
    filenames = []
    for file in batch_files:
        base64_images.append(get_base64_image(file))
        filenames.append(os.path.basename(file))
    
    prompt = f"{SYSTEM_PROMPT}\n\nHere are the images for this batch. The filenames in order are: {', '.join(filenames)}."
    
    # 2. Build the Ollama API payload
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "images": base64_images,
        "stream": False
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(OLLAMA_URL, data=data, headers={'Content-Type': 'application/json'})
    
    # 3. Call the Ollama API
    print("Waiting for Ollama to analyze images (this may take a while depending on your GPU)...")
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            report_content = result.get('response', '')
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
    folder_path = input("Enter the folder path containing the images: ").strip()
    
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
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Split into batches
    batches = [images[i:i + BATCH_SIZE] for i in range(0, total_images, BATCH_SIZE)]
    
    for i, batch in enumerate(batches, start=1):
        process_batch(batch, i, folder_path, date_str)
        
    print("\nAll batches processed successfully!")

if __name__ == "__main__":
    main()
