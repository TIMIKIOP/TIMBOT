"""Local Ollama provider over its OpenAI-compatible HTTP API."""
from typing import Any
import json
from uuid import uuid4
from urllib.parse import urlsplit
from time import perf_counter

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageToolCall

from timbot.services.providers.base import AIModelUnavailableError, AIProvider, AIProviderError, AIProviderUnavailableError, EmbeddingBatch

class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, config):
        self.base_url = config.ollama_base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("OLLAMA_BASE_URL must be an absolute HTTP(S) URL.")
        if parsed.hostname.lower() == "openai.com" or parsed.hostname.lower().endswith(".openai.com"):
            raise ValueError("Ollama provider cannot use an OpenAI server URL.")
        if parsed.hostname.lower() in {"api.openai.com", "www.openai.com"}:
            raise ValueError("Ollama provider cannot use an OpenAI server URL.")
        self.chat_model = config.ollama_chat_model
        self.embedding_model = config.ollama_embedding_model
        self.embedding_profile = f"{self.name}:{self.embedding_model}:{config.embedding_dimensions}"
        self.embedding_dimensions = config.embedding_dimensions
        self.timeout = config.ai_request_timeout_seconds
        self.max_retries = config.ai_max_retries
        # Explicit base URL, dummy local credential, and no redirects prevent
        # accidental fall-through to the OpenAI default endpoint.
        http_client = httpx.Client(follow_redirects=False, timeout=self.timeout, trust_env=False)
        self._client = OpenAI(
            api_key="ollama-local-no-auth",
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            http_client=http_client,
        )

    def chat(self, **kwargs: Any):
        try:
            response = self._client.chat.completions.create(model=self.chat_model, **kwargs)
            # Some local models return a tool request as JSON content even when
            # served through Ollama's OpenAI-compatible API. Normalize only
            # names declared by the current request; never execute free-form
            # tool names or arguments outside the service's existing allowlist.
            if kwargs.get("tools") and kwargs.get("tool_choice", "auto") != "none":
                response = self._normalize_content_tool_call(response, kwargs["tools"])
            return response
        except (APIConnectionError, APITimeoutError) as exc:
            raise AIProviderUnavailableError("Ollama is not responding or did not finish in time.") from exc
        except APIStatusError as exc:
            if exc.status_code == 404:
                raise AIModelUnavailableError(f"Ollama chat model '{self.chat_model}' is not installed.") from exc
            if exc.status_code >= 500:
                raise AIProviderUnavailableError("Ollama inference failed temporarily.") from exc
            raise AIProviderError("Ollama rejected the chat request; check model and tool compatibility.") from exc
        except OpenAIError as exc:
            raise AIProviderError("Ollama chat request failed.") from exc

    @staticmethod
    def _normalize_content_tool_call(response, tools):
        if not response.choices:
            return response
        choice = response.choices[0]
        message = choice.message
        if message.tool_calls or not message.content:
            return response
        try:
            request = json.loads(message.content)
        except (TypeError, json.JSONDecodeError):
            return response
        name = request.get("name") if isinstance(request, dict) else None
        allowed_names = {
            item.get("function", {}).get("name")
            for item in tools
            if isinstance(item, dict) and isinstance(item.get("function"), dict)
        }
        if name not in allowed_names:
            return response
        arguments = request.get("arguments", request.get("parameters", {}))
        if isinstance(arguments, list):
            arguments = arguments[0] if len(arguments) == 1 else {"items": arguments}
        if not isinstance(arguments, dict):
            return response
        call = ChatCompletionMessageToolCall.model_validate({
            "id": f"ollama_{uuid4().hex}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        })
        normalized_message = message.model_copy(update={"tool_calls": [call], "content": None})
        normalized_choice = choice.model_copy(update={"message": normalized_message, "finish_reason": "tool_calls"})
        return response.model_copy(update={"choices": [normalized_choice, *response.choices[1:]]})

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        started = perf_counter()
        try:
            result = self._client.embeddings.create(model=self.embedding_model, input=texts)
        except (APIConnectionError, APITimeoutError) as exc:
            raise AIProviderUnavailableError("Ollama is not responding or did not finish in time.") from exc
        except APIStatusError as exc:
            if exc.status_code == 404:
                raise AIModelUnavailableError(f"Ollama embedding model '{self.embedding_model}' is not installed.") from exc
            if exc.status_code >= 500:
                raise AIProviderUnavailableError("Ollama embedding failed temporarily.") from exc
            raise AIProviderError("Ollama rejected the embedding request; check model compatibility.") from exc
        except OpenAIError as exc:
            raise AIProviderError("Ollama embedding request failed.") from exc
        ordered = sorted(result.data, key=lambda item: item.index)
        return EmbeddingBatch(
            vectors=[list(item.embedding) for item in ordered],
            token_count=getattr(getattr(result, "usage", None), "total_tokens", None),
            latency_ms=round((perf_counter() - started) * 1000),
        )

    @staticmethod
    def _tagged(model: str) -> str:
        final_part = model.rsplit("/", 1)[-1]
        return model if ":" in final_part else f"{model}:latest"

    def health(self) -> dict[str, Any]:
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        try:
            response = httpx.get(f"{root}/api/tags", timeout=min(self.timeout, 3.0), follow_redirects=False, trust_env=False)
            response.raise_for_status()
            installed = {str(item.get("name", "")) for item in response.json().get("models", [])}
            installed_profiles = {self._tagged(name) for name in installed}
            chat_installed = self._tagged(self.chat_model) in installed_profiles
            embedding_installed = self._tagged(self.embedding_model) in installed_profiles
            return {
                "available": True,
                "chat_model_available": chat_installed,
                "embedding_model_available": embedding_installed,
                "error": None if chat_installed and embedding_installed else "One or more configured models are not installed.",
            }
        except (httpx.HTTPError, ValueError):
            return {
                "available": False,
                "chat_model_available": False,
                "embedding_model_available": False,
                "error": "Ollama is not responding at the configured local endpoint.",
            }
