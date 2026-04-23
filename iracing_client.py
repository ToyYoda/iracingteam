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
    def __init__(self, cookie_header: str | None = None):
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
        # We send cookies verbatim as a Cookie: header rather than using the
        # cookie jar — iRacing's cookies come with Domain/Path/HttpOnly
        # attributes the user can't paste, and requests' jar would otherwise
        # refuse to attach them to api requests due to the missing metadata.
        self.cookie_header: str = cookie_header or ""
        self.cust_id = None
        self.display_name = None

    # -- cookie-based login -------------------------------------------------

    def set_cookies_from_blob(self, blob: str) -> int:
        """Normalize a pasted cookie blob into a single `Cookie:` header value.
        Accepts either a full Cookie header line, a semicolon-separated value,
        or one name=value per line. Returns the number of cookies stored."""
        if not blob:
            self.cookie_header = ""
            return 0
        cleaned = blob.strip()
        if cleaned.lower().startswith("cookie:"):
            cleaned = cleaned.split(":", 1)[1]
        parts: list[str] = []
        for chunk in cleaned.replace("\r", "").split("\n"):
            parts.extend(chunk.split(";"))
        pairs: list[str] = []
        for pair in parts:
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"')
            if name:
                pairs.append(f"{name}={value}")
        self.cookie_header = "; ".join(pairs)
        return len(pairs)

    def cookie_names(self) -> list[str]:
        return [p.split("=", 1)[0] for p in self.cookie_header.split("; ") if "=" in p]

    def fetch_self(self) -> dict:
        """Hit /data/member/info to confirm cookies work and learn cust_id."""
        info = self.get("/data/member/info")
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
                "Cookies were accepted but no cust_id was returned."
            )
        self.cust_id = cust_id
        self.display_name = display_name
        return root

    def cookies_dict(self):
        """Return a simple dict for Flask-session serialization."""
        return {"cookie_header": self.cookie_header}

    @classmethod
    def from_session_dict(cls, d: dict | None) -> "IRacingClient":
        return cls(cookie_header=(d or {}).get("cookie_header"))

    # -- generic GET --------------------------------------------------------

    def _request_headers(self) -> dict:
        headers = {}
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        return headers

    def get(self, path: str, params: dict | None = None):
        """GET an iRacing data endpoint, transparently resolving link/chunks."""
        r = self.session.get(
            f"{BASE_URL}{path}",
            params=params,
            headers=self._request_headers(),
            timeout=30,
        )
        if r.status_code == 401:
            names = ", ".join(self.cookie_names()) or "(none)"
            raise IRacingAuthError(
                f"iRacing returned 401 on {path}. Cookies we sent: {names}. "
                "The Data API may now require an OAuth2 Bearer token instead "
                "of session cookies — check the Authorization header on a "
                "working browser request to members-ng.iracing.com."
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
