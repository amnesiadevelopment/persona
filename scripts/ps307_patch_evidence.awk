# PS-307 — extract TREE-CHECKABLE EVIDENCE from one of our unified-diff patches.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY EVIDENCE HAS TO COME OUT OF THE PATCH RATHER THAN OUT OF A STAMP
# ─────────────────────────────────────────────────────────────────────────────
# Upstream's `apply_patches()` writes `.patched.stamp` and skips itself when the
# stamp is present. The stamp records THAT patching happened, never WHICH series
# was applied. So on a preserved tree the stamp is precisely the thing that
# lies, and a verification reading it would report success in exactly the
# scenario that drops all 16 of our patches. The evidence has to be read out of
# the SOURCE TREE, and to read it out of the tree you first have to know what to
# look for. That is what this script produces.
#
# Invoked twice over the same patch (`awk -f this p.patch p.patch`) so the first
# pass can build the exclusion set the second pass filters against.
#
# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT — one TSV record per evidence item
# ─────────────────────────────────────────────────────────────────────────────
#   <path> <TAB> <kind> <TAB> <text>
#
#   kind=newfile   the patch CREATES <path>; <text> is empty. The file existing
#                  is unambiguous evidence, and it is the strongest kind we have
#                  — no coincidence can conjure a file upstream does not ship.
#   kind=added     <text> is a line the patch ADDS to <path>. Present in a
#                  patched tree, absent from an unpatched one.
#   kind=removed   <text> is a line the patch DELETES from <path>. Absent from a
#                  patched tree, present in an unpatched one. Emitted ONLY for a
#                  file whose section yields no usable `added` candidate —
#                  009-webdriver.patch is exactly that case in our set (it
#                  deletes two lines and adds none), so this is a live path and
#                  not a defensive limb.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE FILTERS, AND WHY EACH ONE EARNS ITS PLACE
# ─────────────────────────────────────────────────────────────────────────────
# A candidate is only worth anything if finding it in the tree licenses the
# conclusion "this patch is applied". Four things break that licence, and each
# is excluded:
#
# 1. TOO SHORT. `}` or `#endif` occurs everywhere. Minimum 30 characters after
#    stripping, so a hit is a hit on something specific.
#
# 2. NOT DISTINGUISHING. An added line that ALSO appears in the patch as a
#    context or deleted line is present in the tree either way, so finding it
#    proves nothing. Pass 1 collects those; pass 2 drops any candidate in the
#    set. (And symmetrically for `removed`.)
#
# 3. AN `#include`. Includes are the one line shape that genuinely coincides:
#    ungoogled's own 111 patches add `components/ungoogled/...` includes to
#    files of their own, so `+#include "components/ungoogled/ungoogled_switches.h"`
#    is a line our patch adds AND a line that can legitimately exist in a tree
#    carrying none of our patches. Excluded outright rather than reasoned about
#    per-file.
#
# 4. ANYTHING DOMAIN-SHAPED. Domain substitution runs AFTER patching
#    (`apply_domsub` follows `apply_patches`) and rewrites domains in place, so
#    a candidate containing one would be looked for in a form the tree no longer
#    holds — a false ABSENT, which on this gate means a build failed for a
#    reason that is not true. The exclusion is deliberately WIDER than
#    upstream's `domain_regex.list`: anything matching `<host>.com|net|org|gl`
#    goes, whatever the domain. Over-broad is the safe direction — it can only
#    discard candidates, never accept a bad one — and it costs nothing here:
#    measured against all 16 patches at 152.0.7977.75-1, ZERO candidate lines
#    match, so the filter removes nothing while making the guarantee mechanical
#    instead of remembered.
#
# A file section left with no candidate at all is NOT silently dropped: the
# caller counts sections and refuses to verify a patch whose evidence is empty,
# because "nothing to check" must never read as "checked and fine".
#
# ─────────────────────────────────────────────────────────────────────────────
# ⭐ PS-374 — CODE CANDIDATES ARE TAKEN BEFORE COMMENT ONES, AND THAT ORDERING
# IS LOAD-BEARING RATHER THAN COSMETIC
# ─────────────────────────────────────────────────────────────────────────────
# The four filters above decide whether a candidate is USABLE. They do not rank
# the usable ones, and until PS-374 the cap was spent in PATCH ORDER — first
# `MAX_PER_FILE` usable lines, whatever they were.
#
# A comment and a line of code are not equally good evidence, because they do
# not fail together. THE FAILURE THIS GATE EXISTS TO CATCH — a rebase that drops
# or mangles a patch — characteristically leaves the explanatory comment in
# place while the code line it describes is gone or reverted: `patch` reapplies
# a comment block that still lands cleanly, a hand-resolved conflict keeps the
# prose and loses the one-liner, a careless revert touches the statement and not
# the paragraph above it. A claim that pins only the comment is then satisfied
# by a tree in which the patch does nothing at all.
#
# MEASURED, NOT HYPOTHESISED. 001-disable-runtime.enable.patch is exactly that
# shape: each of its three hunks opens with a multi-line `// persona
# fingerprint: ...` block ahead of its ONE code line, so at the shipped cap of 3
# the extractor emitted SIX claims for it — all six comments, ZERO code:
#
#     v8-runtime-agent-impl.cc   3 claims, all `// persona fingerprint: ...`
#     v8-runtime-agent-impl.h    3 claims, all `// persona fingerprint: ...`
#     claims pinning `return false`, `if (!enabled())`, `if (enabled())`:  0
#
# `scripts/ps374_falsify_patch_evidence.sh` reverts precisely those three code
# lines to the upstream `m_enabled` field, leaves every comment untouched, and
# runs this extractor's own matcher over the result: **6 of 6 claims held**. The
# guard reported a clean pass over a patch that was functionally gone. That is a
# false green demonstrated by execution, not argued.
#
# The fix is a PREFERENCE, not an exclusion. A comment is still usable evidence
# — it is better than nothing, and a hunk that genuinely adds only prose must
# still be checkable — so comments are kept as a FALLBACK and taken only once
# the code candidates are exhausted. Raising `MAX_PER_FILE` was the alternative
# and is strictly worse: it would fix 001 at today's comment lengths and say
# nothing about the next patch whose prose runs one line longer, whereas
# ordering makes the property hold for any cap and any patch.
#
# ⚠️ THE CAP STILL BINDS. This changes WHICH candidates are taken, never HOW
# MANY, so the check's cost is unchanged. A section whose every usable candidate
# is a comment still yields comment claims rather than none.

