#!/bin/bash
# PS-349 arm runner. Detached from the agent shell deliberately: this
# environment reaps a foreground shell call that runs for minutes (PS-171's
# arm G was never taken for exactly this reason), so each arm is launched with
# setsid and polled from outside.
cd /workspace/persona/readings/ps349-2026-09-09 || exit 1
ARM="$1"; HOLD="$2"; LOOPS="${3:-6}"
export PS349_ARM="$ARM" PS349_HOLD="$HOLD" PS349_LOOPS="$LOOPS"
export PS349_OBS_OUT="$PWD/${ARM}.txt" PS349_OUT="/tmp/ps349/${ARM}.json"
xvfb-run -a python3 observe.py > "/tmp/ps349/${ARM}.console" 2>&1
echo "RC=$?" >> "/tmp/ps349/${ARM}.console"
