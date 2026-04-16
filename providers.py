import asyncio
import httpx
import logging
import os

log = logging.getLogger(__name__)

MAX_RETRIES = 5


class LLMProvider:
    async def complete(self, prompt: str) -> str:
        raise NotImplementedError


class AnthropicProvider(LLMProvider):
    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        self.model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

    def _headers(self) -> dict:
        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        else:
            headers["x-api-key"] = self.api_key
        return headers

    async def complete(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            for attempt in range(MAX_RETRIES):
                r = await client.post(
                    f"{self.base_url.rstrip('/')}/v1/messages",
                    headers=self._headers(),
                    json={
                        "model": self.model,
                        "max_tokens": 8192,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if r.status_code == 429:
                    wait = int(r.headers.get("retry-after", 2 ** attempt))
                    if wait > 60:
                        raise RuntimeError(
                            f"Groq rate limit: лимит исчерпан, сброс через {wait // 60} мин. "
                            f"Попробуй позже."
                        )
                    log.warning("Rate limit (429), ждём %ds (попытка %d/%d)...", wait, attempt + 1, MAX_RETRIES)
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                if "content" not in data:
                    raise ValueError(f"Unexpected Anthropic response: {data}")
                return data["content"][0]["text"]
            raise RuntimeError("Превышено количество попыток после rate limit")


class OpenAIProvider(LLMProvider):
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o")

    async def complete(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            for attempt in range(MAX_RETRIES):
                r = await client.post(
                    f"{self.base_url.rstrip('/')}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": 8192,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if r.status_code == 429:
                    wait = int(r.headers.get("retry-after", 2 ** attempt))
                    if wait > 60:
                        raise RuntimeError(
                            f"Groq rate limit: лимит исчерпан, сброс через {wait // 60} мин. "
                            f"Попробуй позже."
                        )
                    log.warning("Rate limit (429), ждём %ds (попытка %d/%d)...", wait, attempt + 1, MAX_RETRIES)
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                if "choices" not in data:
                    raise ValueError(f"Unexpected OpenAI response: {data}")
                return data["choices"][0]["message"]["content"]
            raise RuntimeError("Превышено количество попыток после rate limit")


def get_provider() -> LLMProvider:
    name = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    if name == "openai":
        p = OpenAIProvider()
        if not p.api_key:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
        log.info("LLM provider: OpenAI | model=%s base_url=%s", p.model, p.base_url)
        return p
    if name == "anthropic":
        p = AnthropicProvider()
        if not p.api_key and not p.auth_token:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic but neither ANTHROPIC_API_KEY "
                "nor ANTHROPIC_AUTH_TOKEN is set"
            )
        log.info("LLM provider: Anthropic | model=%s base_url=%s", p.model, p.base_url)
        return p
    raise ValueError(f"Unknown LLM_PROVIDER={name!r}. Use 'anthropic' or 'openai'.")
