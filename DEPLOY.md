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

## Which account to use

**Do not put this on a work account.** Three things make it a bad fit:

- A **published Pages site is public either way.** Even when the source repo is
  private, the site itself is served publicly — only GitHub Enterprise Cloud can
  restrict who opens it. So a private repo does not hide the dashboard; it only
  hides the code.
- On the **Free plan, Pages does not work from a private repo at all.** Pages is
  available in public repositories on Free, and in private repositories only on
  Pro, Team or Enterprise.
- Actions are **free and unlimited on public repos**, but a private repo draws
  from a 2,000 minute monthly quota. Rebuilding every 15 minutes is about 2,900
  runs a month, which blows past that in a couple of weeks.

A private repo under your **personal** namespace is not visible to your
organisation's owners — they only see repos owned by the org. But a public repo,
and your activity on it, does show on your profile.

**The clean answer: use a different account.** A second GitHub account takes two
minutes and any email address, keeps the repo public so Pages and Actions are
free, and never touches your work profile. If you already have a personal
GitHub, use that.

If you would rather not create an account at all, skip to Option B.

## Option A — GitHub Pages (recommended)

Free, permanent URL, and it refreshes itself every 15 minutes.

1. Sign in to the account you want this on — **not the work one**.

2. Create an empty repository. Call it whatever you like; the name becomes part
   of the URL. **Make it public** so Pages and Actions stay free.

3. Push this folder to it:

   ```
   git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git
   git push -u origin main
   ```

4. In the repository: **Settings → Pages → Build and deployment → Source** and
   choose **GitHub Actions**.

5. **Actions** tab → *Build and publish dashboard* → **Run workflow**.

Your link is then:

```
https://YOUR-NAME.github.io/YOUR-REPO/
```

Bookmark it, share it, open it on your phone. From then on it rebuilds every 15
minutes by itself, and on every push.

> GitHub pauses scheduled workflows on repositories with no activity for 60
> days. If the page ever stops updating, open the Actions tab and press
> **Enable workflow**.

### Commit identity

The history here is authored as `kriss <kriss@users.noreply.github.com>` so no
real address ends up in a public repo. Once you know the account you are using,
point it at that account's own no-reply address:

```
git config user.email "YOUR-ID+YOUR-NAME@users.noreply.github.com"
```

You will find the exact address under **GitHub → Settings → Emails → Keep my
email addresses private**.

## Option B — Netlify Drop (no account at all)

Go to **[app.netlify.com/drop](https://app.netlify.com/drop)** and drag the
`docs` folder onto the page. You get a public URL in a few seconds, with no
sign-up, no repository and no connection to any account of yours.

The catch: it publishes the snapshot as it stands and does not refresh itself.
Re-run `python build.py` and drag the folder again whenever you want it current.
Good for sending a friend a link today; Option A is better for a bookmark.

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
