# iracingteam

> **Status: parked (April 2026).** Blocked on iRacing auth changes — see
> [Current status](#current-status) below. Code is kept so it can be revived
> once OAuth client-ID issuance reopens.

A small Flask web app that uses your iRacing account to build a "driver pool"
— either one of your iRacing leagues or your friends list (plus yourself) —
and then shows:

1. Every iRacing series that any pool member has scored championship points in
   during the current season.
2. Each pool member's standings (series raced, races run, championship points)
   for the current season.

Data is collected live from the iRacing `members-ng` Data API.

## Current status

The app is functionally complete but cannot authenticate against the iRacing
Data API as of April 2026. Two things changed at iRacing:

1. **Legacy email+password auth** at `POST members-ng.iracing.com/auth` was
   **retired in the 2026 S1 release (Dec 9 2025)**. The endpoint now returns
   `405 Not Allowed`. No account-level toggle brings it back.
2. **OAuth2 client-ID issuance is paused.** iRacing is evaluating third-party
   usage and has not announced a reopening date. Without a client ID, the app
   cannot use the supported OAuth2 Authorization Code flow.

A cookie-paste fallback was built (the current `/login` screen) on the theory
that session cookies from a browser login would authenticate Data API calls.
**Verified not to work:** `GET members-ng.iracing.com/data/member/info` returns
`401` even with a fully valid browser session cookie. The public `/data/*`
endpoints now require an OAuth2 Bearer token; the iRacing UI has moved its
own traffic to an internal BFF on `members.iracing.com/bff/*` which is not a
public API.

### How to revive this

- **Preferred:** when iRacing reopens OAuth2 client-ID issuance, request one
  ([OAuth docs](https://oauth.iracing.com/oauth2/book/introduction.html)),
  then replace `iracing_client.IRacingClient` with an OAuth Authorization
  Code flow. The rest of the app (pool selection, series aggregation,
  standings rendering) can be reused unchanged — only the HTTP/auth layer
  needs swapping.
- **Alternative (unofficial, fragile):** retarget the client at the
  undocumented `members.iracing.com/bff/*` endpoints the iRacing UI uses.
  These are cookie-authenticated and work today, but are undocumented,
  unstable, and should only be used for private/personal tooling.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://localhost:5000>. You'll reach the cookie-paste login page,
which will fail at `/data/member/info` with a 401 until one of the revival
paths above is implemented.

## Notes

- iRacing's Data API rate-limits aggressively; the standings page makes one
  `results/search_series` call per pool member. Large pools will be slow.
- The "teams" concept is mapped to your iRacing **leagues**: picking a league
  uses its full roster as the driver pool.
- The current season is inferred from today's date (calendar quarter).
