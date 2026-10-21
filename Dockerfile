# Backend image. Built for Hugging Face Spaces (Docker SDK), but nothing here
# is Spaces-specific beyond the default port, so it runs on any container host.
FROM python:3.12-slim

# git: set4_remediation/version_store.py versions the policy doc with real git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv

# Spaces runs containers as UID 1000; matching it keeps app dirs writable.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    HF_HOME=/home/user/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH=/home/user/app/.venv/bin:$PATH
WORKDIR /home/user/app

# Dependencies first, so code changes don't invalidate this (slow) layer.
COPY --chown=user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Bake the ONNX embedding + reranker models into the image so the first
# request after a cold start (or sleep, on free hosts) doesn't wait on a
# download. Loading through retrieval.py keeps the model names in one place;
# only it and its import are copied here so code edits don't re-download.
COPY --chown=user retrieval.py vector_store.py ./
RUN python -c "import retrieval; retrieval.get_embed_model(); retrieval.get_rerank_model()"

COPY --chown=user . .

# Spaces' local disk is ephemeral - never fall back to a local vector store there.
ENV CHROMA_REQUIRE_CLOUD=true \
    PORT=7860
EXPOSE 7860

# Single worker on purpose: jobs.py keeps the job registry in process memory.
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT} --workers 1"]
