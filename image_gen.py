"""Image generation for LinkedIn posts via the OpenAI Images API.

Requires OPENAI_API_KEY in .env. Uses the gpt-image family — DALL-E 3 was
retired (as of 2026-08, OpenAI returns "the model 'dall-e-3' does not
exist"). Unlike DALL-E 3, gpt-image models don't return a URL — only base64
image data — so generate_image() returns bytes directly.
"""

import base64
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")


def generate_image(image_prompt: str) -> bytes | None:
    """Generate an image and return its raw bytes, or None on failure/empty prompt."""
    if not image_prompt or image_prompt.strip() == "empty":
        logger.info("Skipping image generation: empty prompt")
        return None

    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    logger.info(f"Generating image with {OPENAI_IMAGE_MODEL}...")
    try:
        response = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=image_prompt,
            size="1024x1024",
            quality=OPENAI_IMAGE_QUALITY,
            n=1,
        )
        image_bytes = base64.b64decode(response.data[0].b64_json)
        logger.info(f"Image generated ({len(image_bytes)} bytes)")
        return image_bytes
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return None
