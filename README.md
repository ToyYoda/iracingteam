# iracingteam

A small Flask web app that uses your iRacing account to build a "driver pool"
— either one of your iRacing leagues or your friends list (plus yourself) —
and then shows:

1. Every iRacing series that any pool member has scored championship points in
   during the current season.
2. Each pool member's standings (series raced, races run, championship points)
   for the current season.

Data is collected live from the iRacing `members-ng` Data API.

## Run locally

```bash
pip install -r requirements.txt
export FLASK_SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')
python app.py
```

Then open <http://localhost:5000>.

## Authentication

iRacing retired email+password auth on the Data API in the 2026 S1 release
(Dec 9 2025) and has paused issuing OAuth client IDs to third parties, so
this app **cannot log you in directly**. Instead it accepts session cookies
from a working browser login:

1. Sign in to <https://members.iracing.com> in your browser.
2. Open DevTools (F12) → Network tab. Reload, click any request whose host
   is `members-ng.iracing.com`.
3. In **Headers → Request Headers**, copy the entire `Cookie:` value.
4. Paste it on the app's login page.

Cookies typically last about a day; refresh by repeating the steps.

## Notes

- iRacing's Data API rate-limits aggressively; the standings page makes one
  `results/search_series` call per pool member. Large pools will be slow.
- The "teams" concept is mapped to your iRacing **leagues**: picking a league
  uses its full roster as the driver pool.
- The current season is inferred from today's date (calendar quarter).
