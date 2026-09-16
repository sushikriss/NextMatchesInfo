# Putting this online

The dashboard becomes a plain HTML page that any free static host will serve.
Nothing runs on your computer, and the link works for anyone you send it to.

## Why it is pre-rendered rather than live-in-the-browser

ESPN's edge sits behind a bot filter. A request carrying a browser fingerprint
is refused with **403 Access Denied**, while the same request from a script is
answered normally — and a web page cannot pretend to be a script, because
browsers will not let JavaScript change its own `User-Agent`.

So the page cannot call ESPN from your browser. Instead `build.py` renders the
whole dashboard to static HTML, and a scheduled job re-renders it every 15
minutes. The page you open is already finished, so it appears instantly.

Countdowns, the lineup viewer and the fact tooltips are all client-side, so they
stay live no matter how old the build is.

---

## Option A — GitHub Pages (recommended)

Free, permanent URL, and it refreshes itself every 15 minutes.

1. Create an empty repository on github.com. **Make it public** — scheduled
   Actions are free without limit on public repos.

2. Push this folder to it:

   ```
   git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git
   git push -u origin main
   ```

3. In the repository: **Settings → Pages → Build and deployment → Source** and
   choose **GitHub Actions**.

4. **Actions** tab → *Build and publish dashboard* → **Run workflow**.

Your link is then `https://YOUR-NAME.github.io/YOUR-REPO/` — bookmark it, share
it, open it on your phone.

From then on it rebuilds every 15 minutes by itself, and on every push.

> GitHub pauses scheduled workflows on repositories with no activity for 60
> days. If the page ever stops updating, open the Actions tab and press
> **Enable workflow**.

## Option B — Netlify Drop (fastest, no account needed)

Go to **[app.netlify.com/drop](https://app.netlify.com/drop)** and drag the
`docs` folder onto the page. You get a public URL in a few seconds.

This publishes the snapshot as it is now and does not refresh itself, so it is
best for showing someone quickly. Re-run `python build.py` and drop the folder
again to update it.

---

## Updating it yourself

```
python build.py      # re-render docs/index.html from live ESPN data
git add -A && git commit -m "refresh" && git push
```

The scheduled job does exactly this, so you rarely need to.

## About the anthems

`build.py` does **not** publish your anthem files. They are commercial
recordings, and putting them on a public URL would be distributing them — so
`.gitignore` keeps them out of the repository and the meter simply does not
appear on the hosted page.

If you host it somewhere private and want the sound, build with:

```
python build.py --with-anthems
```

That copies the audio into `docs/anthems/` and the hover-to-play meter comes
back. On a hosted page the browser wants one click anywhere on the page before
it will allow sound; after that, hovering works as it does locally.

## Running it locally as before

Nothing has changed. `python main.py` or **Open Dashboard.bat** still work, with
live data on every refresh and the anthems playing from your own folder.
