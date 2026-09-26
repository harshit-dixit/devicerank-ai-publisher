"""Send model requests from a private, runtime-supplied multipart template."""

import json
from typing import Any, Type
from urllib.parse import urlparse

import requests
from pydantic import BaseModel


class PrivateLLM:
    """Keep endpoint, headers, form field names, and fixed values outside the repo."""

    def __init__(self, secret: str):
        try:
            config = json.loads(secret)
            url = config["url"]
            headers = config.get("headers", {})
            form = config["form"]
            grounded_form = config.get("grounded_form", {})
            response_path = config["response_text_path"]
            if (
                not isinstance(url, str)
                or urlparse(url).scheme != "https"
                or not urlparse(url).netloc
                or not isinstance(headers, dict)
                or not isinstance(form, dict)
                or not form
                or not isinstance(grounded_form, dict)
                or not isinstance(response_path, str)
                or not response_path
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in form.items())
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in grounded_form.items())
                or "{{SYSTEM_PROMPT_JSON}}" not in json.dumps(form)
                or "{{USER_PROMPT_JSON}}" not in json.dumps(form)
            ):
                raise ValueError("invalid configuration")
        except (ValueError, KeyError, TypeError):
            raise ValueError("PRIVATE_LLM_REQUEST is invalid") from None

        self._url = url
        self._headers = headers
        self._form = form
        self._grounded_form = grounded_form
        self._response_path = response_path

    @property
    def supports_grounding(self) -> bool:
        return bool(self._grounded_form)

    def generate(
        self,
        system_prompt: str,
        prompt: str,
        response_schema: Type[BaseModel],
        *,
        grounded: bool = False,
    ) -> BaseModel:
        if grounded and not self._grounded_form:
            raise ValueError("PRIVATE_LLM_REQUEST has no grounded_form configuration")
        schema_instruction = (
            "\nReturn only a JSON object matching this schema, without Markdown fences:\n"
            + json.dumps(response_schema.model_json_schema(), separators=(",", ":"))
        )
        replacements = {
            "{{SYSTEM_PROMPT_JSON}}": json.dumps(system_prompt + schema_instruction),
            "{{USER_PROMPT_JSON}}": json.dumps(prompt),
        }
        template = {**self._form, **self._grounded_form} if grounded else self._form
        form = {
            key: self._replace_tokens(value, replacements)
            for key, value in template.items()
        }
        try:
            response = requests.post(
                self._url,
                headers=self._headers,
                files={key: (None, value) for key, value in form.items()},
                timeout=120,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Private model request failed (HTTP {response.status_code})")
            try:
                payload = response.json()
            except ValueError:
                raise ValueError("Private model response is not JSON") from None
            value = self._extract(payload, self._response_path.split("."))
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Private model response contains no text")
            value = value.strip()
            if value.startswith("```"):
                lines = value.splitlines()
                if len(lines) >= 3 and lines[-1].strip() == "```":
                    value = "\n".join(lines[1:-1]).strip()
            try:
                return response_schema.model_validate_json(value)
            except ValueError:
                raise ValueError("Private model response did not match the expected JSON schema") from None
        except requests.RequestException:
            # Request exceptions can include the URL, headers, or other private data.
            raise RuntimeError("Private model request failed (network error)") from None

    @staticmethod
    def _replace_tokens(value: str, replacements: dict[str, str]) -> str:
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        return value

    @classmethod
    def _extract(cls, value: Any, path: list[str]) -> Any:
        if not path:
            return value
        head, *tail = path
        if head == "*" and isinstance(value, list):
            parts = [cls._extract(item, tail) for item in value]
            return next((part for part in parts if isinstance(part, str) and part.strip()), None)
        if isinstance(value, list) and head.isdecimal():
            index = int(head)
            return cls._extract(value[index], tail) if index < len(value) else None
        if isinstance(value, dict):
            return cls._extract(value.get(head), tail)
        return None
