# Putting this online

**Live now:** <https://sushikriss.github.io/NextMatchesInfo/> — rebuilt from
live ESPN data every 15 minutes by `.github/workflows/deploy.yml`.

`build.py` turns the dashboard into one self-contained HTML file in `docs/`.
Any free static host will serve it. Nothing runs on your computer, and the link
works for anyone you send it to.

## Why it is pre-rendered

ESPN's edge sits behind a bot filter. A request carrying a browser fingerprint
gets **403 Access Denied**, while the same request from a script is answered
normally — and a web page cannot pretend to be a script, because browsers will
not let JavaScript change its own `User-Agent`.

So the page cannot call ESPN from your browser. It is rendered here instead and
published as finished HTML, which is why it appears instantly with no spinner.
Countdowns, the lineup viewer and the fact tooltips are client-side, so they
stay live however old the build is.

## Pick a host

| | Account | Card | Refreshes itself | Effort |
| --- | --- | --- | --- | --- |
| **A. Drag and drop** | no | no | no — you re-drop | seconds |
| **B. GitLab Pages** | yes | **yes** | every 4 hours | ~10 min |
| **C. GitHub Pages** | yes | no | every 15 min | ~5 min |

The page always shows how old it is next to the timestamp — *just now*,
*2 hours ago* — and turns amber past three hours, so a stale snapshot is never
mistaken for live data.

---

## Option A — Drag and drop, no account

Double-click **`Publish to the web.bat`**. It re-renders the page, then opens
your folder and [app.netlify.com/drop](https://app.netlify.com/drop). Drag the
**`docs`** folder onto the page and you get a public link in a few seconds.

No sign-up, no card, no repository, nothing tied to any account of yours.

To update it, run the same file again and drag `docs` across again. If you want
the link to stay the same and be yours permanently, Netlify will offer to let
you claim the site with a free account.

Other hosts that work the same way, if you prefer one of them: **static.app**,
**tiiny.host**, **yapp.page**, **neocities.org**. All take a folder containing
`index.html`, which is exactly what `docs` is.

## Option B — GitLab Pages

Two things to know first.

**GitLab wants a card on file before it will run CI.** Shared runners require
credit or debit verification — a zero-value authorisation, no charge. GitLab
Pages only deploys through a CI job, so there is no way around it.

**The free tier gives 400 compute minutes a month.** Each rebuild takes a minute
or two, so the schedule below runs every 4 hours (~180 runs a month). Every 15
minutes would need ~2,900 runs, far past the budget.

One thing GitLab does better than GitHub: **Pages works from a private project
on the free tier**, so your code can stay private while the site is public.

1. Create a **new blank project** — no README, no template. Private is fine.
2. Push:
   ```
   git remote add origin https://gitlab.com/YOUR-NAME/YOUR-PROJECT.git
   git push -u origin main
   ```
3. Complete card verification if GitLab prompts.
4. **Build → Pipeline schedules → New schedule**, interval `0 */4 * * *`,
   target branch `main`.
5. Your link is under **Deploy → Pages**, usually
   `https://YOUR-NAME.gitlab.io/YOUR-PROJECT/`.

Tuning the schedule against the 400-minute budget:

| Cron | Rebuilds/day | Runs/month | Fits? |
| --- | --- | --- | --- |
| `0 */6 * * *` | 4 | ~120 | comfortably |
| `0 */4 * * *` | 6 | ~180 | yes — the default |
| `0 */2 * * *` | 12 | ~360 | tight |
| `*/15 * * * *` | 96 | ~2,900 | no |

## Option C — GitHub Pages

The best economics of the three: on a **public** repo, Actions are free and
unlimited, so it rebuilds every 15 minutes, and there is no card check. The
workflow is already written at `.github/workflows/deploy.yml`.

1. Create an empty **public** repo — not on a work account.
2. `git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git`
   then `git push -u origin main`
3. **Settings → Pages → Build and deployment → Source → GitHub Actions**
4. Link: `https://YOUR-NAME.github.io/YOUR-REPO/`

On GitHub's free plan Pages only works from a **public** repo. Scheduled
workflows pause after 60 days of repository inactivity; re-enable from the
Actions tab.

---

## Updating it

```
python build.py
```

That re-renders `docs/index.html` from live ESPN data. On Option A, drag the
folder across again (or just run `Publish to the web.bat`). On B and C the
scheduled job does it for you, and a `git push` triggers a rebuild too.

## Commit identity

The history is authored as `kriss <kriss@users.noreply.github.com>` so no real
address ends up in a public repo. Point it at your own no-reply address once you
know which account you are using:

```
git config user.email "YOUR-ADDRESS"
```

On GitLab that is under **Preferences → Emails**; on GitHub, **Settings →
Emails → Keep my email addresses private**.

## About the anthems

The recordings in `anthems/` are your own, so they ship with the page. `build.py`
copies them into `docs/anthems/`, re-encoding to 128 kbps where ffmpeg is
installed — about 2.6 MB each instead of 6.5 MB, which is what lets a hover start
playing almost at once. Without ffmpeg they are copied unchanged and everything
still works, just with a larger download.

**Nothing to click.** When a club has **won** its last match, hovering anywhere
on its Last Match card fades the anthem in; moving the pointer away fades it out
and pauses it where it stopped, so hovering again resumes from there. A loss or a
draw gets no meter and no sound. Only one plays at a time — hovering a second
card fades the first one down.

**The one unavoidable click.** Browsers refuse to start audio before you have
interacted with a site, so on a freshly opened tab the card reads *click once to
enable sound*. One click anywhere lifts it for the rest of the visit, and if you
click while the pointer is already over a card, that anthem starts immediately.

If you ever need a build with no audio in it — hosting something you may not
distribute — `python build.py --no-anthems` goes back to asking each viewer for
their own file, which their browser keeps and never uploads.

## Running it locally as before

Nothing has changed. `python main.py` or **Open Dashboard.bat** still work, with
live data on every refresh and the anthems playing from your own folder.
