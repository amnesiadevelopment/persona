"""The per-engine MASKING-vector matrix, stated as a matrix — including its
unknowns.

The masking twin of ``test_engine_antileak_matrix.py``, and it exists for the
same reason that file does: every vector is implemented TWICE, on two engines,
with no shared policy layer, so a vector added to one engine is invisible on the
other until somebody happens to look. The cost has already been paid twice on
this vector family, one engine at a time — PS-11 found that a Chromium worker
spawned from a worker ran completely unspoofed, and PS-205 found the SAME defect
on the Firefox locale payload months later. A contract makes the twin visible at
the moment the first is closed.

THE MATRIX IS NOT "12 vs 4", AND A FILE CLAIMING SO WOULD BE WRONG.
Chromium builds twelve masking extensions and the Firefox launch path registers
four spoofs, but the difference is not nine gaps. Firefox here is a PATCHED
engine that spoofs natively, so an absent JS spoof legitimately often means "the
engine already did it" — and three of persona's own builders say exactly that in
their own headers (``voice_ext``, ``gpu_ext``, ``device_ext``). ``coherence.py``
also pins the Firefox engine to ``os_type == "windows"`` and forbids it a mobile
os_type outright, so several vectors are not applicable to that engine BY
CONSTRUCTION rather than missing from it.

THE DEFECT THIS FILE ADDRESSES IS NOT THAT A VECTOR IS UNCOVERED. It is that
nobody could tell an INTENTIONAL absence from an UNNOTICED one. Every cell below
therefore carries one of five positions, and the fifth is the deliverable:

* ``COVERED``          — this engine installs a spoof for this vector, on its own
                         launch path, through its own route.
* ``COVERED_ELSEWHERE`` — covered, but NOT through this engine's spoof route. The
                         route is named in the cell; a cell that cannot name one
                         is not this position.
* ``NOT_COVERED_RECORDED`` — deliberately not covered, and the reason is RECORDED
                         IN THE TREE. The cell quotes it and names its file, and
                         a test below re-reads that file, so deleting the reason
                         turns this suite red rather than silently converting a
                         recorded decision into an unexplained gap.
* ``NOT_APPLICABLE``   — the engine cannot reach the configuration this vector
                         addresses. The cell names the constraint that makes it
                         so, and a test below asserts that constraint still holds.
* ``NOT_ESTABLISHED``  — NOTHING IN THE TREE RECORDS A POSITION. An honest
                         unknown, and never a claim of coverage. These cells are
                         the actual finding: ``stealth`` and ``measuretext``.
                         Three names left this list on the day each was
                         established BY MEASUREMENT — ``geo`` (PS-312, now
                         NOT_COVERED_RECORDED); the mediaDevices half of
                         ``device`` (PS-330, now NOT_COVERED_RECORDED — a real
                         headful launch found the engine already answering with
                         a constant synthetic roster carrying empty ids); and,
                         running the OTHER direction, Chromium's position on
                         Firefox's ``outer-size`` (#327, now COVERED_ELSEWHERE
                         via a launch argument rather than a spoof). The list
                         shrinks only against a reading; ``test_the_open_cells_
                         are_the_deliverable_and_are_named`` pins what is left
                         AS DATA so this sentence cannot quietly disagree with
                         it.

This is a CHARACTERIZATION test, on exactly the terms its network sibling states:
it pins the matrix AS IT IS TODAY, unknowns included, and is green on day one —
on all three CI platforms, which is a stronger claim than it sounds and was not
free. The Chromium column is read off a launch's argv, and ``process.py`` builds
the ``--load-extension`` entries with ``os.path.join``, so the SAME product code
hands this file ``\\``-separated paths on Windows. Round 1 of this ticket parsed
them with a ``/``-only split: four tests failed loudly there and a fifth — a
NEGATIVE assertion — passed VACUOUSLY, and would have kept passing with the
product's proxy gate deleted. Hence two standing rules in the helpers below: the
argv parser is separator-agnostic and RAISES on anything it cannot parse rather
than skipping it, and every negative assertion is anchored by a positive one
taken from the same reading. It does not demand parity, and it closes no cell.

* Add a vector to one engine only -> the census tests below fail, forcing the
  parity question to be answered out loud.
* Establish a position on purpose -> change that cell from ``NOT_ESTABLISHED`` in
  the same commit. That edit IS the record that the question was answered.

WHERE EACH CELL READS. The sibling file's ``_firefox_effective_proxied`` helper
carries the rule this one obeys too: "a sentinel that reads below the layer it
guards is silently worthless." So the Chromium column is read from the argv a
LAUNCH actually produces (the ``--load-extension`` list, through the existing
spawn harness), not from the source; and the Firefox column is read from the
init-script SOURCE THE BUILDERS EMIT, which is the layer that engine's spoofs
actually arrive on. Where a cell can only be read structurally, it is read by
walking the product's own AST rather than by matching text.

⚠️ NO BROWSER WAS EXECUTED. There is no engine binary and no display in this
container, so what every cell below establishes is the CODE SHAPE — which
builders a launch calls, under which conditions, and which spoof source each
engine emits. That a vector is INSTALLED is not evidence it reached the page; the
``masking_layer`` module makes the same distinction in its own words ("an install
is still not a reading"). This matrix pins installation, deliberately and only.

The matrix, for ONE profile per column:

| vector      | Chromium                              | Firefox                          |
|-------------|---------------------------------------|----------------------------------|
| native      | build_native_extension (always)       | ELSEWHERE: _native_cloak_js prelude |
| locale      | build_locale_extension (always)       | COVERED: _install_spoof("locale") |
| voice       | build_voice_extension (always)        | NOT COVERED, reason recorded      |
| stealth     | build_stealth_extension (always)      | ⭐ POSITION NOT ESTABLISHED       |
| measuretext | build_measuretext_extension (always)  | ⭐ POSITION NOT ESTABLISHED       |
| audio       | build_audio_extension (always)        | COVERED: raw add_init_script      |
| mobile      | build_mobile_extension (mobile only)  | NOT APPLICABLE (coherence)       |
| device      | build_device_extension (desktop only) | SPLIT: screen ELSEWHERE / media NOT COVERED, reason recorded |
| webgl       | build_webgl_extension (always)        | COVERED: _install_spoof("webgl")  |
| gpu         | build_gpu_extension (always; PER ARM) | engine-authored arm (per-arm)     |
| canvas_ctx  | build_canvas_ctx_extension (always)   | NOT APPLICABLE (coherence)       |
| geo         | build_geo_extension (proxy only)      | NOT COVERED, reason recorded      |
| outer-size  | ELSEWHERE: --window-size launch arg   | COVERED (resolution chosen only)  |
"""

import ast
import inspect
from pathlib import Path, PurePath

import pytest

from src.models.profile import Profile
from src.services.browser import invisible_launch as il
from src.services.browser import process as process_mod
from src.services.browser.audio_ext import firefox_audio_init_script
from src.services.browser.gpu_ext import ENGINE_AUTHORED_IDENTITY_ARMS
from src.services.browser.webgl_ext import firefox_webgl_init_script
from src.services.profile import coherence
from src.services.verify import masking_layer
from tests.test_process import _StoreWithCheckedProxy, _spawn_chromium_args

# The repo, anchored to THIS FILE rather than to the process CWD. Every path in
# this file is repo-relative, and a bare ``open()`` on one resolves against
# wherever pytest happened to be invoked from — green from the repo root and
# FileNotFoundError from anywhere else (which is how round 1 failed on the macOS
# and Windows runners). The convention is already this repo's
# (``test_ci_verification_gates``, ``test_build_config``, ``test_assets``,
# ``test_canvas_loopback_probe`` all do it); an anti-rot test that cannot find
# the file it guards reports the reader's CWD, not the tree.
REPO_ROOT = Path(__file__).resolve().parents[1]

# --- positions ---------------------------------------------------------------

COVERED = "covered"
COVERED_ELSEWHERE = "covered_elsewhere"
NOT_COVERED_RECORDED = "not_covered_recorded"
NOT_APPLICABLE = "not_applicable"
NOT_ESTABLISHED = "position_not_established"

# The one builder in ``spawn_browser`` that is NOT a masking vector. Excluded on
# the product's OWN stated grounds rather than this file's: ``masking_layer``
# excludes it from persona's masking layer in the same words — "a settings
# override, not masking". Named as data so the census below can subtract it and
# a future masking builder cannot hide behind the same subtraction.
NON_MASKING_BUILDERS = {"search"}

