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
        # iRacing rejects/redirects unfamiliar UAs in some regions; use a
        # plain browser-ish UA. Content-Type is set per-request below.
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            }
        )
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
        # Browser-y headers; Cloudflare in front of iRacing sometimes 405s
        # requests that don't look like an XHR from members.iracing.com.
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://members-ng.iracing.com",
            "Referer": "https://members-ng.iracing.com/",
        }
        # `allow_redirects=False`: a 3xx after POST would be re-issued by
        # `requests` as GET on /auth, which iRacing answers with 405.
        r = self.session.post(
            f"{BASE_URL}/auth",
            json=payload,
            headers=headers,
            timeout=30,
            allow_redirects=False,
        )
        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("Location", "")
            raise IRacingAuthError(
                f"iRacing auth redirected to {location!r} — usually means "
                "CAPTCHA/verification is required. Sign in once via the "
                "iRacing website, then retry."
            )
        if r.status_code >= 400:
            allow = r.headers.get("Allow", "?")
            server = r.headers.get("Server", "?")
            ctype = r.headers.get("Content-Type", "?")
            body = (r.text or "")[:300].replace("\n", " ")
            raise IRacingAuthError(
                f"iRacing auth HTTP {r.status_code} "
                f"(Server={server!r}, Allow={allow!r}, Content-Type={ctype!r}). "
                f"Body: {body!r}"
            )

        try:
            data = r.json()
        except ValueError:
            raise IRacingAuthError(
                f"iRacing auth returned non-JSON (HTTP {r.status_code}): "
                f"{r.text[:200]!r}"
            )

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
