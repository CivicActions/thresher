"""FastEmbed ONNX embedding wrapper."""

from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger("thresher.embedder")


class Embedder:
    """Wrapper around fastembed for generating text embeddings.

    Uses ONNX runtime for fast CPU-based inference.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        max_tokens: int = 512,
    ):
        self.model_name = model_name
        self.max_tokens = max_tokens
        self._model = None

    def _ensure_model(self) -> None:
        """Lazy-load the embedding model."""
        if self._model is None:
            from fastembed import TextEmbedding

            logger.info("Loading embedding model: %s", self.model_name)
            self._model = TextEmbedding(model_name=self.model_name)
            logger.info("Embedding model loaded")

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (each is list[float] of size vector_size).
        """
        if not texts:
            return []
        self._ensure_model()
        assert self._model is not None
        embeddings = list(self._model.embed(list(texts)))
        return [emb.tolist() for emb in embeddings]

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        results = self.embed_texts([text])
        return results[0]

    def preload(self) -> None:
        """Pre-load the embedding model (call before parallel workers start)."""
        self._ensure_model()
