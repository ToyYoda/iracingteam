"""Thin wrapper around the iRacing members-ng Data API.

The Data API works in two steps: most GETs return a JSON envelope with a
short-lived S3 `link` (or a chunked `data.chunk_info`) and the actual payload
sits at that URL. `get()` transparently follows both forms.

Auth uses the hashed-password scheme: base64(sha256(password + lowercase(email))).
"""

import base64
import hashlib
import requests

BASE_URL = "https://members-ng.iracing.com"


class IRacingAuthError(Exception):
    pass


class IRacingClient:
    def __init__(self, cookies=None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "iracingteam/0.1"})
        if cookies:
            self.session.cookies.update(cookies)
        self.cust_id = None
        self.display_name = None

    @staticmethod
    def _encode_password(email: str, password: str) -> str:
        digest = hashlib.sha256((password + email.lower()).encode("utf-8")).digest()
        return base64.b64encode(digest).decode("utf-8")

    def authenticate(self, email: str, password: str):
        payload = {"email": email, "password": self._encode_password(email, password)}
        r = self.session.post(f"{BASE_URL}/auth", json=payload, timeout=30)
        if r.status_code >= 400:
            raise IRacingAuthError(f"iRacing auth HTTP {r.status_code}")
        data = r.json()
        if data.get("authcode") == 0 or data.get("verificationRequired"):
            raise IRacingAuthError(data.get("message") or "Login failed")
        self.cust_id = data.get("custId")
        self.display_name = data.get("displayName")
        return data

    def cookies_dict(self):
        return self.session.cookies.get_dict()

    def get(self, path: str, params: dict | None = None):
        """GET an iRacing data endpoint, transparently resolving link/chunks."""
        r = self.session.get(f"{BASE_URL}{path}", params=params, timeout=30)
        if r.status_code == 401:
            raise IRacingAuthError("iRacing session expired; please log in again")
        r.raise_for_status()
        payload = r.json()

        if isinstance(payload, dict) and "link" in payload:
            r2 = requests.get(payload["link"], timeout=30)
            r2.raise_for_status()
            payload = r2.json()

        if isinstance(payload, dict):
            chunk_info = (payload.get("data") or {}).get("chunk_info") if isinstance(payload.get("data"), dict) else payload.get("chunk_info")
            if chunk_info and chunk_info.get("chunk_file_names"):
                base = chunk_info.get("base_download_url", "")
                merged = []
                for fname in chunk_info["chunk_file_names"]:
                    rc = requests.get(base + fname, timeout=30)
                    rc.raise_for_status()
                    merged.extend(rc.json())
                if "data" in payload and isinstance(payload["data"], dict):
                    payload["data"]["results"] = merged
                else:
                    payload["results"] = merged

        return payload
