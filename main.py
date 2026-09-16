#!/usr/bin/env python3
"""
Real Madrid & Manchester United match dashboard.

Opens a browser dashboard showing, for both clubs:
  * the last completed match - final score, goal-by-goal timeline with minutes,
    scorers, assists, penalties/own goals and cards
  * the next fixture - date, kick-off time and stadium
  * the full league table with the club's row highlighted

All kick-off times are converted to Bulgarian local time (Europe/Sofia).

Data: ESPN's public soccer API. No API key, no third-party packages.
Run:  python main.py
"""

import http.server
import json
import os
import subprocess
import tempfile
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone, tzinfo
from datetime import time as _T
from html import escape

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports/soccer"

# ESPN team ids: Real Madrid = 86, Manchester United = 360.
TEAMS = [
    {
        "key": "rma",
        "espn_id": "86",
        "name": "Real Madrid",
        "league": "esp.1",
        "league_name": "LaLiga",
        "accent": "#FEBE10",
        "glow": "254, 190, 16",
        "ring": "254, 190, 16",
        "crest": "https://a.espncdn.com/i/teamlogos/soccer/500/86.png",
        "villain": False,
        "anthem": "real_madrid",
        "anthem_keys": ("real_madrid", "madrid", "hala"),
        "anthem_label": "Madrid Anthem",
    },
    {
        "key": "mun",
        "espn_id": "360",
        "name": "Manchester United",
        "league": "eng.1",
        "league_name": "Premier League",
        "accent": "#DA291C",
        "glow": "218, 41, 28",
        "ring": "218, 41, 28",
        "crest": "https://a.espncdn.com/i/teamlogos/soccer/500/360.png",
        "villain": False,
        "anthem": "man_united",
        "anthem_keys": ("man_united", "united", "glory"),
        "anthem_label": "United Anthem",
    },
    {
        # Tracked, not supported. Shares LaLiga with Real Madrid, so this lane
        # skips the duplicate table and runs a rivalry panel instead.
        "key": "fcb",
        "espn_id": "83",
        "name": "Barcelona",
        "league": "esp.1",
        "league_name": "LaLiga",
        "accent": "#c1121f",
        "glow": "60, 70, 90",
        "ring": "193, 18, 31",
        "crest": "https://a.espncdn.com/i/teamlogos/soccer/500/83.png",
        "villain": True,
        "anthem": None,
        "rival_of": "86",
    },
]

VILLAIN_IDS = {t["espn_id"] for t in TEAMS if t.get("villain")}
ANTHEM_KEYS = {t["anthem"]: t.get("anthem_keys", (t["anthem"],))
               for t in TEAMS if t.get("anthem")}

CACHE_SECONDS = 60


# --------------------------------------------------------------------------
# Bulgarian time (Europe/Sofia)
# --------------------------------------------------------------------------
# Windows ships no IANA database, so zoneinfo("Europe/Sofia") fails unless the
# `tzdata` package is installed. Bulgaria follows the EU rule exactly, so we
# implement it directly and stay dependency-free:
#   EET  = UTC+2 (winter)
#   EEST = UTC+3, from the last Sunday of March 01:00 UTC
#                 to the last Sunday of October 01:00 UTC


def _last_sunday(year, month):
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = first_next - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() + 1) % 7)


class Bulgaria(tzinfo):
    """Europe/Sofia: EET (UTC+2) / EEST (UTC+3) under the EU daylight rule."""

    STD = timedelta(hours=2)
    DST = timedelta(hours=3)

    @staticmethod
    def _summer_utc(naive_utc):
        """Is this naive UTC instant inside Bulgarian summer time?"""
        y = naive_utc.year
        start = datetime.combine(_last_sunday(y, 3), _T(1, 0))    # 01:00 UTC
        end = datetime.combine(_last_sunday(y, 10), _T(1, 0))     # 01:00 UTC
        return start <= naive_utc < end

    @classmethod
    def _summer_local(cls, dt):
        """Same rule on the local wall clock (03:00 / 04:00 switch points).

        Local 03:00-04:00 on the October switch day happens twice; ``fold=1``
        marks the second, already-winter pass through it.
        """
        naive = dt.replace(tzinfo=None)
        y = naive.year
        start = datetime.combine(_last_sunday(y, 3), _T(3, 0))    # EET -> EEST
        end = datetime.combine(_last_sunday(y, 10), _T(4, 0))     # EEST -> EET
        if not (start <= naive < end):
            return False
        if end - timedelta(hours=1) <= naive < end and getattr(dt, "fold", 0):
            return False
        return True

    def utcoffset(self, dt):
        if dt is None:
            return self.STD
        return self.DST if self._summer_local(dt) else self.STD

    def dst(self, dt):
        if dt is None:
            return timedelta(0)
        return timedelta(hours=1) if self._summer_local(dt) else timedelta(0)

    def tzname(self, dt):
        if dt is None:
            return "EET"
        return "EEST" if self._summer_local(dt) else "EET"

    def fromutc(self, dt):
        # dt carries UTC wall-clock values tagged with this zone. Decide the
        # offset from the UTC instant, which is unambiguous, then shift.
        summer = self._summer_utc(dt.replace(tzinfo=None))
        local = dt + (self.DST if summer else self.STD)
        if not summer:
            end = datetime.combine(_last_sunday(local.year, 10), _T(4, 0))
            if end - timedelta(hours=1) <= local.replace(tzinfo=None) < end:
                local = local.replace(fold=1)   # the repeated local hour
        return local


BG = Bulgaria()


def parse_utc(stamp):
    """ESPN stamps look like 2026-09-20T14:15Z."""
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None


def to_bg(dt_utc):
    return dt_utc.astimezone(BG) if dt_utc else None


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def fmt_date(dt):
    return "%s, %d %s %d" % (DAYS[dt.weekday()], dt.day, MONTHS[dt.month - 1], dt.year)


def fmt_time(dt):
    return "%02d:%02d %s" % (dt.hour, dt.minute, dt.tzname())


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def get_json(url, timeout=20):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) football-dashboard/1.0",
            "Accept": "application/json",
            # ESPN gzips when offered; urllib will not decode it for us.
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _logo(team):
    for logo in team.get("logos") or []:
        if "dark" not in (logo.get("rel") or []):
            return logo.get("href")
    logos = team.get("logos") or []
    return logos[0].get("href") if logos else team.get("logo")


def _side(competitors, which):
    for c in competitors:
        if c.get("homeAway") == which:
            return c
    return competitors[0] if competitors else {}


def _team_block(competitor):
    team = competitor.get("team") or {}
    score = competitor.get("score")
    if isinstance(score, dict):
        score = score.get("displayValue")
    return {
        "id": str(team.get("id") or ""),
        "name": team.get("displayName") or team.get("name") or "TBD",
        "short": team.get("shortDisplayName") or team.get("abbreviation") or "",
        "crest": _logo(team),
        "score": score,
        "winner": competitor.get("winner"),
        "shootout": competitor.get("shootoutScore"),
    }


LEAGUE_LOGO = "https://a.espncdn.com/i/leaguelogos/soccer/500/%s.png"


def league_logo(league):
    """nextEvent carries no logos array, but alternateId is the CDN file name."""
    for logo in league.get("logos") or []:
        if "dark" not in (logo.get("rel") or []):
            return logo.get("href")
    alt = league.get("alternateId")
    return LEAGUE_LOGO % alt if alt else ""


def parse_next_match(team_info):
    """Next fixture across every competition, from the team endpoint."""
    events = team_info.get("nextEvent") or []
    if not events:
        return None
    ev = events[0]
    comp = (ev.get("competitions") or [{}])[0]
    venue = comp.get("venue") or {}
    address = venue.get("address") or {}
    competitors = comp.get("competitors") or []
    league = ev.get("league") or {}
    round_name = (ev.get("seasonType") or {}).get("name") or ""
    return {
        "competition": league.get("name") or round_name or ev.get("name") or "",
        "comp_logo": league_logo(league),
        "comp_short": league.get("shortName") or league.get("abbreviation") or league.get("name") or "",
        "comp_slug": league.get("slug") or "",
        "is_tournament": bool(league.get("isTournament")),
        "round": round_name,
        "date_utc": parse_utc(ev.get("date")),
        "time_valid": ev.get("timeValid", True),
        "venue": venue.get("fullName") or "Venue to be confirmed",
        "city": ", ".join(x for x in [address.get("city"), address.get("country")] if x),
        "neutral": comp.get("neutralSite", False),
        "home": _team_block(_side(competitors, "home")),
        "away": _team_block(_side(competitors, "away")),
    }


def find_last_played(schedule):
    """Most recent finished match across every competition."""
    played = []
    for ev in schedule.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        state = (((comp.get("status") or {}).get("type")) or {}).get("state")
        when = parse_utc(ev.get("date"))
        if state == "post" and when:
            played.append((when, ev))
    if not played:
        return None
    played.sort(key=lambda p: p[0], reverse=True)
    return played[0][1]


# Which horizontal band a position sits in: 0 keeper ... 4 attack.
POSITION_LINE = {
    "G": 0, "GK": 0,
    "D": 1, "CD": 1, "CB": 1, "SW": 1, "LB": 1, "RB": 1, "LWB": 1, "RWB": 1, "WB": 1,
    "DM": 2, "CDM": 2, "CM": 2, "M": 2, "LM": 2, "RM": 2, "MF": 2,
    "AM": 3, "CAM": 3,
    "F": 4, "FW": 4, "CF": 4, "ST": 4, "S": 4, "LW": 4, "RW": 4, "W": 4,
}


def line_of(abbr):
    return POSITION_LINE.get((abbr or "").upper().split("-")[0], 2)


def x_rank(abbr):
    """Left-to-right order inside a band, read off the position code."""
    a = (abbr or "").upper()
    if a.startswith("L"):
        return 0
    if a.endswith("-L"):
        return 1
    if a.endswith("-R"):
        return 3
    if a.startswith("R"):
        return 4
    return 2


def _shirt(athlete):
    for img in athlete.get("jerseyImages") or []:
        if "dark" not in (img.get("rel") or []):
            return img.get("href")
    images = athlete.get("jerseyImages") or []
    return images[0].get("href") if images else ""


def _player(entry):
    athlete = entry.get("athlete") or {}
    goals = owns = 0
    yellow = red = False
    for play in entry.get("plays") or []:
        if play.get("ownGoal"):
            owns += 1
        elif play.get("didScore") or play.get("scoringPlay"):
            goals += 1
        if play.get("yellowCard"):
            yellow = True
        if play.get("redCard"):
            red = True
    return {
        "name": athlete.get("shortName") or athlete.get("displayName") or "",
        "full": athlete.get("displayName") or "",
        "jersey": entry.get("jersey") or "",
        "shirt": _shirt(athlete),
        "pos": (entry.get("position") or {}).get("abbreviation") or "",
        "starter": bool(entry.get("starter")),
        "on": bool(entry.get("subbedIn")),
        "off": bool(entry.get("subbedOut")),
        "goals": goals,
        "owns": owns,
        "yellow": yellow,
        "red": red,
    }


def parse_lineups(data):
    """Starting XI laid out in bands, plus the bench, for each side."""
    out = {}
    for block in data.get("rosters") or []:
        side = block.get("homeAway") or "home"
        players = [_player(e) for e in block.get("roster") or []]
        starters = [p for p in players if p["starter"]]

        bands = {}
        for p in starters:
            bands.setdefault(line_of(p["pos"]), []).append(p)
        rows = []
        for key in sorted(bands):
            row = sorted(bands[key], key=lambda p: (x_rank(p["pos"]), p["name"]))
            rows.append(row)

        out[side] = {
            "team": (block.get("team") or {}).get("displayName") or "",
            "crest": _logo(block.get("team") or {}),
            "formation": block.get("formation") or "",
            "rows": rows,
            "bench": [p for p in players if not p["starter"]],
        }
    return out


