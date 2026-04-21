"""
AI Client service using OpenAI-compatible API.

Works with Ollama, OpenAI, Azure OpenAI, or any OpenAI-compatible endpoint.
"""

import logging
from typing import Optional

import httpx
from openai import OpenAI

from app.config import get_settings_manager

logger = logging.getLogger(__name__)


class AIClient:
    """
    Client for OpenAI-compatible AI APIs.

    Supports embeddings and chat completions.
    Compatible with Ollama, OpenAI, and other OpenAI-compatible services.
    """

    _instance: Optional["AIClient"] = None
    _client: Optional[OpenAI] = None

    def __new__(cls) -> "AIClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _get_client(self) -> OpenAI:
        """Get or create the OpenAI client with current settings."""
        settings_mgr = get_settings_manager()
        ai_settings = settings_mgr.ai_settings

        # Build an httpx.Client if proxy or custom certificate is configured
        http_client: Optional[httpx.Client] = None
        if ai_settings.proxy_url or ai_settings.ssl_certificate_path:
            verify: bool | str = ai_settings.ssl_certificate_path or True
            if ai_settings.proxy_url:
                proxy_auth = (
                    (ai_settings.proxy_username, ai_settings.proxy_password or "")
                    if ai_settings.proxy_username else None
                )
                proxy = httpx.Proxy(url=ai_settings.proxy_url, auth=proxy_auth)
                http_client = httpx.Client(proxy=proxy, verify=verify)
            else:
                http_client = httpx.Client(verify=verify)

        # Always recreate client to pick up setting changes
        self._client = OpenAI(
            base_url=ai_settings.api_base_url,
            api_key=ai_settings.api_key,
            **({"http_client": http_client} if http_client else {}),
        )
        return self._client

    def generate_embedding(self, text: str) -> list[float]:
        """
        Generate embedding vector for a single text.

        Args:
            text: The text to embed.

        Returns:
            List of floats representing the embedding vector.

        Raises:
            Exception: If API call fails.
        """
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        settings_mgr = get_settings_manager()
        model = settings_mgr.ai_settings.embedding_model

        client = self._get_client()

        try:
            response = client.embeddings.create(
                model=model,
                input=text
            )
            embedding = response.data[0].embedding
            logger.debug(f"Generated embedding with {len(embedding)} dimensions")
            return embedding

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise RuntimeError(f"Failed to generate embedding: {e}") from e

    def generate_embeddings(self, texts: list[str], batch_size: int = 10) -> list[list[float]]:
        """
        Generate embedding vectors for multiple texts (batch processing).

        Processes texts in smaller batches for Ollama compatibility.

        Args:
            texts: List of texts to embed.
            batch_size: Number of texts per API call (default 10 for Ollama).

        Returns:
            List of embedding vectors.

        Raises:
            Exception: If API call fails.
        """
        if not texts:
            return []

        # Filter empty texts and track original indices
        valid_items = [(i, t) for i, t in enumerate(texts) if t and t.strip()]
        if not valid_items:
            return []

        settings_mgr = get_settings_manager()
        model = settings_mgr.ai_settings.embedding_model

        client = self._get_client()
        all_embeddings: list[list[float]] = []

        total_batches = (len(valid_items) + batch_size - 1) // batch_size
        logger.info(f"Processing {len(valid_items)} texts in {total_batches} batches")

        try:
            for batch_num in range(total_batches):
                start_idx = batch_num * batch_size
                end_idx = min(start_idx + batch_size, len(valid_items))
                batch_texts = [t for _, t in valid_items[start_idx:end_idx]]

                logger.debug(f"Embedding batch {batch_num + 1}/{total_batches} ({len(batch_texts)} texts)")

                response = client.embeddings.create(
                    model=model,
                    input=batch_texts
                )

                # Sort by index to maintain order within batch
                sorted_data = sorted(response.data, key=lambda x: x.index)
                batch_embeddings = [item.embedding for item in sorted_data]
                all_embeddings.extend(batch_embeddings)

                if (batch_num + 1) % 10 == 0 or batch_num + 1 == total_batches:
                    logger.info(f"Embeddings progress: {batch_num + 1}/{total_batches} batches complete")

            logger.info(f"Generated {len(all_embeddings)} embeddings successfully")
            return all_embeddings

        except Exception as e:
            logger.error(f"Batch embedding generation failed at batch {batch_num + 1}: {e}")
            raise RuntimeError(f"Failed to generate embeddings: {e}") from e

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Generate a chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            temperature: Sampling temperature (0.0 to 1.0).
            max_tokens: Maximum tokens in response.

        Returns:
            The generated text response.

        Raises:
            Exception: If API call fails.
        """
        settings_mgr = get_settings_manager()
        model = settings_mgr.ai_settings.chat_model

        client = self._get_client()

        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens

            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""

            logger.debug(f"Chat completion generated: {len(content)} chars")
            return content

        except Exception as e:
            logger.error(f"Chat completion failed: {e}")
            raise RuntimeError(f"Failed to generate chat completion: {e}") from e

    def list_models(self) -> list[str]:
        """
        List available models from the configured API.

        Returns:
            Sorted list of model IDs.

        Raises:
            Exception: If API call fails.
        """
        client = self._get_client()
        try:
            response = client.models.list()
            model_ids = sorted([m.id for m in response.data])
            logger.info(f"Listed {len(model_ids)} models from API")
            return model_ids
        except Exception as e:
            logger.error(f"Failed to list models: {e}")
            raise RuntimeError(f"Failed to list models: {e}") from e

    def test_connection(self) -> dict[str, bool]:
        """
        Test connection to the AI API.

        Returns:
            Dict with 'embeddings' and 'chat' status.
        """
        results = {"embeddings": False, "chat": False}

        # Test embeddings
        try:
            _ = self.generate_embedding("test")
            results["embeddings"] = True
            logger.info("Embedding API test: OK")
        except Exception as e:
            logger.warning(f"Embedding API test failed: {e}")

        # Test chat
        try:
            _ = self.chat_completion([{"role": "user", "content": "Hi"}], max_tokens=5)
            results["chat"] = True
            logger.info("Chat API test: OK")
        except Exception as e:
            logger.warning(f"Chat API test failed: {e}")

        return results


# Singleton instance
_ai_client: Optional[AIClient] = None


def get_ai_client() -> AIClient:
    """Get the singleton AI client instance."""
    global _ai_client
    if _ai_client is None:
        _ai_client = AIClient()
    return _ai_client
