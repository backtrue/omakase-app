import json
import os
from typing import Optional

from .schemas import VlmMenuResponse


def _parse_json_fallback(text: str) -> VlmMenuResponse:
    # Some model responses may wrap JSON in markdown; keep v1 minimal.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.replace("json", "", 1).strip()
    data = json.loads(stripped)
    return VlmMenuResponse.model_validate(data)


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        vlm_model: str,
        image_model: str,
    ) -> None:
        from google import genai

        self._genai = genai
        self._api_key = api_key
        self.vlm_model = vlm_model
        self.image_model = image_model
        try:
            timeout_s = float(os.getenv("GENAI_HTTP_TIMEOUT_SECONDS", "300"))
        except Exception:
            timeout_s = 300.0
        timeout_ms = max(1000, int(timeout_s * 1000))
        try:
            self._client = genai.Client(api_key=api_key, http_options={"timeout": timeout_ms})
        except TypeError:
            self._client = genai.Client(api_key=api_key)

    def parse_menu_from_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> VlmMenuResponse:
        from google.genai import types

        max_output_tokens = int(os.getenv("VLM_MAX_OUTPUT_TOKENS", "8192"))
        temperature = float(os.getenv("VLM_TEMPERATURE", "0.2"))

        response = self._client.models.generate_content(
            model=self.vlm_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VlmMenuResponse,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        if getattr(response, "parsed", None) is not None:
            return response.parsed

        # Fallback for cases where parsing is not returned.
        if getattr(response, "text", None):
            return _parse_json_fallback(response.text)

        raise RuntimeError("Gemini VLM returned no parsed JSON and no text")

    async def parse_menu_from_image_async(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> VlmMenuResponse:
        from google.genai import types

        max_output_tokens = int(os.getenv("VLM_MAX_OUTPUT_TOKENS", "8192"))
        temperature = float(os.getenv("VLM_TEMPERATURE", "0.2"))

        response = await self._client.aio.models.generate_content(
            model=self.vlm_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VlmMenuResponse,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        if getattr(response, "parsed", None) is not None:
            return response.parsed

        if getattr(response, "text", None):
            return _parse_json_fallback(response.text)

        raise RuntimeError("Gemini VLM returned no parsed JSON and no text")

    def generate_food_image_bytes(
        self,
        *,
        prompt: str,
        aspect_ratio: str = "1:1",
    ) -> bytes:
        from google.genai import types

        # Prefer Imagen for text-to-image.
        if self.image_model.startswith("imagen-"):
            result = self._client.models.generate_images(
                model=self.image_model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                    safety_filter_level="BLOCK_LOW_AND_ABOVE",
                    person_generation="ALLOW_ADULT",
                ),
            )
            if not result.generated_images:
                raise RuntimeError("Imagen returned no images")
            return result.generated_images[0].image.image_bytes

        # Fallback: Gemini native image generation model.
        response = self._client.models.generate_content(
            model=self.image_model,
            contents=[prompt],
        )

        for part in getattr(response, "parts", []) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None) is not None:
                data = inline.data
                # Depending on SDK version, this may be bytes or base64 string.
                if isinstance(data, str):
                    import base64

                    return base64.b64decode(data)
                return data

        raise RuntimeError("Image model returned no inline image data")
