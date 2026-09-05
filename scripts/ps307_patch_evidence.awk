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
    n_added = 0
    n_removed = 0
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
    if (c == "+") {
        if (!usable(t)) next
        if (t in pre) next          # present either way — proves nothing
        if (n_added >= MAX_PER_FILE) next
        added[++n_added] = t
    } else if (c == "-") {
        if (!usable(t)) next
        if (t in post) next         # re-added by this same patch
        if (n_removed >= MAX_PER_FILE) next
        removed[++n_removed] = t
    }
}

# Emitted at the END of a section rather than as we go, because `removed` is a
# FALLBACK: it is only worth emitting once we know the section produced no
# `added` candidate. 009-webdriver.patch is that section in our set.
function flush(   i) {
    if (path == "") return
    if (n_added > 0) {
        for (i = 1; i <= n_added; i++) printf "%s\t%s\t%s\n", path, "added", added[i]
    } else if (n_removed > 0) {
        for (i = 1; i <= n_removed; i++) printf "%s\t%s\t%s\n", path, "removed", removed[i]
    } else if (!created) {
        # Nothing usable and nothing created. Say so explicitly — the caller
        # must be able to tell "no evidence" from "evidence, all satisfied".
        printf "%s\t%s\t\n", path, "noevidence"
    }
    for (i = 1; i <= n_added; i++) delete added[i]
    for (i = 1; i <= n_removed; i++) delete removed[i]
    n_added = 0
    n_removed = 0
    path = ""
    created = 0
}

END { if (NR != FNR) flush() }