# The installation CONDITION each Chromium builder sits under, written exactly as
# the source of its enclosing ``if`` reads. "" means unconditional. A cell that
# says "covered" for a gated builder would claim coverage the product only
# SOMETIMES provides, so the condition is part of the cell, not a footnote — and
# ``test_chromium_conditions_match_the_source`` re-derives every one of these
# from the product's AST, so a gate that moves turns this file red.
CHROMIUM_CONDITIONS = {
    "native": "",
    "locale": "",
    "voice": "",
    "stealth": "",
    "measuretext": "",
    "search": "not _platform.IS_LINUX",
    "audio": "",
    "mobile": "is_mobile and preset is not None",
    "device": "NOT (is_mobile and preset is not None)",
    "webgl": "",
    "gpu": "",
    "canvas_ctx": "",
    "geo": "proxy",
}

# The Firefox launch path's spoof registry, as it reads today. The label is the
# one ``_install_spoof`` is called with.
#
# ``audio`` JOINED THIS DICT IN PS-302, and this edit is the record the
# superseded ``test_firefox_audio_bypasses_the_spoof_registry`` asked for in
# those words ("fold the route in, delete this test's raw-call expectation in
# the same commit, and add 'audio' to FIREFOX_SPOOF_CONDITIONS"). Every Firefox
# spoof now goes through ``_install_spoof``; nothing bypasses it — see
# ``test_firefox_spoofs_all_go_through_the_registry``.
FIREFOX_SPOOF_CONDITIONS = {
    "outer-size": "_res_overrides is not None",
    "locale": "",
    "webgl": "",
    "audio": "",
}

# --- the matrix --------------------------------------------------------------
#
# One entry per vector. ``chromium`` / ``firefox`` are (position, note) — the
# note is the cell's evidence, and for a NOT_COVERED_RECORDED cell it quotes the
# in-tree reason verbatim so a test can go and re-read it.

MATRIX = {
    "native": {
        "chromium": (COVERED, "build_native_extension, unconditional"),
        "firefox": (
            COVERED_ELSEWHERE,
            "NOT via the spoof registry: _native_cloak_js() is inlined as a "
            "PRELUDE into every init script this engine installs. Its own "
            "docstring names the relationship — 'The Firefox counterpart of "
            "native_ext.py' — and states why the extension route cannot reach "
            "here ('Firefox launches through this module and loads no persona "
            "extension'). The cloak text also differs on purpose: SpiderMonkey's "
            "three-line native form, not V8's one-line one.",
        ),
    },
    "locale": {
        "chromium": (COVERED, "build_locale_extension, unconditional"),
        "firefox": (COVERED, '_install_spoof("locale", ...), unconditional'),
    },
    "voice": {
        "chromium": (COVERED, "build_voice_extension, unconditional"),
        "firefox": (
            NOT_COVERED_RECORDED,
            "The stealth-Firefox engine already returns a small "
            "Windows-plausible set",
        ),
    },
    "stealth": {
        "chromium": (COVERED, "build_stealth_extension, unconditional"),
        "firefox": (
            NOT_ESTABLISHED,
            "No spoof, and NO RECORDED REASON anywhere in the tree. The vector "
            "re-adds two Chrome-only Navigator APIs (navigator.connection."
            "downlinkMax, ServiceWorkerRegistration.prototype.index) that "
            "CreepJS counts toward 'like headless'. A plausible position is "
            "'not applicable — Firefox exposes neither API, so a real Firefox "
            "is missing them too' — but plausible is not recorded, and this "
            "file will not mint a position the tree does not hold.",
        ),
    },
    "measuretext": {
        "chromium": (COVERED, "build_measuretext_extension, unconditional"),
        "firefox": (
            NOT_ESTABLISHED,
            "No spoof, and NO RECORDED REASON. The Chromium builder repairs "
            "noise the FINGERPRINT ENGINE injects into Canvas::measureText, so "
            "a plausible position is 'not applicable — a different engine does "
            "not inject that noise'. Not recorded, so not claimed.",
        ),
    },
    "audio": {
        "chromium": (COVERED, "build_audio_extension, unconditional"),
        "firefox": (
            COVERED,
            "firefox_audio_init_script(seed), unconditional, installed through "
            '_install_spoof("audio", ...) like every other Firefox spoof '
            "(PS-302). The COVERAGE is what this cell states; the ROUTE is "
            "pinned separately in "
            "test_firefox_spoofs_all_go_through_the_registry, so folding the "
            "route through the registry changed one and not the other — the "
            "cell read COVERED before that fold and reads COVERED after it.",
        ),
    },
    "mobile": {
        "chromium": (
            COVERED,
            "build_mobile_extension, on the mobile branch only — mutually "
            "exclusive with build_device_extension",
        ),
        "firefox": (
            NOT_APPLICABLE,
            "coherence refuses engine 'firefox' with a mobile os_type — Rule 1 "
            "on the mobile-OS ground AND Rule 2 independently, since Firefox "
            "may carry no os_type but 'windows'. So no Firefox profile can be "
            "the configuration this vector addresses. Not a gap: unreachable "
            "by construction.",
        ),
    },
    "device": {
        "chromium": (
            COVERED,
            "build_device_extension, on the NON-mobile branch only — screen "
            "geometry, devicePixelRatio, matchMedia AND "
            "navigator.mediaDevices.enumerateDevices()",
        ),
        "firefox": (
            NOT_COVERED_RECORDED,
            "SPLIT, and each half is answered by a DIFFERENT route — which is "
            "why one position for the pair would have claimed too much. The "
            "SCREEN half is covered conditionally at the ENGINE layer: when a "
            "resolution was chosen, the launch pins kwargs['pin'] = "
            "{screen.width/height/avail_*/dpr} (zoom.stealth.screen.*), so "
            "screen.* is spoofed without any persona init script. The "
            "mediaDevices half ships NO spoof, and PS-330 established that BY "
            "MEASUREMENT rather than leaving it an unknown: on a real headful "
            "launch the engine already answers enumerateDevices() with a "
            "constant synthetic 1 audioinput + 1 videoinput whose deviceId and "
            "groupId are the EMPTY STRING on every profile — on a host with no "
            "/dev/snd and no /dev/video* at all. No host fact escapes and no "
            "per-profile identifier exists to link, so a JS override would "
            "trade a native-rendering method for a visible one and buy "
            "nothing. The reason lives in invisible_launch.py beside the "
            "_install_spoof calls, where a reader asks why there is no device "
            "one.",
        ),
    },
    "webgl": {
        "chromium": (COVERED, "build_webgl_extension, unconditional"),
        "firefox": (
            COVERED,
            '_install_spoof("webgl", firefox_webgl_init_script(seed)), '
            "unconditional. Closed by PS-78; before it, the readPixels "
            "perturbation was not merely unwired on this engine but "
            "UNREACHABLE.",
        ),
    },
    "gpu": {
        "chromium": (
            COVERED,
            "build_gpu_extension, unconditional AT THE CALL SITE — but the "
            "extension STANDS ITSELF DOWN on the arms in "
            "ENGINE_AUTHORED_IDENTITY_ARMS. So this cell is PER ARM, not per "
            "engine, and flattening it would restate the exact unqualified "
            "claim gpu_ext.py now carries a warning against.",
        ),
        "firefox": (
            NOT_COVERED_RECORDED,
            "So on windows and macos the engine ALREADY authors a plausible, "
            "seed-derived",
        ),
    },
    "canvas_ctx": {
        "chromium": (
            COVERED,
            "build_canvas_ctx_extension, unconditional at the call site; the "
            "extension enforces iOS-only from its baked OS, so a non-iOS "
            "profile's copy returns before touching getContext",
        ),
        "firefox": (
            NOT_APPLICABLE,
            "The vector restores Safari's legacy webkit-3d alias for iOS "
            "profiles, and coherence refuses engine 'firefox' with os_type "
            "'ios' twice over (Rules 1 and 2). Unreachable by construction.",
        ),
    },
    "geo": {
        "chromium": (
            COVERED,
            "build_geo_extension, on proxied profiles only. Built in DENY mode "
            "when the exit has no usable coordinates, so a proxied profile "
            "always gets it and a proxy-less one never does.",
        ),
        "firefox": (
            NOT_COVERED_RECORDED,
            "getCurrentPosition is answered LOCALLY and yields no coordinates, "
            "so there is no host position for a spoof to displace. PS-312 "
            "established this BY MEASUREMENT on a real proxied headful launch, "
            "reading the value the page's own callback receives: permission "
            "'granted' -> error code 2 (POSITION_UNAVAILABLE) in ~12ms, "
            "proxied and direct alike; the shipped 'prompt' default -> neither "
            "callback fires. The cause is the engine's own "
            "geo.provider.network.url: \"\", isolated by A/B (that one pref "
            "pointed at a reachable provider returns a position in ~18ms — "
            "which is also the positive control proving the null is real). "
            "Shipping a spoof would REPLACE a native refusal that reads clean "
            "with a JS override a detector can see, to close a hole that is "
            "not open.",
        ),
    },
    # THE REVERSE CELL. Every row above is a Chromium builder asking after
    # Firefox; this one runs the other way, which is why the matrix is not
    # simply "Firefox is missing things".
    "outer-size": {
        "chromium": (
            COVERED_ELSEWHERE,
            "COVERED, but NOT by a spoof at all — and that is what makes this "
            "cell COVERED_ELSEWHERE rather than COVERED. THE ROUTE IS A "
            "LAUNCH ARGUMENT: spawn_browser's `elif desktop_resolution is not "
            "None:` arm in src/services/browser/process.py emits "
            "--window-size=<pick> on a DESKTOP profile whose operator chose a "
            "custom resolution, so the real window is created at the size the "
            "spoofed screen claims. There is no build_outer_size_extension "
            "and no `outer_size` census key, and device_ext.py is BYTE-"
            "IDENTICAL to base — applyScreenPatch pins screen.* and does not "
            "touch outerWidth/outerHeight. "
            "THE POSITION WAS RE-DECIDED DELIBERATELY when the route moved "
            "from the extension to the launch layer, because the ground it "
            "was first chosen on had shifted. COVERED is wrong twice over: "
            "test_no_cell_claims_coverage_without_a_route requires a COVERED "
            "Chromium cell to name a builder in the census and there is none "
            "to name, and COVERED is defined above as installing a spoof "
            "'through its own route' — nothing here installs a spoof at all. "
            "NOT_COVERED_RECORDED is wrong because the vector IS covered, not "
            "declined. NOT_ESTABLISHED is wrong because a reading exists. "
            "COVERED_ELSEWHERE survives on its own stated terms — 'covered, "
            "but NOT through this engine's spoof route; the route is named in "
            "the cell' — and a launch argument is about as far from the spoof "
            "route as a route can be while still being one. Note that status "
            "is deliberately NOT machine-checked (a route outside the census "
            "cannot be), so the truth of this prose is its only compensating "
            "control; #327 round 2 left this note describing a mechanism it "
            "had just deleted, and that is exactly the failure the file's "
            "'INTENTIONAL vs UNNOTICED' premise exists to refuse. "
            "test_a_forced_desktop_launch_really_carries_the_window_cap below "
            "now holds the load-bearing half of it against real argv. "
            "#327 ESTABLISHED the cell by measurement before any spoof was "
            "written: real headful chromium under Xvfb at 1920x1080, with an "
            "operator-picked 1280x720, read outer 1919x1079 against screen "
            "1280x720 — an impossible pair on BOTH axes — while the AUTO "
            "branch on the same seed and the same window read outer 1919x1079 "
            "against screen 2560x1440 and was already coherent. AUTO is the "
            "positive control that makes the FORCED reading a finding rather "
            "than a category. "
            "WHY THE WINDOW AND NOT THE REPORTED SIZE, which is the reasoning "
            "a future agent will come here for. Three relations a real "
            "browser always satisfies: R1 outer >= inner, R2 inner <= screen, "
            "R3 outer <= screen. With live inner 1919 and a 1280 pick, R1 "
            "needs outer >= 1919 and R3 needs outer <= 1280 — an EMPTY "
            "interval. So NO value the reporting layer can emit satisfies "
            "both, and clamping outer down to the screen (which #327 tried "
            "first, in device_ext, and deleted) only chooses R1's violation "
            "instead of R3's: it reported a window smaller than its own "
            "content, oW-iW = -639, the exact signature invisible_launch.py's "
            "restore-leak probe calibrates as leaking. The root is R2, and "
            "`inner` is the real content box — not spoofable at the reporting "
            "layer without breaking layout on real pages. Capping the WINDOW "
            "fixes R2 at source and all three then hold at once: measured "
            "live on the capped FORCED profile, inner [1280,577] outer "
            "[1280,720] screen [1280,720]. (Those post-fix values were driven "
            "independently by the reviewing seat over CDP, not only by the "
            "author.) This is Firefox's own answer to the same question — "
            "_seed_window_size, #216, 'a window can't be wider than its "
            "screen' — reached here through a launch flag because Chromium "
            "has no persisted window-size seed. "
            "#167 IS NOT RE-OPENED: that leak was the FORCED branch falling "
            "through to the auto-pick and reporting ~4K, and nothing here "
            "participates in choosing W/H. Driven live rather than argued: a "
            "2560x1440 pick on a 1920x1080 display still reads screen "
            "[2560,1440], the operator's pick honoured verbatim, and the cap "
            "composes with --force-device-scale-factor=1.5 (the flag #167 was "
            "actually about) with all three relations holding. AUTO is "
            "untouched by construction — parse_resolution('auto') is None, so "
            "the arm cannot fire and no --window-size is emitted. "
            "⚠️ THE BOUND, stated so this is a bounded claim and not a flat "
            "one: the cap sizes the window AT LAUNCH. A window the operator "
            "RESIZES AFTERWARDS can still produce the impossible pair, and "
            "nothing in the tree re-asserts the relation after startup. The "
            "reporting-layer pin did not cover that case either — it merely "
            "reported a different impossible pair when it happened. The "
            "exposed population is custom-resolution desktop profiles only; "
            "AUTO is the default and is coherent on its own.",
        ),
        "firefox": (
            COVERED,
            '_install_spoof("outer-size", ...), only when a resolution was '
            "chosen (_res_overrides is not None). On Auto the window opens at "
            "the engine's own screen, where outer already agrees.",
        ),
    },
}

