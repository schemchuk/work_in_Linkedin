"""
LinkedIn API client.

Supports:
- reading profile info via OpenID Connect
- uploading images (versioned Images API)
- publishing text and image posts (versioned Posts API)

Requires LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_URN in environment.
Run `python linkedin_auth.py` first to obtain them.
"""

import logging
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

REST_API_BASE = "https://api.linkedin.com/rest"
OPENID_USERINFO = "https://api.linkedin.com/v2/userinfo"

# Versioned REST API: each version is supported for ~1 year after release.
# Override with LINKEDIN_VERSION in .env when this one gets sunset.
LINKEDIN_VERSION = os.getenv("LINKEDIN_VERSION", "202607")

# Characters reserved by the Posts API "little text format" in commentary
_LITTLE_TEXT_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set. Run python linkedin_auth.py first.")
    return value


def _get_headers() -> dict:
    token = _require_env("LINKEDIN_ACCESS_TOKEN")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }


def _get_upload_headers() -> dict:
    token = _require_env("LINKEDIN_ACCESS_TOKEN")
    return {"Authorization": f"Bearer {token}"}


def _check_response(response: requests.Response) -> None:
    if response.status_code == 401:
        raise RuntimeError(
            "LinkedIn API returned 401 Unauthorized — the access token has likely "
            "expired (tokens live ~60 days). Re-authorize with: "
            "python linkedin_auth.py --force"
        )
    response.raise_for_status()


def escape_little_text(text: str) -> str:
    """Escape characters reserved by the Posts API commentary format."""
    return _LITTLE_TEXT_RESERVED.sub(r"\\\1", text)


def get_user_info(token: str | None = None) -> dict:
    """Get basic user info via OpenID Connect userinfo endpoint."""
    headers = {"Authorization": f"Bearer {token}"} if token else _get_upload_headers()
    response = requests.get(OPENID_USERINFO, headers=headers, timeout=30)
    _check_response(response)
    return response.json()


def get_person_urn(token: str | None = None) -> str:
    """Return the LinkedIn person URN for the authenticated user."""
    user_info = get_user_info(token)
    person_id = user_info.get("sub")
    if not person_id:
        raise ValueError("Could not determine LinkedIn person ID from userinfo")
    return f"urn:li:person:{person_id}"


def upload_image_to_linkedin(image_bytes: bytes) -> str:
    """Upload image bytes to LinkedIn and return the image URN."""
    person_urn = _require_env("LINKEDIN_PERSON_URN")
    headers = _get_headers()

    response = requests.post(
        f"{REST_API_BASE}/images?action=initializeUpload",
        headers=headers,
        json={"initializeUploadRequest": {"owner": person_urn}},
        timeout=30,
    )
    _check_response(response)
    value = response.json()["value"]

    upload_response = requests.put(
        value["uploadUrl"],
        data=image_bytes,
        headers=_get_upload_headers(),
        timeout=120,
    )
    _check_response(upload_response)

    image_urn = value["image"]
    logger.info(f"Image uploaded to LinkedIn: {image_urn}")
    return image_urn


def publish_post(post_text: str, asset_urn: str | None = None) -> dict:
    """Publish a post to LinkedIn. Returns a dict with the new post's ID."""
    person_urn = _require_env("LINKEDIN_PERSON_URN")
    headers = _get_headers()

    payload = {
        "author": person_urn,
        "commentary": escape_little_text(post_text),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    if asset_urn:
        payload["content"] = {"media": {"id": asset_urn}}

    response = requests.post(
        f"{REST_API_BASE}/posts",
        headers=headers,
        json=payload,
        timeout=30,
    )
    _check_response(response)

    post_id = response.headers.get("x-restli-id")
    logger.info(f"Post published successfully. ID: {post_id}")
    return {"id": post_id}


def main():
    """Simple CLI test: read profile info."""
    logging.basicConfig(level=logging.INFO)
    info = get_user_info()
    print("LinkedIn user info:")
    for key, value in info.items():
        print(f"  {key}: {value}")
    print(f"\nPerson URN: {get_person_urn()}")


if __name__ == "__main__":
    main()
