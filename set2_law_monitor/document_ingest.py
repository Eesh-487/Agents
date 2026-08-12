"""Set 2: generic ingestion of arbitrary documents (PDF/DOCX/TXT/MD) into
Chroma, so a user's own company documents can be compared against ingested
law text in Set 3. Also reused by law_scraper.py to ingest downloaded bill
PDFs, so there's one text-extraction/chunking/embedding path, not two.
"""
import os

import docx
from pypdf import PdfReader

import vector_store
from retrieval import embed_documents

DEFAULT_COLLECTION = "user_documents"


def extract_text_from_pdf(path):
    reader = PdfReader(path)
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def extract_text_from_docx(path):
    document = docx.Document(path)
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())


def extract_text(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    if ext == ".docx":
        return extract_text_from_docx(file_path)
    if ext in (".txt", ".md"):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    raise ValueError(f"Unsupported file type '{ext}'. Supported: .pdf, .docx, .txt, .md")


def chunk_text(text, chunk_size=1200, overlap=150):
    """Generic paragraph-aware chunking. Unlike law_ingest.py's section-number
    chunker (which relies on an Act's numbered-section structure), arbitrary
    user documents can't be assumed to have that structure, so this chunks
    by accumulated paragraph size instead, carrying a small overlap forward
    for context continuity across chunk boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) > chunk_size:
            chunks.append(current.strip())
            current = current[-overlap:] + "\n\n" + paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current.strip():
        chunks.append(current.strip())
    return chunks


def ingest_document(file_path, collection_name=DEFAULT_COLLECTION, doc_label=None, extra_metadata=None):
    """Extracts, chunks, embeds, and upserts one document into the given
    Chroma collection. Returns a summary dict."""
    text = extract_text(file_path)
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError(f"No extractable text found in {file_path}")

    doc_label = doc_label or os.path.basename(file_path)
    base_metadata = {"source_document": doc_label}
    if extra_metadata:
        base_metadata.update(extra_metadata)

    ids = [f"{doc_label}-chunk-{i}" for i in range(len(chunks))]
    metadatas = [{**base_metadata, "chunk_index": i} for i in range(len(chunks))]

    embeddings = embed_documents(chunks)
    vector_store.upsert_chunks(collection_name, ids, embeddings, chunks, metadatas)

    return {
        "status": "ingested",
        "chunk_count": len(chunks),
        "collection": collection_name,
        "document": doc_label,
    }
