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
# ⭐ THE TWO RANKING RULES NEST — PS-374 FIRST, THEN PS-382 WITHIN IT
# ─────────────────────────────────────────────────────────────────────────────
# PS-374 and PS-382 landed against the same function from different tickets and
# were predicted to collide (PR #316's own escalation comment said so, and it
# was right). They answer DIFFERENT questions, so they compose rather than
# compete:
#
#     code beats comment   ← WHICH KIND of evidence fails together with the code
#     then longest first   ← HOW LIKELY an accidental upstream match is
#
# ⛔ TAKING EITHER SIDE WHOLESALE RE-OPENS THE OTHER TICKET'S DEFECT, and both
# tickets' tests would still pass on their own branch, so nothing would say so.
# MEASURED, and this is why the nesting is not a tidiness preference:
#
#     rule            fixes 014 (PS-382)?   fixes 001 (PS-374)?
#     length only     ✓                     ✗✗  WORSE than no rule at all
#     kind only       ✗                     ✓
#     nested          ✓                     ✓
#
# Length alone REGRESSES 001 because comments are prose and prose is long: a
# 77-char `// persona fingerprint: ...` outranks the 37-char
# `bool enabled() const { return false; }`, so ranking by length promotes
# precisely the evidence PS-374 demonstrated by sabotage is worthless. Ranking
# by length WITHIN each of PS-374's buckets keeps both properties: the code
# bucket is drained first, and the most specific code line in it wins.
#
# Both rationales are kept below in full rather than merged into a summary —
# each records a measured false verdict, and a reader arriving from either
# ticket needs the one they came for.

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
    if (t in upstream_writes) return 0
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# FILTER 5 (PS-408) — A LINE UNMODIFIED UPSTREAM ALREADY WRITES IS NOT EVIDENCE
# ─────────────────────────────────────────────────────────────────────────────
# ⛔ THIS IS THE ROOT CAUSE OF PS-408, AND NEITHER RANKING RULE REACHES IT.
#
# `014-client-rects.patch` adds `const auto [min, max] = Extents();` to
# `ui/gfx/geometry/quad_f.cc`. Chromium 152.0.7977.75 writes that same line at
# `quad_f.cc:171`, inside `QuadF::IntersectsRect` — a separating-axis test with
# nothing to do with our `QuadF::Offset`. Verified against the tag the build
# actually uses, not assumed:
#
#     curl -s "https://chromium.googlesource.com/chromium/src/+/152.0.7977.75/\
#       ui/gfx/geometry/quad_f.cc?format=TEXT" | base64 -d | grep -n '<the line>'
#     → 171:  const auto [min, max] = Extents();
#
# ⚠️ THE `?format=TEXT` + base64 PAIR IS THE NON-OBVIOUS BIT — googlesource has
# no raw endpoint and serves HTML otherwise, so piping it to grep "works" and
# matches syntax-highlighting markup instead of source.
#
# ⛔ WHY RANKING CANNOT FIX THIS, measured rather than reasoned about. The
# idiom is CODE, so PS-374's code-over-comment bucket never demotes it; it is 33
# characters, so PS-382's longest-first ranking placed it 3rd of 3 and kept it.
# PR #316 appeared to fix `014` only because a 24-char Chinese COMMENT happened
# to out-rank it by byte length — i.e. by promoting exactly the evidence PS-374
# demonstrated by sabotage is worthless. That was luck wearing the shape of a
# fix, and the nested rule (which is otherwise strictly better) removes the luck
# and puts the bad claim straight back:
#
#     rule           001 code claims   014 carries the upstream idiom?
#     kind only      2                 YES  ✗
#     length only    0  ✗✗             no   ✓   ← "fixed" by promoting prose
#     nested         2                 YES  ✗
#     nested + this  2                 no   ✓
#
# So the ranking rules and this filter are answering different questions and all
# three are kept. Ranking decides WHICH of several good candidates to spend the
# budget on; this decides whether a candidate is a candidate AT ALL.
#
# ⭐ THE SET IS MEASURED AND VENDORED, NOT GUESSED. `UPSTREAM_LINES` names a file
# of literal lines known to exist in unmodified upstream; `scripts/
# ps408_upstream_claim_sweep.sh` regenerates it by fetching every file our
# patches touch, at the pinned tag, and testing every `added` claim against it.
# The sweep over all 16 patches found EXACTLY ONE such line — this one — which
# is what makes a vendored list the right shape rather than a growing allowlist:
# it is not an exception carved out for a failing patch, it is the measured
# answer to "which of our claims does upstream already write?"
#
# ⛔ NOT AN ALLOWLIST OF PATCHES, AND THE DIFFERENCE IS THE WHOLE POINT. An
# allowlist would exempt `014` from checking and blind the instrument at a site
# it is built to watch. This drops ONE non-discriminating LINE and leaves every
# other claim `014` makes fully load-bearing — the patch still yields five
# claims, including `WithinEpsilon(width, 0.0f)` in the very same file.
#
# ⚠️ IT IS AN INPUT, NOT A HARDCODED TABLE: with `UPSTREAM_LINES` unset the
# filter is inert and the extractor behaves exactly as before, so no caller is
# silently changed and the file stays honest about what it was given.
function load_upstream_lines(   line, n) {
    if (UPSTREAM_LINES == "") return
    while ((getline line < UPSTREAM_LINES) > 0) {
        if (line ~ /^[ \t]*$/) continue
        if (line ~ /^#/) continue          # the file's own comments
        upstream_writes[strip(line)] = 1
        n++
    }
    close(UPSTREAM_LINES)
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

BEGIN { FS = "\n"; load_upstream_lines() }

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
    # ⭐ PS-374 + PS-382 NESTED. Candidates are collected into TWO ranked buckets
    # (code, comment) and the cap is applied at flush(), not here. Capping during
    # collection is what spent 001's whole budget on the comment block that
    # precedes each of its code lines — the cap was gone before the extractor
    # ever reached `bool enabled() const { return false; }`. Collection is
    # unbounded, selection is ranked, and the emitted COUNT is unchanged.
    #
    # ⚠️ DEDUPLICATED WITHIN THE SECTION, and this became load-bearing the moment
    # the corroboration rule in ps307_verify_patches_in_tree.sh made COUNT decide
    # a verdict. A patch can add the SAME text at several sites —
    # 014-client-rects.patch adds the identical `kDisableSpoofing ... find(
    # "clientrects")` guard line at three places in `element.cc` and `range.cc` —
    # and the verifier answers all of them with ONE `grep -F` for one string, so
    # they hold or fail TOGETHER. They are one piece of evidence recorded three
    # times. Under the old any-one rule that was merely wasteful; under a
    # threshold it is WRONG, because a single accidental match would supply two
    # or three "corroborating" claims and could carry the threshold by itself.
    # One text, one claim.
    if (c == "+") {
        if (!usable(t)) next
        if (t in pre) next          # present either way — proves nothing
        if (t in seen_added) next   # same string already collected here
        seen_added[t] = 1
        if (comment_shaped(t)) added_c[++n_added_c] = t
        else                        added_k[++n_added_k] = t
    } else if (c == "-") {
        if (!usable(t)) next
        if (t in post) next         # re-added by this same patch
        if (t in seen_removed) next
        seen_removed[t] = 1
        if (comment_shaped(t)) removed_c[++n_removed_c] = t
        else                        removed_k[++n_removed_k] = t
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
# ⭐ PS-374 + PS-382 NESTED — this is where the CAP is applied, and where the two
# ranking rules compose. `emit_ranked` fills the budget from the CODE bucket
# first and tops it up from the COMMENT bucket only if room remains (PS-374), and
# WITHIN each bucket it takes the LONGEST candidates (PS-382). So a hunk with one
# code line and three comment lines yields the code line, and a hunk with four
# code lines yields the three most specific of them.
#
# ⛔ THE ORDER OF THE TWO IS NOT INTERCHANGEABLE. Length-first regresses 001:
# comments are prose and prose is long, so a 77-char `// persona fingerprint:`
# outranks the 37-char `bool enabled() const { return false; }` and the extractor
# goes back to emitting the exact evidence PS-374 proved by sabotage is
# worthless. Kind-first, length-within-kind keeps both properties.
#
# At most MAX_PER_FILE claims are emitted per file per kind, exactly as before —
# this changes WHICH candidates are taken, never HOW MANY, so the check's cost is
# unchanged.
function emit_ranked(kind, code_n, code_a, cmt_n, cmt_a,    i, taken, pick) {
    taken = 0
    # The code bucket, longest first, emitted in FILE ORDER among the selected so
    # the evidence still reads down the patch even though it was chosen by length.
    if (code_n > 0) {
        delete pick
        select_longest(code_a, code_n, pick)
        for (i = 1; i <= code_n && taken < MAX_PER_FILE; i++) {
            if (!(i in pick)) continue
            printf "%s\t%s\t%s\n", path, kind, code_a[i]
            taken++
        }
    }
    # Comments are a FALLBACK, not an exclusion: a hunk that genuinely adds only
    # prose must still be checkable. Same length ranking within the bucket.
    if (taken < MAX_PER_FILE && cmt_n > 0) {
        delete pick
        select_longest(cmt_a, cmt_n, pick)
        for (i = 1; i <= cmt_n && taken < MAX_PER_FILE; i++) {
            if (!(i in pick)) continue
            printf "%s\t%s\t%s\n", path, kind, cmt_a[i]
            taken++
        }
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
    # The dedupe sets are per SECTION, not per patch: the same text in two
    # different files is two independent claims (two files must each carry it),
    # while the same text twice in one file is one. Clearing here is what makes
    # the distinction.
    delete seen_added
    delete seen_removed
    n_added_k = 0
    n_added_c = 0
    n_removed_k = 0
    n_removed_c = 0
    path = ""
    created = 0
}

END { if (NR != FNR) flush() }
