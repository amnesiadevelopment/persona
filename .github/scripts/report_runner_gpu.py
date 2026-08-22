"""Record what the runner's GPU actually is, and label the reading honestly.

WHY THIS EXISTS AS A SCRIPT RATHER THAN AS A TEST

persona SPOOFS the GPU. If a spoof holds, what a page reads is the spoofed
value rather than what the host draws with — so most of the masking surface
(automation tells, patched-function shape, property descriptors, realm
coverage, declared-machine coherence, client hints, locale, timezone) is
answerable on a machine with no graphics hardware at all, and the browser job
answers it.

Exactly one class is NOT answerable here, and it is known and named. A GitHub
hosted runner has no GPU, so WebGL renders in software (llvmpipe / SwiftShader
/ Apple's software path). A declared discrete GPU string over a
software-rendered canvas is an IMPOSSIBLE PAIR, and the masking charter already
classifies that as a HOST-FACT LEAK — the operator's deployment environment
reaching through the mask — rather than as a configuration mistake.

On a hosted runner that pair is present BY CONSTRUCTION, on every run,
permanently. It is not a regression, it will never be fixed here, and no amount
of persona-side work removes it.

THE THREE RULES THIS SCRIPT ENFORCES BY BEING SHAPED THE WAY IT IS

  1. RECORDED WITH ITS REASON. The reading is printed and written to the job
     summary with the cause attached, so a reader meets the explanation and the
     value at the same moment.

  2. NEVER COUNTED AS A PASS. This is deliberately NOT a pytest test and it
     deliberately never asserts. A green assertion here would enter the record
     as evidence that persona's GPU masking was verified, which is the precise
     misreading this file exists to prevent. It reports; it does not judge.

  3. NEVER RAISED AS A NEW DEFECT, AND NEVER QUIETLY DELETED. It exits 0 even
     when the pair is present, so it cannot open a spurious bug; and it runs on
     every browser job, so the fact cannot vanish from the record by neglect.

WHAT IS DELIBERATELY NOT DONE HERE: nothing installs a driver, forces a
renderer string, or otherwise makes the runner claim hardware it does not have.
That would corrupt the one reading this environment is genuinely unable to
give, and it is explicitly out of scope.
"""

from __future__ import annotations

import os
import pathlib
import sys

# Substrings that identify a SOFTWARE rasteriser. Matched case-insensitively
# against the unmasked renderer string the runner's real GL stack reports.
SOFTWARE_RENDERER_MARKERS = (
    "llvmpipe",      # Mesa's software rasteriser (Linux runners)
    "swiftshader",   # Google's software GL (commonly seen under headless)
    "software",      # generic "... (Software)" suffixes
    "microsoft basic render",  # Windows' fallback adapter
    "apple software renderer",
    "generic renderer",
    "mesa offscreen",
)

# The page-side read. Uses the UNMASKED extension where available, because the
# masked strings are exactly what a spoof would rewrite — the point here is to
# learn what the HOST draws with, not what a page is told.
_READ_RENDERER = """() => {
  const out = { ok: false };
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) { out.error = 'no webgl context'; return out; }
    out.ok = true;
    out.vendor = gl.getParameter(gl.VENDOR);
    out.renderer = gl.getParameter(gl.RENDERER);
    out.version = gl.getParameter(gl.VERSION);
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    if (dbg) {
      out.unmaskedVendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
      out.unmaskedRenderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
    }
  } catch (e) { out.error = String(e); }
  return out;
}"""


def looks_like_software(*values: object) -> bool:
    """True when any reported renderer string names a software rasteriser."""
    for value in values:
        if not value:
            continue
        low = str(value).lower()
        if any(marker in low for marker in SOFTWARE_RENDERER_MARKERS):
            return True
    return False


def explanation_for(renderer: object, software: bool) -> list[str]:
    """Pick the explanation for a reading — THE BRANCH ITSELF, as one function.

    Extracted so a test can exercise the DISPATCH rather than only the three
    texts it dispatches to. Testing the texts alone left a hole: neutering the
    `renderer is None` branch in main() kept every text intact and every
    assertion green, while a runner that answered NOTHING silently started
    reading as a runner that reported hardware.
    """
    if software:
        return explain_software_rendering()
    if renderer is None:
        # THE DISTINCTION THAT MATTERS. No renderer string at all is the ABSENCE
        # of data, not evidence of hardware. Collapsing this into the "no
        # software rasteriser" branch would let a runner that answered NOTHING
        # read as a runner that reported a GPU.
        return explain_no_reading()
    return explain_hardware_reading(renderer)


