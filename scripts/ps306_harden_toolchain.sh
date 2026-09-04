#!/bin/bash
# PS-306 — make the trial build survive an INTERMITTENT `update_rust.py` segfault.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT BROKE, AND WHY THE REPAIR HAS TO LIVE HERE
# ─────────────────────────────────────────────────────────────────────────────
# The PS-299 trial build of Chromium 152.0.7977.75-1 died three times in the
# `prepare` phase, each time after ~12 minutes, and the compile step never ran.
# The infra operator diagnosed it live on the runner: `tools/rust/update_rust.py`
# SEGFAULTS INTERMITTENTLY UNDER BUILD LOAD. In isolation it is fine — 14 runs in
# the same container image, 8 via `python3` and 6 via its shebang, every one
# exit 0 — and one trial-build run did get past this point. The script is not
# broken; the INVOCATION does not tolerate a transient crash.
#
# The call is upstream's, in `scripts/shared.sh`:
#
#     setup_toolchain() {
#         if [ "$_host_arch" = x64 ]; then
#             "${_src_dir}/tools/rust/update_rust.py"      <-- this line
#             "${_src_dir}/tools/clang/scripts/update.py"
#
# and `setup_toolchain` is called from `.github/scripts/build.sh` INSIDE the
# container, so it cannot be wrapped from outside: by the time
# `ps218_build.sh prepare` runs, the only place a repair can exist is on disk.
# And `ucpl/` is checked out fresh on every dispatch (`actions/checkout@v4`
# defaults `clean` to true), so it cannot be committed once either — it has to
# be RE-APPLIED PER RUN. That is the whole reason this script exists as a
# workflow step rather than as a patch in this repo.
#
# `scripts/ps218_stage_patches.sh` is the precedent: modify the checked-out ucpl
# tree from our side, keyed to `UCPL_DIR`, and guard loudly when the tree is not
# the shape we expected.
#
# ─────────────────────────────────────────────────────────────────────────────
# KEYED TO THE CALL, NEVER TO A LINE NUMBER
# ─────────────────────────────────────────────────────────────────────────────
# A repair keyed to a line number silently targets the WRONG LINE on a different
# tag — the worst available failure, because it would produce a build that ran
# with an unknown modification and reported a number for it. So the call is
# matched as text, and the match must be EXACTLY ONE. Zero means upstream moved
# it (the tag is not the shape we verified) and more than one means the anchor
# is ambiguous; both stop the run rather than guess.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE VERIFICATION RESTS ON THE TOOLCHAIN, NOT ON VERSION AND NOT ON `$?`
# ─────────────────────────────────────────────────────────────────────────────
# A retry loop that exhausts its attempts and lets the build continue toward a
# confusing downstream error is WORSE than the failure it replaces. Getting that
# right takes THREE things, and the first two are not obvious — each of them was
# a live false-green in the first cut of this script.
#
# 1. A CRASHED ATTEMPT'S LEFTOVERS MUST BE DESTROYED BEFORE THE NEXT ATTEMPT.
#    `update_rust.py` starts with a stamp short-circuit (152.0.7977.75,
#    `tools/rust/update_rust.py:136-139`):
#
#        if os.path.exists(output_dir):
#            if version == GetStampVersion() and not glob.glob(...):
#                return 0            # downloads nothing
#
#    and `GetStampVersion()` (:68-78) reads ONLY `third_party/rust-toolchain/
#    VERSION`. That file is one member among many inside the archive
#    (`DownloadAndUnpack` -> `tarfile.extractall`, `update.py:194-207`), and a
#    SIGSEGV — the exact failure being retried — has a real window after VERSION
#    lands and before the tree is complete (the remaining members, the `os.utime`
#    loop, the trailing `assert`). So a crashed attempt can leave a VERSION that
#    MATCHES the wanted revision beside a toolchain with no `bin/rustc` — and the
#    retry would then hit the short-circuit and "succeed" instantly over it. The
#    retry therefore wipes `third_party/rust-toolchain` before every attempt
#    after the first, which also restores the retry's intended meaning: a
#    genuinely fresh attempt. (Attempt 1 does NOT wipe: a legitimately
#    up-to-date tree should still short-circuit, which is what makes the step
#    cheap on a re-run.)
#
# 2. THE ARTIFACT TEST IS `bin/rustc`, NOT `VERSION`. VERSION is the stamp the
#    failing script itself wrote; `bin/rustc` is what Chromium actually consumes
#    (`build/config/rust.gni:436` lists `//third_party/rust-toolchain/bin/rustc`
#    as a build input). Both are checked, because "VERSION present, rustc
#    absent" is the partial-extraction shape and it deserves to name itself in
#    the log rather than read as a generic miss.
#
# 3. EXHAUSTED RETRIES FAIL, FULL STOP. A present artifact must never launder a
#    failed final attempt: three segfaults that each leave a stale VERSION are
#    still three segfaults, and the run must die HERE where the cause is named.
#
# And every failed attempt is printed WITH ITS EXIT CODE. A silent retry turns
# an intermittent failure into an invisible chronic one, which is precisely the
# outcome this ticket names as unacceptable.
set -euo pipefail

