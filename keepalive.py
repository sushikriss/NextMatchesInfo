#!/usr/bin/env python3
"""Keep the published dashboard rebuilding itself.

GitHub switches off a scheduled workflow after 60 days with no push to the
repository, and it disables the whole workflow - `push` and the manual Run
workflow button included - so it cannot recover on its own.

Run this every month or two (`reset-matches.bat` does it for you):

  * Normally it pushes one empty commit. That resets the 60-day clock, and
    because the workflow also runs on push, the page rebuilds straight away.

  * If the workflow has already been switched off, it turns it back on first.
    Enabling alone would leave the clock still expired, so it pushes as well;
    one extra empty commit is cheap next to the site freezing again.

Reading the workflow state needs no credentials. Turning one back on does, and
only then is the credential Git already uses for pushing read - never printed,
and sent nowhere but api.github.com.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
HERE = os.path.dirname(os.path.abspath(__file__))


def run(args, **kwargs):
    """git, with output captured so this script controls what gets printed."""
    return subprocess.run(args, cwd=HERE, capture_output=True, text=True, **kwargs)


def repo_slug():
    """"owner/name" from the git remote, or None if this is not a GitHub clone."""
    got = run(["git", "config", "--get", "remote.origin.url"])
    if got.returncode != 0:
        return None
    match = re.match(r"(?:https://|git@)github\.com[:/](.+?)(?:\.git)?$",
                     got.stdout.strip())
    return match.group(1) if match else None


def api(path, method="GET", token=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=b"" if method == "PUT" else None)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "keepalive")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return resp.status, (json.loads(body) if body else None)


def workflows(slug):
    """Every workflow and its state. Needs no authentication."""
    try:
        _, data = api("/repos/%s/actions/workflows" % slug)
    except urllib.error.HTTPError as exc:
        print("  ! could not read the workflow state: HTTP %s %s"
              % (exc.code, exc.reason))
        if exc.code == 403:
            print("    (the unauthenticated API allows 60 requests an hour)")
        return None
    except urllib.error.URLError as exc:
        print("  ! could not reach GitHub: %s" % exc.reason)
        return None
    return data.get("workflows", [])


def stored_token():
    """The credential Git Credential Manager already holds for github.com."""
    got = subprocess.run(["git", "credential", "fill"],
                         input="protocol=https\nhost=github.com\n\n",
                         cwd=HERE, capture_output=True, text=True, timeout=60)
    for line in got.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return None


def enable(slug, workflow):
    """Turn a switched-off workflow back on. True if it is now active."""
    name = os.path.basename(workflow["path"])
    print("  ! the workflow is switched off (%s)" % workflow["state"])
    print("    turning it back on ...")

    token = stored_token()
    if not token:
        print("    could not find a saved GitHub credential.")
        return False

    try:
        status, _ = api("/repos/%s/actions/workflows/%s/enable" % (slug, name),
                        method="PUT", token=token)
    except urllib.error.HTTPError as exc:
        print("    GitHub refused: HTTP %s %s" % (exc.code, exc.reason))
        if exc.code in (403, 404):
            print("    the saved credential is not allowed to do this;")
            print("    use the Enable workflow button instead.")
        return False
    except urllib.error.URLError as exc:
        print("    could not reach GitHub: %s" % exc.reason)
        return False

    print("    done (HTTP %d) - the schedule is running again." % status)
    return True


def main():
    slug = repo_slug()
    if not slug:
        print("  ! this folder has no GitHub remote - nothing to keep alive.")
        return 1
    print("  repository: %s" % slug)

    found = workflows(slug)
    if found is None:
        # Cannot tell either way, and the push below is worth making regardless
        # - but say plainly that the switched-off case went unchecked.
        print("    so this run could not check whether it is switched off.")
        print("    pushing anyway; if the page still says it has stopped")
        print("    rebuilding, enable it at https://github.com/%s/actions" % slug)
    else:
        off = [w for w in found if w["state"] != "active"]
        if not off:
            print("  workflow: active")
        for workflow in off:
            if not enable(slug, workflow):
                print()
                print("  Open this and press \"Enable workflow\":")
                print("    https://github.com/%s/actions" % slug)
                return 1

    # An empty commit keeps nothing junk in the history, resets the 60-day
    # clock, and triggers a rebuild because the workflow runs on push too.
    print("  pushing a commit to reset the 60-day clock ...")
    made = run(["git", "commit", "--allow-empty", "-q",
                "-m", "Keep the scheduled rebuild alive"])
    if made.returncode != 0:
        print("  ! could not create the commit:")
        print("    %s" % (made.stderr.strip() or made.stdout.strip())[:300])
        return 1

    pushed = run(["git", "push", "-q", "origin", "HEAD"])
    if pushed.returncode != 0:
        print("  ! the push failed, so the clock was NOT reset:")
        print("    %s" % (pushed.stderr.strip() or pushed.stdout.strip())[:300])
        print("    usually this just means Git needs you to sign in again.")
        return 1

    print("  pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
