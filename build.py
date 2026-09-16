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
BROWSER_ANTHEM = """
<style>
.eq[data-slot]{cursor:pointer;position:relative}
.eq[data-slot].ready{opacity:.45}
/* Until a file is chosen the meter is a plainly labelled button instead of
   four mystery bars. */
.eq.need{
  width:auto;height:auto;gap:7px;opacity:1;align-items:center;
  background:#e5f4fe;border:1px solid #a9d9f6;border-radius:999px;
  padding:4px 11px;font-size:10.5px;font-weight:800;letter-spacing:.08em;
  text-transform:uppercase;color:#0b6aa8;white-space:nowrap;transition:.15s;
}
.eq.need:hover,.eq.need:focus{background:#d3ecfd;border-color:#7cc4ee;outline:none}
.eq.need b{font-size:13px;line-height:1}
</style>
<script>
(function(){
  var DB = 'dashboard-anthems', STORE = 'files', FADE = 430;

  function open(){
    return new Promise(function(res, rej){
      var req = indexedDB.open(DB, 1);
      req.onupgradeneeded = function(){ req.result.createObjectStore(STORE); };
      req.onsuccess = function(){ res(req.result); };
      req.onerror = function(){ rej(req.error); };
    });
  }
  function run(mode, fn){
    return open().then(function(db){
      return new Promise(function(res, rej){
        var t = db.transaction(STORE, mode), request = fn(t.objectStore(STORE));
        t.oncomplete = function(){ res(request ? request.result : null); };
        t.onerror = function(){ rej(t.error); };
      });
    });
  }
  var load = function(k){ return run('readonly', function(s){ return s.get(k); }); };
  var save = function(k, v){ return run('readwrite', function(s){ return s.put(v, k); }); };

  function fade(audio, target, done){
    clearInterval(audio._fade);
    var from = audio.volume, started = performance.now();
    audio._fade = setInterval(function(){
      var k = Math.min(1, (performance.now() - started) / FADE);
      audio.volume = Math.max(0, Math.min(1, from + (target - from) * k));
      if (k >= 1) { clearInterval(audio._fade); if (done) { done(); } }
    }, 25);
  }

  var cards = [].slice.call(document.querySelectorAll('.card[data-anthem="browser"]'));

  function attach(card, meter, blob){
    if (card._audio) { URL.revokeObjectURL(card._audio.src); card._audio.pause(); }
    var audio = new Audio(URL.createObjectURL(blob));
    audio.preload = 'auto';
    card._audio = audio;
    meter.classList.remove('need');
    meter.classList.add('ready');
    meter.innerHTML = '<i></i><i></i><i></i><i></i>';
    meter.title = 'Hover the card to play the ' + meter.getAttribute('data-label') +
                  '. Click to choose a different file.';

    if (card._wired) { return; }
    card._wired = true;

    card.addEventListener('mouseenter', function(){
      var mine = card._audio;
      if (!mine) { return; }
      cards.forEach(function(other){
        if (other === card || !other._audio || other._audio.paused) { return; }
        var t = other._audio;
        fade(t, 0, function(){ t.pause(); other.classList.remove('playing'); });
      });
      if (mine.paused) { mine.volume = 0; }
      var started = mine.play();
      if (started && started.catch) {
        started.catch(function(){
          document.addEventListener('click', function once(){
            document.removeEventListener('click', once);
            if (card.matches(':hover') && card._audio) { card._audio.play().catch(function(){}); }
          });
        });
      }
      card.classList.add('playing');
      fade(mine, 1);
    });

    card.addEventListener('mouseleave', function(){
      var mine = card._audio;
      if (!mine) { return; }
      fade(mine, 0, function(){ mine.pause(); card.classList.remove('playing'); });
    });
  }

  function choose(card, meter, slot){
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = 'audio/*';
    input.addEventListener('change', function(){
      var file = input.files && input.files[0];
      if (!file) { return; }
      save(slot, file).then(function(){ attach(card, meter, file); })
        .catch(function(){ attach(card, meter, file); });   // play it even if storing fails
    });
    input.click();
  }

  cards.forEach(function(card){
    var meter = card.querySelector('.eq[data-slot]');
    if (!meter) { return; }
    var slot = meter.getAttribute('data-slot');
    var label = meter.getAttribute('data-label');
    meter.classList.add('need');
    meter.innerHTML = '<b>&#9834;</b><span>Add ' + label + '</span>';
    meter.title = 'Pick your ' + label + ' file. It stays on this device and is never uploaded.';

    meter.addEventListener('click', function(e){ e.stopPropagation(); choose(card, meter, slot); });
    meter.addEventListener('keydown', function(e){
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(card, meter, slot); }
    });

    load(slot).then(function(blob){ if (blob) { attach(card, meter, blob); } })
      .catch(function(){ /* private mode, or storage blocked - the + still works */ });
  });
})();
</script>
"""


FRESHEN_JS = """
<style>
#age.stale{color:#c2410c;font-weight:700}
</style>
<script>
(function(){
  var BUILT = %(built)d, MAX_AGE = %(max_age)d, TRIED = 'dashReloadTried';
  var stamp = document.querySelector('.stamp');

  function ago(){
    var secs = Math.round((Date.now() - BUILT) / 1000);
    if (secs < 90) { return 'just now'; }
    var mins = Math.round(secs / 60);
    if (mins < 60) { return mins + ' min ago'; }
    var hours = Math.round(mins / 60);
    if (hours < 36) { return hours + (hours === 1 ? ' hour ago' : ' hours ago'); }
    var days = Math.round(hours / 24);
    return days + (days === 1 ? ' day ago' : ' days ago');
  }

  function paintAge(){
    if (!stamp) { return; }
    var tag = document.getElementById('age');
    if (!tag) { tag = document.createElement('span'); tag.id = 'age'; stamp.appendChild(tag); }
    tag.textContent = ' · ' + ago();
    tag.className = (Date.now() - BUILT > 3 * 3600 * 1000) ? 'stale' : '';
  }
  paintAge();
  setInterval(paintAge, 30000);

  function stale(){ return Date.now() - BUILT > MAX_AGE; }
  try { if (!stale()) { sessionStorage.removeItem(TRIED); } } catch (e) {}

  // Coming back to a stale tab, try once for a newer build. On a host that
  // serves a frozen snapshot the reload changes nothing, so never loop.
  document.addEventListener('visibilitychange', function(){
    if (document.hidden || !stale()) { return; }
    try {
      if (sessionStorage.getItem(TRIED)) { return; }
      sessionStorage.setItem(TRIED, '1');
    } catch (e) {}
    location.reload();
  });

  var btn = document.getElementById('refresh');
  if (btn) {
    btn.addEventListener('click', function(e){
      e.preventDefault();
      try { sessionStorage.removeItem(TRIED); } catch (e2) {}
      location.reload();
    });
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
    extra = freshen + (BROWSER_ANTHEM if main.ANTHEM_MODE == "browser" else "")
    page = page.replace("</body></html>", extra + "</body></html>")
    return page


def build(with_anthems=False):
    if not with_anthems:
        # The recordings are copyrighted, so nothing is shipped with the page.
        # Instead the viewer picks their own file once and the browser keeps it.
        main.ANTHEM_MODE = "browser"

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
        print("anthems: not published - the page asks you for a file and keeps it in your browser")
    return 0


if __name__ == "__main__":
    sys.exit(build("--with-anthems" in sys.argv[1:]))
