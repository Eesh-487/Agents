"""Shared hybrid retrieval: ANN (dense) + BM25 (sparse) fused via Reciprocal
Rank Fusion, then reranked with a cross-encoder. Used by every RAG pipeline
in this project (Set 2's law search now, Set 3's gap analysis later) -
naive single-vector similarity search misses exact legal terms/section
references that BM25 catches, and a reranker corrects ordering mistakes
either retrieval method makes on its own.
"""
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

import vector_store

_EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_RERANK_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
# BGE-family models are trained to expect this instruction prefix on the
# query side only (not on documents) for best retrieval quality.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_embed_model = None
_rerank_model = None


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(_EMBED_MODEL_NAME)
    return _embed_model


def get_rerank_model():
    global _rerank_model
    if _rerank_model is None:
        _rerank_model = CrossEncoder(_RERANK_MODEL_NAME)
    return _rerank_model


def embed_documents(texts):
    return get_embed_model().encode(texts, normalize_embeddings=True).tolist()


def embed_query(query):
    return get_embed_model().encode([_BGE_QUERY_PREFIX + query], normalize_embeddings=True).tolist()[0]


def _tokenize(text):
    return text.lower().split()


def _bm25_search(query, corpus, top_k):
    """corpus: list of (id, document, metadata) tuples. Returns the top_k by BM25 score."""
    if not corpus:
        return []
    tokenized_corpus = [_tokenize(doc) for _, doc, _ in corpus]
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(_tokenize(query))
    ranked_indices = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [corpus[i] for i in ranked_indices]


def _reciprocal_rank_fusion(ranked_id_lists, k=60):
    """Standard RRF: combines multiple ranked id lists into one fused ranking
    without needing the two methods' raw scores to be on comparable scales
    (BM25 scores and cosine distances aren't)."""
    scores = {}
    for ranked_ids in ranked_id_lists:
        for rank, item_id in enumerate(ranked_ids):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda item_id: scores[item_id], reverse=True)


def hybrid_search(query, collection_name, top_k=5, candidate_k=15):
    """ANN + BM25 -> RRF fusion -> cross-encoder rerank -> top_k results.
    Returns a list of {"id", "document", "metadata", "rerank_score"}."""

    query_embedding = embed_query(query)
    ann_results = vector_store.query_ann(collection_name, query_embedding, top_k=candidate_k)

    full_corpus = vector_store.get_all_chunks(collection_name)
    bm25_results = _bm25_search(query, full_corpus, top_k=candidate_k)

    fused_ids = _reciprocal_rank_fusion(
        [[r[0] for r in ann_results], [r[0] for r in bm25_results]]
    )[:candidate_k]

    lookup = {r[0]: (r[1], r[3]) for r in ann_results}  # id -> (document, metadata)
    lookup.update({r[0]: (r[1], r[2]) for r in bm25_results})

    candidates = [(item_id, *lookup[item_id]) for item_id in fused_ids if item_id in lookup]
    if not candidates:
        return []

    rerank_model = get_rerank_model()
    pairs = [(query, document) for _, document, _ in candidates]
    rerank_scores = rerank_model.predict(pairs)

    reranked = sorted(zip(candidates, rerank_scores), key=lambda pair: pair[1], reverse=True)
    return [
        {"id": item_id, "document": document, "metadata": metadata, "rerank_score": float(score)}
        for (item_id, document, metadata), score in reranked[:top_k]
    ]