# The recorded reasons, and WHERE they live, as (path, verbatim quote). A
# NOT_COVERED_RECORDED cell is only honest while its reason still exists;
# ``test_recorded_reasons_still_in_tree`` re-reads each file, so deleting or
# rewording a reason turns this suite red instead of silently downgrading a
# recorded decision to an unexplained absence.
#
# The QUOTE is kept separate from the cell's note deliberately. The note is this
# file's prose and may be edited freely; the quote is the tree's own words and
# may not. Collapsing them would make an innocent rewrite of the note look like
# a tree change, and vice versa.
RECORDED_REASON_SOURCES = {
    "voice": (
        "src/services/browser/voice_ext.py",
        "Firefox engine already returns a small Windows-plausible set; this "
        "brings chromium to parity",
    ),
    "gpu": (
        "src/services/browser/gpu_ext.py",
        "So on windows and macos the engine ALREADY authors a plausible, "
        "seed-derived identity",
    ),
    # PS-312. Written into the FIREFOX ARM of spawn_browser — beside the cfg
    # dict that carries `locale` and `timezone` and not `lat`/`lon`, which is
    # exactly where a reader asks the question the reason answers.
    "geo": (
        "src/services/browser/process.py",
        "getCurrentPosition is answered LOCALLY and yields no coordinates",
    ),
    # PS-330. Written into invisible_launch.py beside the `_install_spoof`
    # calls — the place a reader asks why there is a webgl spoof, an audio
    # spoof and a locale spoof but no device one. The quote is the MEASURED
    # finding rather than the argument built on it: an argument can be
    # rewritten without changing the tree's behaviour, but this sentence is a
    # reading, and if it stops being true the cell is wrong.
    #
    # ⚠️ KEPT TO ONE SOURCE LINE, deliberately. The reason lives in a COMMENT
    # block, so every line it wraps onto begins with a `#` — and `_collapse`
    # only normalises whitespace, it does not strip comment markers. A quote
    # spanning two comment lines therefore can NEVER match, however faithfully
    # it is copied. A future reason quoted from a comment must be a single
    # line for the same reason.
    "device": (
        "src/services/browser/invisible_launch.py",
        "came back as the EMPTY STRING on every",
    ),
}

