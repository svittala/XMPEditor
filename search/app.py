#!/usr/bin/env python3
"""
app.py — Streamlit UI for searching culling reports.

Two tabs:
  - Search: semantic search over kept images; ranked cards with filename,
    status, 1-5 scores, description, and suggested edits.
  - Ask:    conversational RAG; the local Ollama LLM answers questions over the
    retrieved images and cites them by filename.

Run:
    streamlit run search/app.py

Prereqs: build the index first (python search/build_index.py --rebuild) and
have Ollama running with the embedding + chat models pulled.
"""

import sys
from pathlib import Path

import streamlit as st

# Allow running via `streamlit run search/app.py` from anywhere.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import search_core as sc


@st.cache_resource(show_spinner="Loading vector store…")
def load_vectorstore(db_path):
    return sc.get_vectorstore(db_path)


@st.cache_data(show_spinner=False)
def cached_categories(db_path):
    try:
        return sc.list_categories(sc.get_vectorstore(db_path))
    except Exception:
        return []


def _scores_str(meta):
    t, c, a = (meta.get("technical_score"), meta.get("composition_score"),
               meta.get("artistic_score"))
    if t is None and c is None and a is None:
        return ""
    return f"Technical {t}/5 · Composition {c}/5 · Artistic {a}/5"


def render_card(meta, score=None):
    filename = meta.get("filename", "?")
    header = f"**{filename}**"
    if meta.get("category"):
        header += f"  ·  _{meta['category']}_"
    if meta.get("status"):
        header += f"  ·  {meta['status']}"
    if score is not None:
        header += f"  ·  distance `{score:.3f}`"
    st.markdown(header)
    if meta.get("title"):
        st.markdown(f"**{meta['title']}**")
    scores = _scores_str(meta)
    if scores:
        st.caption(scores)
    if meta.get("description"):
        st.write(meta["description"])
    if meta.get("editing_suggestions"):
        st.markdown(f"**Suggested edits:** {meta['editing_suggestions']}")
    src = meta.get("report_file", "")
    if src:
        st.caption(f"from {src}")
    st.divider()


def main():
    st.set_page_config(page_title="Culling Report Search", layout="wide")
    st.title("📸 Culling Report Search")

    with st.sidebar:
        st.header("Settings")
        db_path = st.text_input("Chroma DB path", value=str(sc.DEFAULT_DB_PATH))
        top_k = st.slider("Results (top-k)", min_value=1, max_value=30, value=10)
        chat_model = st.text_input("Chat model", value=sc.CHAT_MODEL)
        categories = cached_categories(db_path)
        category = st.selectbox("Category filter", options=["(all)"] + categories)
        category = None if category == "(all)" else category
        st.caption(f"Embedding model: {sc.EMBED_MODEL}")
        st.caption(f"Ollama: {sc.OLLAMA_BASE_URL}")

    try:
        vs = load_vectorstore(db_path)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not open the vector store at '{db_path}': {e}\n\n"
                 "Build it first with: python search/build_index.py --rebuild")
        return

    search_tab, ask_tab = st.tabs(["🔎 Search", "💬 Ask"])

    with search_tab:
        query = st.text_input("Search images", placeholder=
                              "e.g. serene fjord with towering cliffs")
        if query:
            with st.spinner("Searching…"):
                results = sc.semantic_search(vs, query, k=top_k, category=category)
            if not results:
                st.info("No matches. Try a broader query or clear the category filter.")
            for r in results:
                render_card(r["metadata"], score=r["score"])

    with ask_tab:
        if "chat" not in st.session_state:
            st.session_state.chat = []
        for msg in st.session_state.chat:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        question = st.chat_input("Ask about your images…")
        if question:
            st.session_state.chat.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    answer, docs = sc.answer_question(
                        vs, question, k=min(top_k, 6),
                        category=category, chat_model=chat_model)
                st.markdown(answer)
                if docs:
                    with st.expander(f"Sources ({len(docs)} image(s))"):
                        for d in docs:
                            render_card(d.metadata)
            st.session_state.chat.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
