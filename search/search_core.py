#!/usr/bin/env python3
"""
search_core.py — shared retrieval logic for the culling-report search tool.

No UI here: this module builds the Ollama embeddings + Chroma vector store,
runs semantic search, and assembles the RAG chain. Both build_index.py and
the Streamlit app (app.py) import from here so the models/paths stay in sync.

Configurable via environment variables:
    XMP_SEARCH_EMBED_MODEL   embedding model      (default: nomic-embed-text)
    XMP_SEARCH_CHAT_MODEL    chat/RAG model        (default: llama3.1)
    XMP_SEARCH_CHAT_MODELS   comma-separated choices offered in the UI
                             (default: llama3.1,qwen3:1.7b,phi4-mini)
    XMP_SEARCH_DB            Chroma persist dir     (default: <repo>/chroma_db)
    OLLAMA_HOST / OLLAMA_URL Ollama base url        (default: http://localhost:11434)
"""

import os
import re
from pathlib import Path

from langchain_chroma import Chroma
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_core.prompts import ChatPromptTemplate

REPO_ROOT = Path(__file__).resolve().parent.parent

EMBED_MODEL = os.environ.get("XMP_SEARCH_EMBED_MODEL", "nomic-embed-text")
CHAT_MODEL = os.environ.get("XMP_SEARCH_CHAT_MODEL", "llama3.1")
# Chat models selectable in the UI (Ollama tags). Lighter ones first-class so a
# modest machine can avoid llama3.1. The default is always included.
CHAT_MODELS = [m.strip() for m in os.environ.get(
    "XMP_SEARCH_CHAT_MODELS", "llama3.1,qwen3:1.7b,phi4-mini").split(",") if m.strip()]
if CHAT_MODEL not in CHAT_MODELS:
    CHAT_MODELS.insert(0, CHAT_MODEL)
DEFAULT_DB_PATH = Path(os.environ.get("XMP_SEARCH_DB", str(REPO_ROOT / "chroma_db")))
COLLECTION = "culling"

# langchain-ollama reads OLLAMA_HOST itself; keep a base_url for clarity/override.
OLLAMA_BASE_URL = (
    os.environ.get("OLLAMA_HOST")
    or os.environ.get("OLLAMA_URL")
    or "http://localhost:11434"
)

_RAG_SYSTEM = (
    "You are a photo-culling assistant. Answer the user's question using ONLY "
    "the retrieved image entries below. Each entry has a filename, category, "
    "title, status (portfolio-ready or needs edits), 1-5 scores (technical, "
    "composition, artistic), description, and suggested edits. Cite every image "
    "you refer to by its exact filename. If nothing relevant was retrieved, say "
    "so plainly instead of guessing.\n\n"
    "Retrieved images:\n{context}"
)


def _scores_str(m):
    """Render the three scores from a document's metadata, or '' if absent."""
    t, c, a = (m.get("technical_score"), m.get("composition_score"),
               m.get("artistic_score"))
    if t is None and c is None and a is None:
        return ""
    return f"technical {t}/5, composition {c}/5, artistic {a}/5"


def get_embeddings():
    return OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)


def get_vectorstore(db_path=None):
    return Chroma(
        collection_name=COLLECTION,
        embedding_function=get_embeddings(),
        persist_directory=str(db_path or DEFAULT_DB_PATH),
    )


def semantic_search(vs, query, k=10, category=None):
    """Return a list of {score, metadata, content} dicts, best match first.
    A lower score is a closer match (Chroma returns a distance)."""
    where = {"category": category} if category else None
    hits = vs.similarity_search_with_score(query, k=k, filter=where)
    results = []
    for doc, score in hits:
        results.append({
            "score": score,
            "content": doc.page_content,
            "metadata": doc.metadata,
        })
    return results


def list_categories(vs):
    """Distinct, sorted category values present in the collection (for a
    UI filter dropdown)."""
    data = vs.get(include=["metadatas"])
    cats = {
        (m or {}).get("category", "").strip()
        for m in data.get("metadatas", [])
    }
    return sorted(c for c in cats if c)


def _format_context(docs):
    blocks = []
    for d in docs:
        m = d.metadata
        blocks.append(
            f"- filename: {m.get('filename', '?')}\n"
            f"  category: {m.get('category', '')}\n"
            f"  title: {m.get('title', '')}\n"
            f"  status: {m.get('status', '')}\n"
            f"  scores: {_scores_str(m)}\n"
            f"  description: {m.get('description', '')}\n"
            f"  suggested edits: {m.get('editing_suggestions', '')}"
        )
    return "\n".join(blocks) if blocks else "(no images retrieved)"


def answer_question(vs, question, k=6, category=None, chat_model=None):
    """RAG: retrieve the top-k image entries, ask the local LLM to answer over
    them, and return (answer_text, source_docs) so the UI can show which images
    grounded the answer."""
    where = {"category": category} if category else None
    docs = vs.similarity_search(question, k=k, filter=where)

    llm = ChatOllama(model=chat_model or CHAT_MODEL, base_url=OLLAMA_BASE_URL)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _RAG_SYSTEM),
        ("human", "{question}"),
    ])
    chain = prompt | llm
    resp = chain.invoke({"context": _format_context(docs), "question": question})
    answer = getattr(resp, "content", str(resp))
    # Reasoning models (e.g. qwen3) may inline their chain of thought.
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
    return answer, docs
