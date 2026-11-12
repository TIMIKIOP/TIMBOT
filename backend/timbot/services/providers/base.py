"""Provider contract used by chat, embeddings, and readiness checks."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

class AIProviderError(Exception):
    """Provider-neutral inference failure safe to handle without leaking details."""

class AIProviderUnavailableError(AIProviderError):
    """The configured provider cannot currently serve requests; safe to retry."""

class AIModelUnavailableError(AIProviderError):
    """The provider is reachable but its configured model is not installed."""

@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    token_count: int | None
    latency_ms: int

class AIProvider(ABC):
    name: str
    chat_model: str
    embedding_model: str
    embedding_profile: str

    @abstractmethod
    def chat(self, **kwargs: Any):
        """Create one chat completion using the provider's configured model."""

    @abstractmethod
    def embed(self, texts: list[str]) -> EmbeddingBatch:
        """Create one embedding for every input text, preserving input order."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return safe availability metadata without credentials or secrets."""