UCPL_DIR="${UCPL_DIR:?UCPL_DIR must point at the ungoogled-chromium-portablelinux checkout}"
TREE="${1:-unknown}"

SHARED="${UCPL_DIR}/scripts/shared.sh"

# The one anchor. Upstream's line is exactly:
#     `        "${_src_dir}/tools/rust/update_rust.py"`
# i.e. the call ALONE on its line, at tag 152.0.7977.75-1. Matched with leading
# and trailing whitespace tolerated so re-indentation upstream does not break
# it, but nothing else on the line — a line that also did something else is not
# the line we verified and must not be silently rewritten.
CALL_RE='^[[:space:]]*"\$\{_src_dir\}/tools/rust/update_rust\.py"[[:space:]]*$'
FUNC_RE='^setup_toolchain\(\)[[:space:]]*\{'
MARKER='PS-306: retry the intermittent update_rust.py segfault'

mkdir -p record
REPORT="record/ps306-toolchain-retry-${TREE}.txt"

echo "== PS-306: hardening the toolchain step against an intermittent update_rust.py segfault =="
echo "tree:  ${TREE}"
echo "file:  ${SHARED}"
echo

if [ ! -f "$SHARED" ]; then
  echo "::error::PS-306: ${SHARED} does not exist. The ucpl checkout is not the shape this repair was written against; refusing to continue."
  exit 1
fi

# Idempotent. The step runs once per job, but a re-applied patch would nest the
# retry inside itself, and a second application is a far better thing to notice
# than to survive.
if grep -qF "$MARKER" "$SHARED"; then
  echo "PS-306: ${SHARED} already carries the retry; nothing to do."
  {
    echo "# PS-306 — toolchain retry (tree: ${TREE})"
    echo "# recorded: $(date -Is)"
    echo "status: already-applied (no change made)"
  } > "$REPORT"
  exit 0
fi

# ── the two anchors, each checked for EXACTLY ONE match ──────────────────────
call_hits="$(grep -cE "$CALL_RE" "$SHARED" || true)"
func_hits="$(grep -cE "$FUNC_RE" "$SHARED" || true)"

if [ "$call_hits" -ne 1 ]; then
  echo "::error::PS-306: expected exactly ONE bare \`update_rust.py\` call in ${SHARED}, found ${call_hits}."
  echo "::error::The repair is keyed to the CALL, not to a line number, precisely so a different tag fails HERE instead of silently rewriting the wrong line."
  echo "::error::Re-read setup_toolchain() at the tag being built and update the anchor before dispatching again."
  grep -nF 'update_rust.py' "$SHARED" || true
  exit 1
fi

if [ "$func_hits" -ne 1 ]; then
  echo "::error::PS-306: expected exactly ONE \`setup_toolchain()\` definition in ${SHARED}, found ${func_hits}."
  exit 1
fi