def parse_team_stats(data):
    out = {}
    for block in (data.get("boxscore") or {}).get("teams") or []:
        side = block.get("homeAway") or "home"
        out[side] = {s.get("name"): s.get("displayValue")
                     for s in block.get("statistics") or []}
    return out


def parse_match_detail(event_id, our_team_id):
    """Full detail for one finished match: score, goal timeline, cards."""
    data = get_json("%s/all/summary?event=%s" % (BASE, event_id))
    header = data.get("header") or {}
    comp = (header.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    home = _team_block(_side(competitors, "home"))
    away = _team_block(_side(competitors, "away"))

    info = data.get("gameInfo") or {}
    venue = info.get("venue") or {}
    address = venue.get("address") or {}

    goals, cards = [], []
    for ev in data.get("keyEvents") or []:
        etype = ev.get("type") or {}
        slug = (etype.get("type") or "").lower()
        label = (etype.get("text") or "").lower()
        minute = ((ev.get("clock") or {}).get("displayValue") or "").strip()
        team_id = str(((ev.get("team") or {}).get("id")) or "")
        people = [(p.get("athlete") or {}).get("displayName") for p in ev.get("participants") or []]
        people = [p for p in people if p]

        if ev.get("scoringPlay"):
            tag = ""
            if "own" in slug or "own goal" in label:
                tag = "OG"
            elif "penalty" in slug or "penalty" in label:
                tag = "PEN"
            goals.append({
                "minute": minute or ("SO" if ev.get("shootout") else ""),
                "player": people[0] if people else (ev.get("shortText") or "Goal"),
                "assist": people[1] if len(people) > 1 and tag != "OG" else "",
                "tag": tag,
                "shootout": bool(ev.get("shootout")),
                "side": "home" if team_id == home["id"] else "away",
                "text": ev.get("text") or "",
            })
        elif "card" in slug:
            cards.append({
                "minute": minute,
                "player": people[0] if people else "",
                "kind": "red" if "red" in slug else "yellow",
                "second": "yellow-red" in slug,
                "side": "home" if team_id == home["id"] else "away",
            })

    ours, theirs = (home, away) if home["id"] == str(our_team_id) else (away, home)
    result = "?"
    try:
        mine, yours = int(ours["score"]), int(theirs["score"])
        result = "W" if mine > yours else ("L" if mine < yours else "D")
    except (TypeError, ValueError):
        pass

    shootout = None
    if home.get("shootout") is not None and away.get("shootout") is not None:
        shootout = "%s-%s on penalties" % (home["shootout"], away["shootout"])

    league = header.get("league") or {}
    return {
        "competition": league.get("name") or "",
        "comp_logo": _logo(league),
        "comp_slug": league.get("slug") or "",
        "is_tournament": bool(league.get("isTournament")),
        "round": "",
        "date_utc": parse_utc(comp.get("date")),
        "status": ((comp.get("status") or {}).get("type") or {}).get("detail") or "Full Time",
        "venue": venue.get("fullName") or "",
        "city": ", ".join(x for x in [address.get("city"), address.get("country")] if x),
        "attendance": info.get("attendance"),
        "home": home,
        "away": away,
        "goals": goals,
        "cards": cards,
        "result": result,
        "we_are": "home" if home["id"] == str(our_team_id) else "away",
        "opponent": theirs["name"],
        "shootout": shootout,
        "lineups": parse_lineups(data),
        "team_stats": parse_team_stats(data),
    }


def parse_standings(league):
    data = get_json("%s/%s/standings" % (STANDINGS_BASE, league))
    children = data.get("children") or []
    table = data.get("standings") or (children[0].get("standings") if children else None)
    if not table:
        return []
    rows = []
    for entry in table.get("entries") or []:
        stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats") or []}
        team = entry.get("team") or {}
        rows.append({
            "rank": stats.get("rank") or "",
            "id": str(team.get("id") or ""),
            "name": team.get("displayName") or "",
            "short": team.get("shortDisplayName") or team.get("abbreviation") or "",
            "crest": _logo(team),
            "gp": stats.get("gamesPlayed") or "0",
            "w": stats.get("wins") or "0",
            "d": stats.get("ties") or "0",
            "l": stats.get("losses") or "0",
            "gf": stats.get("pointsFor") or "0",
            "ga": stats.get("pointsAgainst") or "0",
            "gd": stats.get("pointDifferential") or "0",
            "pts": stats.get("points") or "0",
        })

    def rank_key(row):
        try:
            return int(row["rank"])
        except (TypeError, ValueError):
            return 999

    rows.sort(key=rank_key)
    return rows


def load_team(cfg):
    """Everything for one club. Errors land in the payload, they do not raise."""
    out = {"cfg": cfg, "error": None, "next": None, "last": None, "table": [], "position": None}
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_team = pool.submit(get_json, "%s/all/teams/%s" % (BASE, cfg["espn_id"]))
            f_sched = pool.submit(get_json, "%s/all/teams/%s/schedule" % (BASE, cfg["espn_id"]))
            f_table = pool.submit(parse_standings, cfg["league"])
            team_info = (f_team.result() or {}).get("team") or {}
            schedule = f_sched.result() or {}
            out["table"] = f_table.result()

        out["position"] = team_info.get("standingSummary")
        out["next"] = parse_next_match(team_info)
        last_event = find_last_played(schedule)
        if last_event:
            out["last"] = parse_match_detail(last_event["id"], cfg["espn_id"])
    except Exception as exc:  # network hiccup, schema drift, anything
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def load_all():
    with ThreadPoolExecutor(max_workers=len(TEAMS)) as pool:
        return list(pool.map(load_team, TEAMS))


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#eef2f8; --panel:#ffffff; --panel2:#f6f8fc; --soft:#f1f5f9;
  --line:#e3e9f1; --line2:#eef2f7;
  --txt:#101828; --mut:#667a92; --dim:#90a1b5;
  --win:#0f9d76; --draw:#c2820b; --loss:#dc2f3c;
  --blue:#0b7fc7;
  --hl:#bae6fd;                   /* light blue - your clubs in the table */
  --hl-ink:#0b3f5c;
  --vil:#fde2e4;                  /* the enemy, highlighted in disgust */
  --vil-ink:#7a1020;
  --radius:17px;
  --shadow:0 1px 2px rgba(16,24,40,.05), 0 14px 32px -24px rgba(16,24,40,.45);
}
html,body{margin:0;padding:0}
body{
  background:
    radial-gradient(880px 440px at 6% -12%, rgba(254,190,16,.28), transparent 60%),
    radial-gradient(880px 440px at 50% -12%, rgba(218,41,28,.16), transparent 60%),
    radial-gradient(880px 440px at 94% -12%, rgba(60,70,90,.16), transparent 60%),
    var(--bg);
  background-attachment:fixed;
  color:var(--txt);
  font-family:"Plus Jakarta Sans","Segoe UI Variable Text",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:16px;line-height:1.5;
  -webkit-font-smoothing:antialiased;
  padding:0 0 60px;
}
.wrap{max-width:1880px;margin:0 auto;padding:0 22px}

