# Real Madrid & Manchester United dashboard

A single-file Python script that opens a browser dashboard with, for each club:

- **Last match** — competition and result on the left, stadium and date on the
  right, final score, and a goal-by-goal timeline with the minute, scorer and
  `OG` / `PEN` markers. Each side's bookings sit under that side's own crest.
  A **Lineups & match stats** button opens both starting XIs on a pitch, with
  kits, shirt numbers, goal/card/substitution markers, who came off the bench,
  and the full team-stats comparison beside it.
- **Next match** — competition badge, date, kick-off time, stadium, home/away and
  a live countdown, plus a banner saying what is riding on the game.
  A match played today shows **Today** in an amber badge with a pulsing dot
  instead of the full date, on both the last and next match.
- **League table** — with your club's row highlighted in light blue.

**Every kick-off time is shown in Bulgarian time (Europe/Sofia).** ESPN publishes
times in UTC, so a 21:00 kick-off in Madrid appears here as 22:00.

## The third lane

Barcelona is tracked as the **enemy**, not as a club you support: a dark banner,
an "Enemy watch" tag, inverted result badges (`ANNOYINGLY WON`, `LOST 🎉`) and
headings that read *Latest crime* and *Next crime scene*. In the LaLiga table
they are highlighted in red rather than your light blue.

They share LaLiga with Real Madrid, so their fourth panel skips the duplicate
table and shows a head-to-head instead: league positions, a verdict on the gap,
comparison bars for points, wins and goals, and a closing remark.

## Winner celebration

When Real Madrid or Manchester United win their last match, that club's banner
gets an animated shine sweep with a pulsing glow in the club's colour. The
Barcelona banner does the same thing when Barcelona **lose** — that is the good
outcome.

Animations are disabled automatically if your system asks for reduced motion.

## Anthems

When a club wins its last match, a small blue level meter appears at the right of
that card's **LAST MATCH** header. **Hover anywhere over the card** and the
club's anthem fades in and the bars start moving; move the pointer away and it
fades out and pauses, keeping its place. Hover again and it resumes from there.
Only one anthem plays at a time.

| Club | File |
| --- | --- |
| Real Madrid | `anthems/real_madrid.mp3` |
| Manchester United | `anthems/man_united.mp3` |

**You need to supply the audio** — put your own files in the `anthems/` folder.
Filenames are flexible: any audio file whose name contains **madrid** or **hala**
is used for Real Madrid, and anything containing **united** or **glory** for
Manchester United. So a file called
`Hala Madrid...y nada mas (feat. RedOne).mp3` is picked up as-is.

To force one exact file when several could match, name it `real_madrid.mp3` or
`man_united.mp3` — an exact name always beats a keyword match. `.m4a`, `.ogg`,
`.wav` and `.opus` work too. The meter only appears when a matching file exists.

Browsers normally block audio that starts without a click, so `main.py` launches
Chrome or Edge itself with `--autoplay-policy=no-user-gesture-required` in a
dedicated profile under `%LOCALAPPDATA%ootball-dashboard`. That is what lets
hover alone start the music. Run with `--default-browser` to use your normal
browser instead, where the meter still appears but the sound stays blocked.

## Two ways to use it

**Online**, as a bookmarkable link that updates itself — see **[DEPLOY.md](DEPLOY.md)**.
`python build.py` renders the whole dashboard to `docs/index.html`, which any
free static host will serve. Config is included for GitLab Pages
(`.gitlab-ci.yml`) and GitHub Pages (`.github/workflows/deploy.yml`).

**Locally**, as the app below.

## Running it locally

Double-click **`Open Dashboard.bat`**, or from a terminal:

```
python main.py
```

The browser opens automatically. Press `Ctrl+C` in the console to stop.

| Flag | Effect |
| --- | --- |
| `--no-browser` | Start the server without opening a browser |
| `--default-browser` | Use your normal browser instead of the autoplay-enabled window |
| `--print` | Print the rendered HTML to stdout and exit |
| `--help` | Show usage |

