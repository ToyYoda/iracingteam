"""Thin wrapper around the iRacing members-ng Data API.

iRacing retired legacy email+password authentication in the 2026 S1 release
(Dec 9 2025) and has paused issuing OAuth client IDs to third parties, so this
client cannot perform a fresh login on its own. Instead, callers paste session
cookies obtained from a working browser login, and we reuse them for Data API
calls until they expire.

The Data API works in two steps: most GETs return a JSON envelope with a
short-lived S3 `link` (or a chunked `data.chunk_info`) and the actual payload
sits at that URL. `get()` transparently follows both forms.
"""

import requests

BASE_URL = "https://members-ng.iracing.com"


class IRacingAuthError(Exception):
    pass


class IRacingClient:
    def __init__(self, cookies=None):
        self.session = requests.Session()
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

    # -- cookie-based login -------------------------------------------------

    def set_cookies_from_blob(self, blob: str) -> int:
        """Accept either a raw `Cookie:` header value (`a=1; b=2`) or one
        cookie per line (`a=1`). Returns the number of cookies stored."""
        if not blob:
            return 0
        # Strip a leading "Cookie:" header prefix if the user pasted the
        # whole header line.
        cleaned = blob.strip()
        if cleaned.lower().startswith("cookie:"):
            cleaned = cleaned.split(":", 1)[1]
        # Allow either ';' or newlines as separators.
        parts = []
        for chunk in cleaned.replace("\r", "").split("\n"):
            parts.extend(chunk.split(";"))
        count = 0
        for pair in parts:
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"')
            if name:
                self.session.cookies.set(name, value, domain=".iracing.com")
                count += 1
        return count

    def fetch_self(self) -> dict:
        """Hit /data/member/info to confirm cookies work and learn cust_id."""
        info = self.get("/data/member/info")
        # Endpoint sometimes returns the user object directly, sometimes
        # under a top-level wrapper.
        root = info
        if isinstance(info, dict):
            for key in ("data", "member"):
                if isinstance(info.get(key), dict):
                    root = info[key]
                    break
        if not isinstance(root, dict):
            raise IRacingAuthError("Unexpected /data/member/info response shape")
        cust_id = root.get("cust_id") or root.get("custId")
        display_name = root.get("display_name") or root.get("displayName")
        if not cust_id:
            raise IRacingAuthError(
                "Cookies were accepted by the API but no cust_id was returned. "
                "They may be missing the irsso_membersv2 cookie."
            )
        self.cust_id = cust_id
        self.display_name = display_name
        return root

    def cookies_dict(self):
        return self.session.cookies.get_dict()

    # -- generic GET --------------------------------------------------------

    def get(self, path: str, params: dict | None = None):
        """GET an iRacing data endpoint, transparently resolving link/chunks."""
        r = self.session.get(f"{BASE_URL}{path}", params=params, timeout=30)
        if r.status_code == 401:
            raise IRacingAuthError(
                "iRacing rejected the session — cookies are invalid or expired. "
                "Re-copy them from your browser and sign in again."
            )
        r.raise_for_status()
        payload = r.json()

        if isinstance(payload, dict) and "link" in payload:
            r2 = requests.get(payload["link"], timeout=30)
            r2.raise_for_status()
            payload = r2.json()

        if isinstance(payload, dict):
            chunk_info = (
                (payload.get("data") or {}).get("chunk_info")
                if isinstance(payload.get("data"), dict)
                else payload.get("chunk_info")
            )
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
