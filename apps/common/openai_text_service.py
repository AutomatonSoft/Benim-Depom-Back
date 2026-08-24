from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)


class OpenAITextServiceError(Exception):
    """Safe error for an AI generation job; never contains API credentials"""


class OpenAITextConfigurationError(OpenAITextServiceError):
    """OpenAI integration is disabled or incorrectly configured"""


class OpenAITextResponseError(OpenAITextServiceError):
    """The provider returned an unusable response"""


@dataclass(frozen=True)
class OpenAITextResult:
    model: str
    data: dict[str, Any]


class OpenAITextService:
    """
    Shared OpenAI Responses API client

    It retries transiet failures on the current model, then uses the next model
    from OPENAI_TEXT_MODELS. It never logs or exposes OPENAI_API_KEY
    """

    RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}

    def __init__(self) -> None:
        if not settings.OPENAI_ENABLED:
            raise OpenAITextConfigurationError("OpenAI generation is disabled")

        if not settings.OPENAI_API_KEY:
            raise OpenAITextConfigurationError("OPENAI_API_KEY is not configured")

        if not settings.OPENAI_TEXT_MODELS:
            raise OpenAITextConfigurationError(
                "OPENAI_TEXT_MODELS must contain at least one model"
            )

        self.client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.OPENAI_TEXT_TIMEOUT_SECONDS,
            max_retries=0,
        )

    @classmethod
    def _is_retryable_error(cls, error: Exception) -> bool:
        if isinstance(
            error,
            (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError),
        ):
            return True

        if isinstance(error, APIStatusError):
            return error.status_code in cls.RETRYABLE_STATUS_CODES

        return False

    @staticmethod
    def _read_json_output(response) -> dict[str, Any]:
        output_text = (response.output_text or "").strip()

        if not output_text:
            raise OpenAITextResponseError(
                "OpenAI returned an empty structured response"
            )

        try:
            result = json.loads(output_text)

        except json.JSONDecodeError:
            raise OpenAITextResponseError("OpenAI returned invalid JSON")

        if not isinstance(result, dict):
            raise OpenAITextResponseError(
                "OpenAI structured response must be a JSON object"
            )

        return result

    def generate_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> OpenAITextResult:
        """
        Generate one object matching a strict JSON Schema.

        The caller owns the prompt and schema
        This class only handles transport, retries, fallback models and JSON parsing
        """

        errors: list[str] = []
        attempts_per_model = max(settings.OPENAI_TEXT_MAX_RETRIES_PER_MODEL, 1)

        for model in settings.OPENAI_TEXT_MODELS:
            for attempt in range(1, attempts_per_model + 1):
                try:
                    response = self.client.responses.create(
                        model=model,
                        instructions=instructions,
                        input=input_text,
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": schema_name,
                                "strict": True,
                                "schema": schema,
                            }
                        },
                    )

                    return OpenAITextResult(
                        model=model, data=self._read_json_output(response)
                    )

                except OpenAITextResponseError:
                    errors.append(f"{model}: invalid structured response")
                    break

                except Exception as error:
                    if not self._is_retryable_error(error):
                        raise OpenAITextServiceError(
                            f"OpenAI request failed for model '{model}'"
                        ) from error

                    errors.append(
                        f"{model}: temporary provider error"
                        f"(attempt {attempt}/{attempts_per_model})"
                    )

                    if attempt < attempts_per_model:
                        time.sleep(settings.OPENAI_TEXT_RETRY_DELAY_SECONDS)
        raise OpenAITextServiceError(
            "All configured OpenAI models are temporarily unavailable"
            f"Attempts: {'; '.join(errors)}"
        )