## Requirements

Python 3.9+ and an internet connection. **No pip packages and no API key** —
everything uses the standard library.

Bulgaria's EET/EEST switchover is computed from the EU daylight-saving rule
directly, so the script does not need the `tzdata` package that Windows lacks.

The page loads Plus Jakarta Sans from Google Fonts and falls back to Segoe UI if
that is unreachable.

## Where the data comes from

ESPN's public football API:

| Data | Endpoint |
| --- | --- |
| Next fixture (all competitions) | `site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{id}` |
| Played matches | `.../soccer/all/teams/{id}/schedule` |
| Goals, cards, final score | `.../soccer/all/summary?event={id}` |
| League table | `site.api.espn.com/apis/v2/sports/soccer/{league}/standings` |

Team ids are Real Madrid `86`, Manchester United `360` and Barcelona `83`;
leagues are `esp.1` and `eng.1`. The "next match" looks across *all*
competitions, so a Champions League or cup tie shows up ahead of the next league
game.

Results are cached for 60 seconds; the **Refresh** button forces a re-fetch.

## Tracking different clubs

Edit the `TEAMS` list near the top of `main.py`. Find a club's id in its ESPN
URL — `espn.com/soccer/club/_/id/86/real-madrid` → `86`. Set `"villain": True`
for anyone you want mocked rather than supported, and the layout adds a column
automatically.

## About the lineup view

Lineups come from ESPN's own match rosters: formation, starting XI, positions,
substitutions and per-player goals and cards. Players are drawn with ESPN's
per-match **kit images** — ESPN publishes no headshots for football, so there are
no face photos, and it publishes no player ratings either, so the numbered
ratings you see on sites like Sofascore are not available here.

Positions are placed from each player's position code (`LB`, `CD-L`, `AM-R` and
so on) rather than from the formation string, which keeps the shape honest even
when a side lines up asymmetrically.

## Row alignment

Cards in the same row reserve the same space for the blocks that not every club
has — bookings under a crest, and the stakes banner — so the goal lists, dates,
kick-off times and buttons all sit on the same line across the three columns.
The reserved heights are computed from the data each time the page renders, so
they grow and shrink with the fixtures rather than being hard-coded.

## Did you know?

The **?** in the next-match header, opposite the label, holds a pool of facts
about whichever competition the fixture belongs to — LaLiga, Premier League,
Champions League, Carabao Cup, FA Cup, Copa del Rey, Europa League, plus a
general football pool that tops up every competition. Hover it and you get one;
hover again and you get another, cycling through the whole pool.

The facts live in the `FACTS` dictionary in `main.py`, keyed by ESPN league slug.
Add your own to any pool, or add a new slug for a competition not covered yet.

## Competition badges and stakes

Each match shows its competition's own badge — LaLiga, Premier League, Champions
League, Carabao Cup and so on. ESPN's league logo file is named after the
league's `alternateId`, so the badge costs no extra request.

The banner on the next match is worked out only from things that can be checked,
never guessed:

| Badge | When |
| --- | --- |
| **DO OR DIE** | A single-leg cup round — FA Cup, Carabao Cup, Copa del Rey. Losing ends the run that night. |
| **KNOCKOUT TIE** | A knockout round of a two-legged competition, where one defeat is not final. |
| **MADRID DERBY**, **MANCHESTER DERBY**, … | The fixture is a known rivalry. |
| **EL CLÁSICO** | Real Madrid vs Barcelona. You will know it when you see it. |
| **SIX-POINTER** | Both clubs sit in the top six of the same league. |
| **FOUR-POINT SWING** | The opponent is within two places in the table. |
| *(nothing)* | An ordinary fixture — no invented drama. |

A competition's own `isTournament` flag decides what counts as knockout, and the
round name keeps group stages out of it: a Champions League *League Phase* game
gets no banner, while a *Round of 16* tie does.
