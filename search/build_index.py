#!/usr/bin/env python3
"""
build_index.py — ingest culling reports into a local Chroma vector store.

Each KEPT image in each report becomes one searchable document (one vector),
embedded with a local Ollama embedding model. The report's own regex parser
(apply_culling_report.parse_report) is reused so we index exactly what the
culling helpers wrote, with no separate markdown-chunking logic.

Usage:
    python search/build_index.py                         # reports=testresults
    python search/build_index.py --reports testresults --rebuild
    python search/build_index.py --reports DIR1 --reports DIR2 --db chroma_db

Options:
    --reports PATH   a report .md file or a directory of them (repeatable;
                     default: the repo's testresults/ folder)
    --db DIR         Chroma persist directory (default: repo-root chroma_db/)
    --rebuild        drop and recreate the collection before indexing
                     (otherwise documents are upserted by stable id)

Prereqs: a running Ollama with the embedding model pulled, e.g.
    ollama pull nomic-embed-text
"""

import argparse
import re
import sys
from pathlib import Path

# The reusable parser and file reader live in the repo root, one level up.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import xmp_common as xc  # noqa: E402
from apply_culling_report import (  # noqa: E402
    resolve_reports,
    parse_report,
    _clean_field,
)

from search_core import (  # noqa: E402
    DEFAULT_DB_PATH,
    COLLECTION,
    get_embeddings,
)
from langchain_chroma import Chroma  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

DEFAULT_REPORTS_DIR = REPO_ROOT / "testresults"

_FOLDER_RE = re.compile(r"^\*\*Folder:\*\*\s*`?([^`\n]+)`?", re.MULTILINE)
_DATE_RE = re.compile(r"^\*\*Date:\*\*\s*(.+)$", re.MULTILINE)
_BATCH_RE = re.compile(r"batch[_\s]*(\d+)", re.IGNORECASE)

# Add documents to Chroma in chunks so a huge corpus streams with progress
# instead of one giant call.
ADD_BATCH = 100


def parse_header(text, report_path):
    """Pull folder/date/batch out of a report header for citation metadata.
    Batch falls back to the number in the filename (e.g. ..._batch_14.md)."""
    folder_m = _FOLDER_RE.search(text)
    date_m = _DATE_RE.search(text)
    batch_m = _BATCH_RE.search(text) or _BATCH_RE.search(report_path.name)
    return {
        "folder": folder_m.group(1).strip() if folder_m else "",
        "date": date_m.group(1).strip() if date_m else "",
        "batch": batch_m.group(1) if batch_m else "",
    }


def build_documents(report_path):
    """Turn one report file into (documents, ids) for its kept images.
    Returns ([], [], n_entries) counts so the caller can log skips."""
    text = xc.read_text(report_path)
    header = parse_header(text, report_path)
    entries = parse_report(text)

    docs, ids = [], []
    for entry in entries:
        if entry["status"] != "keep":
            continue
        fields = entry["fields"]
        filename = entry["filename"]
        title = _clean_field(fields.get("title")) or ""
        category = _clean_field(fields.get("category")) or ""
        description = _clean_field(fields.get("description")) or ""
        edits = _clean_field(fields.get("editing suggestions")) or ""

        # The embedded text: the semantically rich fields, labeled.
        page_content = (
            f"Title: {title}\n"
            f"Category: {category}\n"
            f"Description: {description}\n"
            f"Editing suggestions: {edits}"
        )
        metadata = {
            "filename": filename,
            "title": title,
            "category": category,
            "description": description,
            "editing_suggestions": edits,
            "report_file": report_path.name,
            "batch": header["batch"],
            "folder": header["folder"],
        }
        docs.append(Document(page_content=page_content, metadata=metadata))
        # Stable id -> re-running upserts the same image instead of duplicating.
        ids.append(f"{report_path.stem}:{filename}")

    return docs, ids, len(entries)


def main():
    parser = argparse.ArgumentParser(
        description="Index culling reports into a Chroma vector store.")
    parser.add_argument("--reports", action="append", metavar="PATH",
                         help="report .md file or directory (repeatable; "
                              f"default: {DEFAULT_REPORTS_DIR})")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH),
                         help=f"Chroma persist directory (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--rebuild", action="store_true",
                         help="drop and recreate the collection before indexing")
    args = parser.parse_args()

    report_inputs = args.reports or [str(DEFAULT_REPORTS_DIR)]

    report_files = []
    for r in report_inputs:
        found = resolve_reports(r)
        if not found:
            print(f"warning: no report found for '{r}'", file=sys.stderr)
        report_files.extend(found)

    if not report_files:
        print("error: no report files to index", file=sys.stderr)
        sys.exit(1)

    embeddings = get_embeddings()
    vs = Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=args.db,
    )

    if args.rebuild:
        # Drop everything in the collection so stale/removed images don't linger.
        existing = vs.get()
        if existing["ids"]:
            vs.delete(ids=existing["ids"])
        print(f"rebuild: cleared {len(existing['ids'])} existing document(s)")

    all_docs, all_ids = [], []
    total_entries = 0
    for report_path in report_files:
        docs, ids, n_entries = build_documents(report_path)
        total_entries += n_entries
        if not docs:
            print(f"  {report_path.name}: 0 keeps indexed "
                  f"({n_entries} entries parsed) — check format")
            continue
        print(f"  {report_path.name}: {len(docs)} keep(s) indexed")
        all_docs.extend(docs)
        all_ids.extend(ids)

    if not all_docs:
        print("\nNo keep entries found in any report — nothing indexed.")
        sys.exit(1)

    for i in range(0, len(all_docs), ADD_BATCH):
        chunk_docs = all_docs[i:i + ADD_BATCH]
        chunk_ids = all_ids[i:i + ADD_BATCH]
        vs.add_documents(documents=chunk_docs, ids=chunk_ids)
        print(f"  embedded {min(i + ADD_BATCH, len(all_docs))}/{len(all_docs)}")

    print(f"\nIndexed {len(all_docs)} kept image(s) from {len(report_files)} "
          f"report(s) ({total_entries} total entries parsed) into {args.db}")


if __name__ == "__main__":
    main()
