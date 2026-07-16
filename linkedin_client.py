"""
LinkedIn API client examples.

Requires a valid access token stored in .linkedin_token.json by linkedin_auth.py.
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN_FILE = ".linkedin_token.json"
API_BASE = "https://api.linkedin.com/v2"


def load_token() -> dict:
    """Load access token from local file."""
    if not os.path.exists(TOKEN_FILE):
        raise FileNotFoundError(
            f"Token file {TOKEN_FILE} not found. Run python linkedin_auth.py first."
        )
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_headers(token: dict) -> dict:
    """Build request headers with Bearer token."""
    return {
        "Authorization": f"Bearer {token['access_token']}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202306",
    }


def get_user_info(token: dict) -> dict:
    """Get basic user info via OpenID Connect userinfo endpoint."""
    response = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers=get_headers(token),
    )
    response.raise_for_status()
    return response.json()


def get_profile(token: dict) -> dict:
    """Get LinkedIn profile using r_liteprofile / OpenID scopes."""
    # With openid profile email scopes, use userinfo for basic profile data.
    # For more fields, you may need r_basicprofile (restricted) and different endpoints.
    return get_user_info(token)


def create_text_post(token: dict, text: str) -> dict:
    """Create a simple text post on LinkedIn (requires w_member_social scope).

    Requires the app to have 'Share on LinkedIn' product approved.
    """
    user_info = get_user_info(token)
    author_urn = f"urn:li:person:{user_info['sub']}"

    payload = {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    response = requests.post(
        f"{API_BASE}/ugcPosts",
        headers={**get_headers(token), "Content-Type": "application/json"},
        json=payload,
    )
    response.raise_for_status()
    return {"status_code": response.status_code, "headers": dict(response.headers)}


def main():
    token = load_token()

    print("\n📋 Fetching profile info...")
    profile = get_profile(token)
    print(json.dumps(profile, indent=2, ensure_ascii=False))

    # Example: post creation is commented out to avoid accidental publishing.
    # Uncomment and use only if 'Share on LinkedIn' product is approved.
    # result = create_text_post(token, "Hello from my LinkedIn automation project!")
    # print("Post created:", result)


if __name__ == "__main__":
    main()
