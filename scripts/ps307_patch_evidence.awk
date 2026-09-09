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
# WHICH MAX_PER_FILE CANDIDATES — THE LONGEST, NOT THE FIRST (PS-382)
# ─────────────────────────────────────────────────────────────────────────────
# The four filters above decide what is ELIGIBLE. They do not decide what is
# CHOSEN, and until PS-382 the choice was simply "the first MAX_PER_FILE in file
# order" — which is an accident of where the author put the line, not a
# statement about how much it proves.
#
# It cost a real red build. `014-client-rects.patch`'s section in
# `ui/gfx/geometry/quad_f.cc` adds, in this order:
#
#     void QuadF::Offset(float x_offset, float y_offset) {     ← ours, unmistakably
#     // 计算轴对齐边界框的宽高                                   ← ours, unmistakably
#     const auto [min, max] = Extents();                       ← a C++17 idiom
#     ...
#     if (WithinEpsilon(width, 0.0f) || WithinEpsilon(height, 0.0f)) {
#
# File order took the first three, so the generic structured binding became one
# of `014`'s claims and the `WithinEpsilon` guard — nearly twice as long and
# genuinely distinctive — was never considered. Upstream 152.0.7977.75 writes
# that same structured binding at `quad_f.cc:171`, inside `QuadF::IntersectsRect`
# and nothing to do with our patch. The claim therefore held against a perfectly
# clean control, and run 34405524686 failed the whole engine-trial-build on it.
#
# So candidates are now ranked by LENGTH and the longest are taken. This is not
# a new idea being introduced; it is filter 1 carried through to its conclusion.
# That filter already says length is this file's proxy for specificity ("`}` or
# `#endif` occurs everywhere. Minimum 30 characters ... so a hit is a hit on
# something specific"). Using it only as a FLOOR and then ignoring it while
# choosing among everything above the floor is the inconsistency; a 33-character
# idiom and a 64-character guard are not equally good evidence and were being
# treated as though the first one to appear won.
#
# ⚠️ Length is a PROXY, not a proof of distinctiveness — a long line can still
# coincide, and this ranking makes that rarer without making it impossible.
# It is deliberately paired with, and NOT a substitute for, the corroboration
# rule in ps307_verify_patches_in_tree.sh: ranking lowers the RATE of accidental
# matches, corroboration stops any single one from condemning a tree. Neither
# alone is enough, which is why PS-382 changed both.
#
# Ties break on file order (lowest index first) and the selected claims are
# emitted in file order, so the output is deterministic and still reads down the
# patch the way the patch is written.

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
    # EVERY eligible candidate is collected, not just the first MAX_PER_FILE.
    # The cap is applied when the section is flushed, so the choice can be made
    # by SPECIFICITY over the whole section rather than by where a line happens
    # to sit in it. See the PS-382 note in the header.
    #
    # ⚠️ DEDUPLICATED WITHIN THE SECTION, and this became load-bearing with the
    # length ranking. A patch can add the SAME text at several sites —
    # 014-client-rects.patch adds the identical `kDisableSpoofing ... find(
    # "clientrects")` guard line at three places in `element.cc` and `range.cc`
    # — and duplicates are all exactly as long as each other, so a ranking by
    # length selects the same string two or three times. That is not three
    # claims: the verifier answers all of them with ONE `grep -F` for one
    # string, so they hold or fail together. Under file order the effect was
    # merely wasteful; under the corroboration rule this patch's other half
    # introduces it would be WRONG, because a single accidental match would be
    # counted as two or three corroborating ones and could carry a threshold on
    # its own. One text, one claim.
    if (c == "+") {
        if (!usable(t)) next
        if (t in pre) next          # present either way — proves nothing
        if (t in seen_added) next   # same string already collected here
        seen_added[t] = 1
        added[++n_added] = t
    } else if (c == "-") {
        if (!usable(t)) next
        if (t in post) next         # re-added by this same patch
        if (t in seen_removed) next
        seen_removed[t] = 1
        removed[++n_removed] = t
    }
}

# Mark the MAX_PER_FILE longest candidates in `pool` (indices 1..n), writing the
# chosen indices into `pick` as keys.
#
# Selection is by repeated max rather than by sorting, because awk has no
# portable sort and MAX_PER_FILE is 3: three passes over a handful of candidates
# is cheaper and needs no gawk extension. `length()` is the same measure filter 1
# uses for its floor — see the header for why length is the proxy being carried
# through, and why it is a proxy rather than a proof.
function select_longest(pool, n, pick,   taken, i, best, best_len, want) {
    want = MAX_PER_FILE + 0
    if (want < 1) want = 1
    if (want > n) want = n
    for (taken = 0; taken < want; taken++) {
        best = 0
        best_len = -1
        for (i = 1; i <= n; i++) {
            if (i in pick) continue
            # Strictly greater, so an exact tie keeps the EARLIER line and the
            # output stays deterministic across awk implementations.
            if (length(pool[i]) > best_len) {
                best_len = length(pool[i])
                best = i
            }
        }
        if (best == 0) return
        pick[best] = 1
    }
}

# Emitted at the END of a section rather than as we go, because `removed` is a
# FALLBACK: it is only worth emitting once we know the section produced no
# `added` candidate. 009-webdriver.patch is that section in our set.
#
# The end of the section is also the only place the MAX_PER_FILE choice can be
# made honestly: picking the longest candidates requires having seen them all.
function flush(   i, pick) {
    if (path == "") return
    if (n_added > 0) {
        delete pick
        select_longest(added, n_added, pick)
        # Emitted in FILE ORDER among the selected, so the evidence reads down
        # the patch even though it was chosen by length.
        for (i = 1; i <= n_added; i++)
            if (i in pick) printf "%s\t%s\t%s\n", path, "added", added[i]
    } else if (n_removed > 0) {
        delete pick
        select_longest(removed, n_removed, pick)
        for (i = 1; i <= n_removed; i++)
            if (i in pick) printf "%s\t%s\t%s\n", path, "removed", removed[i]
    } else if (!created) {        # Nothing usable and nothing created. Say so explicitly — the caller
        # must be able to tell "no evidence" from "evidence, all satisfied".
        printf "%s\t%s\t\n", path, "noevidence"
    }
    for (i = 1; i <= n_added; i++) delete added[i]
    for (i = 1; i <= n_removed; i++) delete removed[i]
    # The dedupe sets are per SECTION, not per patch: the same text in two
    # different files is two independent claims (two files must each carry it),
    # while the same text twice in one file is one. Clearing here is what makes
    # the distinction.
    delete seen_added
    delete seen_removed
    n_added = 0
    n_removed = 0
    path = ""
    created = 0
}

END { if (NR != FNR) flush() }
