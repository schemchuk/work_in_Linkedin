"""DALL-E 3 image generation for LinkedIn posts.

Requires OPENAI_API_KEY in .env.
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def generate_image(image_prompt: str) -> str | None:
    """Generate an image via DALL-E 3 and return the URL."""
    if not image_prompt or image_prompt.strip() == "empty":
        logger.info("Skipping image generation: empty prompt")
        return None

    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    logger.info("Generating image with DALL-E 3...")
    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=image_prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        url = response.data[0].url
        logger.info("Image generated")
        return url
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return None


def download_image(url: str) -> bytes | None:
    """Download image bytes from a URL."""
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.error(f"Failed to download image from {url}: {e}")
        return None
