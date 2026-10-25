"""
RemoveEmptyFolders - recursively remove empty folders under a folder you choose.

    python RemoveEmptyFolders.py                 # asks for the folder
    python RemoveEmptyFolders.py "E:\\Photos\\Inbox"

Lists every folder that is empty (or contains only other empty folders), then
asks you to type DELETE before removing anything. The folder you choose is
kept. A folder containing any file, even Thumbs.db, is never removed.
"""

import os
import sys


def long_path(path):
    """Windows: the \\\\?\\ prefix lets os.* handle paths over 260 characters."""
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        if path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path[2:]
        return "\\\\?\\" + path
    return path


def find_empty_folders(root):
    """Empty folders under root, deepest first, root excluded. A folder counts
    as empty once all of its subfolders are going to be removed."""
    empty = set()
    ordered = []
    for dirpath, dirnames, filenames in os.walk(long_path(root), topdown=False):
        if dirpath == long_path(root):
            continue
        if filenames:
            continue
        if all(os.path.join(dirpath, d) in empty for d in dirnames):
            empty.add(dirpath)
            ordered.append(dirpath)
    return ordered


def display(path):
    return path[4:] if path.startswith("\\\\?\\") and not path.startswith("\\\\?\\UNC") else path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    raw = argv[0] if argv else input("Enter the folder path: ")
    # Windows "Copy as path" and drag-and-drop wrap the path in quotes
    root = raw.strip().strip('"').strip("'")
    if not os.path.isdir(long_path(root)):
        print(f"Folder not found: {root!r}")
        return 1

    empty = find_empty_folders(root)
    for p in empty:
        print(f"  empty: {display(p)}")
    if not empty:
        print("No empty folders found.")
        return 0

    if input(f"\nType DELETE to remove {len(empty)} empty folder(s): ").strip() != "DELETE":
        print("Cancelled.")
        return 1

    removed = 0
    for p in empty:
        try:
            os.rmdir(p)  # refuses non-empty folders
            removed += 1
        except OSError as ex:
            print(f"  skipped {display(p)}: {ex}")
    print(f"Removed {removed} folder(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
