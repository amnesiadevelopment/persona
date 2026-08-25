"""PS-1 round 2 — build one self-contained review bundle per direction.

WHY THIS SHAPE, stated plainly because it departs from the usual recipe.

The usual bundle is the rendered DOM of the real page with its assets inlined.
That cannot work here and it is worth being exact about why, rather than
quietly shipping something that looks like a bundle and is not one: persona is
**flet**, which is **Flutter**, which paints the entire interface into a single
``<canvas>``. The DOM carries only ``flt-semantics`` — Flutter's invisible
accessibility mirror, deliberately parked off-screen so a screen reader finds
it and a user does not. Dumping that DOM yields a document with the app's TEXT
and none of its PIXELS.

That is not a prediction, it is a measurement: round 1's alternatives were
built exactly that way, and opening one from ``file://`` renders a **blank
white page** (108 ``flt-semantics`` nodes, zero visible output). The owner
judges from screenshots, so the defect never surfaced — but the bundle was not
carrying the design.

So the bundle here embeds the REAL CAPTURED PIXELS of the real running
application as a data URI, and wraps them in the review furniture the reviewer
actually needs: the design rationale, and a **state switcher** so the open /
collapsed / paused states of one direction can be compared in place.

The switcher is a CSS ``:checked`` sibling selector over hidden radios — no
JavaScript at all. That is a deliberate choice against the alternative of
inlining a script: native state needs nothing inlined, cannot break at
``file://``, and leaves nothing to caveat. Every control in these bundles
works offline, so the honesty banner names the one thing that is a still frame
(the live event feed) rather than pretending otherwise.

GROWTH TOLERANCE: no block here clamps or fixes a height. Prose columns wrap
and grow downward; the figure is ``max-width:100%`` so a wider or narrower
screenshot reflows rather than overflowing; the state tabs wrap onto a second
line rather than being cut. A longer tradeoff note makes its card taller.
"""

from __future__ import annotations

import base64
import html
import os
import sys

OUT_DIR = "/tmp/ps1-bundles"

# (state id, tab label, screenshot path, caption under the frame)
VARIANTS = {
    "D": {
        "title": "D — Full-width console dock",
        "subtitle": "The log owns the whole bottom edge, under the sidebar and the page.",
        "row": "profile · message · time",
        "frames": [
            (
                "open",
                "Open",
                "/tmp/D-open.png",
                "Open. The profile column is a fixed left ruler, so eight events "
                "for four profiles read as four vertical groups rather than eight "
                "sentences. Header carries the live count and the follow state; "
                "the grip above it drags the whole console taller or shorter.",
            ),
            (
                "collapsed",
                "Collapsed",
                "/tmp/D-collapsed2.png",
                "Collapsed to a 34px strip — and still reporting. The pulse takes "
                "the colour of the newest event's severity, the newest line stays "
                "readable, and +41 counts what arrived while it was shut. "
                "Collapsing costs height, not awareness.",
            ),
            (
                "paused",
                "Scrolled up (paused)",
                "/tmp/D-paused2.png",
                "Scrolled up to read. Following stops dead — the label reads "
                "'paused — reading' and new events no longer move the viewport. "
                "'30 new ↓' is both the count of what is waiting and the one "
                "click back to the tail.",
            ),
        ],
        "optimised": (
            "Optimised for <strong>watching many profiles at once</strong>. The "
            "profile name is pulled out of the prose into its own fixed column, so "
            "the eye runs down one ruler instead of re-reading each line to find "
            "which machine it is about. Severity is a dot at the far left, ahead of "
            "all text, so a failure is findable peripherally without reading a word."
        ),
        "gives_up": (
            "Gives up page height, permanently, whenever it is open — this is the "
            "widest reading line of the three and the most expensive. It also puts "
            "the log at the bottom edge, furthest from the profile rows it "
            "describes."
        ),
    },
    "E": {
        "title": "E — Split dock: stream + session digest",
        "subtitle": "The same bottom strip, shared with a standing per-profile digest.",
        "row": "message · profile · time",
        "frames": [
            (
                "open",
                "Open",
                "/tmp/E-open3.png",
                "Open. The stream keeps the left ~80% and the right 286px carries "
                "THIS SESSION — the latest state per profile, newest last, with the "
                "same severity colour. The stream says what just happened; the "
                "digest says where things stand.",
            ),
            (
                "collapsed",
                "Collapsed",
                "/tmp/E-coll.png",
                "Collapsed. Identical strip to the other two directions — the cost "
                "of a shut log is deliberately not one of the variables under "
                "comparison. Red pulse, newest line, +29 waiting.",
            ),
        ],
        "optimised": (
            "Optimised for <strong>answering “is anything stuck?” without reading "
            "the stream</strong>. Because the digest already carries the profile "
            "name, the stream row leads with the MESSAGE instead — what happened "
            "first, which machine second. That is the opposite emphasis to D, and it "
            "is the point: here you scan for events, and look right for state."
        ),
        "gives_up": (
            "Gives up ~286px of reading width to the digest, so long lines have less "
            "room than in D. It also spends screen on information that is a "
            "projection of the same events — worth it only if you genuinely ask "
            "“where do things stand” more often than you read the stream."
        ),
    },
    "F": {
        "title": "F — Overlay sheet",
        "subtitle": "The log floats above the page instead of displacing it.",
        "row": "profile · message · time",
        "frames": [
            (
                "open",
                "Open",
                "/tmp/F-open.png",
                "Open. An 880px rounded sheet anchored to the bottom-right, floating "
                "over the profile list — note the page behind it is still full "
                "height and unshifted. Opening and closing the log never reflows the "
                "page underneath.",
            ),
            (
                "collapsed",
                "Collapsed",
                "/tmp/F-coll.png",
                "Collapsed to the same live strip, still floating — the page below "
                "has not moved a pixel between these two frames. +49 waiting.",
            ),
        ],
        "optimised": (
            "Optimised for <strong>a page that never moves</strong>. The profile row "
            "you were about to click does not shift because a log opened. Rows use "
            "D's profile-first ruler, since the sheet is wide enough for it."
        ),
        "gives_up": (
            "Gives up the bottom-right of the page to occlusion — it COVERS content "
            "rather than displacing it, so the last rows of a long profile list sit "
            "underneath it while it is open. It is also the least “installed” "
            "looking of the three: a panel over the app rather than part of its "
            "frame."
        ),
    },
}

