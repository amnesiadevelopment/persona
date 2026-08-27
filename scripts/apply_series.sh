#!/usr/bin/env bash
# Sequentially apply the 127-patch series against a fetched minimal Chromium tree
# and classify each patch. Usage: apply_series.sh <treedir> <label>
#
# Classification per patch:
#   CLEAN     - applies with zero fuzz, zero offset
#   OFFSET    - applies but hunks shifted (rebase noise, no human needed)
#   FUZZ      - applies only with fuzz (context drifted; human should eyeball)
#   CONFLICT  - does NOT apply; requires manual rebase work
#   NO_TARGET - target file absent from our minimal tree (not evaluable here)
set -u
TREE="$1"; LABEL="$2"
RES="results-${LABEL}.tsv"
: > "$RES"
printf "patch\tstatus\tfiles_total\tfiles_missing\thunks\thunks_failed\tdetail\n" >> "$RES"

while read -r p; do
  [ -z "$p" ] && continue
  PF="patches/$p"
  # which files does this patch touch, and are they in the tree?
  mapfile -t tf < <(grep -E '^\+\+\+ ' "$PF" | sed -E 's|^\+\+\+ (b/)?||' | awk '{print $1}' | grep -v '^/dev/null' | sort -u)
  total=${#tf[@]}; missing=0
  for f in "${tf[@]}"; do [ -f "$TREE/$f" ] || missing=$((missing+1)); done

  hunks=$(grep -c '^@@' "$PF" || true)

  if [ "$total" -gt 0 ] && [ "$missing" -eq "$total" ]; then
    # every target absent -> created by an earlier patch or pruned; try anyway
    :
  fi

  out=$(patch -p1 -d "$TREE" --forward --no-backup-if-mismatch -F0 < "$PF" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    if echo "$out" | grep -q 'with fuzz'; then st=FUZZ
    elif echo "$out" | grep -q 'offset'; then st=OFFSET
    else st=CLEAN; fi
    failed=0
  else
    # retry allowing fuzz, to separate "needs fuzz" from "genuinely conflicts"
    patch -p1 -d "$TREE" --forward --no-backup-if-mismatch --dry-run -F3 < "$PF" >/dev/null 2>&1
    if [ $? -eq 0 ]; then
      patch -p1 -d "$TREE" --forward --no-backup-if-mismatch -F3 < "$PF" >/dev/null 2>&1
      st=FUZZ; failed=0
    else
      st=CONFLICT
      failed=$(echo "$out" | grep -c 'FAILED at' || true)
    fi
  fi
  if [ "$total" -gt 0 ] && [ "$missing" -eq "$total" ] && [ "$st" = "CONFLICT" ]; then
    st=NO_TARGET
  fi
  detail=$(echo "$out" | grep -E 'FAILED at|can.t find file|No such file' | head -2 | tr '\n' ';' | cut -c1-160)
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$p" "$st" "$total" "$missing" "$hunks" "$failed" "$detail" >> "$RES"
done < series.txt

echo "=== $LABEL ==="
awk -F'\t' 'NR>1{c[$2]++} END{for(k in c) printf "  %-10s %s\n", k, c[k]}' "$RES"
