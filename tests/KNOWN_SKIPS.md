# Known skips — the measured position

A test that fails is a message. A test that silently declines to run is the
*absence* of a message, indefinitely, and it looks exactly like success.

This file is the baseline that makes the next unexpected skip legible. Without
it, the run's new skip-reporting just produces a list nobody can rank: you
cannot tell "that one always skips, it needs Windows" from "that one used to
run here and stopped".

**This is not a number to drive down.** A test that correctly skips because a
contributor does not have Node is working as designed. Reducing the count by
making a skip silent again is the exact defect this record exists to prevent.

## How to read a run

Skip reasons print on every run — no flag, nothing to know:

```
$ python -m pytest -q
...
SKIPPED [2] tests/test_ff_language_override.py:512: playwright not installed
```

To make a skip *consequential* on a machine provisioned to run the thing, see
[Declaring capabilities](#declaring-capabilities) below.

## Measured position

Measured on the CI-equivalent Linux container at `96e574b` with a clean tree:
**43 failed, 2377 passed, 17 skipped**.

> **This table is a reading of one environment, not the universal position.**
> It was measured on a container where `pip install .` had *not* been run. A
> machine that has installed the project sees a materially different set — an
> independent run during review, on a container carrying `playwright 1.61.0`
> and `invisible_core`, reported **0 failed, 14 skipped**, and there the
> `browser` skips come from the *launch* guard rather than the import guard.
> Read a difference from this table as "a different environment" first, and
> only then as a regression; what makes a skip legible is the reason it gives,
> which prints on every run, not this snapshot's arithmetic.

> The 43 failures are this container missing `invisible_core`, `aiohttp` and
> `PIL` — they are the same before and after this change and are not caused by
> it. A machine that has run `pip install .` does not see them.

There are **21 skip guards across 12 test files** in `tests/` (9
`importorskip`, 12 `pytest.skip(`). Only the 16 sites below fired here — the
rest ran, which is itself worth knowing.

### Structural — a dependency this project does not require of a contributor

These are expected to skip on an ordinary developer machine, and expected to
skip in the current CI. Nothing is wrong when they do.

| Where | Reason given | Capability |
|---|---|---|
| `test_ff_language_override.py:512` (×2) | `playwright not installed` | `browser_firefox` |
| `test_ff_language_override.py:527` | `playwright not installed` | `browser_firefox` |
| `test_invisible_launch.py:718` | `could not import 'invisible_playwright'` | `engine` |
| `test_invisible_launch.py:2051` | `could not import 'invisible_core'` | `engine` |
| `test_invisible_launch.py:5203` | `could not import 'invisible_playwright'` | `engine` |
| `test_assets.py:40`, `:81` | `could not import 'PIL.Image'` | — (dev extra) |

The three `test_ff_language_override.py` skips are the ones that matter most.
They are the real-Firefox probes — the suite's strongest evidence, checked
against the only oracle that counts (SpiderMonkey itself), including the sweep
asserting that no function in the realm betrays itself by shape. **So far as
can be established they have never run in CI**, and the suite reported green
the whole time.

### Platform-bound — this OS cannot host the test

Correct everywhere except the named platform. Not a provisioning gap on Linux.

| Where | Reason given |
|---|---|
| `test_app_update.py:819` | live relaunch-bat run needs Windows + the .NET csc compiler |
| `test_invisible_launch.py:971` | exercises the real Windows PowerShell/WMI pid query path |
| `test_invisible_launch.py:1564` | exercises the real EnumWindows/ctypes enumeration path |
| `test_invisible_launch.py:3867` | exercises the real Toolhelp/PEB process scan |
| `test_update_verify.py:217`, `:238` | exercises the real Windows `apply_and_restart` `os._exit` path |
| `test_main_utf8_fs.py:76` | this platform's C locale still yields a UTF-8 filesystem encoding |

### Environment-bound — a machine that is simply missing something

| Where | Reason given |
|---|---|
| `test_apply_restart.py:131`, `:136` | no real AppImage available |

### Guards that did NOT fire here

Worth recording, because a *future* skip from one of these is a change of
state rather than the status quo:

* the **node** probes (`native_mask_probe.py:141`, `test_worker_wrap.py:250`,
  `test_ff_language_override.py:333`, `test_gpu_ext.py:529`/`:974`,
  `test_title_ext.py:251`, `test_canvas_ctx_ext.py:155`/`:355`) — `node` is on
  PATH here, so all of these ran;
* `test_geo_disproven_refusal.py:575` (`flet`) — installed here;
* `test_cert_terminator.py:580` (running as root) and
  `test_peer_auth.py:49` (`SO_PEERCRED`) — neither condition held.

## Which guard fires in CI — measured, not reasoned

The browser probes are guarded **twice**, and it matters which one fires,
because it decides what a future CI slice has to provision:

```python
sync_playwright = pytest.importorskip(              # the import guard
    "playwright.sync_api", reason="playwright not installed").sync_playwright
...
except Exception as exc:
    pytest.skip(f"firefox not runnable here: {exc}")   # the launch guard
```

`pyproject.toml` pins the engine as `invisible_playwright` from a git ref, and
that package is a playwright fork — so whether it satisfies
`import playwright.sync_api` was an open question. Measured at the pinned
commit `353df4f`:

* it ships **no** `playwright` package of its own (only `src/invisible_playwright`);
* it **hard-depends on upstream** `playwright>=1.55,<=1.61.0`.

Confirmed by installing that range in a clean venv: `import playwright.sync_api`
succeeds, and `p.firefox.launch()` then fails with
`Executable doesn't exist at .../firefox-1532/firefox/firefox`.

**Therefore: wherever `pip install .` has run — which includes the release
pipeline — the import guard PASSES and the LAUNCH guard is the one that fires.**
The pip package is not the missing piece; the **browser binary** is. A CI slice
that provisions only the Python package will not make these probes run, and
will still report green.

(The `playwright not installed` reason in the table above is this bare
container, where `pip install .` has not run. It is not what CI sees.)

## Declaring capabilities

A skip is honest on a laptop and dishonest on a machine provisioned to run the
thing. An environment says what it supports:

```bash
PERSONA_REQUIRED_CAPABILITIES=browser python -m pytest      # or --require-capability browser
```

In that environment, a skip of the browser probes becomes a **failure** naming
what was missing and how to provision it. Declaring nothing changes nothing:
an ordinary developer run still skips and still passes.

Capabilities: `browser`, `browser_firefox`, `browser_chromium`, `node`,
`engine`, `ui_driver` (see `conftest.py`). Multiple are comma- or
space-separated. A name that is not one of these is a hard error, not a silent
no-op — a typo that quietly disabled the guard would be the original defect
wearing a new hat.

### `browser` is an umbrella, and what it leaves out is the point

`browser` used to mean, verbatim, "a real Firefox the playwright API can
launch" — one name for one engine, with no way to say anything about the other.
It is now an UMBRELLA over `browser_firefox`, so it declares exactly what it
declared before: `PERSONA_REQUIRED_CAPABILITIES=browser` and every existing
`@pytest.mark.requires_capability("browser")` police the Firefox probes and
nothing new.

What the split buys is that the table can now SAY what is missing.
**`browser_chromium` is deliberately not under the umbrella.** Chromium is not
the secondary engine — `src/services/profile/coherence.py:78` reads
`DEFAULT_ENGINE = "chromium"`, and it is the engine every impossible
os_type/engine pair is reconciled toward (`:99`, `:162`). The engine the
product defaults to is the one no gate ever launches. What rides on it:
`src/services/browser/process.py` returns early for firefox (`:353-356`) before
all 13 `extensions.append` calls — audio, WebGL, GPU, device, voice, locale,
mobile, native-cloak, stealth, canvas-ctx, measuretext, search, geo. None is
exercised by any gate on any platform.

Every declared run now PRINTS that gap next to the green line it qualifies, so
"ok browser" can never again be read as "both engines are covered". Folding
chromium into the umbrella instead would turn every declaring job red for want
of PROVISIONING rather than for want of correctness — a gate that fails for the
wrong reason teaches its reader to ignore it.

**This ships no new coverage, and the distinction is deliberate.** It converts
a silent gap into a declared one, which is genuinely less than "chromium is now
tested". Provisioning that engine is a separate, larger question: it is NOT
`python -m playwright install chromium` (the product launches
fingerprint-chromium, not playwright's build) — the binary arrives via the
`download_engine` route `.github/workflows/engine-autoupdate.yml:104-115` uses
against a pinned baseline tag, so which build, which pin and which runner are
all open. `browser_chromium` is opt-in and declaring it today would be a
promise no machine can keep.

**Nothing infers support from the presence of the thing being checked.** A
guard that reasoned "playwright imported, therefore this machine should run
browser tests" would conclude "not supported here" on exactly the machine where
support broke — the one case that has to be loud. The only input is the
operator's declaration; `tests/test_skip_visibility.py` enforces that
structurally.

## Not yet wired

**Firefox is now provisioned in CI** — `.github/workflows/ci.yml:152` runs
`python -m playwright install firefox` and `:320` declares
`PERSONA_REQUIRED_CAPABILITIES: browser`, so the provisioning cannot silently
rot back to skipping. (The paragraph that used to sit here called that a
follow-up living entirely in `.github/workflows/`; it has since landed.)

**The chromium engine is not, and that is the open one.** See the umbrella
section above: `browser_chromium` exists as a NAMED capability precisely so the
gap is attributable rather than invisible, but nothing provisions it and the
umbrella deliberately does not cover it. Closing it is a separate slice —
which build, which pin, which runner — and until then declaring
`browser_chromium` is a promise no machine can keep.

When those probes first run for real, `tells` may come back non-empty. That is
a genuine finding and belongs to the masking direction as its own ticket —
it is not a reason to adjust the assertion.
