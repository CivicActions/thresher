# thresher Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-03-29

## Active Technologies
- Python 3.13 (Docker), 3.14.3 (local dev) + fastembed (ONNX embeddings), qdrant-client (vector store), fastmcp (MCP server), pydantic (settings) (007-multi-model-embedding)
- Qdrant (vector search), GCS (document/queue storage) (007-multi-model-embedding)

- Python 3.11+ (Docker image: 3.13) + google-cloud-storage, kubernetes, pyyaml, jsonschema (006-parallel-archive-expansion)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.11+ (Docker image: 3.13): Follow standard conventions

## Recent Changes
- 007-multi-model-embedding: Added Python 3.13 (Docker), 3.14.3 (local dev) + fastembed (ONNX embeddings), qdrant-client (vector store), fastmcp (MCP server), pydantic (settings)

- 006-parallel-archive-expansion: Added Python 3.11+ (Docker image: 3.13) + google-cloud-storage, kubernetes, pyyaml, jsonschema

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
