# Contract: Configuration Schema Changes

**Feature**: 007-multi-model-embedding

## config_schema.json — `embedding` Section

### New Schema (replaces existing)

```json
{
  "embedding": {
    "type": "object",
    "properties": {
      "default": {
        "type": "string",
        "description": "Name of the default embedding model from the models map"
      },
      "models": {
        "type": "object",
        "additionalProperties": {
          "type": "object",
          "required": ["model", "vector_size", "vector_name"],
          "properties": {
            "model": { "type": "string" },
            "vector_size": { "type": "integer", "minimum": 1 },
            "vector_name": { "type": "string" },
            "max_tokens": { "type": "integer", "minimum": 1, "default": 512 },
            "index_prefix": { "type": "string", "default": "" },
            "query_prefix": { "type": "string", "default": "" }
          }
        }
      },
      "model": { "type": "string" },
      "vector_size": { "type": "integer", "minimum": 1 },
      "vector_name": { "type": "string" },
      "max_tokens": { "type": "integer", "minimum": 1 }
    }
  }
}
```

**Backward compatibility**: The flat fields (`model`, `vector_size`, `vector_name`, `max_tokens`) remain valid. When `models` is absent or empty, the flat fields are used to create a single `"default"` model entry.

## config_schema.json — Routing Rule `embedding` Field

```json
{
  "routing": {
    "properties": {
      "rules": {
        "items": {
          "properties": {
            "embedding": {
              "type": "string",
              "description": "Name of embedding model config to use for this rule's collection"
            }
          }
        }
      }
    }
  }
}
```

## defaults.yaml Changes

Current:
```yaml
embedding:
  model: "sentence-transformers/all-MiniLM-L6-v2"
  vector_size: 384
  vector_name: "fast-all-minilm-l6-v2"
  max_tokens: 512
```

Updated (backward-compatible — flat fields preserved as fallback):
```yaml
embedding:
  model: "sentence-transformers/all-MiniLM-L6-v2"
  vector_size: 384
  vector_name: "fast-all-minilm-l6-v2"
  max_tokens: 512
```

No change to defaults.yaml — the default remains the legacy single-model format. Users opt-in to multi-model by adding `embedding.models` and `embedding.default` in their config file.

## Production Config Example

```yaml
embedding:
  default: docs
  models:
    docs:
      model: "nomic-ai/nomic-embed-text-v1.5"
      vector_size: 768
      vector_name: "nomic-v1.5"
      max_tokens: 512
      index_prefix: "search_document: "
      query_prefix: "search_query: "
    code:
      model: "jinaai/jina-embeddings-v2-base-code"
      vector_size: 768
      vector_name: "jina-code-v2"
      max_tokens: 512

routing:
  default_collection: "vista"
  rules:
    - name: rpms-source-code
      file_group: ["mumps-source", "mumps-globals", "general-source"]
      path: ["^(?i).*ihs\\.gov", "^(?i).*rpms"]
      collection: rpms-source
      embedding: code
    - name: rpms-docs
      path: ["^(?i).*ihs\\.gov", "^(?i).*rpms"]
      collection: rpms
    - name: vista-source-code
      file_group: ["mumps-source", "mumps-globals", "general-source"]
      collection: vista-source
      embedding: code
```
