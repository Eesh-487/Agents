"""Set 2 (Law Monitor), scoped to the DPDP Act 2023 for this pass: clean the
extracted gazette PDF text, chunk it by legal section, embed, and write to
the Chroma collection retrieval.py's hybrid search reads from.
"""
import re

import vector_store
from retrieval import embed_documents

COLLECTION_NAME = "dpdp_act_2023"
SOURCE_PATH = "data/law_sources/dpdp_act_2023.txt"

# Government gazette PDFs interleave bilingual headers/footers and marginal
# notes with the actual operative text. These patterns strip that noise.
_NOISE_PATTERNS = [
    re.compile(r"[ऀ-ॿ]"),  # any Devanagari text
    re.compile(r"EXTRAORDINARY", re.I),
    re.compile(r"PART\s+II", re.I),
    re.compile(r"REGISTERED NO", re.I),
    re.compile(r"PUBLISHED\s+BY\s+AUTHORITY", re.I),
    re.compile(r"MINISTRY OF LA", re.I),
    re.compile(r"Legislative Department", re.I),
    re.compile(r"^New Delhi, the", re.I),
    re.compile(r"Separate paging", re.I),
    re.compile(r"^xxxGID", re.I),
    re.compile(r"^SEC\.\s*\d+\]", re.I),
    re.compile(r"THE GAZETTE OF INDIA", re.I),
    re.compile(r"^\d+\s*$"),  # bare page numbers
]

# Top-level section headers look like "12. (1) ..." at the start of a line -
# distinct from sub-clause markers like "(a)"/"(1)" which don't start the line.
_SECTION_HEADER_RE = re.compile(r"^(\d{1,3})\.\s")


def _is_noise_line(line):
    stripped = line.strip()
    if not stripped:
        return True
    return any(pattern.search(stripped) for pattern in _NOISE_PATTERNS)


def clean_text(raw_text):
    kept_lines = [line for line in raw_text.splitlines() if not _is_noise_line(line)]
    return "\n".join(kept_lines)


def chunk_by_section(cleaned_text):
    chunks = []
    current_section = None
    current_lines = []

    for line in cleaned_text.splitlines():
        match = _SECTION_HEADER_RE.match(line)
        if match:
            if current_section is not None and current_lines:
                chunks.append({"section": current_section, "text": "\n".join(current_lines).strip()})
            current_section = match.group(1)
            current_lines = [line]
        elif current_section is not None:
            current_lines.append(line)

    if current_section is not None and current_lines:
        chunks.append({"section": current_section, "text": "\n".join(current_lines).strip()})

    return chunks


def ingest():
    with open(SOURCE_PATH, "r", encoding="utf-8") as f:
        raw_text = f.read()

    cleaned = clean_text(raw_text)
    chunks = chunk_by_section(cleaned)
    print(f"Parsed {len(chunks)} sections from {SOURCE_PATH}")

    ids = [f"dpdp-section-{chunk['section']}" for chunk in chunks]
    documents = [chunk["text"] for chunk in chunks]
    metadatas = [
        {"section": chunk["section"], "act": "Digital Personal Data Protection Act, 2023", "source": "meity.gov.in"}
        for chunk in chunks
    ]

    print("Embedding chunks...")
    embeddings = embed_documents(documents)

    print(f"Writing to Chroma collection '{COLLECTION_NAME}'...")
    vector_store.clear_collection(COLLECTION_NAME)
    vector_store.upsert_chunks(COLLECTION_NAME, ids, embeddings, documents, metadatas)
    print("Done.")

    return {"status": "ingested", "chunk_count": len(chunks), "collection": COLLECTION_NAME}


if __name__ == "__main__":
    print(ingest())
