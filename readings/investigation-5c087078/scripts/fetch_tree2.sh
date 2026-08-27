#!/usr/bin/env bash
# Fetch touched files at a Chromium tag, WITH RETRIES and modest parallelism.
# The first version of this used -P 24 and silently lost files to rate limiting:
# the control run at 144 showed 84 "absent" files, of which most returned 200 on
# a serial retry. Only genuinely-404 paths are real absences (they are generated
# by ungoogled's build tooling or created by the patch series, not upstream).
# Usage: fetch_tree2.sh <chromium_tag> <outdir> <filelist>
set -u
TAG="$1"; OUT="$2"; LIST="$3"
mkdir -p "$OUT"

fetch_one() {
  f="$1"; tag="$2"; out="$3"
  dest="$out/$f"
  [ -s "$dest" ] && { echo "OK $f"; return; }
  mkdir -p "$(dirname "$dest")"
  for attempt in 1 2 3 4; do
    code=$(curl -s -o "$dest.b64" -w "%{http_code}" --max-time 60 \
       "https://chromium.googlesource.com/chromium/src/+/refs/tags/${tag}/${f}?format=TEXT")
    if [ "$code" = "200" ] && [ -s "$dest.b64" ]; then
      if base64 -d "$dest.b64" > "$dest" 2>/dev/null; then
        rm -f "$dest.b64"; echo "OK $f"; return
      fi
    fi
    if [ "$code" = "404" ]; then
      rm -f "$dest.b64"; echo "404 $f"; return
    fi
    sleep $((attempt * 2))
  done
  rm -f "$dest.b64" "$dest"
  echo "FAIL $f"
}
export -f fetch_one

xargs -a "$LIST" -P 6 -I{} bash -c 'fetch_one "$@"' _ {} "$TAG" "$OUT" \
  > "fetchlog2-${TAG}.txt" 2>&1

echo "tag=$TAG present=$(grep -c '^OK ' "fetchlog2-${TAG}.txt") real404=$(grep -c '^404 ' "fetchlog2-${TAG}.txt") failed=$(grep -c '^FAIL ' "fetchlog2-${TAG}.txt")"
