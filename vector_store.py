import chromadb
from dotenv import load_dotenv

load_dotenv()

_LOCAL_PATH = "data/chroma_local"

_client = None
_using_cloud = None


def get_client():
    """Chroma Cloud is primary; falls back to a local persistent store if the
    cloud client can't be reached at all - missing/invalid credentials, no
    credits left, or the network being down. The check happens once (a real
    network call, not just constructing the client, since CloudClient() alone
    doesn't fail on bad credentials - only an actual request does) and the
    result is cached for the rest of the process.

    Caveat: this is a dev/demo continuity feature, not a production strategy.
    If this process is one of several replicas, a silent fallback means that
    replica's writes land in its own local store instead of the shared cloud
    one - other replicas won't see them. Watch the printed warning below;
    don't assume data is in the cloud store just because it was written.
    """
    global _client, _using_cloud
    if _client is not None:
        return _client

    try:
        candidate = chromadb.CloudClient()  # reads CHROMA_API_KEY/TENANT/DATABASE from env
        candidate.list_collections()  # forces a real request - bad creds/no credits only surface here
        _client = candidate
        _using_cloud = True
    except Exception as exc:
        print(f"[vector_store] Chroma Cloud unavailable ({exc}) - falling back to local Chroma at '{_LOCAL_PATH}'.")
        _client = chromadb.PersistentClient(path=_LOCAL_PATH)
        _using_cloud = False

    return _client


def is_using_cloud():
    get_client()
    return _using_cloud


def get_collection(name):
    return get_client().get_or_create_collection(name=name)


def upsert_chunks(collection_name, ids, embeddings, documents, metadatas):
    collection = get_collection(collection_name)
    collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)


def get_all_chunks(collection_name):
    """Returns every chunk in the collection - used to build the BM25 index,
    since BM25 needs the full corpus, not just an ANN query result."""
    collection = get_collection(collection_name)
    result = collection.get(include=["documents", "metadatas"])
    return list(zip(result["ids"], result["documents"], result["metadatas"]))


def query_ann(collection_name, query_embedding, top_k):
    collection = get_collection(collection_name)
    result = collection.query(query_embeddings=[query_embedding], n_results=top_k)
    ids = result["ids"][0]
    documents = result["documents"][0]
    distances = result["distances"][0]
    metadatas = result["metadatas"][0]
    return list(zip(ids, documents, distances, metadatas))


def clear_collection(collection_name):
    """Deletes and recreates the collection - each ingestion run starts clean."""
    client = get_client()
    try:
        client.delete_collection(name=collection_name)
    except Exception:
        pass
    return client.get_or_create_collection(name=collection_name)
