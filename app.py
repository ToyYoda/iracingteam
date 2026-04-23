"""Flask front-end for the iRacing driver-pool tracker.

Flow:
  /            -> login form
  POST /login  -> authenticate with iRacing, stash cookies in Flask session
  /select      -> pick a league (team) or the friends list as the driver pool
  POST /pool   -> build pool, store cust_ids, redirect to /standings
  /standings   -> list series any pool member scored points in this season,
                  plus each member's standing in every such series
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from iracing_client import IRacingAuthError, IRacingClient


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")


def _client_from_session() -> IRacingClient | None:
    cookies = session.get("iracing_cookies")
    cust_id = session.get("cust_id")
    if not cookies or not cust_id:
        return None
    client = IRacingClient(cookies=cookies)
    client.cust_id = cust_id
    client.display_name = session.get("display_name")
    return client


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("cust_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    if session.get("cust_id"):
        return redirect(url_for("select"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    blob = (request.form.get("cookies") or "").strip()
    if not blob:
        flash("Paste your iRacing session cookies to sign in.")
        return render_template("login.html"), 400

    client = IRacingClient()
    n = client.set_cookies_from_blob(blob)
    if n == 0:
        flash("No cookies could be parsed from the pasted text.")
        return render_template("login.html"), 400

    try:
        client.fetch_self()
    except IRacingAuthError as exc:
        flash(f"iRacing rejected the session: {exc}")
        return render_template("login.html"), 401
    except Exception as exc:  # network / unexpected
        flash(f"Could not reach iRacing: {exc}")
        return render_template("login.html"), 502

    session.clear()
    session["iracing_cookies"] = client.cookies_dict()
    session["cust_id"] = client.cust_id
    session["display_name"] = client.display_name
    return redirect(url_for("select"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Pool selection
# ---------------------------------------------------------------------------


def _fetch_leagues(client: IRacingClient) -> list[dict]:
    """Return leagues the current user belongs to."""
    try:
        data = client.get("/data/league/membership", params={"include_league": 1})
    except Exception:
        return []
    # Envelope may be the list itself or wrapped under "data" / "success".
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return data["data"]
        if isinstance(data.get("results"), list):
            return data["results"]
    return []


def _fetch_friends(client: IRacingClient) -> list[dict]:
    """Return the logged-in user's friends list (best-effort)."""
    try:
        profile = client.get(
            "/data/member/profile", params={"cust_id": client.cust_id}
        )
    except Exception:
        return []
    # Profile envelope; iRacing exposes `friends` on the authenticated user's profile.
    root = profile.get("data") if isinstance(profile, dict) and isinstance(profile.get("data"), dict) else profile
    if not isinstance(root, dict):
        return []
    friends = root.get("friends") or root.get("member_friends") or []
    if isinstance(friends, list):
        return friends
    return []


def _fetch_league_roster(client: IRacingClient, league_id: int) -> list[dict]:
    try:
        data = client.get("/data/league/get", params={"league_id": league_id, "include_licenses": False})
    except Exception:
        return []
    root = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if isinstance(root, dict):
        return root.get("roster") or []
    return []


@app.route("/select")
@login_required
def select():
    client = _client_from_session()
    leagues = _fetch_leagues(client)
    friends = _fetch_friends(client)
    return render_template(
        "select.html",
        display_name=session.get("display_name"),
        cust_id=session.get("cust_id"),
        leagues=leagues,
        friends=friends,
    )


@app.route("/pool", methods=["POST"])
@login_required
def pool():
    client = _client_from_session()
    choice = request.form.get("choice")
    members: list[dict] = []

    if choice == "friends":
        friends = _fetch_friends(client)
        members = [
            {"cust_id": f.get("cust_id"), "display_name": f.get("display_name")}
            for f in friends
            if f.get("cust_id")
        ]
        pool_label = "Friends list"
    elif choice and choice.startswith("league:"):
        try:
            league_id = int(choice.split(":", 1)[1])
        except ValueError:
            flash("Invalid league selection.")
            return redirect(url_for("select"))
        roster = _fetch_league_roster(client, league_id)
        members = [
            {"cust_id": m.get("cust_id"), "display_name": m.get("display_name")}
            for m in roster
            if m.get("cust_id")
        ]
        # Label with league name if we can find it.
        leagues = _fetch_leagues(client)
        league_name = next(
            (l.get("league_name") or l.get("league", {}).get("league_name")
             for l in leagues
             if (l.get("league_id") == league_id) or (l.get("league", {}).get("league_id") == league_id)),
            f"League #{league_id}",
        )
        pool_label = f"League: {league_name}"
    else:
        flash("Please pick a league or the friends list.")
        return redirect(url_for("select"))

    # Always include the logged-in user in the pool.
    me_id = session["cust_id"]
    if not any(m["cust_id"] == me_id for m in members):
        members.insert(
            0,
            {"cust_id": me_id, "display_name": session.get("display_name") or f"#{me_id}"},
        )

    session["pool"] = members
    session["pool_label"] = pool_label
    return redirect(url_for("standings"))


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------