SHARED = """
<p>All three carry the <strong>same three behaviours</strong>, so the choice is
purely about where the log lives and how big it is:</p>
<ul>
  <li><strong>Collapsible, and still alive when collapsed.</strong> A chevron
      shuts it to a 34px strip that keeps the newest line, a severity-coloured
      pulse, and a count of everything that arrived while it was shut.</li>
  <li><strong>A row you can scan.</strong> Severity dot, then real aligned
      columns instead of one run of prose, so a stack of rows forms vertical
      rulers.</li>
  <li><strong>Scrolling that behaves.</strong> It follows the tail while you
      are at the bottom, stops the moment you scroll up to read, and offers
      “N new ↓” as the explicit way back. Reading is never interrupted by an
      arrival.</li>
</ul>
<p>The old panel replaced its entire child list on every flush, so any scroll
position you established pointed at children that no longer existed — which is
the mechanism behind “не адекватно скролится”. These keep one list across
flushes, so a position survives the next event.</p>
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — persona Activity Log (PS-1)</title>
<style>
  :root {{
    --bg:#0B0B0C; --panel:#121214; --edge:#26262B;
    --ink:#F2F2F4; --dim:#A0A0A8; --accent:#A8FF3F; --warn:#E6B43C;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:28px 22px 64px; }}
  .banner {{
    background:#1A1508; border:1px solid #4A3C12; border-left:3px solid var(--warn);
    border-radius:8px; padding:12px 16px; margin-bottom:24px;
    color:#E8D9A8; font-size:13.5px;
  }}
  .banner strong {{ color:var(--warn); }}
  h1 {{ font-size:27px; line-height:1.25; margin:0 0 6px; letter-spacing:-.2px; }}
  .sub {{ color:var(--dim); margin:0 0 22px; font-size:15px; }}
  .tag {{
    display:inline-block; font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
    color:var(--accent); border:1px solid #3A5416; background:#141A08;
    border-radius:999px; padding:5px 10px; margin:0 6px 6px 0;
  }}
  /* State switcher: hidden radios + :checked sibling selectors. No JavaScript,
     so it works from file:// forever and there is nothing to inline. */
  .sw input {{ position:absolute; opacity:0; pointer-events:none; }}
  .tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 14px; }}
  .tabs label {{
    cursor:pointer; user-select:none; border:1px solid var(--edge);
    background:var(--panel); color:var(--dim); border-radius:7px;
    padding:9px 15px; font-size:13.5px; min-height:40px; display:flex;
    align-items:center; transition:border-color .15s,color .15s,background .15s;
  }}
  .tabs label:hover {{ color:var(--ink); border-color:#3A3A42; }}
  .frame {{ display:none; }}
  figure {{ margin:0; }}
  figure img {{
    display:block; width:100%; max-width:100%; height:auto;
    border:1px solid var(--edge); border-radius:9px; background:#000;
  }}
  figcaption {{
    color:var(--dim); font-size:13.5px; margin-top:11px; max-width:78ch;
  }}
  {rules}
  .cards {{
    display:grid; grid-template-columns:repeat(auto-fit,minmax(290px,1fr));
    gap:16px; margin-top:30px; align-items:start;
  }}
  .card {{
    background:var(--panel); border:1px solid var(--edge);
    border-radius:9px; padding:16px 18px;
  }}
  .card h2 {{
    font-size:11.5px; letter-spacing:.09em; text-transform:uppercase;
    color:var(--dim); margin:0 0 9px; font-weight:700;
  }}
  .card p {{ margin:0; font-size:14px; }}
  .card.give {{ border-left:3px solid var(--warn); }}
  .shared {{
    margin-top:30px; border-top:1px solid var(--edge); padding-top:22px;
    color:#D6D6DC; font-size:14px;
  }}
  .shared ul {{ padding-left:20px; }}
  .shared li {{ margin-bottom:7px; }}
  footer {{
    margin-top:34px; padding-top:16px; border-top:1px solid var(--edge);
    color:var(--dim); font:12.5px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;
  }}
</style>
</head>
<body>
<div class="wrap">

  <div class="banner">
    <strong>About this bundle.</strong> persona is a <em>flet</em> (Flutter)
    application: it paints its whole interface into a single
    <code>&lt;canvas&gt;</code>, so a captured DOM carries the app's text but
    none of its pixels — round&nbsp;1's bundles were built that way and render
    blank when opened from disk. This bundle therefore embeds the
    <strong>real captured pixels of the real running application</strong>
    (seeded with 8 profiles and a live event feed, at 1440&times;950). The
    state switcher below is pure CSS and works offline. What is
    <strong>inert</strong> here: the event feed is a still frame, so timestamps
    and counts do not advance — in the running app they do.
  </div>

  <h1>{title}</h1>
  <p class="sub">{subtitle}</p>
  <div>
    <span class="tag">row: {row}</span>
    <span class="tag">branch: {branch}</span>
    <span class="tag">PS-1 · round 2</span>
  </div>

  <div class="sw">
{radios}
    <div class="tabs">
{labels}
    </div>
{frames}
  </div>

  <div class="cards">
    <div class="card">
      <h2>What the row is optimised for</h2>
      <p>{optimised}</p>
    </div>
    <div class="card give">
      <h2>What this direction gives up</h2>
      <p>{gives_up}</p>
    </div>
  </div>

  <div class="shared">{shared}</div>

  <footer>
    persona 3.0.0 · captured from the running application at 1440&times;950<br>
    branch {branch} (unmerged, no PR) · visual draft PS-1
  </footer>
</div>
</body>
</html>
"""