function strip(s) {
    gsub(/^[ \t]+/, "", s)
    gsub(/[ \t]+$/, "", s)
    return s
}

# The four rejection rules above, in one place so both passes agree.
function usable(t) {
    if (length(t) < 30) return 0
    if (t ~ /^#[ \t]*include/) return 0
    if (t ~ /[A-Za-z0-9_-]+\.(com|net|org|gl)([^A-Za-z0-9]|$)/) return 0
    return 1
}

# Is this line PROSE rather than code? Used only to RANK usable candidates (see
# the PS-374 block in the header) — never to reject one.
#
# Deliberately conservative: it recognises the comment openers that actually
# occur in our patch set and nothing else, because the cost of the two errors is
# asymmetric. Misreading code as a comment merely demotes a good candidate
# behind another good one; misreading a comment as code promotes the exact
# evidence this ranking exists to demote, silently.
#
#   //...        C++ line comment — every one of 001's six claims
#   /*...        C++ block comment opener
#   *...         a block comment's continuation line
#   # ...        gn / shell comment. The space matters: `#include` is already
#                rejected by usable(), but `#define` and `#if` are CODE and must
#                not be demoted — neither has a space after the `#`, so neither
#                matches.
function comment_shaped(t) {
    if (t ~ /^\/\//) return 1
    if (t ~ /^\/\*/) return 1
    if (t ~ /^\*/) return 1
    if (t ~ /^#[ \t]/) return 1
    return 0
}

BEGIN { FS = "\n" }

# ── pass 1: everything a candidate must NOT be ──────────────────────────────
# Context lines and deleted lines are in the pre-image, added lines are in the
# post-image. A text appearing on both sides cannot tell the two apart.
NR == FNR {
    if ($0 ~ /^--- / || $0 ~ /^\+\+\+ / || $0 ~ /^@@/ || $0 ~ /^diff --git/ || $0 ~ /^index /) next
    if (substr($0, 1, 1) == " ") { pre[strip(substr($0, 2))] = 1; next }
    if (substr($0, 1, 1) == "-") { pre[strip(substr($0, 2))] = 1; next }
    if (substr($0, 1, 1) == "+") { post[strip(substr($0, 2))] = 1; next }
    next
}

# ── pass 2: walk the sections and emit ──────────────────────────────────────
# `--- ` is read BEFORE `+++ `, and it is what says whether the file is being
# created: `--- /dev/null` is the new-file marker. Both are matched before the
# bare `+`/`-` line rules below, since a `+++` line also starts with `+`.
/^--- / {
    flush()
    from_dev_null = ($0 ~ /^--- \/dev\/null/)
    next
}

/^\+\+\+ / {
    path = $0
    sub(/^\+\+\+ [ab]\//, "", path)
    sub(/^\+\+\+ /, "", path)
    sub(/\t.*$/, "", path)
    path = strip(path)
    n_added_k = 0
    n_added_c = 0
    n_removed_k = 0
    n_removed_c = 0
    if (from_dev_null && path != "" && path != "/dev/null") {
        # A created file. Emitted immediately: its evidence is its existence,
        # and no line has to survive anything for that to hold.
        printf "%s\t%s\t\n", path, "newfile"
        created = 1
    } else {
        created = 0
    }
    next
}

/^@@/ { next }
/^diff --git/ { next }
/^index / { next }

{
    if (path == "") next
    c = substr($0, 1, 1)
    t = strip(substr($0, 2))
    # ⭐ PS-374: candidates are collected into TWO ranked buckets and the cap is
    # applied at flush(), not here. Capping during collection is what spent 001's
    # whole budget on the comment block that precedes each of its code lines —
    # the cap was gone before the extractor ever reached `bool enabled() const
    # { return false; }`. Collection is now unbounded and selection is ranked;
    # the emitted COUNT is unchanged.
    if (c == "+") {
        if (!usable(t)) next
        if (t in pre) next          # present either way — proves nothing
        if (comment_shaped(t)) added_c[++n_added_c] = t
        else                        added_k[++n_added_k] = t
    } else if (c == "-") {
        if (!usable(t)) next
        if (t in post) next         # re-added by this same patch
        if (comment_shaped(t)) removed_c[++n_removed_c] = t
        else                        removed_k[++n_removed_k] = t
    }
}

# Emitted at the END of a section rather than as we go, because `removed` is a
# FALLBACK: it is only worth emitting once we know the section produced no
# `added` candidate. 009-webdriver.patch is that section in our set.
#
# ⭐ PS-374: this is also where the CAP is applied and where CODE candidates are
# taken ahead of COMMENT ones. `emit_ranked` fills the budget from the code
# bucket first and tops it up from the comment bucket only if room remains, so a
# hunk with one code line and three comment lines yields the code line — which
# is what patch 001 needed and did not get. At most MAX_PER_FILE claims are
# emitted per file per kind, exactly as before.
function emit_ranked(kind, code_n, code_a, cmt_n, cmt_a,    i, taken) {
    taken = 0
    for (i = 1; i <= code_n && taken < MAX_PER_FILE; i++) {
        printf "%s\t%s\t%s\n", path, kind, code_a[i]
        taken++
    }
    for (i = 1; i <= cmt_n && taken < MAX_PER_FILE; i++) {
        printf "%s\t%s\t%s\n", path, kind, cmt_a[i]
        taken++
    }
    return taken
}

function flush(   i) {
    if (path == "") return
    if (n_added_k + n_added_c > 0) {
        emit_ranked("added", n_added_k, added_k, n_added_c, added_c)
    } else if (n_removed_k + n_removed_c > 0) {
        emit_ranked("removed", n_removed_k, removed_k, n_removed_c, removed_c)
    } else if (!created) {
        # Nothing usable and nothing created. Say so explicitly — the caller
        # must be able to tell "no evidence" from "evidence, all satisfied".
        printf "%s\t%s\t\n", path, "noevidence"
    }
    for (i = 1; i <= n_added_k; i++) delete added_k[i]
    for (i = 1; i <= n_added_c; i++) delete added_c[i]
    for (i = 1; i <= n_removed_k; i++) delete removed_k[i]
    for (i = 1; i <= n_removed_c; i++) delete removed_c[i]
    n_added_k = 0
    n_added_c = 0
    n_removed_k = 0
    n_removed_c = 0
    path = ""
    created = 0
}

END { if (NR != FNR) flush() }