# ``device_ext``'s recorded reason is about what the CHROMIUM engine leaves
# unspoofed, not about Firefox, so it is not a Firefox cell's reason — but it is
# the sentence that explains why the Chromium builder exists at all, and it is
# re-read for the same anti-rot purpose.
DEVICE_EXT_RATIONALE = (
    "The engine spoofs deviceMemory/hardwareConcurrency but not the screen"
)


# --- census helpers: read the product's own AST ------------------------------


def _builder_census():
    """Every ``build_*_extension`` call in ``spawn_browser``, with its condition.

    Walks the PRODUCT's AST rather than matching text, and rather than importing
    a hand-written list. That is the whole drift-proofing: a thirteenth masking
    builder appears here the moment it is added to ``spawn_browser``, and the
    census tests below fail because the matrix does not know it — which is the
    parity question being asked out loud.

    Returns ``{vector: condition_source}``; ``""`` for an unconditional call and
    ``"NOT (...)"`` for one on an ``else`` branch.
    """
    src = inspect.getsource(process_mod)
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "spawn_browser"
    )
    found = {}

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                test = ast.get_source_segment(src, child.test)
                for sub in child.body:
                    walk(sub, stack + [test])
                for sub in child.orelse:
                    walk(sub, stack + [f"NOT ({test})"])
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id.startswith("build_")
                and child.func.id.endswith("_extension")
            ):
                vector = child.func.id[len("build_") : -len("_extension")]
                found[vector] = " and ".join(stack)
            walk(child, stack)

    walk(fn, [])
    return found


def _firefox_spoof_census():
    """Every ``_install_spoof`` call in ``_launch_and_watch``, with its condition,
    plus the raw ``add_init_script`` calls that BYPASS that registry.

    Two returns, because the difference is load-bearing: ``_install_spoof``'s own
    docstring calls itself "the only supported way to add a spoof to this
    engine", and a spoof that goes around it does not get the restored-tab replay
    the registry exists to provide. Nothing goes around it today (PS-302 folded
    audio, the last bypass, back in) — so the second return is expected to be
    EMPTY, and a non-empty one is the defect, not the status quo.
    """
    src = inspect.getsource(il)
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_launch_and_watch"
    )
    spoofs = {}
    raw = []

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            # Skip nested defs: the registry's own helpers call add_init_script
            # as their implementation, which is the registry working, not a
            # bypass of it.
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(child, ast.If):
                test = ast.get_source_segment(src, child.test)
                for sub in child.body:
                    walk(sub, stack + [test])
                for sub in child.orelse:
                    walk(sub, stack + [f"NOT ({test})"])
                continue
            if isinstance(child, ast.Call):
                if (
                    isinstance(child.func, ast.Name)
                    and child.func.id == "_install_spoof"
                ):
                    spoofs[ast.literal_eval(child.args[0])] = " and ".join(stack)
                elif (
                    isinstance(child.func, ast.Attribute)
                    and child.func.attr == "add_init_script"
                ):
                    raw.append(child.lineno)
            walk(child, stack)

    walk(fn, [])
    return spoofs, raw


_EXT_PREFIX = ".persona-"
_EXT_SUFFIX = "-ext"


def _ext_dir_vector(directory):
    """The vector name of one ``--load-extension`` entry.

    Separator-agnostic ON PURPOSE. ``process.py`` builds these with
    ``os.path.join``, so the SAME product code hands this parser ``/``-joined
    paths on Linux and macOS and ``\\``-joined ones on Windows — and a parser
    that splits on ``/`` alone does not fail loudly there, it returns the whole
    unsplit path as the "vector" and quietly poisons every set this file
    compares. ``tests/test_ps283_refused_launch_does_no_work`` records the same
    hazard on the same argv ("on Windows the extension list is
    ``<PROFILE>\\.persona-gpu-ext``") and normalises for the same reason.

    A name that does not fit the product's own ``.persona-<vector>-ext`` shape
    RAISES rather than being skipped: dropping it would shrink the Chromium
    column silently, which is the false green this whole file exists to refuse.
    """
    name = PurePath(directory.replace("\\", "/")).name
    if not (name.startswith(_EXT_PREFIX) and name.endswith(_EXT_SUFFIX)):
        raise AssertionError(
            f"--load-extension entry {directory!r} does not match the product's "
            f"own {_EXT_PREFIX}<vector>{_EXT_SUFFIX} shape. This parser must be "
            f"updated with it — a silently unparsed entry removes a vector from "
            f"the Chromium column and turns every negative assertion below into "
            f"a vacuous pass."
        )
    return name[len(_EXT_PREFIX) : -len(_EXT_SUFFIX)].replace("-", "_")


def _ext_vectors(args):
    """The vectors a LAUNCH actually loaded, read off its argv.

    The oracle for the Chromium column, and deliberately argv rather than source:
    the sibling matrix's rule is that a sentinel must read the layer it guards,
    and ``--load-extension`` is the layer Chromium's masking actually arrives on.

    Raises when the flag is absent or empty. Several cells below assert a
    NEGATIVE ("geo is not in this launch"), and a negative over a set this helper
    built is only a measurement while the set is real — an empty return would
    satisfy every one of them no matter what the product did.
    """
    for a in args:
        if a.startswith("--load-extension="):
            vectors = {_ext_dir_vector(d) for d in a.split("=", 1)[1].split(",") if d}
            assert vectors, "--load-extension= is present but empty"
            return vectors
    raise AssertionError(
        "this launch produced no --load-extension argument at all, so the "
        "Chromium column cannot be read from it. Every cell below would read as "
        "'not installed' — an empty measurement, not a finding."
    )


def _firefox_installed_js():
    """The concatenated SOURCE of every spoof a Firefox launch installs.

    The oracle for the Firefox column. Built by calling the SHIPPED builders, so
    it cannot drift from what the product emits — the same constraint
    ``masking_layer`` states for itself ("everything here is BUILT BY THE SHIPPED
    BUILDERS"). ``locale`` is asked for a locale, ``webgl``/``audio`` for a seed,
    and ``outer-size`` is included because this helper answers "could this engine
    be spoofing vector X anywhere", which must not be narrowed by a
    configuration.
    """
    return "\n".join(
        (
            il._language_override_script("en-US"),
            firefox_webgl_init_script(1234),
            firefox_audio_init_script(1234),
            il._outer_size_override_script(),
        )
    )


def _launch_site_code():
    """``_launch_and_watch``'s own CODE, with comments and docstrings stripped.

    The THIRD Firefox oracle, and it exists because the other two have a shared
    blind spot that a mutation demonstrates rather than a reading of the code
    suggests. ``_firefox_installed_js`` sees what the BUILDERS emit;
    ``_firefox_spoof_census`` sees ``_install_spoof`` calls keyed on their LABEL
    literal. Neither sees a spoof written INLINE at the launch site inside an
    ALREADY-REGISTERED label's payload —

        _install_spoof("audio", firefox_audio_init_script(seed)
            + "\\nnavigator.mediaDevices.enumerateDevices=()=>...")

    — because no builder emits that string and the label is legitimately
    registered. The census's own docstring names the gap ("it cannot see a spoof
    written INLINE at the launch site, since no builder emits it"), and PS-302
    is the tree's record of what that class costs: "the PS-302 bypass it took
    four vectors to notice."

    ⚠️ AST-UNPARSED, NOT ``inspect.getsource``, and the difference is the whole
    reason this helper exists as a helper. The plain module-text read is the
    oracle the NOT_ESTABLISHED sweep refuses in its own words — "a sweep over
    the module would also match a comment ABOUT a vector" — and PS-330 wrote
    exactly such a comment INSIDE this function, so the text form went red on
    the prose explaining an absence. ``ast.unparse`` drops comments and
    docstrings and keeps every executable string literal, so this reads the CODE
    and cannot be fooled by prose OR by a rename of a spoof's label.
    """
    fn = next(
        n
        for n in ast.walk(ast.parse(inspect.getsource(il)))
        if isinstance(n, ast.FunctionDef) and n.name == "_launch_and_watch"
    )
    return ast.unparse(fn)


# --- the census: the list cannot drift from the product ----------------------


