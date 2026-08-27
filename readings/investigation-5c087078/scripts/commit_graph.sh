#!/usr/bin/env bash
# Derive the commit-graph structure of adryfish/fingerprint-chromium.
#
# ⚠️ REQUIRES A FULL CLONE. An earlier pass of this investigation ran this
# reasoning against a `git fetch --depth 1` tree and concluded that every source
# tag was a parentless orphan sharing no history. That was FALSE — it was a
# shallow-clone artefact. In a shallow tree the parent commits are simply not
# present, so `git rev-list --parents` shows none and `git merge-base` finds none.
# The clone below is deliberately NOT shallow.
#
# Emits: commit-graph.json
set -eu
OUT="${1:-/tmp/fpc-full}"

if [ ! -d "$OUT/.git" ]; then
  git clone --quiet https://github.com/adryfish/fingerprint-chromium.git "$OUT"
fi
cd "$OUT"
git fetch --quiet --tags origin
if [ "$(git rev-parse --is-shallow-repository)" != "false" ]; then
  echo "FATAL: repository is shallow; run 'git fetch --unshallow'." >&2
  exit 1
fi

python3 - <<'PY' > /dev/null
PY

{
  echo "{"
  echo "  \"shallow\": false,"
  echo "  \"branches\": [$(git branch -r --format='%(refname:short)' | grep '^origin/' | grep -v HEAD | sed 's|^origin/||' | awk '{printf "%s\"%s\"", (NR>1?", ":""), $0}')],"
  echo "  \"tags\": ["
  first=1
  for t in $(git tag | sort -V); do
    c=$(git rev-parse $t^{commit})
    p=$(git rev-list --parents -n1 $c | cut -d' ' -f2-)
    n=$(git rev-list --count $c)
    r=$(git rev-list --max-parents=0 $c | tr '\n' ' ' | sed 's/ $//')
    d=$(git log -1 --format=%cs $c)
    files=$(git ls-tree --name-only $c | wc -l)
    [ $first -eq 0 ] && echo ","
    first=0
    printf '    {"tag": "%s", "commit": "%s", "parents": "%s", "commits_reachable": %s, "root": "%s", "commit_date": "%s", "toplevel_entries": %s}' \
      "$t" "$c" "$p" "$n" "$r" "$d" "$files"
  done
  echo ""
  echo "  ],"
  echo "  \"merge_bases\": {"
  printf '    "main..144.0.7559.132": "%s",\n' "$(git merge-base main 144.0.7559.132 2>/dev/null || echo none)"
  printf '    "main..148.0.7778.215": "%s",\n' "$(git merge-base main 148.0.7778.215 2>/dev/null || echo none)"
  printf '    "142.0.7444.175..144.0.7559.132": "%s"\n' "$(git merge-base 142.0.7444.175 144.0.7559.132 2>/dev/null || echo none)"
  echo "  },"
  echo "  \"ancestry\": {"
  printf '    "t148_is_ancestor_of_main": %s,\n' "$(git merge-base --is-ancestor 148.0.7778.215 main && echo true || echo false)"
  printf '    "t142_is_ancestor_of_t144": %s,\n' "$(git merge-base --is-ancestor 142.0.7444.175 144.0.7559.132 && echo true || echo false)"
  printf '    "t144_is_ancestor_of_branch144": %s\n' "$(git merge-base --is-ancestor 144.0.7559.132 origin/144.0.7559.132 && echo true || echo false)"
  echo "  }"
  echo "}"
}