/* ---------- top bar ---------- */
header.top{
  position:sticky;top:0;z-index:20;
  backdrop-filter:blur(16px);
  background:rgba(255,255,255,.82);
  border-bottom:1px solid var(--line);
  padding:15px 0;margin-bottom:26px;
}
.top-in{display:flex;align-items:center;gap:20px;flex-wrap:wrap}
h1{margin:0;font-size:22px;font-weight:800;letter-spacing:-.4px;display:flex;align-items:center;gap:10px}
h1 img{width:30px;height:30px;object-fit:contain}
h1 .sep{color:var(--dim);font-weight:500}
h1 .vs-evil{color:var(--dim);font-weight:600;font-size:18px}
.tzline{color:var(--mut);font-size:15px}
.tzline b{color:var(--blue);font-weight:700}
.spacer{flex:1}
.btn{
  appearance:none;border:1px solid var(--line);background:var(--panel);color:var(--txt);
  padding:9px 17px;border-radius:11px;font-size:15px;font-weight:700;cursor:pointer;
  display:inline-flex;align-items:center;gap:8px;text-decoration:none;
  box-shadow:0 1px 2px rgba(16,24,40,.06);transition:.15s;font-family:inherit;
}
.btn:hover{border-color:#c9d6e5;background:#fbfdff;transform:translateY(-1px)}
.stamp{color:var(--dim);font-size:14px;white-space:nowrap}

/* ---------- row grid: every club shares every row ---------- */
.lanes{display:grid;grid-template-columns:repeat(var(--cols,3),minmax(0,1fr));gap:22px;align-items:stretch}
.lane{display:contents}
.slot{display:flex;flex-direction:column;gap:16px;min-width:0;grid-column:var(--col)}
.slot:nth-child(1){grid-row:1}
.slot:nth-child(2){grid-row:2}
.slot:nth-child(3){grid-row:3}
.slot:nth-child(4){grid-row:4}
.slot>.card{flex:1 1 auto}

.card{
  background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  overflow:hidden;box-shadow:var(--shadow);
}
.card-h{
  display:flex;align-items:center;gap:9px;padding:13px 19px;
  border-bottom:1px solid var(--line2);background:var(--panel2);
  font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);
}
.card.evil .card-h{background:#f7f2f4;color:#8d5566}
.card-b{padding:19px}

/* ---------- club banner ---------- */
.club{
  display:flex;align-items:center;gap:16px;padding:18px 21px;
  border-radius:var(--radius);border:1px solid var(--line);
  background:linear-gradient(110deg,var(--tint) 0%,#fff 62%);
  box-shadow:var(--shadow);position:relative;overflow:hidden;
}
.club::after{content:"";position:absolute;inset:0 auto 0 0;width:5px;background:var(--accent);z-index:2}
.club img{width:58px;height:58px;object-fit:contain;position:relative;z-index:2}
.club-txt{position:relative;z-index:2;min-width:0}
.club-name{font-size:23px;font-weight:800;letter-spacing:-.5px;line-height:1.15}
.club-sub{color:var(--mut);font-size:14.5px;margin-top:2px;font-weight:600}

/* the enemy lane */
.club.villain{background:linear-gradient(115deg,#232a38 0%,#313a4e 52%,#4a2436 100%);border-color:#39415a}
.club.villain .club-name{color:#fff}
.club.villain .club-sub{color:#b9c4d8}
.club.villain img{filter:grayscale(.45) contrast(1.05);opacity:.9}
.club.villain::after{background:#c1121f}
.crest-wrap{position:relative;flex:none;z-index:2}
.crest-wrap .mask{position:absolute;right:-7px;bottom:-5px;font-size:23px;filter:drop-shadow(0 2px 3px rgba(0,0,0,.4))}

/* ---------- winner animation ---------- */
.club.winner{animation:win-pulse 2.9s ease-in-out infinite}
.club.winner::before{
  content:"";position:absolute;top:0;bottom:0;left:0;width:38%;z-index:1;pointer-events:none;
  background:linear-gradient(105deg,transparent 0%,rgba(255,255,255,.9) 50%,transparent 100%);
  transform:translateX(-160%);animation:win-sweep 3.6s ease-in-out infinite;
}
.club.winner.villain::before{background:linear-gradient(105deg,transparent 0%,rgba(255,215,120,.42) 50%,transparent 100%)}
@keyframes win-sweep{0%{transform:translateX(-160%)}58%,100%{transform:translateX(420%)}}
@keyframes win-pulse{
  0%,100%{box-shadow:var(--shadow)}
  50%{box-shadow:0 0 0 4px rgba(var(--ring),.34), 0 14px 34px -20px rgba(var(--ring),.9)}
}
@media (prefers-reduced-motion:reduce){
  .club.winner,.club.winner::before{animation:none}
}

/* ---------- chips row: competition left, stadium right ---------- */
.chips{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:15px}
.chips-l{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.chips-r{margin-left:auto;text-align:right;color:var(--mut);font-size:13.5px;font-weight:600;min-width:0;line-height:1.35}
.chips-r b{color:var(--txt);font-weight:700;display:block}
/* today - warm amber, unused elsewhere in the palette so it always reads as "now" */
.today{
  display:inline-flex;align-items:center;gap:8px;
  background:#fff2e8;border:1px solid #ffc9a6;color:#c2410c;
  font-weight:800;letter-spacing:.02em;padding:4px 12px;border-radius:999px;
  font-size:13px;line-height:1.3;
}
.today .dot{
  width:7px;height:7px;border-radius:50%;background:#f97316;flex:none;
  box-shadow:0 0 0 3px rgba(249,115,22,.22);
  animation:today-pulse 1.7s ease-in-out infinite;
}
@keyframes today-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.68)}}
.chips-r .when{display:block;margin-top:4px;font-size:12.5px;color:var(--dim);font-weight:600}
.chips-r .today{margin-top:6px}
.kick .d.today{font-size:20px;padding:6px 17px;margin-bottom:3px}
@media (prefers-reduced-motion:reduce){.today .dot{animation:none}}

.chip{
  font-size:12px;font-weight:800;letter-spacing:.05em;padding:5px 11px;border-radius:999px;
  background:var(--soft);border:1px solid var(--line);color:var(--mut);white-space:nowrap;
}
.chip.comp{color:var(--txt);background:#eef3f9;display:inline-flex;align-items:center;gap:7px;padding-left:7px}
.chip.comp img{width:18px;height:18px;object-fit:contain;flex:none}

/* what is riding on the next match */
.stakes{
  display:flex;align-items:center;gap:12px;flex-wrap:nowrap;margin:0 0 16px;
  padding:11px 14px;border-radius:12px;font-size:13px;font-weight:600;line-height:1.4;
  min-height:var(--stakes-h,46px);
}
.stakes.hold{visibility:hidden;border-color:transparent;background:none}
.stakes b{
  font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;
  padding:5px 11px;border-radius:999px;white-space:nowrap;flex:none;
}
.stakes span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stakes.danger{background:#fdecee;border:1px solid #f6c6cb;color:#8d1f28}
.stakes.danger b{background:#dc2f3c;color:#fff;animation:stake-pulse 1.9s ease-in-out infinite}
.stakes.hot{background:#f5f1ff;border:1px solid #ded4fb;color:#4a2a90}
.stakes.hot b{background:#6d28d9;color:#fff}
.stakes.good{background:#e7f8f2;border:1px solid #b6e6d5;color:#0a6b50}
.stakes.good b{background:#0f9d76;color:#fff}

/* the one fixture that needs no explaining */
.stakes.clasico{
  position:relative;overflow:hidden;justify-content:center;gap:18px;border:none;padding:10px 18px;
  background:linear-gradient(100deg,#0a0d14 0%,#16213b 34%,#4b1330 66%,#2b0a1a 100%);
  background-size:220% 100%;animation:clasico-pan 9s ease-in-out infinite;
  box-shadow:0 10px 30px -14px rgba(75,19,48,.85);
}
.stakes.clasico::after{
  content:"";position:absolute;top:0;bottom:0;left:0;width:34%;pointer-events:none;
  background:linear-gradient(105deg,transparent,rgba(255,255,255,.34),transparent);
  transform:translateX(-180%);animation:clasico-shine 4.4s ease-in-out infinite;
}
.stakes.clasico img{
  width:40px;height:40px;object-fit:contain;flex:none;position:relative;z-index:2;
  filter:drop-shadow(0 3px 7px rgba(0,0,0,.65));animation:clasico-breathe 3.1s ease-in-out infinite;
}
.stakes.clasico img:last-child{animation-delay:1.55s}
.stakes.clasico .body{display:flex;flex-direction:column;align-items:center;gap:3px;position:relative;z-index:2;min-width:0}
.stakes.clasico b{
  background:none;padding:0;font-size:21px;font-weight:800;letter-spacing:.16em;
  color:#fff;text-shadow:0 2px 14px rgba(255,214,140,.55);animation:none;
}
.stakes.clasico .tag{
  font-size:11.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:#ffd98a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
@keyframes clasico-pan{0%,100%{background-position:0% 50%}50%{background-position:100% 50%}}
@keyframes clasico-shine{0%{transform:translateX(-180%)}55%,100%{transform:translateX(420%)}}
@keyframes clasico-breathe{0%,100%{transform:scale(1)}50%{transform:scale(1.11)}}
@media (prefers-reduced-motion:reduce){
  .stakes.clasico,.stakes.clasico::after,.stakes.clasico img{animation:none}
}
@keyframes stake-pulse{0%,100%{box-shadow:0 0 0 0 rgba(220,47,60,.5)}55%{box-shadow:0 0 0 8px rgba(220,47,60,0)}}
@media (prefers-reduced-motion:reduce){.stakes.danger b{animation:none}}
.chip.w{background:#e7f8f2;border-color:#b6e6d5;color:var(--win)}
.chip.d{background:#fdf5e3;border-color:#f0dcae;color:var(--draw)}
.chip.l{background:#fdecee;border-color:#f6c6cb;color:var(--loss)}
.chip.good{background:#e7f8f2;border-color:#b6e6d5;color:var(--win)}
.chip.bad{background:#fdecee;border-color:#f6c6cb;color:var(--loss)}
.chip.home{background:#e5f4fe;border-color:#a9d9f6;color:#0b6aa8}
.chip.away{background:var(--soft)}

/* ---------- scoreline, with each side's cards under its own crest ---------- */
.score{display:grid;grid-template-columns:1fr auto 1fr;align-items:start;gap:12px;margin:6px 0 4px}
.sq{display:flex;flex-direction:column;align-items:center;gap:10px;text-align:center;min-width:0}
.sq img{width:52px;height:52px;object-fit:contain}
.sq .nm{font-size:14px;color:var(--mut);line-height:1.25;font-weight:600;word-break:break-word;
  min-height:2.5em;display:flex;align-items:center;justify-content:center}
.sq.us .nm{color:var(--txt);font-weight:800}
.nums{display:flex;align-items:center;gap:12px;font-size:44px;font-weight:800;letter-spacing:-2px;
  font-variant-numeric:tabular-nums;padding-top:14px}
.nums .vs{font-size:17px;color:var(--dim);font-weight:700;letter-spacing:0}
.nums .dash{color:#cbd5e1;font-weight:500}
.pens{text-align:center;color:var(--draw);font-size:13px;font-weight:800;margin-top:4px}

.bookings{display:flex;flex-direction:column;gap:5px;width:100%;margin-top:4px;
  min-height:calc(var(--book-rows,0) * 34px)}
.bk{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--mut);font-weight:600;
  background:var(--panel2);border:1px solid var(--line);padding:5px 10px;border-radius:9px;
  text-align:left;line-height:1.3}
.bk i{width:9px;height:12px;border-radius:2px;display:inline-block;flex:none}
.bk i.y{background:#f2b70a}
.bk i.r{background:#e0353f}
.bk .mn{font-weight:800;color:var(--txt);font-variant-numeric:tabular-nums;flex:none}
.bk .pl{min-width:0}

/* ---------- goal timeline ---------- */
.tl-h{
  display:flex;align-items:center;gap:9px;margin:18px 0 12px;
  font-size:11px;font-weight:800;letter-spacing:.13em;text-transform:uppercase;color:var(--dim);
}
.tl-h::after{content:"";flex:1;height:1px;background:var(--line)}
.tl{display:grid;grid-template-columns:1fr 58px 1fr;gap:6px 9px;align-items:center}
.tl .ev{display:flex;align-items:center;gap:9px;font-size:15px;padding:7px 2px;min-width:0}
.tl .ev.h{justify-content:flex-end;text-align:right}
.tl .ev.a{justify-content:flex-start}
.tl .min{
  justify-self:center;font-size:12.5px;font-weight:800;color:var(--txt);
  background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:4px 0;width:100%;
  text-align:center;font-variant-numeric:tabular-nums;
}
.tl .who{font-weight:700;min-width:0;color:var(--txt)}
.tl .ast{color:var(--dim);font-size:13px;font-weight:500;white-space:nowrap;
  min-width:0;overflow:hidden;text-overflow:ellipsis;flex:0 1 auto}
.tag{font-size:10px;font-weight:800;letter-spacing:.05em;padding:2px 6px;border-radius:5px;flex:none}
.tag.og{background:#fdecee;color:var(--loss)}
.tag.pen{background:#fdf5e3;color:var(--draw)}
.tag.so{background:#e5f4fe;color:#0b6aa8}
.ball{flex:none;font-size:14px}

/* ---------- next match ---------- */
.card.last-card,.card.next-card{display:flex;flex-direction:column}
.card.last-card .card-b{flex:1;display:flex;flex-direction:column}
.card.last-card .lu-btn{margin-top:auto}
.card.next-card .card-b{flex:1;display:flex;flex-direction:column}
.card.next-card .cd{margin-top:auto}
.kick{text-align:center;margin:6px 0 4px}
.kick .d{min-height:36px;display:flex;align-items:center;justify-content:center}
.kick .d{font-size:16.5px;font-weight:700;color:var(--txt)}
.kick .d.today{display:inline-flex}
.kick .t{font-size:44px;font-weight:800;letter-spacing:-2px;margin:2px 0;color:var(--blue);font-variant-numeric:tabular-nums}
.card.evil .kick .t{color:#9b2233}
.kick .z{font-size:13px;color:var(--mut);font-weight:600}
.cd{
  margin-top:15px;text-align:center;font-size:14px;color:var(--mut);font-weight:600;
  background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:11px;
}
.cd b{color:var(--txt);font-weight:800;font-variant-numeric:tabular-nums}
.cd.live b{color:var(--win)}

/* ---------- league table ---------- */
.tbl-scroll{overflow-x:auto}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:14.5px;min-width:460px}
thead th{
  text-align:center;padding:9px 6px;font-size:11px;font-weight:800;letter-spacing:.09em;
  text-transform:uppercase;color:var(--dim);border-bottom:1px solid var(--line);white-space:nowrap;
}
thead th.club-col{text-align:left;padding-left:3px}
tbody td{padding:9px 6px;text-align:center;border-bottom:1px solid var(--line2);
  font-variant-numeric:tabular-nums;font-weight:600}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--panel2)}
td.pos{width:38px;font-weight:800;color:var(--mut);position:relative}
td.pos::before{content:"";position:absolute;left:0;top:4px;bottom:4px;width:3px;border-radius:2px;background:var(--zone,transparent)}
td.club-col{text-align:left;padding-left:3px}
.tm{display:flex;align-items:center;gap:10px;min-width:0}
.tm img{width:22px;height:22px;object-fit:contain;flex:none}
.tm span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700}
td.pts{font-weight:800;color:var(--txt)}
td.mut{color:var(--mut);font-weight:500}

/* your clubs - light blue */
tbody tr.me td{background:var(--hl);color:var(--hl-ink);font-weight:800;border-bottom-color:#a5d8f5}
tbody tr.me:hover td{background:#a9dcfa}
tbody tr.me td.pos{color:var(--hl-ink)}
tbody tr.me td.mut{color:#2d6285;font-weight:700}
tbody tr.me td.pos::before{background:var(--hl-ink)}
tbody tr.me .tm span{font-weight:800}
tbody tr.me td:first-child{border-top-left-radius:9px;border-bottom-left-radius:9px}
tbody tr.me td:last-child{border-top-right-radius:9px;border-bottom-right-radius:9px}

/* the enemy - not light blue, obviously */
tbody tr.foe td{background:var(--vil);color:var(--vil-ink);font-weight:800;border-bottom-color:#f6c9ce}
tbody tr.foe:hover td{background:#fbd2d6}
tbody tr.foe td.pos{color:var(--vil-ink)}
tbody tr.foe td.mut{color:#93404f;font-weight:700}
tbody tr.foe td.pos::before{background:var(--vil-ink)}
tbody tr.foe .tm img{filter:grayscale(.5)}
tbody tr.foe td:first-child{border-top-left-radius:9px;border-bottom-left-radius:9px}
tbody tr.foe td:last-child{border-top-right-radius:9px;border-bottom-right-radius:9px}

.legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:15px;font-size:12px;color:var(--mut);font-weight:600}
.legend span{display:flex;align-items:center;gap:7px}
.legend i{width:10px;height:10px;border-radius:2px;display:inline-block;border:1px solid rgba(16,24,40,.12)}

/* ---------- rivalry panel (Barcelona lane) ---------- */
.verdict{
  background:linear-gradient(115deg,#232a38,#333c51 60%,#4a2436);color:#fff;border-radius:13px;
  padding:16px 18px;font-size:15.5px;font-weight:700;line-height:1.45;margin-bottom:16px;
}
.verdict .lead{display:block;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  color:#ffb3bd;font-weight:800;margin-bottom:6px}
.cmp{margin-top:18px;display:flex;flex-direction:column;gap:14px}
.cmp-lbl{text-align:center;font-size:11px;font-weight:800;letter-spacing:.11em;
  text-transform:uppercase;color:var(--dim);margin-bottom:6px}
.cmp-row{display:grid;grid-template-columns:52px 1fr 52px;align-items:center;gap:11px}
.cmp-v{font-size:16px;font-weight:800;font-variant-numeric:tabular-nums}
.cmp-v.us{text-align:right;color:var(--hl-ink)}
.cmp-v.foe{text-align:left;color:var(--vil-ink)}
.cmp-bars{display:flex;align-items:center;gap:4px;height:10px}
.cmp-bars span{height:100%;border-radius:3px;min-width:2px}
.cmp-bars .l{background:#7cc9f0}
.cmp-bars .r{background:#ef9aa5}
.mock{margin-top:18px;font-size:13.5px;color:var(--mut);font-weight:600;font-style:italic;text-align:center}

/* ---------- did-you-know hint ---------- */
.hint{
  margin-left:auto;position:relative;flex:none;width:23px;height:23px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;cursor:help;
  background:var(--soft);border:1px solid var(--line);color:var(--mut);
  font-size:12.5px;font-weight:800;letter-spacing:0;transition:.18s;
}
.hint:hover,.hint:focus{background:var(--blue);border-color:var(--blue);color:#fff;outline:none;transform:scale(1.1)}
.hint .pool{display:none}
.hint .bubble{
  position:absolute;top:calc(100% + 11px);right:-3px;width:330px;max-width:min(330px,68vw);
  background:#0f1722;color:#e7eef7;border-radius:13px;padding:14px 16px;
  font-size:13px;font-weight:500;line-height:1.55;text-transform:none;letter-spacing:0;
  text-align:left;box-shadow:0 20px 44px -18px rgba(8,14,22,.8);
  opacity:0;visibility:hidden;transform:translateY(-6px);transition:.2s ease;z-index:30;
}
.hint:hover .bubble,.hint:focus .bubble{opacity:1;visibility:visible;transform:translateY(0)}
.hint .bubble::before{
  content:"";position:absolute;bottom:100%;right:10px;
  border:7px solid transparent;border-bottom-color:#0f1722;
}
.hint .bubble b{
  display:block;font-size:10.5px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;
  color:#7cc9f0;margin-bottom:7px;
}
@media (max-width:560px){.hint .bubble{right:auto;left:50%;transform:translate(-50%,-6px);width:min(300px,86vw)}
  .hint:hover .bubble,.hint:focus .bubble{transform:translate(-50%,0)}
  .hint .bubble::before{right:auto;left:50%;margin-left:-7px}}

/* ---------- anthem: hover the last-match card to play ---------- */
.eq{
  display:flex;align-items:flex-end;gap:3px;height:16px;margin-left:auto;flex:none;
  opacity:.4;transition:opacity .35s ease;
}
.eq i{width:3px;height:4px;border-radius:2px;background:var(--dim);transition:background .35s ease}
.card.playing .eq{opacity:1}
.card.playing .eq i{background:var(--blue);animation:eq .85s ease-in-out infinite}
.card.playing .eq i:nth-child(2){animation-delay:.14s}
.card.playing .eq i:nth-child(3){animation-delay:.28s}
.card.playing .eq i:nth-child(4){animation-delay:.42s}
@keyframes eq{0%,100%{height:4px}50%{height:16px}}
@media (prefers-reduced-motion:reduce){.card.playing .eq i{animation:none;height:11px}}

.empty{color:var(--dim);text-align:center;padding:28px 12px;font-size:15px;font-weight:500}
.err{background:#fdecee;border:1px solid #f6c6cb;color:#a4232e;padding:13px 16px;border-radius:12px;font-size:14px;line-height:1.5}
footer{text-align:center;color:var(--dim);font-size:13px;margin-top:34px;line-height:1.7;font-weight:500}

@media (max-width:1250px){
  .lanes{grid-template-columns:minmax(0,1fr)}
  .lane{display:flex;flex-direction:column;gap:16px}
  .slot{grid-column:auto;grid-row:auto}
}
@media (max-width:560px){
  .wrap{padding:0 14px}
  h1{font-size:19px}
  .nums{font-size:36px}
  .tl{grid-template-columns:1fr 50px 1fr}
  .tl .ev{font-size:13.5px;padding:6px 9px}
  .tl .ast{display:none}
  .kick .t{font-size:36px}
  .sq img{width:44px;height:44px}
}
"""

CSS += """
/* ---------- lineups + match stats ---------- */
.lu-btn{
  width:100%;margin-top:18px;display:flex;align-items:center;justify-content:center;gap:9px;
  padding:12px;border-radius:12px;border:1px solid var(--line);background:var(--panel2);
  font-family:inherit;font-size:14px;font-weight:700;color:var(--txt);cursor:pointer;transition:.15s;
}
.lu-btn:hover{background:#eaf1f9;border-color:#c5d4e6;transform:translateY(-1px)}
.lu-btn .caret{font-style:normal;color:var(--mut)}
.card.evil .lu-btn:hover{background:#f9eef1;border-color:#e6c2cb}

dialog.lu{
  border:none;padding:0;border-radius:20px;width:min(1240px,95vw);max-width:95vw;max-height:92vh;
  background:var(--panel);color:var(--txt);font-family:inherit;
  box-shadow:0 34px 90px -30px rgba(8,14,22,.7);
}
dialog.lu::backdrop{background:rgba(8,14,22,.62);backdrop-filter:blur(3px)}
.lu-head{
  display:flex;align-items:center;gap:15px;padding:16px 20px;
  border-bottom:1px solid var(--line);background:var(--panel2);
}
.lu-score{display:flex;align-items:center;gap:12px;font-size:27px;font-weight:800;font-variant-numeric:tabular-nums}
.lu-score img{width:36px;height:36px;object-fit:contain}
.lu-score span{color:#cbd5e1;font-weight:500}
.lu-meta{color:var(--mut);font-size:13.5px;font-weight:600;min-width:0;line-height:1.35}
.lu-x{margin-left:auto;background:none;border:none;font-size:30px;line-height:1;color:var(--dim);cursor:pointer;font-family:inherit;padding:0 4px}
.lu-x:hover{color:var(--txt)}
dialog.lu :focus{outline:none}
dialog.lu :focus-visible{outline:2px solid var(--blue);outline-offset:2px;border-radius:8px}
.lu-body{
  display:grid;grid-template-columns:minmax(0,1.1fr) minmax(310px,.9fr);gap:22px;padding:20px;
  overflow:auto;max-height:calc(92vh - 78px);
}

/* the pitch */
.pitch-wrap{border-radius:15px;overflow:hidden;border:1px solid #16311f;background:#16311f;
  max-width:min(100%,calc(64vh * 70 / 104));margin:0 auto}
.team-bar{display:flex;align-items:center;gap:10px;padding:10px 14px;color:#fff;font-size:14.5px;font-weight:700}
.team-bar img{width:23px;height:23px;object-fit:contain;flex:none}
.team-bar .tn{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.team-bar .form{margin-left:auto;background:#1f6b3f;color:#d9f7e4;font-size:12px;font-weight:800;padding:3px 11px;border-radius:999px;flex:none}
.pitch{
  position:relative;aspect-ratio:70/104;
  background:repeating-linear-gradient(180deg,#2f7d4a 0 8.34%,#2a7444 8.34% 16.68%);
}
.mark{position:absolute;border:2px solid rgba(255,255,255,.26)}
.halfway{left:0;right:0;top:50%;border-width:0 0 2px 0}
.circle{left:50%;top:50%;width:19%;aspect-ratio:1;transform:translate(-50%,-50%);border-radius:50%}
.spot{left:50%;top:50%;width:6px;height:6px;transform:translate(-50%,-50%);background:rgba(255,255,255,.38);border:none;border-radius:50%}
.box{left:21%;right:21%;height:15%}
.box.top{top:-2px}
.box.bottom{bottom:-2px}
.six{left:35%;right:35%;height:6.4%}
.six.top{top:-2px}
.six.bottom{bottom:-2px}

.pp{position:absolute;transform:translate(-50%,-50%);width:96px;text-align:center;z-index:2}
.shirt{position:relative;width:48px;height:48px;margin:0 auto}
.shirt img{width:100%;height:100%;object-fit:contain;filter:drop-shadow(0 3px 5px rgba(0,0,0,.45))}
.pname{
  margin-top:4px;font-size:11.5px;color:#fff;line-height:1.25;font-weight:600;
  text-shadow:0 1px 3px rgba(0,0,0,.9);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.pname b{font-weight:800}
.marks{position:absolute;right:-8px;top:-5px;display:flex;flex-direction:column;gap:2px;align-items:center}
.marks .b{
  display:flex;align-items:center;justify-content:center;font-style:normal;
  width:18px;height:18px;border-radius:50%;font-size:10px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.5);
}
.marks .own{background:#fecdd3}
.marks .yc{width:12px;height:16px;border-radius:2px;background:#facc15}
.marks .rc{width:12px;height:16px;border-radius:2px;background:#ef4444}
.marks .off{background:#ef4444;color:#fff;font-weight:800}
.marks .on{background:#22c55e;color:#fff;font-weight:800}

.bench{margin-top:16px}
.bench-h{font-size:10.5px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;color:var(--dim);margin-bottom:9px}
.sub{
  display:inline-flex;align-items:center;gap:7px;position:relative;font-size:12.5px;font-weight:600;
  color:var(--mut);background:var(--panel2);border:1px solid var(--line);
  padding:5px 11px;border-radius:9px;margin:0 6px 6px 0;
}
.sub.used{color:var(--txt);border-color:#bfe3cf;background:#effaf3}
.sub .marks{position:static;flex-direction:row;gap:3px}
.sub .marks .b{width:15px;height:15px;font-size:9px;box-shadow:none}
.sub .marks .yc,.sub .marks .rc{width:9px;height:13px}

/* team stats */
.lu-right{border:1px solid var(--line);border-radius:15px;padding:0 17px 16px;background:var(--panel2);align-self:start}
.stat-head{
  display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 2px 13px;
  border-bottom:1px solid var(--line);margin-bottom:7px;
  font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);
}
.stat-head img{width:28px;height:28px;object-fit:contain;flex:none}
.srow{display:grid;grid-template-columns:60px 1fr 60px;align-items:center;gap:10px;padding:7px 0}
.sl{text-align:center;font-size:13px;color:var(--mut);font-weight:600}
.sv{font-size:14.5px;font-weight:800;font-variant-numeric:tabular-nums;text-align:center;padding:5px 0;border-radius:999px;color:var(--txt)}
.sv.lead.h{background:#0f9d76;color:#fff}
.sv.lead.a{background:#0b7fc7;color:#fff}

/* chips sized from the pitch's own height, so rows stay clear of each other */
@supports (width:1cqh){
  .pitch{container-type:size}
  .pp{width:14cqh}
  .shirt{width:6.3cqh;height:6.3cqh}
  .pname{font-size:1.62cqh;margin-top:.35cqh}
  .marks{right:-1.1cqh;top:-.7cqh;gap:.3cqh}
  .marks .b{width:2.5cqh;height:2.5cqh;font-size:1.35cqh}
  .marks .yc,.marks .rc{width:1.7cqh;height:2.3cqh}
}

@media (max-width:900px){
  .lu-body{grid-template-columns:minmax(0,1fr)}
}
"""


def esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def zone_colour(rank, total):
    """Qualification / relegation stripe next to the position number."""
    try:
        rank = int(rank)
    except (TypeError, ValueError):
        return "transparent"
    if rank <= 4:
        return "#10b981"
    if rank == 5:
        return "#3b82f6"
    if rank == 6:
        return "#8b5cf6"
    if total and rank > total - 3:
        return "#ef4444"
    return "transparent"


def render_goal_row(goal):
    """One row of the timeline: left cell, minute, right cell."""
    tags = ""
    if goal["tag"] == "OG":
        tags = '<span class="tag og">OG</span>'
    elif goal["tag"] == "PEN":
        tags = '<span class="tag pen">PEN</span>'
    if goal["shootout"]:
        tags += '<span class="tag so">SO</span>'
    who = '<span class="who">%s</span>' % esc(goal["player"])
    minute = '<div class="min">%s</div>' % esc(goal["minute"] or "-")

    if goal["side"] == "home":
        cell = '<div class="ev h">%s%s<span class="ball">&#9917;</span></div>' % (who, tags)
        return cell + minute + "<div></div>"
    cell = '<div class="ev a"><span class="ball">&#9917;</span>%s%s</div>' % (tags, who)
    return "<div></div>" + minute + cell


def ordinal(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suffix)


# Result badge. The villain lane inverts the sentiment on purpose.
RESULT_CHIP = {"W": ("w", "WON"), "D": ("d", "DRAW"), "L": ("l", "LOST")}
VILLAIN_CHIP = {"W": ("bad", "WON"), "D": ("d", "DRAW"), "L": ("good", "LOST")}


def layout_reserves(data):
    """Rows only line up if every card reserves the same space for the blocks
    that not all of them have: bookings under a crest, and the stakes banner."""
    books = 0
    stakes = False
    for payload in data:
        last = payload.get("last")
        if last:
            for side in ("home", "away"):
                books = max(books, sum(1 for c in last["cards"] if c["side"] == side))

    big = False
    for payload in data:
        call = match_stakes(payload["cfg"], payload.get("next"), payload.get("table") or [])
        if call:
            stakes = True
            if call[2] == "clasico":
                big = True
    return {"books": books, "stakes": stakes, "stakes_h": 78 if big else 46}


def comp_chip(block):
    badge = '<img src="%s" alt="" loading="lazy">' % esc(block["comp_logo"]) if block.get("comp_logo") else ""
    return '<span class="chip comp">%s%s</span>' % (badge, esc(block["competition"]))


def booking_list(cards, side):
    """That side's cards, ready to sit under its own crest."""
    mine = [c for c in cards if c["side"] == side]
    pills = "".join(
        '<span class="bk"><i class="%s"></i><span class="mn">%s</span>'
        '<span class="pl">%s</span></span>' % (
            "r" if c["kind"] == "red" else "y",
            esc(c["minute"]),
            esc(c["player"]) + (" (2nd yellow)" if c["second"] else ""))
        for c in mine)
    return '<div class="bookings">%s</div>' % pills


def day_label(bg_dt):
    """('Today', True) when the match falls on today's date in Bulgaria."""
    if not bg_dt:
        return "", False
    if bg_dt.date() == datetime.now(timezone.utc).astimezone(BG).date():
        return "Today", True
    return fmt_date(bg_dt), False


def when_line(bg_dt):
    """Date and kick-off time, with today called out."""
    if not bg_dt:
        return ""
    label, today = day_label(bg_dt)
    clock = "%02d:%02d" % (bg_dt.hour, bg_dt.minute)
    if today:
        return ('<span class="today"><span class="dot"></span>Today &middot; %s</span>'
                % esc(clock))
    return '<span class="when">%s &middot; %s</span>' % (esc(label), esc(clock))


def venue_line(venue, city, when=""):
    bits = []
    if venue:
        bits.append("<b>%s</b>" % esc(venue))
    if city:
        bits.append(esc(city))
    if when:
        bits.append(when)
    return '<div class="chips-r">%s</div>' % "".join(bits) if bits else ""


def _badges(p):
    marks = []
    for _ in range(p["goals"]):
        marks.append('<i class="b goal">&#9917;</i>')
    for _ in range(p["owns"]):
        marks.append('<i class="b own">&#9917;</i>')
    if p["yellow"]:
        marks.append('<i class="b yc"></i>')
    if p["red"]:
        marks.append('<i class="b rc"></i>')
    if p["off"]:
        marks.append('<i class="b off">&#8595;</i>')
    elif p["on"]:
        marks.append('<i class="b on">&#8593;</i>')
    return '<span class="marks">%s</span>' % "".join(marks) if marks else ""


def _pitch_player(p, left, top):
    shirt = ('<img src="%s" alt="" loading="lazy">' % esc(p["shirt"])) if p["shirt"] else ""
    return (
        '<div class="pp" style="left:%.2f%%;top:%.2f%%" title="%s">'
        '<div class="shirt">%s%s</div>'
        '<div class="pname"><b>%s</b> %s</div></div>'
    ) % (left, top, esc("%s - %s" % (p["full"] or p["name"], p["pos"])),
         shirt, _badges(p), esc(p["jersey"]), esc(p["name"]))


def render_pitch(lineups):
    """Both XIs on one pitch: home attacking down, away attacking up."""
    spots = []
    for side, top_y, bottom_y in (("home", 7.0, 45.0), ("away", 93.0, 55.0)):
        block = lineups.get(side) or {}
        rows = block.get("rows") or []
        if not rows:
            continue
        span = len(rows) - 1
        for i, row in enumerate(rows):
            y = top_y if span == 0 else top_y + (bottom_y - top_y) * i / span
            for j, p in enumerate(row):
                spots.append(_pitch_player(p, (j + 1) * 100.0 / (len(row) + 1), y))

    def bar(side, where):
        block = lineups.get(side) or {}
        crest = ('<img src="%s" alt="">' % esc(block.get("crest"))) if block.get("crest") else ""
        return ('<div class="team-bar %s">%s<span class="tn">%s</span>'
                '<span class="form">%s</span></div>'
                % (where, crest, esc(block.get("team") or ""), esc(block.get("formation") or "")))

    return (
        '<div class="pitch-wrap">%s'
        '<div class="pitch"><div class="mark halfway"></div><div class="mark circle"></div>'
        '<div class="mark spot"></div>'
        '<div class="mark box top"></div><div class="mark six top"></div>'
        '<div class="mark box bottom"></div><div class="mark six bottom"></div>'
        '%s</div>%s</div>'
    ) % (bar("home", "top"), "".join(spots), bar("away", "bottom"))


def render_bench(lineups):
    parts = []
    for side in ("home", "away"):
        block = lineups.get(side) or {}
        bench = block.get("bench") or []
        if not bench:
            continue
        used = [p for p in bench if p["on"]] or bench[:7]
        names = "".join(
            '<span class="sub%s"><b>%s</b> %s%s</span>'
            % (" used" if p["on"] else "", esc(p["jersey"]), esc(p["name"]), _badges(p))
            for p in used)
        parts.append('<div class="bench"><div class="bench-h">%s &middot; %s</div>%s</div>'
                     % (esc(block.get("team") or ""),
                        "on from the bench" if any(p["on"] for p in bench) else "substitutes",
                        names))
    return "".join(parts)


STAT_ROWS = [
    ("Shots", "totalShots", "high"),
    ("Shots on target", "shotsOnTarget", "high"),
    ("Possession", "possessionPct", "high"),
    ("Passes", "totalPasses", "high"),
    ("Pass accuracy", "passAccuracy", "high"),
    ("Fouls", "foulsCommitted", "low"),
    ("Yellow cards", "yellowCards", "low"),
    ("Red cards", "redCards", "low"),
    ("Offsides", "offsides", "low"),
    ("Corners", "wonCorners", "high"),
    ("Saves", "saves", "high"),
    ("Tackles", "totalTackles", "high"),
    ("Interceptions", "interceptions", "high"),
    ("Clearances", "totalClearance", "high"),
]
PERCENT_STATS = {"possessionPct", "passAccuracy"}


def stat_value(stats, key):
    if key == "passAccuracy":
        try:
            accurate = float(stats.get("accuratePasses") or 0)
            total = float(stats.get("totalPasses") or 0)
        except (TypeError, ValueError):
            return None
        return accurate * 100.0 / total if total else None
    try:
        return float(stats.get(key))
    except (TypeError, ValueError):
        return None


def fmt_stat(value, key):
    if value is None:
        return "&ndash;"
    if key in PERCENT_STATS:
        return "%d%%" % round(value)
    if float(value).is_integer():
        return str(int(value))
    return "%.1f" % value


def render_team_stats(last):
    stats = last.get("team_stats") or {}
    home, away = stats.get("home") or {}, stats.get("away") or {}
    if not home and not away:
        return '<div class="empty">Match stats unavailable.</div>'

    rows = []
    for label, key, better in STAT_ROWS:
        a, b = stat_value(home, key), stat_value(away, key)
        if a is None and b is None:
            continue
        lead_h = lead_a = ""
        if a is not None and b is not None and a != b:
            home_wins = (a > b) if better == "high" else (a < b)
            lead_h, lead_a = (" lead", "") if home_wins else ("", " lead")
        rows.append(
            '<div class="srow"><span class="sv h%s">%s</span>'
            '<span class="sl">%s</span>'
            '<span class="sv a%s">%s</span></div>'
            % (lead_h, fmt_stat(a, key), esc(label), lead_a, fmt_stat(b, key)))

    crest = lambda side: ('<img src="%s" alt="">' % esc(last[side]["crest"])) if last[side]["crest"] else ""
    return ('<div class="stat-head">%s<span>Team stats</span>%s</div>%s'
            % (crest("home"), crest("away"), "".join(rows)))


def render_lineup_dialog(cfg, last):
    """Returns (button, dialog). Empty strings when ESPN published no lineups."""
    lineups = last.get("lineups") or {}
    if not lineups and not (last.get("team_stats") or {}):
        return "", ""

    did = "lu-" + cfg["key"]
    button = ('<button class="lu-btn" type="button" data-lu="%s">'
              '<span>Lineups &amp; match stats</span><i class="caret">&#9662;</i></button>') % did

    crest = lambda side: ('<img src="%s" alt="">' % esc(last[side]["crest"])) if last[side]["crest"] else ""
    head = (
        '<div class="lu-head">'
        '<div class="lu-score">%s<b>%s</b><span>&ndash;</span><b>%s</b>%s</div>'
        '<div class="lu-meta">%s &middot; %s</div>'
        '<button class="lu-x" type="button" data-close="%s" title="Close">&times;</button>'
        '</div>'
    ) % (crest("home"), esc(last["home"]["score"]), esc(last["away"]["score"]), crest("away"),
         esc(last["competition"]), esc(last["venue"] or ""), did)

    dialog = (
        '<dialog class="lu" id="%s">%s'
        '<div class="lu-body"><div class="lu-left">%s%s</div>'
        '<div class="lu-right">%s</div></div></dialog>'
    ) % (did, head, render_pitch(lineups), render_bench(lineups), render_team_stats(last))
    return button, dialog


# One-leg cups: losing really does end your run that evening.
SINGLE_LEG_CUPS = {
    "eng.league_cup", "eng.fa", "eng.charity",
    "esp.copa_del_rey", "esp.super_cup",
    "uefa.super_cup", "fifa.cwc",
}

RIVALRIES = {
    frozenset(("86", "83")): "El Cl\u00e1sico",
    frozenset(("86", "1068")): "Madrid Derby",
    frozenset(("360", "382")): "Manchester Derby",
    frozenset(("360", "364")): "North West Derby",
    frozenset(("360", "359")): "United vs Arsenal",
    frozenset(("360", "357")): "Roses Rivalry",
    frozenset(("83", "88")): "Derbi Barcelon\u00ed",
}


def _rank(table, team_id):
    row = next((r for r in table or [] if r["id"] == team_id), None)
    if not row:
        return None
    try:
        return int(row["rank"])
    except (TypeError, ValueError):
        return None


def match_stakes(cfg, nxt, table):
    """What is riding on the next match, worked out only from what we can check:
    the competition's own knockout flag, the fixture list, and the league table."""
    if not nxt:
        return None
    evil = cfg.get("villain")
    us = cfg["espn_id"]
    opponent = nxt["away"]["id"] if nxt["home"]["id"] == us else nxt["home"]["id"]
    comp = nxt.get("comp_short") or nxt["competition"]
    round_name = (nxt.get("round") or "").lower()

    same_league = nxt.get("comp_slug") == cfg["league"]
    mine = _rank(table, us) if same_league else None
    theirs = _rank(table, opponent) if same_league else None
    places = ("%s vs %s. " % (ordinal(mine), ordinal(theirs))) if mine and theirs else ""

    # 1. Knockout football - the competition itself says so.
    group_stage = any(k in round_name for k in ("league phase", "group", "round robin"))
    if nxt.get("is_tournament") and not group_stage:
        if nxt.get("comp_slug") in SINGLE_LEG_CUPS:
            if evil:
                return ("KNOCK THEM OUT", "One leg - lose it and they are out of the %s." % comp, "good")
            return ("DO OR DIE", "One leg, no replay - lose and you are out of the %s." % comp, "danger")
        return ("KNOCKOUT TIE", "Knockout rounds of the %s." % comp, "hot")

    # 2. A fixture that needs no explaining.
    derby = RIVALRIES.get(frozenset((us, opponent)))
    if derby:
        if frozenset((us, opponent)) == frozenset(("86", "83")):
            return ("EL CLÁSICO",
                    "Put them back in their place." if evil else "Everything else can wait.",
                    "clasico")
        tail = "Anyone but them." if evil else "Form goes out of the window."
        return (derby.upper(), places + tail, "hot")

    # 3. Both near the top of the same table.
    if mine and theirs:
        if mine <= 6 and theirs <= 6:
            return ("SIX-POINTER", places + "Three points swing the title race.", "hot")
        if abs(mine - theirs) <= 2:
            return ("FOUR-POINT SWING", places + "Neighbours in the table.", "hot")

    return None


def render_last(cfg, last):
    evil = cfg.get("villain")
    title = "Last match"

    # A club that won gets its anthem on this card, played by hovering it.
    meter = audio = ""
    won = bool(last) and last.get("result") == "W"
    if won and cfg.get("anthem") and find_anthem(cfg["anthem"]):
        meter = ('<span class="eq" title="Hover this card to play the %s">'
                 '<i></i><i></i><i></i><i></i></span>') % esc(cfg.get("anthem_label") or "anthem")
        audio = ('<audio class="anthem-audio" preload="none" src="/anthem/%s"></audio>'
                 % esc(cfg["anthem"]))

    shell = ('<section class="card last-card%s"%s><div class="card-h">%s%s</div>'
             '<div class="card-b">%%s</div>%s</section>') % (
        " evil" if evil else "", ' data-anthem="1"' if audio else "", title, meter, audio)

    if not last:
        return shell % '<div class="empty">No completed match found.</div>'

    badge = (VILLAIN_CHIP if evil else RESULT_CHIP).get(last["result"], ("", "RESULT UNKNOWN"))

    sides = []
    for which in ("home", "away"):
        t = last[which]
        us = " us" if which == last["we_are"] else ""
        crest = '<img src="%s" alt="" loading="lazy">' % esc(t["crest"]) if t["crest"] else ""
        sides.append('<div class="sq%s">%s<div class="nm">%s</div>%s</div>'
                     % (us, crest, esc(t["name"]), booking_list(last["cards"], which)))

    scores = '<div class="nums"><span>%s</span><span class="dash">-</span><span>%s</span></div>' % (
        esc(last["home"]["score"]), esc(last["away"]["score"]))
    pens = '<div class="pens">%s</div>' % esc(last["shootout"]) if last["shootout"] else ""

    if last["goals"]:
        timeline = '<div class="tl-h">Goals</div><div class="tl">%s</div>' % "".join(
            render_goal_row(g) for g in last["goals"])
    else:
        timeline = '<div class="tl-h">Goals</div><div class="empty">Goalless.</div>'

    lu_button, lu_dialog = render_lineup_dialog(cfg, last)
    body = (
        '<div class="chips"><div class="chips-l">%s'
        '<span class="chip %s">%s</span></div>%s</div>'
        '<div class="score">%s<div>%s</div>%s</div>%s%s%s%s'
    ) % (comp_chip(last), badge[0], badge[1],
         venue_line(last["venue"], last["city"], when_line(to_bg(last["date_utc"]))),
         sides[0], scores, sides[1], pens, timeline, lu_button, lu_dialog)
    return shell % body


# Odd corners of football history, sorted by competition. The hint in the
# next-match header cycles through whichever pool matches the fixture.
FACTS = {
    "esp.1": [
        "Athletic Club have only ever signed Basque players, and have still never been "
        "relegated. Their stadium is named after a saint who legend says was thrown to the "
        "lions, and the lions refused to eat him. Hence the nickname: The Lions.",
        "Real Madrid spent part of the 1930s as plain Madrid FC. The Second Republic had "
        "abolished the monarchy, so the club dropped the Royal and took the crown off its "
        "badge. Both went back on after the Civil War.",
        "There is a chapel inside Camp Nou, tucked behind the players' tunnel, so anyone "
        "heading out to face 90,000 people can stop for a quick word first.",
        "Rayo Vallecano's ground only has three sides. A block of flats stands where the "
        "fourth should be, and for years residents watched the football from their balconies.",
        "Real Oviedo were days from folding in 2012 until fans in more than 60 countries "
        "bought shares online. A dying Spanish club was rescued by strangers on the internet, "
        "and then by Carlos Slim, at the time roughly the richest man alive.",
        "The 1943 cup semi-final between Real Madrid and Barcelona finished 11-1. Barcelona's "
        "players said afterwards that police had dropped by their dressing room beforehand to "
        "remind them who was in charge.",
        "A Sevilla derby in 2007 was abandoned after a bottle thrown from the stands knocked "
        "the Sevilla manager unconscious on his own touchline.",
        "Real Madrid's fans are nicknamed the Merengues. The most successful club in European "
        "history is named after a dessert, because of the white shirts.",
    ],
    "eng.1": [
        "In 1996 Manchester United went in 3-0 down at half-time and blamed their grey kit, "
        "saying they could not pick each other out against the crowd. They changed shirts at "
        "the break, lost anyway, and never wore grey again.",
        "Southampton once signed a player because somebody rang the manager pretending to be "
        "George Weah and recommended his cousin. Ali Dia came on as a substitute against Leeds "
        "in 1996, was so bad he was himself substituted, and never played in the league again.",
        "Vinnie Jones was booked three seconds into a match in 1992, before most of the crowd "
        "had finished sitting down.",
        "Peter Schmeichel is the only goalkeeper to score a Premier League goal from open "
        "play, and he did it for Aston Villa rather than for Manchester United.",
        "Chelsea named the first starting eleven in Premier League history without a single "
        "English player in it, back in 1999.",
        "In 2005 a shot crossed the Manchester United line by about a metre and was not given. "
        "Goal-line technology took another eight years to arrive.",
        "Leicester City won the title in 2016 at odds of 5000-1. Bookmakers had been offering "
        "shorter odds on Elvis being found alive.",
        "Robbie Fowler once celebrated a goal by getting down and pretending to snort the "
        "penalty-area line, purely to wind up the away end. It cost him a fine and a ban.",
    ],
}
FACTS.update({
    "uefa.champions": [
        "The whole competition exists because of a newspaper argument. An English paper called "
        "Wolves champions of the world for winning a friendly, and a French journalist got so "
        "irritated he organised a proper tournament to settle it.",
        "The anthem is a rewrite of Handel's Zadok the Priest, sung in English, German and "
        "French because nobody could agree on one language. Large parts of it are just lists "
        "of words.",
        "Real Madrid were allowed to keep the original trophy after winning six. The cup teams "
        "lift today is a 1967 replacement, made by a Swiss jeweller who reportedly spent "
        "around 340 hours on it.",
        "Nottingham Forest have won the European Cup twice and the English league once. They "
        "have been champions of Europe more often than champions of their own country.",
        "In the 1986 final, Steaua Bucharest's goalkeeper saved all four Barcelona penalties "
        "in the shootout. Within months a rare condition in his arms had ended his career.",
        "The trophy has no official nickname, so everybody simply calls it Big Ears.",
        "At half-time in the 2005 final some Liverpool supporters had already left the ground "
        "at 3-0 down. They missed the six minutes that decided it.",
    ],
    "eng.league_cup": [
        "The trophy has had more names than most players have had clubs: the Milk Cup, "
        "Littlewoods, Rumbelows, Coca-Cola, Worthington, Carling, Capital One, and now a Thai "
        "energy drink.",
        "For four years in the 1980s, English football's second knockout trophy was sponsored "
        "by the Milk Marketing Board. Grown men lifted the Milk Cup on national television.",
        "Swindon Town won it in 1969 from the third tier, beating Arsenal on a Wembley pitch "
        "churned into mud by a horse show the week before.",
        "Winning it puts a club into Europe, so it is entirely possible to qualify for a "
        "continental competition while finishing nowhere near the top of the league.",
    ],
    "eng.fa": [
        "The original trophy was stolen from a shop window in Birmingham in 1895 while Aston "
        "Villa held it, and was never seen again. Sixty years later a man claimed he had "
        "melted it down into counterfeit coins.",
        "Cardiff City are the only club to have taken the FA Cup out of England, in 1927.",
    ],
    "esp.copa_del_rey": [
        "The same competition has been the King's Cup, the President of the Republic's Cup and "
        "the Generalisimo's Cup. The trophy stayed put; Spain's rulers kept changing.",
    ],
    "uefa.europa": [
        "Its ancestor was a competition for cities rather than clubs. A combined London side, "
        "picked from players at several different London teams, once reached the final.",
        "The trophy weighs around 15 kilograms and has no handles, which makes lifting it "
        "above your head a genuine athletic event.",
    ],
    "default": [
        "Offside once required three defenders rather than two. When it changed in 1925 the "
        "goals came so thickly that teams had to invent an entirely new defensive position to "
        "cope.",
        "Yellow and red cards were dreamt up by an English referee sitting at a traffic light, "
        "after a World Cup match in which players had no idea they had been booked.",
        "Goalkeepers were allowed to pick up back-passes until 1992. Teams had worked out that "
        "the safest thing to do with a lead was to pass it backwards for minutes at a time.",
        "The crossbar only became compulsory in 1875. Before that the top of the goal was a "
        "piece of tape, and arguments about whether the ball had gone over it were exactly as "
        "common as you would expect.",
    ],
})


def render_hint(slug):
    """The ? in the next-match header. Hovering cycles the competition's facts."""
    pool = (FACTS.get(slug) or []) + FACTS["default"]
    if not pool:
        return ""
    items = "".join("<i>%s</i>" % esc(fact) for fact in pool)
    return ('<span class="hint" tabindex="0" role="button" aria-label="Did you know?">?'
            '<span class="bubble"><b>Did you know?</b><span class="fact"></span></span>'
            '<span class="pool">%s</span></span>') % items


def render_clasico(nxt, call):
    crest = lambda side: ('<img src="%s" alt="">' % esc(nxt[side]["crest"])) if nxt[side]["crest"] else ""
    return (
        '<div class="stakes clasico">%s'
        '<span class="body"><b>%s</b><span class="tag">%s</span></span>%s</div>'
    ) % (crest("home"), esc(call[0]), esc(call[1]), crest("away"))


def render_next(cfg, nxt, table=None, reserve=None):
    evil = cfg.get("villain")
    reserve = reserve or {}
    title = "Next crime scene" if evil else "Next match"
    shell = ('<section class="card next-card%s"><div class="card-h">%s%s</div>'
             '<div class="card-b">%%s</div></section>') % (
        " evil" if evil else "", title,
        render_hint((nxt or {}).get("comp_slug")))

    if not nxt:
        return shell % '<div class="empty">No fixture scheduled yet.</div>'

    at_home = nxt["home"]["id"] == cfg["espn_id"]
    bg = to_bg(nxt["date_utc"])

    sides = []
    for which in ("home", "away"):
        t = nxt[which]
        us = " us" if t["id"] == cfg["espn_id"] else ""
        crest = '<img src="%s" alt="" loading="lazy">' % esc(t["crest"]) if t["crest"] else ""
        sides.append('<div class="sq%s">%s<div class="nm">%s</div></div>' % (us, crest, esc(t["name"])))

    if bg:
        label, today = day_label(bg)
        day = ('<div class="d today"><span class="dot"></span>%s</div>' % esc(label)
               if today else '<div class="d">%s</div>' % esc(label))
        kick = ('<div class="kick">%s<div class="t">%s</div>'
                '<div class="z">%s &middot; Bulgarian time</div></div>') % (
            day, "%02d:%02d" % (bg.hour, bg.minute), esc(bg.tzname()))
        countdown = '<div class="cd" data-kick="%s">Counting down&hellip;</div>' % esc(
            nxt["date_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"))
        if not nxt.get("time_valid", True):
            countdown = '<div class="cd">Kick-off time not confirmed yet.</div>'
    else:
        kick = '<div class="kick"><div class="d">Date to be confirmed</div></div>'
        countdown = ""

    city = nxt["city"] + (" &middot; neutral venue" if nxt.get("neutral") else "")
    # the round only earns a chip when it says more than the competition name
    round_chip = ('<span class="chip">%s</span>' % esc(nxt["round"])
                  if nxt.get("round") and nxt.get("is_tournament") else "")

    call = match_stakes(cfg, nxt, table or [])
    if call and call[2] == "clasico":
        stakes = render_clasico(nxt, call)
    elif call:
        stakes = ('<div class="stakes %s"><b>%s</b><span>%s</span></div>'
                  % (call[2], esc(call[0]), esc(call[1])))
    else:
        stakes = '<div class="stakes hold"></div>' if reserve.get("stakes") else ""

    body = (
        '<div class="chips"><div class="chips-l">%s%s'
        '<span class="chip %s">%s</span></div>%s</div>%s'
        '<div class="score">%s<div class="nums"><span class="vs">vs</span></div>%s</div>%s%s'
    ) % (comp_chip(nxt), round_chip, "home" if at_home else "away",
         "HOME" if at_home else "AWAY", venue_line(nxt["venue"], city), stakes,
         sides[0], sides[1], kick, countdown)
    return shell % body


def render_table(cfg, rows):
    if not rows:
        return ('<section class="card"><div class="card-h">%s table</div>'
                '<div class="card-b"><div class="empty">Table unavailable.</div></div></section>'
                ) % esc(cfg["league_name"])

    total = len(rows)
    body = []
    for r in rows:
        if r["id"] == cfg["espn_id"]:
            flag = "me"
        elif r["id"] in VILLAIN_IDS:
            flag = "foe"
        else:
            flag = ""
        crest = '<img src="%s" alt="" loading="lazy">' % esc(r["crest"]) if r["crest"] else ""
        body.append(
            '<tr class="%s"><td class="pos" style="--zone:%s">%s</td>'
            '<td class="club-col"><div class="tm">%s<span>%s</span></div></td>'
            '<td>%s</td><td class="mut">%s</td><td class="mut">%s</td><td class="mut">%s</td>'
            '<td class="mut">%s</td><td class="mut">%s</td><td>%s</td><td class="pts">%s</td></tr>'
            % (flag, zone_colour(r["rank"], total), esc(r["rank"]),
               crest, esc(r["short"] or r["name"]),
               esc(r["gp"]), esc(r["w"]), esc(r["d"]), esc(r["l"]),
               esc(r["gf"]), esc(r["ga"]), esc(r["gd"]), esc(r["pts"])))

    foe_key = ""
    if any(r["id"] in VILLAIN_IDS for r in rows):
        foe_key = '<span><i style="background:#fde2e4"></i>The enemy</span>'

    return (
        '<section class="card"><div class="card-h">%s table</div><div class="card-b">'
        '<div class="tbl-scroll"><table><thead><tr>'
        '<th>#</th><th class="club-col">Club</th><th>PL</th><th>W</th><th>D</th><th>L</th>'
        '<th>GF</th><th>GA</th><th>GD</th><th>Pts</th></tr></thead><tbody>%s</tbody></table></div>'
        '<div class="legend">'
        '<span><i style="background:#bae6fd"></i>%s</span>%s'
        '<span><i style="background:#10b981"></i>Champions League</span>'
        '<span><i style="background:#3b82f6"></i>Europa League</span>'
        '<span><i style="background:#8b5cf6"></i>Conference League</span>'
        '<span><i style="background:#ef4444"></i>Relegation</span>'
        '</div></div></section>'
    ) % (esc(cfg["league_name"]), "".join(body), esc(cfg["name"]), foe_key)


MOCK_BY_RESULT = {
    "W": "They won their last one. Nobody is perfect.",
    "D": "A draw. Even their good days are boring.",
    "L": "They lost their last match. Beautiful.",
}


COMPARE = [("Points", "pts"), ("Wins", "w"), ("Goals scored", "gf"), ("Goals conceded", "ga")]


def compare_bars(us, them):
    """Head-to-head bars: Real Madrid on the left, the enemy on the right."""
    rows = []
    for label, key in COMPARE:
        try:
            a, b = int(us[key]), int(them[key])
        except (TypeError, ValueError):
            continue
        total = a + b
        left = 50.0 if total == 0 else a * 100.0 / total
        rows.append(
            '<div><div class="cmp-lbl">%s</div><div class="cmp-row">'
            '<span class="cmp-v us">%d</span>'
            '<div class="cmp-bars"><span class="l" style="flex:0 0 %.1f%%"></span>'
            '<span class="r" style="flex:0 0 %.1f%%"></span></div>'
            '<span class="cmp-v foe">%d</span></div></div>'
            % (esc(label), a, left, 100.0 - left, b))
    return '<div class="cmp">%s</div>' % "".join(rows) if rows else ""


def render_rivalry(cfg, payload):
    """Barcelona shares LaLiga with Real Madrid, so show the gap, not a second table."""
    rows = payload["table"]
    us = next((r for r in rows if r["id"] == cfg.get("rival_of")), None)
    them = next((r for r in rows if r["id"] == cfg["espn_id"]), None)

    if not us or not them:
        return ('<section class="card evil"><div class="card-h">The gap</div>'
                '<div class="card-b"><div class="empty">Standings unavailable.</div></div></section>')

    try:
        ur, tr = int(us["rank"]), int(them["rank"])
        gap = abs(int(us["pts"]) - int(them["pts"]))
    except (TypeError, ValueError):
        ur, tr, gap = 0, 0, 0

    if ur < tr:
        lead = "Order restored"
        text = ("Real Madrid are %s, Barcelona %s. %s"
                % (ordinal(ur), ordinal(tr),
                   "Level on points, but ahead where it counts." if gap == 0
                   else "That is %d point%s of daylight." % (gap, "" if gap == 1 else "s")))
    else:
        lead = "Temporary setback"
        text = ("Barcelona are %s, Real Madrid %s%s. It is a long season."
                % (ordinal(tr), ordinal(ur),
                   "" if gap == 0 else " and %d point%s back" % (gap, "" if gap == 1 else "s")))

    rows_html = []
    for row, tone in ((us, "me"), (them, "foe")):
        crest = '<img src="%s" alt="" loading="lazy">' % esc(row["crest"]) if row["crest"] else ""
        rows_html.append(
            '<tr class="%s"><td class="pos">%s</td>'
            '<td class="club-col"><div class="tm">%s<span>%s</span></div></td>'
            '<td>%s</td><td class="mut">%s</td><td class="mut">%s</td><td class="mut">%s</td>'
            '<td class="mut">%s</td><td class="mut">%s</td><td>%s</td><td class="pts">%s</td></tr>'
            % (tone, esc(row["rank"]), crest, esc(row["short"] or row["name"]),
               esc(row["gp"]), esc(row["w"]), esc(row["d"]), esc(row["l"]),
               esc(row["gf"]), esc(row["ga"]), esc(row["gd"]), esc(row["pts"])))

    mock = MOCK_BY_RESULT.get((payload.get("last") or {}).get("result"), "")

    return (
        '<section class="card evil"><div class="card-h">Head to head in the table</div><div class="card-b">'
        '<div class="verdict"><span class="lead">%s</span>%s</div>'
        '<div class="tbl-scroll"><table><thead><tr>'
        '<th>#</th><th class="club-col">Club</th><th>PL</th><th>W</th><th>D</th><th>L</th>'
        '<th>GF</th><th>GA</th><th>GD</th><th>Pts</th></tr></thead><tbody>%s</tbody></table></div>'
        '%s<div class="mock">%s</div>'
        '</div></section>'
    ) % (esc(lead), esc(text), "".join(rows_html), compare_bars(us, them), esc(mock))


def render_lane(payload, column, reserve):
    """One club as four grid slots, so every club shares every row."""
    cfg = payload["cfg"]
    evil = cfg.get("villain")
    result = (payload.get("last") or {}).get("result")

    # Your clubs celebrate a win. The enemy lane celebrates a defeat.
    celebrate = (result == "L") if evil else (result == "W")

    crest = '<div class="crest-wrap"><img src="%s" alt="">%s</div>' % (
        esc(cfg["crest"]), '<span class="mask">&#128520;</span>' if evil else "")

    badge = ""

    banner = (
        '<div class="club%s%s" style="--accent:%s;--tint:rgba(%s,.22);--ring:%s">%s'
        '<div class="club-txt"><div class="club-name">%s</div><div class="club-sub">%s</div>%s</div>'
        '%s</div>'
    ) % (" villain" if evil else "", " winner" if celebrate else "",
         cfg["accent"], cfg["glow"], cfg["ring"], crest,
         esc(cfg["name"]), esc(payload.get("position") or cfg["league_name"]), "", badge)

    if payload["error"]:
        banner += ('<div class="err"><b>Could not load %s.</b><br>%s</div>'
                   % (esc(cfg["name"]), esc(payload["error"])))

    fourth = render_rivalry(cfg, payload) if evil else render_table(cfg, payload["table"])
    slots = [banner, render_last(cfg, payload["last"]), render_next(cfg, payload["next"], payload["table"], reserve), fourth]
    cells = "".join('<div class="slot">%s</div>' % slot for slot in slots)
    return '<div class="lane" style="--col:%d">%s</div>' % (column, cells)


HINT_JS = """
(function(){
  document.querySelectorAll('.hint').forEach(function(hint){
    var pool = [].slice.call(hint.querySelectorAll('.pool i')).map(function(n){ return n.textContent; });
    if (!pool.length) { return; }
    var out = hint.querySelector('.fact');
    var i = Math.floor(Math.random() * pool.length);
    function show(){ out.textContent = pool[i]; i = (i + 1) % pool.length; }
    show();                                   // ready before the first hover
    hint.addEventListener('mouseenter', show); // a new one every time
    hint.addEventListener('focus', show);
  });
})();
"""


LINEUP_JS = """
(function(){
  document.querySelectorAll('.lu-btn').forEach(function(btn){
    btn.addEventListener('click', function(){
      var box = document.getElementById(btn.getAttribute('data-lu'));
      if (box && box.showModal) { box.showModal(); }
    });
  });
  document.querySelectorAll('.lu-x').forEach(function(x){
    x.addEventListener('click', function(){
      var box = document.getElementById(x.getAttribute('data-close'));
      if (box) { box.close(); }
    });
  });
  document.querySelectorAll('dialog.lu').forEach(function(box){
    box.addEventListener('click', function(e){ if (e.target === box) { box.close(); } });
  });
})();
"""


COUNTDOWN_JS = """
(function(){
  function pad(n){return (n<10?'0':'')+n}
  function tick(){
    var now = Date.now();
    document.querySelectorAll('.cd[data-kick]').forEach(function(el){
      var ko = Date.parse(el.getAttribute('data-kick'));
      if (isNaN(ko)) { return; }
      var diff = ko - now;
      if (diff <= 0){
        var mins = Math.floor(-diff/60000);
        el.classList.add('live');
        el.innerHTML = mins < 150
          ? 'Kicked off <b>' + mins + " min</b> ago"
          : 'This match has already been played.';
        return;
      }
      var s = Math.floor(diff/1000);
      var d = Math.floor(s/86400), h = Math.floor(s%86400/3600),
          m = Math.floor(s%3600/60), sec = s%60;
      var out = d > 0 ? '<b>'+d+'d '+h+'h '+pad(m)+'m</b>'
                      : '<b>'+pad(h)+':'+pad(m)+':'+pad(sec)+'</b>';
      el.innerHTML = 'Kick-off in ' + out;
    });
  }
  tick(); setInterval(tick, 1000);
})();
"""


ANTHEM_JS = """
(function(){
  var FADE = 430;

  function fade(audio, target, done){
    clearInterval(audio._fade);
    var from = audio.volume, started = performance.now();
    audio._fade = setInterval(function(){
      var k = Math.min(1, (performance.now() - started) / FADE);
      audio.volume = Math.max(0, Math.min(1, from + (target - from) * k));
      if (k >= 1) { clearInterval(audio._fade); if (done) { done(); } }
    }, 25);
  }

  var cards = [].slice.call(document.querySelectorAll('.card[data-anthem]'));

  cards.forEach(function(card){
    var audio = card.querySelector('.anthem-audio');
    if (!audio) { return; }

    audio.addEventListener('error', function(){
      var eq = card.querySelector('.eq');
      if (eq && eq.parentNode) { eq.parentNode.removeChild(eq); }
      card.removeAttribute('data-anthem');
    });

    card.addEventListener('mouseenter', function(){
      cards.forEach(function(other){
        if (other === card) { return; }
        var track = other.querySelector('.anthem-audio');
        if (track && !track.paused) {
          fade(track, 0, function(){ track.pause(); other.classList.remove('playing'); });
        }
      });
      if (audio.paused) { audio.volume = 0; }   // fade in from silence on a fresh start
      var started = audio.play();
      if (started && started.catch) {
        started.catch(function(){
          document.addEventListener('click', function once(){
            document.removeEventListener('click', once);
            if (card.matches(':hover')) { audio.play().catch(function(){}); }
          });
        });
      }
      card.classList.add('playing');
      fade(audio, 1);
    });

    card.addEventListener('mouseleave', function(){
      fade(audio, 0, function(){ audio.pause(); card.classList.remove('playing'); });
    });
  });
})();
"""


def render_page(data, generated_at, elapsed):
    reserve = layout_reserves(data)
    lanes = "".join(render_lane(p, i + 1, reserve) for i, p in enumerate(data))
    stamp = "%s %s" % (fmt_date(generated_at), fmt_time(generated_at))
    crests = "".join('<img src="%s" alt="">' % esc(t["crest"]) for t in TEAMS)

    friends = [t["name"] for t in TEAMS if not t.get("villain")]
    foes = [t["name"] for t in TEAMS if t.get("villain")]
    heading = '<span class="sep">&amp;</span>'.join("<span>%s</span>" % esc(n) for n in friends)
    if foes:
        heading += '<span class="vs-evil">vs &#128520; %s</span>' % esc(", ".join(foes))

    return """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Real Madrid &amp; Manchester United</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&amp;display=swap">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='14' font-size='14'>&#9917;</text></svg>">
<style>%s</style>
</head><body>
<header class="top"><div class="wrap top-in">
  <h1>%s%s</h1>
  <div class="tzline">All times shown in <b>Bulgarian time</b> (Europe/Sofia)</div>
  <div class="spacer"></div>
  <span class="stamp">Updated %s &middot; %.1fs</span>
  <a class="btn" href="/?refresh=1">&#8635; Refresh</a>
</div></header>
<div class="wrap"><div class="lanes" style="--cols:%d;--book-rows:%d;--stakes-h:%dpx">%s</div>
<footer>Live data from the public ESPN football API &middot; kick-off times converted from UTC to Europe/Sofia (EET/EEST).<br>
Hala Madrid, Glory Glory Man United, and may Barcelona keep dropping points.</footer>
</div>
<script>%s</script>
<script>%s</script>
<script>%s</script>
<script>%s</script>
</body></html>""" % (CSS, crests, heading, esc(stamp), elapsed, len(data),
                     reserve["books"], reserve["stakes_h"], lanes, COUNTDOWN_JS, ANTHEM_JS, LINEUP_JS, HINT_JS)


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------

_cache = {"html": None, "at": 0.0}
_lock = threading.Lock()


def build_page(force=False):
    with _lock:
        if not force and _cache["html"] and (time.time() - _cache["at"]) < CACHE_SECONDS:
            return _cache["html"]
        started = time.time()
        data = load_all()
        elapsed = time.time() - started
        html_page = render_page(data, datetime.now(timezone.utc).astimezone(BG), elapsed)
        _cache.update(html=html_page, at=time.time())
        return html_page


def anthems_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "anthems")


def find_anthem(slug):
    """anthems/<slug>.mp3 if it exists, else any audio file whose name mentions
    the club - so a download keeps whatever name it arrived with."""
    slug = "".join(ch for ch in slug if ch.isalnum() or ch in "_-")
    folder = anthems_dir()
    if not slug or not os.path.isdir(folder):
        return None
    files = sorted(os.listdir(folder))
    for ext in AUDIO_TYPES:
        for name in files:
            if name.lower() == slug + ext:
                return os.path.join(folder, name)
    keys = ANTHEM_KEYS.get(slug, (slug,))
    for name in files:
        stem, ext = os.path.splitext(name)
        if ext.lower() in AUDIO_TYPES and any(k in stem.lower() for k in keys):
            return os.path.join(folder, name)
    return None


AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
               ".oga": "audio/ogg", ".wav": "audio/wav", ".opus": "audio/opus"}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "FootballDashboard/1.0"

    def serve_anthem(self, name):
        path = find_anthem(name)
        if not path:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        with open(path, "rb") as fh:
            blob = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", AUDIO_TYPES[os.path.splitext(path)[1].lower()])
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/anthem/"):
            self.serve_anthem(parsed.path[len("/anthem/"):])
            return
        if parsed.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return
        force = "refresh" in urllib.parse.parse_qs(parsed.query)
        try:
            body = build_page(force=force).encode("utf-8")
        except Exception as exc:
            body = ("<h1>Something went wrong</h1><pre>%s</pre>" % esc(exc)).encode("utf-8")
            self.send_response(500)
        else:
            self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200 if urllib.parse.urlparse(self.path).path in ("/", "/index.html") else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def log_message(self, *args):
        pass  # keep the console clean


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def open_browser(url):
    """Chrome and Edge refuse to autoplay sound without a user gesture. A fresh
    profile plus --autoplay-policy lifts that, so the anthem starts on its own.
    Falls back to the default browser (with a Play button) if neither is here."""
    local = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    candidates = [os.path.join(local, "Google", "Chrome", "Application", "chrome.exe")] + BROWSERS
    profile = os.path.join(local, "football-dashboard", "browser-profile")
    for exe in candidates:
        if not os.path.isfile(exe):
            continue
        try:
            subprocess.Popen([
                exe,
                "--app=" + url,
                "--autoplay-policy=no-user-gesture-required",
                "--user-data-dir=" + profile,
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1600,1000",
            ], close_fds=True)
            return os.path.basename(exe)
        except OSError:
            continue
    webbrowser.open(url)
    return None


def free_port(preferred=8770):
    for port in range(preferred, preferred + 25):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def main():
    argv = sys.argv[1:]

    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    if "--print" in argv:  # dump the HTML to stdout, no server
        sys.stdout.reconfigure(encoding="utf-8")
        print(build_page(force=True))
        return 0

    print("Fetching Real Madrid and Manchester United data ...", flush=True)
    try:
        build_page(force=True)
    except Exception as exc:
        print("  ! could not reach ESPN: %s" % exc, flush=True)
        print("  the page will still open and show what it can.", flush=True)

    port = free_port()
    httpd = Server(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]

    print("")
    print("Dashboard ready at %s" % url, flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    if "--no-browser" not in argv:
        if "--default-browser" in argv:
            threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        else:
            threading.Timer(0.4, lambda: open_browser(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
