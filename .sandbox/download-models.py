"""Pre-download ML models into the local cache.

Used by the Dockerfile to bake models into the sandbox image so tests can run
offline (HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1).

Cache locations (defaults — no env override required):
  fastembed  : /tmp/fastembed_cache  (fastembed uses tempfile.gettempdir())
  HF tokenizer: ~/.cache/huggingface/hub
"""

import sys

MODEL = "sentence-transformers/all-MiniLM-L6-v2"

try:
    from transformers import AutoTokenizer

    AutoTokenizer.from_pretrained(MODEL)
    print(f"  HF tokenizer cached: {MODEL}")
except Exception as e:
    print(f"  WARNING: tokenizer download failed: {e}", file=sys.stderr)
    sys.exit(1)

try:
    from fastembed import TextEmbedding

    TextEmbedding(model_name=MODEL)
    print(f"  fastembed ONNX model cached: {MODEL}")
except Exception as e:
    print(f"  WARNING: fastembed model download failed: {e}", file=sys.stderr)
    sys.exit(1)
