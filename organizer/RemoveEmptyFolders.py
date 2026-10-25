"""
RemoveEmptyFolders - recursively remove empty folders under a folder you choose.

    python RemoveEmptyFolders.py                 # asks for the folder
    python RemoveEmptyFolders.py "E:\\Photos\\Inbox"

Looks at every subfolder. A folder is "empty" if it has no files, or only
Windows/macOS junk (Thumbs.db, desktop.ini, .DS_Store, ...), and all of its
subfolders are empty too. Lists them, then asks you to type DELETE before
removing anything. Folders that are kept are listed with the reason. If the
folder you chose ends up empty too, you are asked separately whether to
remove it as well.
"""

import os
import stat
import sys

JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}


def long_path(path):
    """Windows: the \\\\?\\ prefix lets os.* handle paths over 260 characters."""
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        if path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path[2:]
        return "\\\\?\\" + path
    return path


def display(path):
    return path[4:] if path.startswith("\\\\?\\") and not path.startswith("\\\\?\\UNC") else path


def scan(root):
    """Return (empty, kept, total, root_junk). empty: [(folder, [junk files])]
    deepest first, root excluded. kept: {folder: reason}. root_junk: None if
    the root itself would not be empty, else its junk files (usually [])."""
    root = long_path(root)
    empty, kept = {}, {}
    total = 0
    root_junk = None
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath == root:
            real = [f for f in filenames if f.lower() not in JUNK_NAMES]
            if not real and all(os.path.join(dirpath, d) in empty for d in dirnames):
                root_junk = [os.path.join(dirpath, f) for f in filenames]
            continue
        total += 1
        real = [f for f in filenames if f.lower() not in JUNK_NAMES]
        # a symlink/junction to a folder shows up in dirnames but isn't walked
        # into; treat it as content rather than risk following it
        subs = [os.path.join(dirpath, d) for d in dirnames]
        busy_subs = [s for s in subs if s not in empty]
        if real:
            kept[dirpath] = f"contains file {real[0]!r}" + (f" (+{len(real) - 1} more)" if len(real) > 1 else "")
        elif busy_subs:
            kept[dirpath] = f"contains non-empty folder {os.path.basename(busy_subs[0])!r}"
        else:
            empty[dirpath] = [os.path.join(dirpath, f) for f in filenames]
    return list(empty.items()), kept, total, root_junk


def remove_folder(path, junk):
    for f in junk:
        try:
            os.remove(f)
        except PermissionError:
            os.chmod(f, stat.S_IWRITE)  # read-only attribute on Windows
            os.remove(f)
    os.rmdir(path)  # refuses non-empty folders


def run(root):
    if not os.path.isdir(long_path(root)):
        print(f"Folder not found: {root!r}")
        return 1
    root = long_path(root)
    empty, kept, total, root_junk = scan(root)
    print(f"Scanned {total} subfolder(s) under {display(long_path(root))}")
    for path, junk in empty:
        extra = f"  (only {len(junk)} junk file(s): {os.path.basename(junk[0])})" if junk else ""
        print(f"  empty: {display(path)}{extra}")
    for path, why in sorted(kept.items()):
        print(f"  kept:  {display(path)}  - {why}")
    if not empty and root_junk is None:
        print("\nNo empty folders found.")
        return 0

    if empty:
        if input(f"\nType DELETE to remove {len(empty)} empty folder(s): ").strip() != "DELETE":
            print("Cancelled.")
            return 1
        removed = 0
        failed = False
        for path, junk in empty:
            try:
                remove_folder(path, junk)
                removed += 1
            except OSError as ex:
                failed = True
                print(f"  skipped {display(path)}: {ex}")
        print(f"Removed {removed} folder(s).")
    else:
        failed = False

    if root_junk is not None and not failed:
        print(f"\n{display(root)} is now empty too.")
        if input("Type DELETE to remove it as well: ").strip() == "DELETE":
            try:
                remove_folder(root, root_junk)
                print("Removed it.")
            except OSError as ex:
                print(f"  skipped: {ex}")
        else:
            print("Kept it.")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    interactive = not argv
    raw = argv[0] if argv else input("Enter the folder path: ")
    # Windows "Copy as path" and drag-and-drop wrap the path in quotes
    root = raw.strip().strip('"').strip("'")
    try:
        return run(root)
    finally:
        if interactive:  # keep a double-clicked window open
            input("\nPress Enter to close...")


if __name__ == "__main__":
    sys.exit(main())