def test_matrix_covers_every_shipped_masking_builder():
    # AC1. The vector list is SOURCED FROM THE BUILDERS, never hand-typed: a
    # hand-typed list asserting itself is the false-green shape this instrument
    # exists to refuse. Add a thirteenth masking builder to spawn_browser and
    # this fails until the matrix states BOTH engines' positions on it.
    shipped = set(_builder_census()) - NON_MASKING_BUILDERS
    assert shipped == set(MATRIX) - {"outer-size"}, (
        "the shipped Chromium masking builders and the matrix disagree. A new "
        "builder means a new vector: state its position on BOTH engines here."
    )


def test_search_extension_is_excluded_on_the_products_own_grounds():
    # The one subtraction the census makes, and it is not this file's opinion:
    # masking_layer excludes the same builder from persona's masking layer in
    # the same words. Pinned so the exclusion cannot quietly widen to cover a
    # masking builder somebody would rather not state a position on.
    assert "search" in _builder_census()
    assert "search" not in MATRIX
    assert (
        "a settings override rather than masking"
        in inspect.getdoc(masking_layer.build_chromium_layer)
    )


def test_chromium_conditions_match_the_source():
    # AC2. Each cell states its INSTALLATION CONDITION, re-derived from the
    # product's AST. A cell reading "covered" for a gated builder claims coverage
    # the product only sometimes provides, so moving a gate must turn this red.
    assert _builder_census() == CHROMIUM_CONDITIONS


def test_firefox_spoof_registry_matches_the_matrix():
    # The Firefox column's structural half. The registry is the engine's only
    # supported spoof route, so its contents ARE the set of vectors this engine
    # covers through it — every one of them, since PS-302 folded audio in.
    spoofs, _ = _firefox_spoof_census()
    assert spoofs == FIREFOX_SPOOF_CONDITIONS
    for label in spoofs:
        # The spoof LABEL is the matrix's vector key — asserted rather than
        # translated. The two vocabularies agree today (``outer-size`` is spelled
        # the same on both sides), and pinning that agreement is what a mapping
        # would have hidden: register a spoof under a label no cell names and
        # this fails HERE, naming the label, instead of raising a bare KeyError
        # one line down or — worse — being quietly mapped onto some other row.
        assert label in MATRIX, (
            f"{label!r} is registered as a Firefox spoof but names no row in "
            f"the matrix. Add the vector's row, or reconcile the label with the "
            f"row it belongs to — do not translate it silently."
        )
        assert MATRIX[label]["firefox"][0] == COVERED, (
            f"{label} is registered as a Firefox spoof but the matrix does not "
            f"say COVERED"
        )


def test_firefox_spoofs_all_go_through_the_registry():
    # The route, pinned as a route and deliberately NOT as a coverage cell.
    # This replaces ``test_firefox_audio_bypasses_the_spoof_registry``, which
    # pinned the OPPOSITE (audio outside the registry, exactly one raw
    # ``add_init_script``) and instructed its own successor in those words: "if
    # PS-84 lands, THIS test is the one that goes red ... fold the route in,
    # delete this test's raw-call expectation in the same commit, and add
    # 'audio' to FIREFOX_SPOOF_CONDITIONS." PS-302 landed that fold. The
    # inversion is the record.
    #
    # The coverage cell above is untouched by that landing, which is the point
    # of having separated them: audio was COVERED before and is COVERED now.
    spoofs, raw = _firefox_spoof_census()
    assert "audio" in spoofs
    assert raw == [], (
        f"a raw add_init_script appeared on the Firefox launch path (line(s) "
        f"{raw}). ZERO are expected: every spoof goes through _install_spoof, "
        "which is the only route that also replays into ALREADY-OPEN tabs. A "
        "raw call is a spoof that silently misses the restored-tab replay — "
        "the PS-78 hole the registry exists to close, and the PS-302 bypass it "
        "took four vectors to notice."
    )
    assert MATRIX["audio"]["firefox"][0] == COVERED


# --- the Chromium column, read off a real launch's argv ----------------------


def test_chromium_column_desktop_proxied(monkeypatch, tmp_path):
    # The reference column: a proxied desktop profile gets every unconditional
    # vector, plus device (non-mobile branch) and geo (proxied).
    vectors = _ext_vectors(
        _spawn_chromium_args(
            monkeypatch,
            tmp_path,
            Profile(name="masking-matrix-proxied", proxy="p1"),
            store=_StoreWithCheckedProxy,
        )["args"]
    )
    unconditional = {v for v, c in CHROMIUM_CONDITIONS.items() if c == ""}
    assert unconditional <= vectors
    assert "device" in vectors and "mobile" not in vectors
    assert "geo" in vectors


def test_chromium_geo_cell_is_proxy_conditional(monkeypatch, tmp_path):
    # The condition, asserted rather than merely written down. A direct profile
    # leaves geolocation untouched, so "geo: covered" is true of a PROXIED
    # profile and false of this one — exactly what the cell's condition says.
    #
    # ⚠️ THE NEGATIVE IS ANCHORED BY A POSITIVE, and that pairing is the point of
    # this test rather than a belt-and-braces flourish. ``"geo" not in vectors``
    # is satisfied by ANY set that does not contain the token — including an
    # empty one, and including one of unparsed paths. Round 1 of this ticket
    # shipped exactly that: a ``/``-only split left every Windows entry mangled,
    # four sibling tests failed loudly and THIS one passed vacuously, and it
    # would have gone green with the proxy gate deleted from the product
    # entirely. So the direct launch must first be shown to be a REAL reading
    # (it installs the unconditional vectors), and the proxied companion must be
    # shown to contain the very token whose absence is the claim. Then, and only
    # then, does the absence measure the gate.
    direct = _ext_vectors(
        _spawn_chromium_args(
            monkeypatch, tmp_path, Profile(name="masking-matrix-direct")
        )["args"]
    )
    proxied = _ext_vectors(
        _spawn_chromium_args(
            monkeypatch,
            tmp_path,
            Profile(name="masking-matrix-geo-proxied", proxy="p1"),
            store=_StoreWithCheckedProxy,
        )["args"]
    )
    unconditional = {v for v, c in CHROMIUM_CONDITIONS.items() if c == ""}
    assert unconditional <= direct, (
        "the direct launch did not install the unconditional vectors, so this "
        "reading is malformed and the absence below would measure nothing"
    )
    assert "geo" in proxied, (
        "the proxied companion does not carry 'geo', so either the parser or "
        "the product changed — the absence below is not evidence of the gate"
    )
    assert "geo" not in direct
    assert CHROMIUM_CONDITIONS["geo"] == "proxy"


def test_chromium_mobile_and_device_are_mutually_exclusive(monkeypatch, tmp_path):
    # The other conditional pair, and they are BRANCHES rather than two
    # independent gates: no profile ever gets both, and every profile gets one.
    # iOS rather than Android on purpose — an Android profile must advertise the
    # installed engine's Chromium version and refuses to launch when it cannot
    # be read, which is an environment refusal, not a matrix reading.
    mobile = _ext_vectors(
        _spawn_chromium_args(
            monkeypatch,
            tmp_path,
            Profile(name="masking-matrix-mobile", os_type="ios", device_type="mobile"),
        )["args"]
    )
    assert "mobile" in mobile and "device" not in mobile

    desktop = _ext_vectors(
        _spawn_chromium_args(
            monkeypatch, tmp_path, Profile(name="masking-matrix-desktop")
        )["args"]
    )
    assert "device" in desktop and "mobile" not in desktop


def test_chromium_search_is_the_only_platform_gated_builder(monkeypatch, tmp_path):
    # The platform gate, asserted on the arm it removes something from. Also
    # pins that it removes NOTHING ELSE: a masking vector that silently vanished
    # on Linux would be a per-platform coverage hole no cell above states.
    linux = _ext_vectors(
        _spawn_chromium_args(
            monkeypatch, tmp_path, Profile(name="masking-matrix-linux"), linux=True
        )["args"]
    )
    other = _ext_vectors(
        _spawn_chromium_args(
            monkeypatch, tmp_path, Profile(name="masking-matrix-nonlinux")
        )["args"]
    )
    assert other - linux == {"search"}


# --- the Firefox column, read off the source the builders emit ---------------