call_line="$(grep -nE "$CALL_RE" "$SHARED" | cut -d: -f1)"
echo "anchor: bare update_rust.py call at ${SHARED}:${call_line}"
echo "before:"
sed -n "$((call_line - 2)),$((call_line + 2))p" "$SHARED" | sed 's/^/    /'
echo

# ─────────────────────────────────────────────────────────────────────────────
# The injected retry. Written to a temp file and spliced in by awk so the shell
# quoting of the injected body is not fought over twice.
#
# ⚠️ Upstream's entrypoint is `set -euxo pipefail` and `shared.sh` is `set -euo
# pipefail`. A bare failing command inside this function would abort the whole
# script on the FIRST failed attempt — the retry would never run. `cmd && rc=0
# || rc=$?` is a compound command, so `set -e` does not fire on it, and the
# non-zero status is captured instead of killing the build.
# ─────────────────────────────────────────────────────────────────────────────
INJECT="$(mktemp)"
trap 'rm -f "$INJECT"' EXIT

cat > "$INJECT" <<'PERSONA_PS306_EOF'
# ── PS-306: retry the intermittent update_rust.py segfault ───────────────────
# Injected into this checkout by persona/scripts/ps306_harden_toolchain.sh after
# `actions/checkout` and before the prepare phase. NOT an upstream change: the
# ucpl tree is re-cloned per run, so this is re-applied per run.
#
# `update_rust.py` segfaults intermittently under build load on the persona-build
# runner and takes the whole ~12-minute prepare phase down with it. Three
# attempts, 15s apart, every failure printed WITH ITS EXIT CODE so the flake
# stays countable — and then a check on the TOOLCHAIN rather than on `$?`,
# because a run that exhausts its retries must fail HERE, loudly, instead of
# continuing toward a confusing downstream error.
#
# ⚠️ The two subtleties, both of which were false-greens before they were closed:
#   * A crashed attempt can leave a MATCHING `VERSION` beside an incomplete tree
#     (VERSION is one tar member among many). update_rust.py's own stamp
#     short-circuit would then return 0 without downloading anything, so the
#     leftovers are WIPED before every attempt after the first.
#   * `VERSION` is the stamp the failing script wrote; `bin/rustc` is what
#     Chromium consumes (build/config/rust.gni:436). Both are checked, and a
#     failed final attempt is never laundered by a present artifact.
persona_update_rust_with_retry() {
    local _rust_script="${_src_dir}/tools/rust/update_rust.py"
    local _toolchain_dir="${_src_dir}/third_party/rust-toolchain"
    local _version_file="${_toolchain_dir}/VERSION"
    local _rustc_bin="${_toolchain_dir}/bin/rustc"
    local _attempts=3
    # 15s was the operator's request: long enough for transient build load to
    # subside. Overridable ONLY so the test suite can exercise the loop without
    # sleeping; nothing in the workflow sets it.
    local _pause="${PS306_RETRY_PAUSE_SECONDS:-15}"
    local _rc=0
    local _n=1
    local _made=0

    while [ "$_n" -le "$_attempts" ]; do
        # A RETRY MUST BE A GENUINELY FRESH ATTEMPT. A crashed attempt can leave
        # a VERSION file that matches the wanted revision beside a half-extracted
        # tree; update_rust.py's stamp short-circuit (update_rust.py:136-139)
        # would then return 0 instantly over it, and the build would proceed
        # with no `bin/rustc`. Wiping the directory is what stops a partial
        # toolchain from being mistaken for a complete one.
        #
        # Attempt 1 deliberately does NOT wipe: a legitimately up-to-date tree
        # should still short-circuit, which is what keeps a re-run cheap.
        if [ "$_n" -gt 1 ] && [ -e "$_toolchain_dir" ]; then
            echo "== PS-306: discarding the failed attempt's rust-toolchain tree so the retry is a fresh download =="
            rm -rf "$_toolchain_dir"
        fi

        echo "== PS-306: update_rust.py attempt ${_n}/${_attempts} =="
        _rc=0
        _made="$_n"
        # A compound command: `set -e` does not fire, so a failed attempt is
        # captured rather than aborting the script before the retry can happen.
        "$_rust_script" && _rc=0 || _rc=$?

        if [ "$_rc" -eq 0 ]; then
            echo "== PS-306: update_rust.py attempt ${_n}/${_attempts} exited 0 =="
            break
        fi

        # LOUD, and on the workflow's own channel. A retry that hides how often
        # it fired would make this failure invisible once it becomes chronic.
        #
        # The suffix is computed rather than fixed: a LAST attempt that printed
        # "retrying" would be a false statement in the log, and this log is the
        # thing a reader counts the flake from.
        echo "== PS-306: update_rust.py attempt ${_n}/${_attempts} FAILED with exit code ${_rc} =="
        if [ "$_n" -lt "$_attempts" ]; then
            echo "::warning::PS-306: update_rust.py attempt ${_n}/${_attempts} FAILED with exit code ${_rc} (intermittent segfault under build load; retrying)"
            echo "== PS-306: pausing ${_pause}s before the next attempt =="
            sleep "$_pause"
        else
            echo "::warning::PS-306: update_rust.py attempt ${_n}/${_attempts} FAILED with exit code ${_rc} (no attempts remain)"
        fi
        _n=$((_n + 1))
    done

    echo "== PS-306: update_rust.py finished after ${_made}/${_attempts} attempt(s), last exit code ${_rc} =="

    # ── THE VERIFICATION. ON THE TOOLCHAIN, NOT ON THE STAMP, NOT ON `$?`. ───
    # `VERSION` is one member of the archive and is written by the very script
    # that crashed, so on its own it proves nothing: a SIGSEGV mid-extraction
    # leaves it present beside a tree with no compiler in it. `bin/rustc` is
    # what Chromium actually consumes (build/config/rust.gni:436), so that is
    # the honest test. Both are named separately, because "stamp present,
    # compiler absent" is the partial-extraction shape and a reader who sees it
    # should be told exactly that rather than a generic miss.
    if [ ! -x "$_rustc_bin" ]; then
        if [ -s "$_version_file" ]; then
            echo "::error::PS-306: the Rust toolchain is INCOMPLETE after ${_made} attempt(s): ${_version_file} exists but ${_rustc_bin} is missing or not executable."
            echo "::error::PS-306: that is a half-extracted toolchain — the stamp landed, the compiler did not. The stamp alone would have let update_rust.py short-circuit on a later run and the build would have failed much further downstream."
        else
            echo "::error::PS-306: the Rust toolchain did NOT materialise after ${_made} attempt(s): ${_rustc_bin} is missing."
        fi
        echo "::error::PS-306: refusing to continue the prepare phase without a toolchain — a run that continued from here would fail later with a confusing downstream error instead of naming this."
        return 1
    fi

    if [ ! -s "$_version_file" ]; then
        echo "::error::PS-306: ${_rustc_bin} is present but ${_version_file} is missing or empty after ${_made} attempt(s); the toolchain tree is not the shape Chromium expects. Refusing to continue."
        return 1
    fi

    # ── AND THE EXIT CODE STILL HAS TO BE ZERO. ──────────────────────────────
    # A present artifact must NEVER launder a failed final attempt. Three
    # segfaults that each happened to leave a toolchain behind are still three
    # segfaults, and "making the build go green by other means" is explicitly
    # out of scope: if update_rust.py could not complete, the run dies HERE,
    # where the cause is named, rather than somewhere downstream where it is
    # not.
    if [ "$_rc" -ne 0 ]; then
        echo "::error::PS-306: update_rust.py failed on all ${_attempts} attempts; last exit code ${_rc}. Refusing to continue the prepare phase."
        return 1
    fi

    echo "== PS-306: Rust toolchain verified — ${_rustc_bin} present, stamped at ${_version_file} =="
    sed 's/^/    VERSION: /' "$_version_file" || true

    return 0
}
PERSONA_PS306_EOF