def data_uri(path: str) -> str:
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode()


def build(key: str, spec: dict, branch: str) -> str:
    frames = spec["frames"]
    radios, labels, blocks, rules = [], [], [], []
    for i, (fid, label, png, caption) in enumerate(frames):
        rid = f"{key}-{fid}"
        checked = " checked" if i == 0 else ""
        radios.append(f'    <input type="radio" name="st-{key}" id="{rid}"{checked}>')
        labels.append(
            f'      <label for="{rid}">{html.escape(label)}</label>'
        )
        blocks.append(
            f'    <div class="frame" id="f-{rid}"><figure>'
            f'<img alt="{html.escape(label)} — {html.escape(spec["title"])}" '
            f'src="{data_uri(png)}">'
            f"<figcaption>{caption}</figcaption></figure></div>"
        )
        # The selected radio lights its own tab and reveals its own frame.
        rules.append(
            f"  #{rid}:checked ~ .tabs label[for={rid}] "
            "{ color:#0B0B0C; background:var(--accent); border-color:var(--accent); font-weight:600; }\n"
            f"  #{rid}:checked ~ #f-{rid} " "{ display:block; }"
        )

    return PAGE.format(
        title=html.escape(spec["title"]),
        subtitle=html.escape(spec["subtitle"]),
        row=html.escape(spec["row"]),
        branch=html.escape(branch),
        radios="\n".join(radios),
        labels="\n".join(labels),
        frames="\n".join(blocks),
        rules="\n".join(rules),
        optimised=spec["optimised"],
        gives_up=spec["gives_up"],
        shared=SHARED,
    )


BRANCHES = {
    "D": "viz/PS-1-r2-d-dock",
    "E": "viz/PS-1-r2-e-split",
    "F": "viz/PS-1-r2-f-overlay",
}


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for key, spec in VARIANTS.items():
        for _, _, png, _ in spec["frames"]:
            if not os.path.exists(png):
                sys.exit(f"missing capture: {png}")
        out = os.path.join(OUT_DIR, f"ps1-{key.lower()}.html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(build(key, spec, BRANCHES[key]))
        print(f"{out}  {os.path.getsize(out):,} bytes")


if __name__ == "__main__":
    main()