def explain_no_reading() -> list[str]:
    """Shown when no renderer string could be read AT ALL.

    A separate state from "hardware reported", and the distinction is the whole
    point: an absent reading is NOT evidence that the host has a GPU, and must
    never be allowed to read as one. Measured on ubuntu-24.04, where headless
    Firefox creates no WebGL context and every parameter comes back None.
    """
    return [
        "  NO READING WAS TAKEN — no WebGL context was available at all.",
        "",
        "  This is NOT a claim that the host has a GPU, and NOT a claim that it",
        "  renders in software. It is the absence of data. Do not read it as",
        "  either; the question this job cannot answer stays unanswered.",
        "",
        "  Measured cause on ubuntu-24.04: headless Firefox exposes no WebGL",
        "  context on a runner with no GL stack, so there is no renderer string",
        "  to report.",
    ]


def explain_hardware_reading(renderer: object) -> list[str]:
    """Shown when a real, non-software renderer is reported.

    Also not a pass. A hosted runner reporting hardware (macos-latest reports
    'Apple M1') means the software-rendered pair is ABSENT here — so this
    platform simply does not exhibit the host-fact leak, which is a statement
    about the runner and not a verification of persona's masking.
    """
    return [
        f"  A hardware renderer was reported: {renderer}",
        "",
        "  So the software-rendered pair is ABSENT on this platform. That is a",
        "  fact about this runner, NOT a verification of persona's GPU masking —",
        "  nothing here asserted that a spoof held. Do not record it as a pass.",
    ]


def explain_software_rendering() -> list[str]:
    """The explanation an operator is SHOWN when the pair is present.

    A separate function, and not inline prose, so a test can read exactly what
    gets printed. Asserting on the module's source text instead would also match
    this file's docstring — which is how a mutation that gutted the printed
    explanation once slipped past a test that looked correct.
    """
    return [
        "  CAUSE: this runner has no GPU, so WebGL renders in software.",
        "",
        "  A persona profile that declares a discrete GPU while drawing on a",
        "  software rasteriser is an IMPOSSIBLE PAIR. The masking charter",
        "  classifies that as a HOST-FACT LEAK — the deployment environment",
        "  reaching through the mask — NOT as a persona misconfiguration.",
        "",
        "  On a hosted runner this pair is present BY CONSTRUCTION, on every",
        "  run, permanently. Do NOT read it as evidence about persona, do NOT",
        "  file it as a new defect, and do NOT 'fix' it by installing a driver",
        "  or forcing a renderer string — that would corrupt the one reading",
        "  this environment cannot honestly give.",
        "",
        "  Answering it for real needs hardware this job does not have.",
    ]


def _emit_summary(text: str) -> None:
    """Append to the GitHub job summary when running under Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with pathlib.Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")
    except OSError as exc:  # a summary that cannot be written must not fail CI
        print(f"(could not write job summary: {exc})")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        # The browser job declares the capability, so an import failure there is
        # already a loud pytest failure. This script must not double-report it
        # as a GPU finding, which it is not.
        print(f"playwright unavailable, no GPU reading taken: {exc}")
        return 0

    try:
        with sync_playwright() as pw:
            browser = pw.firefox.launch()
            try:
                page = browser.new_context().new_page()
                page.goto("data:text/html,<meta charset=utf-8><title>gpu</title>")
                reading = page.evaluate(_READ_RENDERER)
            finally:
                browser.close()
    except Exception as exc:
        print(f"could not take a GPU reading: {type(exc).__name__}: {exc}")
        return 0

    renderer = reading.get("unmaskedRenderer") or reading.get("renderer")
    vendor = reading.get("unmaskedVendor") or reading.get("vendor")
    software = looks_like_software(renderer, reading.get("renderer"))

    banner = [
        "",
        "=" * 78,
        "KNOWN-ENVIRONMENTAL READING — NOT A PASS, NOT A PERSONA DEFECT",
        "=" * 78,
        f"  vendor   : {vendor}",
        f"  renderer : {renderer}",
        f"  gl version: {reading.get('version')}",
        f"  software-rendered: {software}",
        "",
    ]
    banner += explanation_for(renderer, software)
    banner.append("=" * 78)
    print("\n".join(banner))

    _emit_summary(
        "\n### GPU reading — known-environmental, not a pass\n\n"
        f"- **renderer**: `{renderer}`\n"
        f"- **vendor**: `{vendor}`\n"
        f"- **software-rendered**: `{software}`\n\n"
        + (
            "A hosted runner has no GPU, so WebGL renders in software. A declared "
            "discrete GPU over a software-rendered canvas is a **host-fact leak**, "
            "present here by construction on every run. It is recorded, never "
            "counted as a pass, and never filed as a new persona defect.\n"
            if software
            else "No software rasteriser was detected, which is unexpected on a "
            "hosted runner — re-read rather than trusting this line.\n"
        )
    )
    # ALWAYS 0. See rule 3 in the module docstring: this reports, it never judges.
    return 0


if __name__ == "__main__":
    sys.exit(main())
