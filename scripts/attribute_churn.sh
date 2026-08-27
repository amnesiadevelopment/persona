#!/usr/bin/env bash
# Attribute FAILED hunks to the FILE they failed in, for one run.
#
# This replays `apply_series.sh` EXACTLY -- same order, same -F0 attempt, same
# -F3 fuzz retry -- so the tree evolves identically and the totals reconcile to
# results-<label>.tsv by construction. The only addition is that the -F0 output
# is parsed for `patching file <path>` / `Hunk #N FAILED`, so each failed hunk is
# charged to the file `patch` was working on when it failed.
#
# Attribution rule (matches results-*.tsv `hunks_failed` semantics exactly):
#   a hunk is counted ONLY when the patch's final status is CONFLICT.
#   CLEAN/OFFSET/FUZZ patches contribute 0, because they applied.
#
# Usage: attribute_churn.sh <treedir> <label>
# Emits: churn-<label>.json  and  churn-<label>-perfile.tsv
set -u
TREE="$1"; LABEL="$2"
RAW="churn-${LABEL}-raw.tsv"
: > "$RAW"

while read -r p; do
  [ -z "$p" ] && continue
  PF="patches/$p"

  mapfile -t tf < <(grep -E '^\+\+\+ ' "$PF" | sed -E 's|^\+\+\+ (b/)?||' | awk '{print $1}' | grep -v '^/dev/null' | sort -u)
  total=${#tf[@]}; missing=0
  for f in "${tf[@]}"; do [ -f "$TREE/$f" ] || missing=$((missing+1)); done

  out=$(patch -p1 -d "$TREE" --forward --no-backup-if-mismatch -F0 < "$PF" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    continue   # CLEAN / OFFSET / FUZZ-free: applied, contributes nothing
  fi

  # retry with fuzz, exactly as apply_series.sh does
  if patch -p1 -d "$TREE" --forward --no-backup-if-mismatch --dry-run -F3 < "$PF" >/dev/null 2>&1; then
    patch -p1 -d "$TREE" --forward --no-backup-if-mismatch -F3 < "$PF" >/dev/null 2>&1
    continue   # FUZZ: applied, contributes nothing
  fi

  # genuine CONFLICT -- but NO_TARGET (every target absent) is not evaluable
  if [ "$total" -gt 0 ] && [ "$missing" -eq "$total" ]; then
    continue
  fi

  # charge each FAILED hunk to the file patch was on at the time
  echo "$out" | awk -v P="$p" '
    /^patching file /      { cur = $3; next }
    /^Hunk #.* FAILED at /  { if (cur != "") print P "\t" cur }
  ' >> "$RAW"
done < series.txt

python3 - "$LABEL" <<'PY'
import sys, json, collections
label = sys.argv[1]
raw = f"churn-{label}-raw.tsv"
per_file = collections.Counter()
per_patch = collections.Counter()
for line in open(raw):
    line = line.rstrip("\n")
    if not line: continue
    patch, f = line.split("\t", 1)
    per_file[f] += 1
    per_patch[patch] += 1

def area(f):
    if f.startswith("third_party/blink/"): return "third_party/blink (Blink)"
    if f.startswith("chrome/browser/"):    return "chrome/browser"
    if f.startswith("chrome/"):            return "chrome/ (other)"
    if f.startswith("components/"):        return "components"
    if f.startswith("content/"):           return "content"
    if f.startswith("services/"):          return "services"
    if f.startswith("net/"):               return "net"
    if f.startswith("ui/"):                return "ui"
    return "other"

per_area = collections.Counter()
for f, n in per_file.items():
    per_area[area(f)] += n

total = sum(per_file.values())
out = {
    "run": label,
    "failed_hunks_total": total,
    "reconciles_to": f"results-{label}.tsv column `hunks_failed`",
    "by_area": dict(per_area.most_common()),
    "by_file": [{"file": f, "failed_hunks": n} for f, n in per_file.most_common()],
    "by_patch": [{"patch": p, "failed_hunks": n} for p, n in per_patch.most_common()],
}
json.dump(out, open(f"churn-{label}.json", "w"), indent=1)
print(f"{label}: total={total} area_sum={sum(per_area.values())} files={len(per_file)} patches={len(per_patch)}")
PY
