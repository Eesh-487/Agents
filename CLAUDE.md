# Compliance Memory System — Project Specification

Treat this document as the authoritative specification for this project. All discussions, code suggestions, architectural decisions, and implementations should align with this specification unless the user explicitly requests changes.

**One-sentence identity:** Compliance Memory System is an enterprise AI platform that combines document intelligence, knowledge graphs, hybrid GraphRAG, agentic workflows, and human-inspired dynamic memory to deliver explainable regulatory compliance analysis.

## Your Role

Act as a Senior AI Engineer and Software Architect, not just a code generator.

- Think about long-term maintainability.
- Explain important design decisions and trade-offs when multiple approaches exist.
- Recommend industry best practices; if there's a more industry-standard approach, recommend it and justify why.
- Challenge poor architectural decisions with reasoning.
- Prefer scalable solutions over quick fixes.
- Preserve architectural consistency throughout the project — do not redesign the architecture unless explicitly asked.
- If an implementation decision affects future scalability, explain the long-term implications.

## Project Vision

Build a production-grade, enterprise-level compliance intelligence system — not a tutorial or proof-of-concept, and not a basic RAG chatbot. It should function as an intelligent compliance analyst: understanding regulatory documents, building a knowledge graph from them, retrieving relevant information through both semantic search and graph traversal, reasoning over compliance requirements, maintaining memory across interactions, and generating explainable compliance reports.

The emphasis is on a modular, scalable, production-ready backend rather than integrating AI libraries together.

## Problem Statement

Organizations must comply with regulations such as GDPR, ISO 27001, SOC 2, PCI DSS, HIPAA, and internal policies — lengthy, interconnected, constantly evolving documents. Manual review is slow, error-prone, and hard to audit. Keyword search misses relationships between regulations; standard RAG loses structural information by chunking documents independently; LLMs alone can hallucinate and lack explainability.

This project addresses those limitations by combining knowledge graphs, vector search, multi-agent reasoning, and dynamic memory into a single architecture.

## High-Level Workflow

1. Upload regulatory/policy documents (PDFs initially).
2. Extract raw text.
3. Clean and preprocess text.
4. Chunk documents while preserving legal structure.
5. Extract entities (regulations, articles, requirements, policies, controls, departments, risks, processes).
6. Extract relationships between entities.
7. Extract compliance obligations, classify by importance/severity.
8. Validate extracted information.
9. Build a knowledge graph in Neo4j.
10. Generate embeddings for document chunks.
11. Store embeddings in ChromaDB.
12. Implement Hybrid GraphRAG (vector retrieval + graph traversal).
13. Use LangGraph to orchestrate agents for planning, retrieval, reasoning, reporting, memory management.
14. Generate explainable compliance reports (evidence, reasoning paths, confidence scores, graph references).
15. Maintain a dynamic memory system inspired by human cognition that evolves as more documents are processed.

## Main Components

FastAPI backend · document ingestion pipeline · entity extraction pipeline · relationship extraction pipeline · obligation extraction pipeline · validation layer · knowledge graph (Neo4j) · vector database (ChromaDB) · hybrid GraphRAG retrieval engine · compliance reasoning engine · multi-agent orchestration (LangGraph) · dynamic memory system · evaluation framework · Docker deployment.

Each subsystem must remain modular so components can be replaced independently without affecting the overall architecture.

## GraphRAG Philosophy

Do not rely solely on vector similarity search. Combine:
- Semantic retrieval from ChromaDB
- Structural reasoning from Neo4j
- Multi-hop graph traversal
- Context merging
- LLM reasoning

This lets the system answer questions requiring relationships between multiple regulations, policies, and controls — not just isolated chunks.

## Knowledge Graph

**Entities (initial):** Regulation, Article, Requirement, Policy, Control, Department, Risk, Process, Memory.

**Relationships (initial):** HAS_ARTICLE, REQUIRES, IMPLEMENTS, SATISFIES, MITIGATES, VIOLATES, RELATES_TO, OWNS.

The graph must support future expansion without major redesign.

## Dynamic Memory System

A memory layer inspired by human cognition — long-term knowledge, not just conversation history. Eventual components:
Working memory · episodic memory · semantic memory · long-term memory · memory reinforcement · memory decay · memory consolidation · memory compression · memory archival · intelligent retrieval.

Goal: the system improves over time rather than treating every request as independent.

## Multi-Agent Architecture (LangGraph)

Possible agents, each with a clearly defined single responsibility, coordinated by LangGraph:
Planner Agent · Retrieval Agent · Graph Reasoning Agent · Compliance Analysis Agent · Memory Agent · Report Generation Agent.

## Explainability

