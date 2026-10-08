#!/bin/bash
# Cancel the assistant's Nexus GPU job and close the ssh connection. Run after the meeting
# so the GPU is freed for others.
HOST="${RGA_NEXUS_HOST:-umiacs}"
STATE="$HOME/.cache/rga-nexus"
SSH=(ssh -o ControlPath="$STATE/ssh-%C")
JOB=$(cat "$STATE/job" 2>/dev/null || true)
if [ -n "$JOB" ]; then
  if "${SSH[@]}" -O check "$HOST" 2>/dev/null || { echo "connecting to cancel job $JOB (approve Duo)..."; true; }; then
    "${SSH[@]}" "$HOST" "scancel $JOB" && echo "cancelled job $JOB"
  fi
  rm -f "$STATE/job"
else
  echo "no job recorded"
fi
"${SSH[@]}" -O exit "$HOST" 2>/dev/null && echo "closed ssh connection" || true