# ── splice: define the function above setup_toolchain(), replace the call ────
# awk, not sed -i with a line number: the two anchors are matched as TEXT, and
# the call's own indentation is preserved so the rewritten line reads naturally
# in the diff a human will look at.
TMP_OUT="$(mktemp)"
trap 'rm -f "$INJECT" "$TMP_OUT"' EXIT

awk -v injectfile="$INJECT" \
    -v call_re='^[ \t]*"\\$\\{_src_dir\\}/tools/rust/update_rust\\.py"[ \t]*$' \
    -v func_re='^setup_toolchain\\(\\)[ \t]*\\{' '
  $0 ~ func_re && !injected {
      while ((getline line < injectfile) > 0) print line
      close(injectfile)
      print ""
      injected = 1
  }
  $0 ~ call_re && !replaced {
      match($0, /^[ \t]*/)
      indent = substr($0, 1, RLENGTH)
      print indent "persona_update_rust_with_retry"
      replaced = 1
      next
  }
  { print }
  END {
      if (!injected)  { print "AWK-ERROR: setup_toolchain() anchor not matched" > "/dev/stderr"; exit 3 }
      if (!replaced)  { print "AWK-ERROR: update_rust.py call not matched"      > "/dev/stderr"; exit 4 }
  }
' "$SHARED" > "$TMP_OUT"

