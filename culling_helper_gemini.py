#!/usr/bin/env python3
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

def process_batch(batch_files, batch_num, folder_path, date_str, model):
    """Sends a batch of images to Gemini and writes the report."""
    print(f"\nProcessing Batch {batch_num} ({len(batch_files)} images)...")
    
    # 1. Prepare images and prompt
    content = []
    filenames = []
    
    # Append the prompt first
    content.append(SYSTEM_PROMPT)
    
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
    # Setup API Key
    api_key = API_KEY or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: No Gemini API key found.")
        print("Please set the GEMINI_API_KEY environment variable or edit the script to include your API_KEY.")
        return
        
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL_NAME)
    
    # Get folder
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
        process_batch(batch, i, folder_path, date_str, model)
        
    print("\nAll batches processed successfully!")

if __name__ == "__main__":
    main()