def test_firefox_covered_cells_emit_real_spoof_source():
    # The Firefox column's behavioural half: every cell claiming COVERED must
    # correspond to a builder that actually emits something. An empty script
    # would be a cell claiming coverage that delivers nothing — which
    # masking_layer guards against in its own arm for the same reason.
    assert il._language_override_script("en-US").strip()
    assert firefox_webgl_init_script(1234).strip()
    assert firefox_audio_init_script(1234).strip()
    assert il._outer_size_override_script().strip()


def test_firefox_not_established_cells_are_not_quietly_covered():
    # AC3, asserted from the other side. A "position not established" cell is an
    # honest unknown, and it stops being honest the moment the engine QUIETLY
    # starts covering the vector. Each token below is the identifying API of its
    # vector; a hit means Firefox grew coverage and the cell must be rewritten
    # rather than left reading "not established".
    #
    # TWO oracles, because either alone has a blind spot. The token sweep reads
    # the SOURCE THE BUILDERS EMIT — the layer this engine's masking arrives on,
    # and the right place to catch a builder that grew a vector — but it cannot
    # see a spoof written INLINE at the launch site, since no builder emits it.
    # The registry census can see exactly that, and cannot see the first. A cell
    # is "not established" only while BOTH agree nothing covers it.
    #
    # Read off emitted source rather than off module text on purpose: a sweep
    # over the module would also match a comment ABOUT a vector.
    js = _firefox_installed_js()
    spoofs, _ = _firefox_spoof_census()
    tokens = {
        "stealth": ("downlinkMax", "ContentIndex"),
        "measuretext": ("measureText",),
        # "geo" left this dict in PS-312, which established the cell by
        # measurement. Its absence-guard did NOT go with it: the same two
        # tokens are still swept in
        # ``test_the_recorded_geo_absence_is_still_an_absence`` below, where
        # they now guard a NOT_COVERED_RECORDED cell instead of an unestablished
        # one. Deleting the guard along with the entry would have traded a
        # stated unknown for an unwatched decision.
        #
        # "device" left it in PS-330 by exactly the same route and under
        # exactly the same rule: ``enumerateDevices`` is still swept, in
        # ``test_the_recorded_device_absence_is_still_an_absence``, against
        # both oracles.
    }
    for vector, names in tokens.items():
        assert MATRIX[vector]["firefox"][0] == NOT_ESTABLISHED
        assert vector not in spoofs, (
            f"a Firefox spoof is now registered for {vector!r}, so the cell is "
            f"no longer 'position not established'. State the position."
        )
        for name in names:
            assert name not in js, (
                f"Firefox now emits {name!r}, so the {vector!r} cell is no "
                f"longer 'position not established'. State the position."
            )


def test_the_recorded_geo_absence_is_still_an_absence():
    # PS-312's cell, guarded from the same side the NOT_ESTABLISHED sweep above
    # guards its own — and this test exists because moving the cell must NOT
    # cost it that guard. The decision recorded in `process.py`'s Firefox arm is
    # "persona ships NO Firefox geolocation spoof, because the engine already
    # refuses locally". A spoof that quietly appeared would make the recorded
    # reason describe a tree that no longer matches it, exactly as a reworded
    # reason would — so both oracles the sweep above uses are applied here:
    # the emitted source, and the spoof registry.
    js = _firefox_installed_js()
    spoofs, _ = _firefox_spoof_census()
    assert MATRIX["geo"]["firefox"][0] == NOT_COVERED_RECORDED
    assert "geo" not in spoofs, (
        "a Firefox geo spoof is now registered, contradicting the recorded "
        "decision in process.py's Firefox arm that persona ships none. Either "
        "the engine's behaviour changed (RE-MEASURE, then restate the cell as "
        "COVERED and give it a FIREFOX_SPOOF_CONDITIONS entry) or the spoof is "
        "the defect."
    )
    for name in ("geolocation", "getCurrentPosition"):
        assert name not in js, (
            f"Firefox now emits {name!r}, so the recorded 'no spoof ships' "
            f"decision no longer describes this tree. Restate the cell."
        )


def test_the_recorded_device_absence_is_still_an_absence():
    # PS-330's cell, guarded exactly as PS-312's is, and for the same reason:
    # moving a cell out of the unknown set must not cost it its guard. The
    # decision recorded in ``invisible_launch.py`` beside the ``_install_spoof``
    # calls is "persona ships NO Firefox mediaDevices spoof, because the engine
    # already answers with a constant synthetic roster carrying empty ids". A
    # spoof that quietly appeared would make that recorded reason describe a
    # tree that no longer matches it.
    #
    # THREE oracles, because each of the first two has a blind spot the other
    # cannot cover and BOTH share a third. The emitted source catches a BUILDER
    # that grew the vector; the registry census catches a NEW LABEL registered
    # for it; and ``_launch_site_code`` catches the case neither can see — an
    # override concatenated INLINE at the launch site into an already-registered
    # label's payload, which emits from no builder and adds no label. That third
    # case is not hypothetical: it is demonstrable by mutation, and before
    # PS-330's second round it was the one hole in this guard.
    js = _firefox_installed_js()
    spoofs, _ = _firefox_spoof_census()
    launch_code = _launch_site_code()
    assert MATRIX["device"]["firefox"][0] == NOT_COVERED_RECORDED
    assert "device" not in spoofs, (
        "a Firefox device spoof is now registered, contradicting the recorded "
        "decision in invisible_launch.py that persona ships none. Either the "
        "engine's behaviour changed (RE-MEASURE with "
        "scripts/ps330_ff_devices_reading.py, then restate the cell as COVERED "
        "and give it a FIREFOX_SPOOF_CONDITIONS entry) or the spoof is the "
        "defect."
    )
    assert "enumerateDevices" not in js, (
        "Firefox now emits 'enumerateDevices', so the recorded 'no spoof "
        "ships' decision no longer describes this tree. Restate the cell."
    )
    # The inline oracle. A positive control rides with it: this helper strips
    # comments, and an empty or comment-only return would satisfy the negative
    # above no matter what the launch site did — the vacuous-pass shape this
    # file's header calls out by name. ``_res_overrides`` is executable code in
    # this function and is asserted below as the screen half's own sentinel, so
    # its presence here proves the channel carries the launch site's CODE.
    assert "_res_overrides = _context_overrides_for(w, h)" in launch_code, (
        "the AST-unparsed launch site does not contain a statement known to be "
        "in it, so the negative below would pass vacuously. Fix this helper "
        "before trusting the absence."
    )
    assert "enumerateDevices" not in launch_code, (
        "'enumerateDevices' now appears in _launch_and_watch's own CODE (not "
        "its comments — this oracle is AST-unparsed). A Firefox device spoof "
        "written inline at the launch site emits from no builder and registers "
        "no new label, so neither oracle above can see it. Either the engine's "
        "behaviour changed (RE-MEASURE with "
        "scripts/ps330_ff_devices_reading.py, then restate the cell) or the "
        "spoof is the defect."
    )


def test_firefox_voice_and_canvas_ctx_absences_are_not_accidental():
    # The two vectors whose Firefox position is a RECORDED decision and a
    # CONSTRUCTION constraint respectively — asserted here as absences so the
    # pair reads as one question with two different answers, not as two gaps.
    js = _firefox_installed_js()
    assert "getVoices" not in js
    assert MATRIX["voice"]["firefox"][0] == NOT_COVERED_RECORDED
    assert "webkit-3d" not in js
    assert MATRIX["canvas_ctx"]["firefox"][0] == NOT_APPLICABLE


def test_firefox_native_cloak_is_the_native_vectors_actual_route():
    # The COVERED_ELSEWHERE cell, asserted rather than asserted-about. The cloak
    # is not registered as a spoof — it is a PRELUDE inlined into the others —
    # so a registry census can never see it, and a matrix built only from the
    # registry would have reported "native" as a Firefox gap. It is not one.
    spoofs, _ = _firefox_spoof_census()
    assert "native" not in spoofs
    cloak = il._native_cloak_js()
    assert cloak.strip()
    for script in (
        il._language_override_script("en-US"),
        il._outer_size_override_script(),
    ):
        assert cloak in script, "the native cloak is no longer inlined as a prelude"
    assert MATRIX["native"]["firefox"][0] == COVERED_ELSEWHERE