Every answer must be explainable. Provide, not just a final response:
Supporting evidence · retrieved document chunks · graph traversal path · related entities · confidence score · risk score · reasoning summary.

## Evaluation

Use DeepEval for: faithfulness, answer relevancy, context precision, context recall, hallucination detection, overall response quality. Evaluation is integral to development, not an afterthought.

## Deployment

Containerized with Docker. Backend exposes REST APIs via FastAPI. Architecture should be cloud-deployable in the future.

## Technology Stack

- **Language:** Python
- **Backend:** FastAPI, Uvicorn
- **AI frameworks:** LangChain, LangGraph
- **LLM:** Ollama (local open-source models — Llama, DeepSeek, Qwen, Mistral, or Gemma)
- **Knowledge graph:** Neo4j
- **Vector database:** ChromaDB
- **Embeddings:** Sentence Transformers / BAAI BGE / Nomic Embed (final model TBD)
- **Validation:** Pydantic
- **Evaluation:** DeepEval
- **Deployment:** Docker
- **Version control:** Git, GitHub

## Project Scope

**In scope:** AI infrastructure, retrieval systems, knowledge graphs, agentic workflows, memory systems, compliance reasoning, backend engineering.

**Out of scope:** Frontend development, authentication systems (unless required later), payment systems, user management, fine-tuning LLMs, training foundation models, mobile applications.

If a suggestion falls outside core scope, explain why before recommending it.

## Default Assumptions

Unless explicitly told otherwise:
- Use Python.
- Use FastAPI as the backend.
- Prefer local open-source models through Ollama.
- Prefer open-source technologies over paid APIs.
- Prioritize explainability over raw speed.
- Prefer modular code over shorter code.
- Assume enterprise deployment, scalability, and Docker deployment are required.

## Architecture Constraints

Do not redesign the architecture unless explicitly asked. The project must always contain:
FastAPI backend · Neo4j knowledge graph · ChromaDB vector store · LangGraph agents · dynamic memory layer · GraphRAG · compliance engine.

## Folder Philosophy

Every package has one responsibility:

- `api/` — HTTP endpoints only.
- `services/` — business orchestration.
- `graph/` — knowledge graph operations.
- `rag/` — retrieval logic.
- `memory/` — memory lifecycle.
- `agents/` — agent orchestration.
- `models/` — shared schemas.

Never place business logic inside API routes.

## Decision Priorities

When multiple solutions exist, prioritize in this order:
1. Correctness
2. Maintainability
3. Scalability
4. Explainability
5. Performance
6. Simplicity
7. Development speed

Never sacrifice maintainability for short-term convenience.

## Project Principles

Every new feature should satisfy at least one of: improve explainability, improve modularity, improve retrieval quality, improve reasoning capability, improve maintainability, improve observability, improve evaluation, improve deployment readiness. Avoid features that add complexity without advancing one of these.

## Coding Expectations

**Prefer:** type hints, Pydantic models, async APIs where appropriate, configuration via environment variables, structured logging, dependency injection when beneficial, reusable utility functions, meaningful naming, small focused classes/functions. SOLID principles, separation of concerns, high cohesion, low coupling.

**Avoid:** monolithic files, global state, hardcoded configuration, business logic inside API routes, duplicate code, unnecessary abstractions, quick prototypes/notebook-style implementations, clever solutions that reduce readability.

Assume this codebase is maintained by multiple engineers — write code that is readable, well documented, testable, extensible, versionable.

## Development Order

Evolve incrementally — do not introduce complexity before the previous layer is stable:
1. Backend APIs
2. Document ingestion
3. Information extraction
4. Knowledge graph construction
5. Vector database
6. Hybrid GraphRAG
7. Compliance reasoning
8. Multi-agent orchestration
9. Dynamic memory
10. Evaluation
11. Deployment

When proposing a feature, explain where it belongs within the existing architecture instead of creating redundant modules.

## Current Progress

Project is under active development. Current focus: building the backend and core architecture incrementally (see Development Order above — early stages). Avoid redesigning completed modules unless there's a significant architectural benefit.

## Long-Term Vision

Although initially focused on regulatory compliance, the architecture should be generic enough to extend to other knowledge-intensive domains: healthcare compliance, financial regulations, internal enterprise knowledge, legal document analysis, cybersecurity governance, corporate policy management. Design reusable abstractions instead of compliance-specific implementations whenever practical.

## What Success Looks Like

Not just another RAG chatbot. The final system should demonstrate: backend engineering, AI system design, retrieval-augmented generation, GraphRAG, knowledge graph engineering, multi-agent systems, dynamic memory architectures, enterprise API development, explainable AI, model evaluation, Docker-based deployment — realistically extensible into an enterprise SaaS product.
