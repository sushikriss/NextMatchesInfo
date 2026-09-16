# Putting this online

The dashboard becomes a plain HTML page that any free static host will serve.
Nothing runs on your computer, and the link works for anyone you send it to.

## Why it is pre-rendered rather than live-in-the-browser

ESPN's edge sits behind a bot filter. A request carrying a browser fingerprint
is refused with **403 Access Denied**, while the same request from a script is
answered normally — and a web page cannot pretend to be a script, because
browsers will not let JavaScript change its own `User-Agent`.

So the page cannot call ESPN from your browser. Instead `build.py` renders the
whole dashboard to static HTML, and a scheduled job re-renders it. The page you
open is already finished, so it appears instantly.

Countdowns, the lineup viewer and the fact tooltips are all client-side, so they
stay live no matter how old the build is.

---

## Option A — GitLab Pages

Two things to know before you start.

**1. GitLab wants a card on file before it will run CI.** Shared runners require
credit or debit card verification — a £0/€0 authorisation, no charge. Without
CI there is no Pages deploy, so this is unavoidable on GitLab. If you would
rather not, use Option B or C.

**2. The free tier gives 400 compute minutes a month.** Each rebuild takes a
minute or two, so the schedule below runs every 4 hours (about 180 runs a
month). Going to every 15 minutes would need roughly 2,900 runs and is far
beyond the free budget.

One thing GitLab does better than GitHub: **Pages works from a private project
on the free tier.** Your code can stay private while the site is public — on
GitHub that needs a paid plan.

### Steps

1. Sign in to the GitLab account you want this on. Create a **new blank
   project** — no README, no template. Private is fine.

2. Push this folder:

   ```
   git remote add origin https://gitlab.com/YOUR-NAME/YOUR-PROJECT.git
   git push -u origin main
   ```

3. If prompted, complete card verification under
   **Settings → CI/CD → Runners**, or at the banner GitLab shows you.

4. The first pipeline runs on push. Watch it under **Build → Pipelines**.

5. Set up the refresh: **Build → Pipeline schedules → New schedule**

   | Field | Value |
   | --- | --- |
   | Description | `Rebuild dashboard` |
   | Interval pattern | Custom → `0 */4 * * *` |
   | Cron timezone | anything; the page always shows Bulgarian time |
   | Target branch | `main` |

6. Find your link under **Deploy → Pages**. It looks like:

   ```
   https://YOUR-NAME.gitlab.io/YOUR-PROJECT/
   ```

   New projects often have **Use unique domain** switched on, which gives a
   longer address instead. Either works; the Pages settings page shows the real
   one.

### Tuning the schedule

Roughly 400 minutes ÷ 2 minutes per run ≈ 200 runs a month.

| Cron | Rebuilds per day | Runs per month | Fits 400 min? |
| --- | --- | --- | --- |
| `0 */6 * * *` | 4 | ~120 | comfortably |
| `0 */4 * * *` | 6 | ~180 | yes — the default here |
| `0 */2 * * *` | 12 | ~360 | tight, likely over |
| `*/15 * * * *` | 96 | ~2,900 | no |

Last match, next fixture and league tables all move slowly, so a few hours of
lag costs you nothing in practice.

---

## Option B — GitHub Pages

Better economics than GitLab: on a **public** repo, Actions are free and
unlimited, so it rebuilds every 15 minutes, and there is no card check.

The workflow is already written at `.github/workflows/deploy.yml`.

1. Create an empty **public** repo on the account you want — not a work one.
2. `git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git`
   then `git push -u origin main`
3. **Settings → Pages → Build and deployment → Source → GitHub Actions**
4. Link: `https://YOUR-NAME.github.io/YOUR-REPO/`

Note that on GitHub's free plan Pages only works from a **public** repo, and the
published site is public regardless. Scheduled workflows pause after 60 days of
repository inactivity; re-enable them from the Actions tab.

## Option C — Netlify Drop (no account, no card)

Go to **[app.netlify.com/drop](https://app.netlify.com/drop)** and drag the
`docs` folder onto the page. A public URL appears in seconds, with no sign-up,
no repository and no card.

The catch: it publishes the snapshot as it stands and never refreshes itself.
Re-run `python build.py` and drag the folder again to update. Good for sending
someone a link today; A or B are better for a bookmark.

---

## Updating it yourself

```
python build.py      # re-render docs/index.html from live ESPN data
git add -A && git commit -m "refresh" && git push
```

The scheduled job does exactly this, so you rarely need to.

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

`build.py` does **not** publish your anthem files. They are commercial
recordings, and putting them on a public URL would be distributing them — so
`.gitignore` keeps them out of the repository and the meter simply does not
appear on the hosted page.

If you host somewhere private and want the sound, build with:

```
python build.py --with-anthems
```

That copies the audio into `docs/anthems/` and the hover-to-play meter comes
back. On a hosted page the browser wants one click anywhere on the page before
it will allow sound; after that, hovering works as it does locally.

## Running it locally as before

Nothing has changed. `python main.py` or **Open Dashboard.bat** still work, with
live data on every refresh and the anthems playing from your own folder.
