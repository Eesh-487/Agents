---
title: Compliance Memory System
emoji: ⚖️
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Compliance Memory System — Backend

FastAPI backend for explainable regulatory compliance analysis: document
ingestion, a Neo4j knowledge graph, hybrid retrieval over Chroma, gap analysis
and remediation drafting. The YAML block above is the Hugging Face Spaces config.

## Run locally

```bash
uv sync
uv run uvicorn api:app --reload
```

On Apple Silicon the LLM runs locally through MLX. Anywhere else, set
`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` to an OpenAI-compatible endpoint.

## Configuration

| Variable | Purpose |
|---|---|
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | Neo4j (Aura in production) |
| `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE` | Chroma Cloud |
| `CHROMA_REQUIRE_CLOUD` | `true` disables the local Chroma fallback (set in the Docker image) |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Hosted OpenAI-compatible LLM; unset means local MLX |

## Health checks

- `GET /health` — liveness.
- `GET /health/dependencies` — live connectivity to Neo4j, Chroma and the configured LLM.

## Deploy to a single VM (Oracle Cloud Always Free)

1. Create an Ubuntu 24.04 VM (shape `VM.Standard.A1.Flex`, 2 OCPU / 4 GB). In its
   subnet's security list, add ingress rules for TCP 80 and 443 from `0.0.0.0/0`.
2. On the VM:
   ```bash
   git clone https://github.com/Eesh-487/Agents.git && cd Agents
   bash deploy/oracle-setup.sh        # Docker + OS firewall; prints a DOMAIN to use
   exit                               # log back in for the docker group
   ```
3. Back on the VM: `cd Agents`, create `.env` from `.env.example` (Neo4j, Chroma, `DOMAIN`), then
   ```bash
   docker compose up -d --build
   curl https://$DOMAIN/health/dependencies
   ```
4. Update later with `git pull && docker compose up -d --build`.