def _current_season_period() -> tuple[int, int]:
    """Approximate iRacing season (year, quarter) from today's date."""
    today = datetime.utcnow()
    quarter = (today.month - 1) // 3 + 1
    return today.year, quarter


def _fetch_member_season_results(
    client: IRacingClient, cust_id: int, year: int, quarter: int
) -> list[dict]:
    """All official race results for a driver in the given season."""
    try:
        data = client.get(
            "/data/results/search_series",
            params={
                "cust_id": cust_id,
                "season_year": year,
                "season_quarter": quarter,
                "official_only": True,
            },
        )
    except Exception:
        return []
    root = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if isinstance(root, dict):
        return root.get("results") or []
    if isinstance(root, list):
        return root
    return []


def _aggregate_results(results: list[dict]) -> dict[int, dict]:
    """Group race results by series and sum championship points."""
    by_series: dict[int, dict] = defaultdict(
        lambda: {"series_id": None, "series_name": None, "points": 0, "races": 0}
    )
    for r in results:
        series_id = r.get("series_id")
        if series_id is None:
            continue
        bucket = by_series[series_id]
        bucket["series_id"] = series_id
        bucket["series_name"] = (
            r.get("series_name") or r.get("series_short_name") or bucket["series_name"]
        )
        # `points` on a session is championship points for that race.
        pts = r.get("points")
        if isinstance(pts, (int, float)):
            bucket["points"] += pts
        bucket["races"] += 1
    return dict(by_series)


@app.route("/standings")
@login_required
def standings():
    client = _client_from_session()
    pool_members = session.get("pool") or []
    if not pool_members:
        return redirect(url_for("select"))

    year, quarter = _current_season_period()

    # Per-driver aggregation: { cust_id: { series_id: {points, races, name} } }
    per_driver: dict[int, dict] = {}
    series_names: dict[int, str] = {}
    # Series where at least one pool member has > 0 points.
    series_with_points: dict[int, dict] = {}

    for member in pool_members:
        cust_id = member["cust_id"]
        results = _fetch_member_season_results(client, cust_id, year, quarter)
        agg = _aggregate_results(results)
        per_driver[cust_id] = agg
        for sid, info in agg.items():
            if info["series_name"]:
                series_names[sid] = info["series_name"]
            if info["points"] > 0:
                entry = series_with_points.setdefault(
                    sid,
                    {
                        "series_id": sid,
                        "series_name": info["series_name"] or f"Series #{sid}",
                        "drivers": 0,
                        "total_points": 0,
                    },
                )
                entry["drivers"] += 1
                entry["total_points"] += info["points"]

    # Per-driver totals for display.
    driver_rows = []
    for member in pool_members:
        cust_id = member["cust_id"]
        entries = per_driver.get(cust_id, {})
        total_points = sum(e["points"] for e in entries.values())
        total_races = sum(e["races"] for e in entries.values())
        series_list = sorted(
            (
                {
                    "series_id": sid,
                    "series_name": info["series_name"] or series_names.get(sid) or f"Series #{sid}",
                    "points": info["points"],
                    "races": info["races"],
                }
                for sid, info in entries.items()
            ),
            key=lambda s: s["points"],
            reverse=True,
        )
        driver_rows.append(
            {
                "cust_id": cust_id,
                "display_name": member.get("display_name") or f"#{cust_id}",
                "total_points": total_points,
                "total_races": total_races,
                "series": series_list,
            }
        )

    driver_rows.sort(key=lambda d: d["total_points"], reverse=True)
    series_rows = sorted(
        series_with_points.values(), key=lambda s: s["total_points"], reverse=True
    )

    return render_template(
        "standings.html",
        pool_label=session.get("pool_label"),
        pool_size=len(pool_members),
        year=year,
        quarter=quarter,
        series_rows=series_rows,
        driver_rows=driver_rows,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
