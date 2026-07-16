"""
LinkedIn API client.

Supports:
- reading profile info via OpenID Connect
- uploading images
- publishing text and image posts

Requires LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_URN in environment.
Run `python linkedin_auth.py` first to obtain them.
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com/v2"
OPENID_USERINFO = "https://api.linkedin.com/v2/userinfo"


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
        "LinkedIn-Version": "202306",
    }


def _get_upload_headers() -> dict:
    token = _require_env("LINKEDIN_ACCESS_TOKEN")
    return {"Authorization": f"Bearer {token}"}


def get_user_info(token: str | None = None) -> dict:
    """Get basic user info via OpenID Connect userinfo endpoint."""
    headers = {"Authorization": f"Bearer {token}"} if token else _get_headers()
    response = requests.get(OPENID_USERINFO, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def get_person_urn(token: str | None = None) -> str:
    """Return the LinkedIn person URN for the authenticated user."""
    user_info = get_user_info(token)
    person_id = user_info.get("sub")
    if not person_id:
        raise ValueError("Could not determine LinkedIn person ID from userinfo")
    return f"urn:li:person:{person_id}"


def upload_image_to_linkedin(image_bytes: bytes) -> str:
    """Upload image bytes to LinkedIn and return the asset URN."""
    person_urn = _require_env("LINKEDIN_PERSON_URN")
    headers = _get_headers()

    register_payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": person_urn,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }

    response = requests.post(
        f"{API_BASE}/assets?action=registerUpload",
        headers=headers,
        json=register_payload,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    upload_url = data["value"]["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset_urn = data["value"]["asset"]

    upload_response = requests.put(
        upload_url,
        data=image_bytes,
        headers=_get_upload_headers(),
        timeout=120,
    )
    upload_response.raise_for_status()

    logger.info(f"Image uploaded to LinkedIn: {asset_urn}")
    return asset_urn


def publish_post(post_text: str, asset_urn: str | None = None) -> dict:
    """Publish a post to LinkedIn. Returns the API response JSON."""
    person_urn = _require_env("LINKEDIN_PERSON_URN")
    headers = _get_headers()

    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": post_text},
                "shareMediaCategory": "IMAGE" if asset_urn else "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    if asset_urn:
        payload["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
            {"status": "READY", "media": asset_urn}
        ]

    response = requests.post(
        f"{API_BASE}/ugcPosts",
        headers=headers,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()
    logger.info(f"Post published successfully. ID: {result.get('id')}")
    return result


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