def test_firefox_device_screen_half_is_pinned_at_the_engine_layer():
    # Why the device cell reads NOT_COVERED_RECORDED rather than COVERED: the
    # screen half genuinely IS covered, by an engine-layer pin rather than a
    # script, while the mediaDevices half ships nothing and PS-330 recorded why.
    # One flat "covered" would still claim too much — the two halves are
    # answered by different routes, and only one of them is a route persona
    # takes.
    src = inspect.getsource(il._launch_and_watch)
    assert 'kwargs["pin"] = {' in src
    for key in ("screen.width", "screen.height", "screen.avail_width"):
        assert f'"{key}"' in src
    # ...and it is CONDITIONAL, which is the other half of why this is not a
    # flat "covered": a profile on Auto chooses no resolution and gets no pin.
    assert "_res_overrides = _context_overrides_for(w, h)" in src
    # ⚠️ THE mediaDevices HALF IS ASSERTED IN
    # ``test_the_recorded_device_absence_is_still_an_absence``, NOT HERE — a
    # MOVE, and one that had to be paid for rather than merely declared. This
    # used to read ``assert "enumerateDevices" not in src`` — a sweep over the
    # function's MODULE TEXT, which is precisely the oracle the NOT_ESTABLISHED
    # sweep above refuses in its own words: *"Read off emitted source rather
    # than off module text on purpose: a sweep over the module would also match
    # a comment ABOUT a vector."* PS-330 wrote exactly such a comment — the
    # recorded reason for shipping no device spoof lives beside the
    # ``_install_spoof`` calls inside this very function — so the old form went
    # red on the prose explaining why the thing it looked for is absent.
    #
    # ROUND 1 OF PS-330 DROPPED THE LINE AND CALLED THE TWO REMAINING ORACLES
    # "STRICTLY STRONGER". THEY WERE NOT, and the reviewer demonstrated it by
    # mutation rather than by argument: an override concatenated inline into the
    # ``audio`` spoof's payload — no new label, no builder touched — was RED at
    # the merge-base on this very line and fully GREEN with it deleted. The
    # emitted-source oracle cannot see it (no builder emits the string) and the
    # census cannot see it (the label is legitimately registered). That is the
    # PS-302 class, and the deleted line was the only thing watching for it.
    #
    # The coverage is restored on an oracle that survives BOTH problems:
    # ``_launch_site_code`` AST-unparses this function, which drops comments (so
    # the recorded reason is invisible to it) and keeps executable code (so the
    # inline mutation is not). The screen-half reads above are on the text form
    # deliberately: they are POSITIVE assertions, where a comment match can only
    # cost a false green on a line that is separately proven by the pin's own
    # keys — whereas the device read is a NEGATIVE, where an oracle that sees
    # too much is the whole failure.
    assert MATRIX["device"]["firefox"][0] == NOT_COVERED_RECORDED


# --- the reverse cell --------------------------------------------------------


def test_outer_size_runs_the_other_direction():
    # AC5's second half. The matrix is not "Firefox is missing nine things": at
    # least one cell has Firefox covered and CHROMIUM answered from the other
    # direction. #327 ESTABLISHED it by measurement and then CAPPED THE WINDOW
    # at launch, so the cell is COVERED_ELSEWHERE — a real route, named in the
    # cell, that is not a spoof route at all.
    spoofs, _ = _firefox_spoof_census()
    assert "outer-size" in spoofs
    assert MATRIX["outer-size"]["firefox"][0] == COVERED
    assert MATRIX["outer-size"]["chromium"][0] == COVERED_ELSEWHERE
    # STILL ASSERTED, and it is the half that keeps its teeth: the census key is
    # derived from a `build_<vector>_extension` NAME, so a future
    # `build_outer_size_extension` would arrive as `outer_size` (UNDERSCORE)
    # while this matrix key is `outer-size` (HYPHEN) — B4's subtraction would
    # drop the hyphen and the underscore would show up as an unexplained extra.
    # Keeping this assertion means that builder cannot appear without someone
    # reconciling the two spellings deliberately. #327 did NOT add one: the
    # route is a launch argument, so the census is unchanged from base.
    assert "outer_size" not in _builder_census()


def test_a_forced_desktop_launch_really_carries_the_window_cap(
    monkeypatch, tmp_path
):
    # THE CELL ABOVE, HELD TRUE RATHER THAN MERELY TRUE. COVERED_ELSEWHERE is
    # deliberately not machine-checked — a route outside the builder census
    # cannot be — so the cell's prose is normally the only thing standing
    # behind it. That is not hypothetical here: #327 round 2 moved this vector
    # from the device extension to a launch argument and left the cell
    # describing the mechanism it had just deleted, green the whole time. A
    # future agent would have gone to applyScreenPatch looking for a pin,
    # found nothing, and been unable to tell whether it was removed, never
    # landed, or moved — which is precisely the INTENTIONAL-vs-UNNOTICED
    # confusion this file exists to refuse.
    #
    # So the load-bearing half of the claim is asserted against the argv a
    # LAUNCH actually produces, using the same harness the Chromium column is
    # read from everywhere else in this file. It cannot prove the cell's prose
    # is well-written; it does mean the named route must still exist.
    forced = _spawn_chromium_args(
        monkeypatch,
        tmp_path,
        Profile(name="masking-matrix-forced-res", resolution="1280x720"),
    )["args"]
    assert "--window-size=1280,720" in forced, (
        "the outer-size cell names spawn_browser's --window-size arm as its "
        "route, and a FORCED desktop launch no longer emits it. Either the "
        "route moved (RESTATE THE CELL — do not leave it naming this one) or "
        "the cap regressed."
    )

    # AUTO is the default and is the control: it is coherent without help, and
    # the cell says so. `parse_resolution("auto")` is None, so the arm cannot
    # fire — asserted rather than assumed, because a cap that fired here would
    # resize every operator's window on a profile that never asked for it.
    auto = _spawn_chromium_args(
        monkeypatch, tmp_path, Profile(name="masking-matrix-auto-res")
    )["args"]
    assert not [a for a in auto if a.startswith("--window-size")], (
        "an AUTO desktop launch now emits --window-size. The cell claims AUTO "
        "is untouched by construction; it no longer is."
    )


def test_outer_size_is_conditional_on_a_chosen_resolution():
    # The Firefox side's condition, which the cell states and this asserts: on
    # Auto the override is not installed at all, because outer already agrees
    # with the engine's own screen.
    spoofs, _ = _firefox_spoof_census()
    assert spoofs["outer-size"] == "_res_overrides is not None"


# --- the ⭐ constant drift, surfaced rather than normalized away -------------


def test_masking_layer_vector_vocabulary_is_drifted_from_the_builders():
    # AC5's first half, and the finding that decided this file's construction.
    #
    # masking_layer declares ELEVEN vector constants against TWELVE masking
    # builders: 'mobile' has no constant. That is why the matrix sources its
    # list from the BUILDERS and not from these constants — sourcing it here
    # would have silently dropped a vector and produced exactly the false green
    # an instrument like this exists to prevent.
    #
    # Pinned as a CHARACTERIZATION, not a demand: this file does not close the
    # drift (that is product-shaped work with its own evidence). Close it on
    # purpose and this test goes red — delete the expected difference in the
    # same commit, and that deletion IS the record.
    constants = {
        v
        for k, v in vars(masking_layer).items()
        if k.isupper() and isinstance(v, str) and k == v.upper().replace("-", "_")
    }
    shipped = set(_builder_census()) - NON_MASKING_BUILDERS
    assert shipped - constants == {"mobile"}, (
        "the masking_layer vector vocabulary and the shipped builders no longer "
        "differ by exactly {'mobile'}. If the drift was closed on purpose, "
        "update this cell; if a NEW vector drifted, that is the defect."
    )
    assert constants - shipped == set()


def test_firefox_vectors_constant_names_only_registry_spoofs():
    # The companion reading. masking_layer's FIREFOX_VECTORS is three names, and
    # those three are exactly the registry spoofs MINUS outer-size — which that
    # module excludes deliberately, because a harness profile chooses no
    # resolution so the product would not install it either. Pinned so the two
    # cannot drift apart without somebody noticing.
    #
    # The right-hand side used to carry an explicit `| {"audio"}` union, because
    # audio was in FIREFOX_VECTORS while sitting OUTSIDE the registry. PS-302
    # folded it in, so the union became a no-op and is dropped: the constant is
    # now exactly the registry minus the one deliberate exclusion, which is a
    # stronger statement than the same set reached by adding a term back.
    spoofs, _ = _firefox_spoof_census()
    assert set(masking_layer.FIREFOX_VECTORS) == set(spoofs) - {"outer-size"}


