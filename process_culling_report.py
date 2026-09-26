import os
import json
import urllib.request
import urllib.error
import subprocess
import argparse
import glob

# Ensure you have a capable model pulled in Ollama
OLLAMA_MODEL = "qwen2.5vl:7b"
OLLAMA_URL = "http://localhost:11434/api/generate"

SYSTEM_PROMPT = """You are an expert data extractor. I will give you a photography culling report in Markdown.
Your task is to extract the images to keep and discard, and output ONLY a JSON object.
Do NOT output any markdown blocks like ```json ... ```, just output the raw JSON.
The JSON must have this exact structure:
{
  "folder": "the folder path extracted from the top of the report",
  "keeps": [
    {
      "filename": "image1.jpg",
      "categories": "Category1, Category2",
      "editing_suggestions": "Suggested edits..."
    }
  ],
  "discards": [
    "image2.jpg", "image3.jpg"
  ]
}
For 'keeps', join the categories into a comma-separated string, and combine all editing suggestions into a single string.
If a filename is something like 'image-2.jpg' or has extensions, keep the exact filename.
"""

def extract_data_from_report(report_text):
    prompt = f"{SYSTEM_PROMPT}\n\nHere is the report:\n{report_text}"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json" # This asks Ollama to strictly output JSON (works well in recent Ollama versions)
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(OLLAMA_URL, data=data, headers={'Content-Type': 'application/json'})
    
    try:
        print("Waiting for Ollama to process the report...")
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            response_text = result.get('response', '').strip()
            
            # Clean up if it still outputs markdown blocks
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
                
            try:
                return json.loads(response_text)
            except json.JSONDecodeError:
                print("Failed to parse JSON from Ollama response:")
                print(response_text)
                return None
    except urllib.error.URLError as e:
        print(f"Error communicating with Ollama: {e.reason}")
        return None

def find_xmp_file(folder, image_filename):
    """Finds the corresponding .xmp file for a given image filename."""
    base_name = os.path.splitext(image_filename)[0]
    xmp_path = os.path.join(folder, f"{base_name}.xmp")
    if os.path.exists(xmp_path):
        return xmp_path
    
    # Check for uppercase extension if lowercase not found
    xmp_path_upper = os.path.join(folder, f"{base_name}.XMP")
    if os.path.exists(xmp_path_upper):
        return xmp_path_upper
        
    return None

def process_report(report_path):
    if not os.path.exists(report_path):
        print(f"Error: Report file not found at {report_path}")
        return

    with open(report_path, "r", encoding="utf-8") as f:
        report_text = f.read()

    print(f"Sending {os.path.basename(report_path)} to Ollama for extraction...")
    data = extract_data_from_report(report_text)
    
    if not data:
        print("Could not extract data.")
        return

    folder = data.get("folder", "").strip('`').strip()
    # Fallback to the report's directory if the extracted folder is invalid or not found
    if not folder or not os.path.isdir(folder):
        print(f"Warning: Extracted folder '{folder}' from report is invalid or doesn't exist.")
        folder = os.path.dirname(os.path.abspath(report_path))
        print(f"Falling back to report directory: {folder}")
    else:
        print(f"Source folder identified: {folder}")

    # Resolve xmpwrite.py path
    xmpwrite_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xmpwrite.py")
    if not os.path.exists(xmpwrite_path):
        print(f"Error: Could not find xmpwrite.py at {xmpwrite_path}")
        return

    keeps = data.get("keeps", [])
    discards = data.get("discards", [])

    print(f"\nExtracted {len(keeps)} keeps and {len(discards)} discards.")

    # 1. Process Keeps
    print("\n--- Processing Keeps ---")
    for keep in keeps:
        filename = keep.get("filename")
        if not filename:
            continue
            
        xmp_file = find_xmp_file(folder, filename)
        if xmp_file:
            print(f"Updating Keep: {filename} -> {os.path.basename(xmp_file)}")
            cmd = ["python", xmpwrite_path, xmp_file, "--pick", "1"]
            
            cats = keep.get("categories", "")
            if cats:
                cmd.extend(["--add-tags", cats])
                
            desc = keep.get("editing_suggestions", "")
            if desc:
                cmd.extend(["--description", desc])
                
            subprocess.run(cmd)
        else:
            print(f"Warning: XMP file not found for keep '{filename}' in {folder}")

    # 2. Process Discards
    print("\n--- Processing Discards ---")
    for discard_filename in discards:
        xmp_file = find_xmp_file(folder, discard_filename)
        if xmp_file:
            print(f"Updating Discard: {discard_filename} -> {os.path.basename(xmp_file)}")
            cmd = ["python", xmpwrite_path, xmp_file, "--pick", "-1"]
            subprocess.run(cmd)
        else:
            print(f"Warning: XMP file not found for discard '{discard_filename}' in {folder}")
            
    print("\nFinished processing report.")

def main():
    parser = argparse.ArgumentParser(description="Process culling reports with Ollama and update XMP files")
    parser.add_argument("path", help="Path to a culling report markdown file or a directory containing them")
    args = parser.parse_args()
    
    path = args.path
    if os.path.isfile(path):
        process_report(path)
    elif os.path.isdir(path):
        # Process all markdown files starting with culling_report
        reports = glob.glob(os.path.join(path, "culling_report*.md"))
        if not reports:
            print(f"No culling_report*.md files found in {path}")
            return
        for report in reports:
            print(f"\n======================================")
            print(f"Processing {report}")
            print(f"======================================")
            process_report(report)
    else:
        print(f"Error: Invalid path '{path}'")

if __name__ == "__main__":
    main()
