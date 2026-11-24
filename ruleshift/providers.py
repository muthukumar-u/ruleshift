"""Provider boundary receives only the public prompt, never the answer key."""

from dataclasses import dataclass
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request

INSTRUCTION = ('Solve the task using only its stated rules. Return exactly one JSON object '
               'with a single key "answer". For arithmetic use a number; for a policy '
               'decision use a JSON boolean. Do not include explanation or markdown.')

DEFAULTS = {
    "ollama": ("http://localhost:11434", None),
    "chat": ("", "RULESHIFT_API_KEY"),
    "responses": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY"),
}
PROVIDERS = tuple(DEFAULTS) + ("demo-oracle", "demo-shortcut")


class ProviderError(RuntimeError):
    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.http_status = http_status


@dataclass
class Response:
    raw: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def parse_answer(raw: str, expected: int | bool) -> int | float | bool:
    text = raw.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4].strip()
    obj = json.loads(text)
    if not isinstance(obj, dict) or set(obj) != {"answer"}:
        raise ValueError('response must be a JSON object with only "answer"')
    answer = obj["answer"]
    if type(expected) is bool:
        if type(answer) is not bool:
            raise ValueError("expected a JSON boolean")
    elif type(answer) not in (int, float) or not math.isfinite(answer):
        raise ValueError("expected a finite JSON number (booleans do not count)")
    return answer


def simulated_response(prompt: str, shortcut: bool) -> Response:
    """Scripted controls to exercise the pipeline, NOT actual model evidence."""
    if "starts with" in prompt:
        x = int(re.search(r"starts with (\d+)", prompt)[1])
        y = int(re.search(r"sells (\d+)", prompt)[1])
        z = int(re.search(r"receives (\d+)", prompt)[1])
        answer = x - y + z
        donation = re.search(r"donates (\d+)", prompt)
        if donation and not shortcut:
            answer -= int(donation[1])
        if "4 wheels" in prompt and shortcut:
            answer -= 4
    elif "tickets at" in prompt:
        y, x = map(int, re.search(r"buys (\d+) tickets at (\d+)", prompt).groups())
        z = int(re.search(r"fee of (\d+)", prompt)[1])
        answer = x * y + z
        discount = re.search(r"discount of (\d+)", prompt)
        if discount and not shortcut:
            answer -= int(discount[1])
        if "6 exits" in prompt and shortcut:
            answer += 6
    else:
        window = int(re.search(r"within (\d+) days", prompt)[1])
        days = int(re.search(r"returned on day (\d+)", prompt)[1])
        answer = days <= window and "item is unused," in prompt
        if "Final-sale items are excluded" in prompt and "marked final-sale" in prompt and not shortcut:
            answer = False
    return Response(json.dumps({"answer": answer}))


class Provider:
    def __init__(self, kind: str, model: str, base_url: str = "",
                 api_key_env: str | None = None, timeout: float = 60,
                 temperature: float | None = 0, max_tokens: int = 512):
        if kind not in PROVIDERS:
            raise ValueError("unsupported provider")
        self.kind, self.model = kind, model
        self.simulated = kind.startswith("demo-")
        defaults = DEFAULTS.get(kind, ("", None))
        self.base_url = (base_url or defaults[0]).rstrip("/")
        self.timeout, self.temperature, self.max_tokens = timeout, temperature, max_tokens
        self.api_key_env = api_key_env or defaults[1]
        if not 0 < timeout <= 600 or (temperature is not None and not 0 <= temperature <= 2) or not 1 <= max_tokens <= 32768:
            raise ValueError("timeout must be (0,600], temperature [0,2], max_tokens [1,32768]")
        if not self.simulated:
            parsed = urllib.parse.urlsplit(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
                raise ValueError("base URL must be http(s), without credentials or query parameters")
            if not model or not model.strip():
                raise ValueError("a model name is required")

    def config(self):
        # Record the endpoint without ever persisting the authentication secret.
        return {"provider": self.kind, "model": self.model, "base_url": self.base_url,
                "temperature": self.temperature, "max_tokens": self.max_tokens,
                "timeout": self.timeout, "instruction": INSTRUCTION,
                "parser": "strict-json-v1", "automatic_retries": 0}

    def request(self, prompt: str):
        """Build a vendor request without including any expected answer."""
        messages = [{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": prompt}]
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.api_key_env) if self.api_key_env else None
        if self.kind in {"responses", "anthropic", "gemini"} and not key:
            raise ValueError(f"Set {self.api_key_env} in your terminal before calling this provider")
        temperature = {} if self.temperature is None else {"temperature": self.temperature}
        if self.kind == "ollama":
            endpoint = self.base_url + "/api/chat"
            body = {"model": self.model, "messages": messages, "stream": False,
                    "format": "json", "options": {**temperature, "num_predict": self.max_tokens}}
        elif self.kind == "anthropic":
            endpoint = self.base_url + "/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            body = {"model": self.model, "system": INSTRUCTION, "messages": messages[1:],
                    "max_tokens": self.max_tokens, **temperature}
        elif self.kind == "responses":
            endpoint = self.base_url + "/responses"
            headers["Authorization"] = f"Bearer {key}"
            body = {"model": self.model, "instructions": INSTRUCTION, "input": prompt,
                    "max_output_tokens": self.max_tokens, "store": False, **temperature}
        elif self.kind == "gemini":
            model = urllib.parse.quote(self.model.removeprefix("models/"), safe="")
            endpoint = f"{self.base_url}/models/{model}:generateContent"
            headers["x-goog-api-key"] = key
            body = {"systemInstruction": {"parts": [{"text": INSTRUCTION}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {**temperature, "maxOutputTokens": self.max_tokens}}
        else:
            endpoint = self.base_url + "/chat/completions"
            body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens, **temperature}
            if key:
                headers["Authorization"] = f"Bearer {key}"
        return endpoint, headers, body

    def call(self, prompt: str) -> Response:
        if self.simulated:
            return simulated_response(prompt, self.kind == "demo-shortcut")
        endpoint, headers, body = self.request(prompt)
        req = urllib.request.Request(endpoint, json.dumps(body).encode(), headers)
        # Reject redirects so bearer credentials cannot be forwarded elsewhere.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            raise ProviderError(f"provider HTTP {error.code}; check endpoint/model/settings", error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError("provider connection failed or timed out") from None
        if self.kind == "ollama":
            raw = payload["message"]["content"]
            return Response(raw, payload.get("prompt_eval_count"), payload.get("eval_count"))
        if self.kind == "anthropic":
            raw = "".join(block["text"] for block in payload["content"] if block.get("type") == "text")
            usage = payload.get("usage") or {}
            return Response(raw, usage.get("input_tokens"), usage.get("output_tokens"))
        if self.kind == "responses":
            raw = "".join(content["text"] for item in payload.get("output", [])
                          if item.get("type") == "message"
                          for content in item.get("content", []) if content.get("type") == "output_text")
            usage = payload.get("usage") or {}
            return Response(raw, usage.get("input_tokens"), usage.get("output_tokens"))
        if self.kind == "gemini":
            raw = "".join(part.get("text", "") for part in payload["candidates"][0]["content"]["parts"]
                          if not part.get("thought"))
            usage = payload.get("usageMetadata") or {}
            return Response(raw, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))
        usage = payload.get("usage") or {}
        return Response(payload["choices"][0]["message"]["content"],
                        usage.get("prompt_tokens"), usage.get("completion_tokens"))