# --- the per-arm GPU cell, kept qualified ------------------------------------


def test_gpu_cell_is_per_arm_not_per_engine():
    # The cell gpu_ext.py explicitly asks not to be flattened. Its call site is
    # unconditional, so a census alone reads "covered everywhere" — and that is
    # the unqualified claim the file now carries a measured warning against
    # (PS-161, 15 seeds/arm: linux HOLDS, windows STALE, macos STALE). On
    # windows the extension stands ITSELF down and the engine authors the pair.
    assert ENGINE_AUTHORED_IDENTITY_ARMS == frozenset({"windows"})
    assert _builder_census()["gpu"] == ""
    assert "PER ARM" in MATRIX["gpu"]["chromium"][1]


def test_gpu_firefox_cell_sits_on_the_engine_authored_arm():
    # And the reason the Firefox side of that cell is a RECORDED non-coverage
    # rather than a gap: coherence pins the Firefox engine to os_type
    # 'windows', which is precisely the arm where the engine already authors a
    # plausible seed-derived identity. Coherent by construction.
    assert coherence.FIREFOX_OS == "windows"
    assert coherence.FIREFOX_OS in ENGINE_AUTHORED_IDENTITY_ARMS
    assert MATRIX["gpu"]["firefox"][0] == NOT_COVERED_RECORDED


# --- the constraints the NOT_APPLICABLE cells rest on ------------------------


def test_not_applicable_cells_rest_on_a_live_coherence_rule():
    # A NOT_APPLICABLE cell is only honest while the constraint holds, so the
    # constraint is ASSERTED rather than cited. Relax it and these two cells
    # become live questions — which is exactly when this must go red.
    #
    # ⚠️ IT IS A DISJUNCTION, AND SAYING "Rule 1" ALONE WOULD BE HALF TRUE —
    # measured, not assumed: disabling Rule 1 by itself leaves both cells
    # perfectly refused, because Rule 2 (firefox may carry no os_type but
    # 'windows') independently rejects 'android' and 'ios'. Both rules must go
    # before these cells open. So the assertion is on the OUTCOME the cells
    # actually depend on — "no Firefox profile can be mobile" — and not on
    # whichever rule happens to be answering today.
    for os_type in ("android", "ios"):
        assert not coherence.is_coherent(os_type, "firefox")
        assert coherence.coherent_engine(os_type, "firefox") == "chromium"
    for vector in ("mobile", "canvas_ctx"):
        assert MATRIX[vector]["firefox"][0] == NOT_APPLICABLE


def test_firefox_os_type_absence_is_coherent_not_a_gap():
    # The honesty clause, pinned. os_type legitimately appears zero times on the
    # Firefox launch path, and that is NOT an uncovered vector: the engine
    # reports Windows regardless, so coherence refuses every other os_type for
    # it. A future reader must not read the absence as a hole.
    assert not coherence.is_coherent("macos", "firefox")
    assert not coherence.is_coherent("linux", "firefox")
    assert coherence.is_coherent("windows", "firefox")


# --- anti-rot: the recorded reasons must still exist -------------------------


def _collapse(text):
    """Whitespace-collapsed text, for re-reading a quote out of wrapped prose."""
    return " ".join(text.split())


def test_recorded_reasons_still_in_tree():
    # A NOT_COVERED_RECORDED cell quotes a reason the TREE holds. If that
    # sentence is deleted or reworded, the cell silently degrades into an
    # unexplained absence — the exact confusion this matrix exists to remove —
    # so the quote is re-read at its source on every run.
    # Compared with whitespace COLLAPSED on both sides, deliberately. The quotes
    # are prose in a wrapped docstring, so a re-wrap moves the newlines without
    # changing a word — and a test that went red on a re-flow would train its
    # reader to re-quote reflexively, which is the habit that lets a genuine
    # rewording through. A word change still fails.
    for vector, (path, quote) in RECORDED_REASON_SOURCES.items():
        assert MATRIX[vector]["firefox"][0] == NOT_COVERED_RECORDED
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert _collapse(quote) in _collapse(text), (
            f"the recorded reason for the {vector!r} cell is gone from "
            f"{path}. Either restore it or restate the cell's position — "
            f"do not leave a cell citing a reason the tree no longer holds."
        )


def test_device_ext_rationale_still_in_tree():
    # Not a Firefox cell's reason (it describes what the CHROMIUM engine leaves
    # unspoofed), but it is the sentence that justifies the Chromium builder
    # existing at all, and the matrix's device row leans on it. Re-read for the
    # same anti-rot purpose, and kept separate so the two are not conflated.
    text = (REPO_ROOT / "src/services/browser/device_ext.py").read_text(
        encoding="utf-8"
    )
    assert _collapse(DEVICE_EXT_RATIONALE) in _collapse(text)


# --- shape invariants: an unknown may never read as coverage -----------------


@pytest.mark.parametrize("vector", sorted(MATRIX))
def test_every_cell_states_a_position_and_its_evidence(vector):
    # AC3's structural guarantee. Every cell on both engines carries one of the
    # five positions and a non-empty note — there is no way to add a vector to
    # this matrix and leave a side blank, and a blank would read as "no problem"
    # rather than as "nobody looked".
    positions = {
        COVERED,
        COVERED_ELSEWHERE,
        NOT_COVERED_RECORDED,
        NOT_APPLICABLE,
        NOT_ESTABLISHED,
    }
    row = MATRIX[vector]
    assert set(row) == {"chromium", "firefox"}
    for engine in ("chromium", "firefox"):
        position, note = row[engine]
        assert position in positions
        assert note.strip(), f"{vector}/{engine} states a position with no evidence"


def test_the_open_cells_are_the_deliverable_and_are_named():
    # The headline, pinned as data so it cannot drift out of the docstring. These
    # are the vectors nobody had established a position on — the actual finding,
    # and the thing a future slice closes ONE AT A TIME, deleting each name here
    # in the commit that establishes it.
    unknown = {
        f"{engine}:{vector}"
        for vector, row in MATRIX.items()
        for engine in ("chromium", "firefox")
        if row[engine][0] == NOT_ESTABLISHED
    }
    assert unknown == {
        "firefox:stealth",
        "firefox:measuretext",
        # "firefox:device" was here until PS-330 established it BY MEASUREMENT
        # — a real headful launch reading what a page receives from
        # enumerateDevices(), on three profiles with three distinct seeds.
        # Its deletion IS that commit's record; the cell now reads
        # NOT_COVERED_RECORDED and its reason is re-read out of the tree by
        # ``test_recorded_reasons_still_in_tree``, with the absence itself
        # still guarded from both sides by
        # ``test_the_recorded_device_absence_is_still_an_absence``.
        #
        # "firefox:geo" was here until PS-312 established it BY MEASUREMENT.
        # Its deletion IS that commit's record; the cell now reads
        # NOT_COVERED_RECORDED and its reason is re-read out of the tree by
        # ``test_recorded_reasons_still_in_tree``.
        #
        # "chromium:outer-size" was here until #327 established it, likewise by
        # measurement and likewise in the commit that deleted it. Two cells left
        # this set on the same day by the same route, which is what the set is
        # for: it shrinks only against a reading.
    }


def test_no_cell_claims_coverage_without_a_route():
    # The invariant that makes the matrix worth asserting: a COVERED cell must
    # be traceable to a route the product actually takes — a Chromium builder or
    # a Firefox spoof — and never to prose. A cell that cannot name one is
    # COVERED_ELSEWHERE (which names a different route) or NOT_ESTABLISHED.
    builders = _builder_census()
    spoofs, _ = _firefox_spoof_census()
    for vector, row in MATRIX.items():
        if row["chromium"][0] == COVERED:
            assert vector in builders, f"{vector} claims Chromium coverage with no builder"
        if row["firefox"][0] == COVERED:
            # No escape hatch. This used to read `or vector == "audio"`,
            # because audio claimed Firefox coverage through a raw
            # add_init_script that no registry census could see. PS-302 removed
            # the bypass, so the exemption it needed is gone too — leaving it
            # would let a future raw-installed vector claim COVERED unchecked.
            assert vector in spoofs, (
                f"{vector} claims Firefox coverage with no registered spoof"
            )
