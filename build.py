#!/usr/bin/env python3
"""Render the dashboard to a static page that can live on any free host.

ESPN's edge blocks requests that carry a browser fingerprint, so the page
cannot call the API from the visitor's browser - it is rendered here instead
and published as plain HTML. A scheduled CI job re-runs this to keep it fresh.

    python build.py            -> docs/index.html
"""

import io
import os
import re
import sys
import time
import urllib.parse

import main

OUT_DIR = "docs"

# A bookmarked tab should not sit on yesterday's fixtures.
FRESHEN_JS = """
<script>
(function(){
  var BUILT = %(built)d, MAX_AGE = %(max_age)d;
  function stale(){ return Date.now() - BUILT > MAX_AGE; }
  document.addEventListener('visibilitychange', function(){
    if (!document.hidden && stale()) { location.reload(); }
  });
  var btn = document.getElementById('refresh');
  if (btn) {
    btn.addEventListener('click', function(e){ e.preventDefault(); location.reload(); });
  }
})();
</script>
"""


def static_page():
    page = main.build_page(force=True)

    # The refresh link pointed at the local server; here it just reloads.
    old_btn = '<a class="btn" href="/?refresh=1">&#8635; Refresh</a>'
    assert old_btn in page, "refresh control not found - main.py changed?"
    page = page.replace(old_btn, '<button class="btn" id="refresh" type="button">&#8635; Refresh</button>')

    # Anthems are served from a folder next to the page rather than by a route.
    def relink(match):
        slug = match.group(1)
        found = main.find_anthem(slug)
        name = os.path.basename(found) if found else (slug + ".mp3")
        return 'src="anthems/%s"' % urllib.parse.quote(name)

    page = re.sub(r'src="/anthem/([A-Za-z0-9_\-]+)"', relink, page)

    freshen = FRESHEN_JS % {"built": int(time.time() * 1000), "max_age": 10 * 60 * 1000}
    page = page.replace("</body></html>", freshen + "</body></html>")
    return page


def build(with_anthems=False):
    if not with_anthems:
        # The anthems are copyrighted recordings. They stay on your machine
        # unless you explicitly ask for them, and the meter hides itself.
        main.find_anthem = lambda slug: None

    page = static_page()
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    target = os.path.join(OUT_DIR, "index.html")
    io.open(target, "w", encoding="utf-8", newline="\n").write(page)
    # stops GitHub Pages running the page through Jekyll
    io.open(os.path.join(OUT_DIR, ".nojekyll"), "w", encoding="utf-8").write("")

    size = len(page.encode("utf-8")) / 1024.0
    print("wrote %s (%.0f KB)" % (target, size))

    if with_anthems:
        import shutil
        dest = os.path.join(OUT_DIR, "anthems")
        if not os.path.isdir(dest):
            os.makedirs(dest)
        copied = []
        for name in sorted(os.listdir(main.anthems_dir())):
            stem, ext = os.path.splitext(name)
            if ext.lower() in main.AUDIO_TYPES:
                shutil.copy2(os.path.join(main.anthems_dir(), name), os.path.join(dest, name))
                copied.append(name)
        print("anthems copied: %s" % (", ".join(copied) if copied else "none found"))
    else:
        print("anthems: not published (run with --with-anthems to include them)")
    return 0


if __name__ == "__main__":
    sys.exit(build("--with-anthems" in sys.argv[1:]))
