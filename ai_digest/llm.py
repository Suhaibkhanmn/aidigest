import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .config import env_value


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    used_fallback: bool = False


class LLMProvider:
    def generate(self, prompt: str, *, temperature: float = 0.4, max_output_tokens: int = 2048) -> LLMResponse:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    def __init__(self, model: str, api_key_env: str):
        self.model = model
        self.api_key = env_value(api_key_env)

    def generate(self, prompt: str, *, temperature: float = 0.4, max_output_tokens: int = 2048) -> LLMResponse:
        if not self.api_key:
            return OfflineProvider(self.model).generate(
                prompt, temperature=temperature, max_output_tokens=max_output_tokens
            )

        encoded_model = urllib.parse.quote(self.model, safe="")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{encoded_model}:generateContent?key={urllib.parse.quote(self.api_key)}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "topP": 0.9,
                "maxOutputTokens": max_output_tokens,
            },
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        data = {}
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except Exception:
                if attempt == 0:
                    time.sleep(1)
                    continue
                return OfflineProvider(self.model).generate(
                    prompt, temperature=temperature, max_output_tokens=max_output_tokens
                )

        text = ""
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text += part.get("text", "")
        if not text.strip():
            return OfflineProvider(self.model).generate(
                prompt, temperature=temperature, max_output_tokens=max_output_tokens
            )
        return LLMResponse(text=text.strip(), provider="gemini", model=self.model)


class OfflineProvider(LLMProvider):
    def __init__(self, model: str = "local-preview"):
        self.model = model

    def generate(self, prompt: str, *, temperature: float = 0.4, max_output_tokens: int = 2048) -> LLMResponse:
        return LLMResponse(text="", provider="offline", model=self.model, used_fallback=True)


def provider_for(provider: str, model: str, api_key_env: str) -> LLMProvider:
    if provider == "gemini":
        return GeminiProvider(model=model, api_key_env=api_key_env)
    return OfflineProvider(model=model)