# ── verify the rewrite before it is allowed to become the build's input ──────
if ! grep -qF "$MARKER" "$TMP_OUT"; then
  echo "::error::PS-306: the retry function was not injected. Refusing to install the rewritten shared.sh."
  exit 1
fi
if [ "$(grep -cE "$CALL_RE" "$TMP_OUT" || true)" -ne 0 ]; then
  echo "::error::PS-306: a bare update_rust.py call survived the rewrite. Refusing to install the rewritten shared.sh."
  exit 1
fi
if [ "$(grep -cE '^[[:space:]]*persona_update_rust_with_retry[[:space:]]*$' "$TMP_OUT" || true)" -ne 1 ]; then
  echo "::error::PS-306: expected exactly one call to persona_update_rust_with_retry after the rewrite."
  exit 1
fi
# A syntax error here would surface ~12 minutes later as an incomprehensible
# failure inside the container. Catch it in this step, where it names itself.
if ! bash -n "$TMP_OUT"; then
  echo "::error::PS-306: the rewritten shared.sh does not parse. Refusing to install it."
  exit 1
fi

cat "$TMP_OUT" > "$SHARED"

new_line="$(grep -nE '^[[:space:]]*persona_update_rust_with_retry[[:space:]]*$' "$SHARED" | cut -d: -f1)"
echo "after:"
sed -n "$((new_line - 2)),$((new_line + 2))p" "$SHARED" | sed 's/^/    /'
echo
echo "PS-306: retry installed — 3 attempts, 15s apart, each retry from a wiped rust-toolchain tree, verified against third_party/rust-toolchain/bin/rustc."

# ── record it, like every other step in this workflow ────────────────────────
{
  echo "# PS-306 — toolchain retry applied to the checked-out ucpl tree"
  echo "# tree: ${TREE}"
  echo "# recorded: $(date -Is)"
  echo "# file: ${SHARED}"
  echo "# sha256: $(sha256sum "$SHARED" | cut -d' ' -f1)"
  echo
  echo "status: applied"
  echo "attempts: 3"
  echo "pause_seconds: 15"
  echo "wipes_between_attempts: third_party/rust-toolchain (attempts 2..n, so a partial tree cannot satisfy update_rust.py's stamp short-circuit)"
  echo "verified_artifact: third_party/rust-toolchain/bin/rustc (must exist and be executable) + VERSION (must be non-empty)"
  echo "on_exhausted_retries: FAIL (a present artifact never launders a failed final attempt)"
  echo
  echo "== setup_toolchain() as installed =="
  sed -n "/^setup_toolchain()/,/^}/p" "$SHARED"
} > "$REPORT"

echo "recorded -> ${REPORT}"
