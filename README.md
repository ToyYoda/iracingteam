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

Then open <http://localhost:5000> and sign in with your iRacing email and
password. Credentials are forwarded to `https://members-ng.iracing.com/auth`
and only the returned session cookies are kept in the Flask session.

## Notes

- iRacing's Data API rate-limits aggressively; the standings page makes one
  `results/search_series` call per pool member. Large pools will be slow.
- The "teams" concept is mapped to your iRacing **leagues**: picking a league
  uses its full roster as the driver pool.
- The current season is inferred from today's date (calendar quarter).
