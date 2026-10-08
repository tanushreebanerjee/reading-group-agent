#!/bin/bash
# Start (or reattach to) the assistant's model server on a Nexus GPU and tunnel it to
# http://127.0.0.1:11435 on this Mac. Connect the UMD VPN first.
#
#   scripts/nexus_up.sh            # submit the job if needed, wait until ready, open the tunnel
#   scripts/nexus_up.sh --check    # show job and tunnel status
#   scripts/nexus_down.sh          # cancel the job and close the connection (after the meeting)
#
# Uses ONE ssh connection for everything (one Duo approval per run). Re-run it after a
# VPN drop: it finds the running job and only reopens the tunnel.
# Settings (override with environment variables):
HOST="${RGA_NEXUS_HOST:-umiacs}"                 # ssh alias for the login node
ACCOUNT="${RGA_NEXUS_ACCOUNT:-vulcan}"
PARTITION="${RGA_NEXUS_PARTITION:-vulcan-ampere}"
QOS="${RGA_NEXUS_QOS:-vulcan-default}"
GRES="${RGA_NEXUS_GRES:-gpu:1}"                  # e.g. gpu:rtxa6000:1 to pin a 48 GB card
TIME="${RGA_NEXUS_TIME:-04:00:00}"
MODELS="${RGA_NEXUS_MODELS:-qwen2.5:32b}"
LOCAL_PORT="${RGA_NEXUS_PORT:-11435}"            # local Ollama keeps 11434

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STATE="$HOME/.cache/rga-nexus"; mkdir -p "$STATE"
SOCK="$STATE/ssh-%C"
SSH=(ssh -o ControlPath="$SOCK" -o ServerAliveInterval=30 -o ServerAliveCountMax=3)

connect() {
  if "${SSH[@]}" -O check "$HOST" 2>/dev/null; then return; fi
  echo "connecting to $HOST (approve the Duo prompt)..."
  "${SSH[@]}" -fN -o ControlMaster=yes -o ControlPersist=8h "$HOST"
}
remote() { "${SSH[@]}" "$HOST" "$@"; }
job_state() { remote "squeue -h -j $1 -o '%T %N %r' 2>/dev/null" || true; }
ready_line() { remote "grep -h RGA_READY ~/rga/rga-ollama-$1.log 2>/dev/null | tail -1" || true; }
tunnel_ok() { curl -fs -m 3 "http://127.0.0.1:$LOCAL_PORT/api/version" >/dev/null 2>&1; }

if [ "${1:-}" = "--check" ]; then
  JOB=$(cat "$STATE/job" 2>/dev/null || true)
  echo "job: ${JOB:-none}"
  if "${SSH[@]}" -O check "$HOST" 2>/dev/null && [ -n "$JOB" ]; then
    echo "state: $(job_state "$JOB")"
    echo "server: $(ready_line "$JOB")"
  else
    echo "ssh: not connected"
  fi
  tunnel_ok && echo "tunnel: ok (http://127.0.0.1:$LOCAL_PORT)" || echo "tunnel: down"
  exit 0
fi

connect

JOB=$(cat "$STATE/job" 2>/dev/null || true)
if [ -n "$JOB" ] && [ -n "$(job_state "$JOB")" ]; then
  echo "reusing job $JOB"
else
  remote "mkdir -p ~/rga && cat > ~/rga/serve_ollama.sbatch" < "$HERE/nexus/serve_ollama.sbatch"
  JOB=$(remote "cd ~/rga && sbatch --parsable --account=$ACCOUNT --partition=$PARTITION --qos=$QOS \
         --gres=$GRES --time=$TIME --export=ALL,RGA_MODELS='$MODELS' serve_ollama.sbatch")
  echo "$JOB" > "$STATE/job"
  echo "submitted job $JOB ($PARTITION, $GRES, $TIME)"
fi

# wait for the job to start and the model to be loaded
LAST=""
while true; do
  ST=$(job_state "$JOB")
  if [ -z "$ST" ]; then
    echo "job $JOB is gone. Last log lines:"; remote "tail -20 ~/rga/rga-ollama-$JOB.log" || true
    rm -f "$STATE/job"; exit 1
  fi
  READY=$(ready_line "$JOB")
  [ -n "$READY" ] && break
  MSG="$ST | $(remote "tail -1 ~/rga/rga-ollama-$JOB.log 2>/dev/null | tr '\r' '\n' | tail -1 | cut -c1-90" || true)"
  [ "$MSG" != "$LAST" ] && echo "  $MSG"; LAST="$MSG"
  sleep 10
done
NODE=$(echo "$READY" | sed -E 's/.*node=([^ ]+).*/\1/')
PORT=$(echo "$READY" | sed -E 's/.*port=([0-9]+).*/\1/')

# (re)open the tunnel through the existing connection: no new Duo prompt
"${SSH[@]}" -O cancel -L "$LOCAL_PORT:$NODE:$PORT" "$HOST" 2>/dev/null || true
"${SSH[@]}" -O forward -L "$LOCAL_PORT:$NODE:$PORT" "$HOST"
for _ in 1 2 3 4 5; do tunnel_ok && break; sleep 1; done
if tunnel_ok; then
  echo "ready: $MODELS on $NODE, at http://127.0.0.1:$LOCAL_PORT"
  echo "pick \"Nexus · Qwen2.5 32B\" on the control page, or see README > Nexus GPU for making it the default"
else
  echo "tunnel did not come up (node $NODE port $PORT)"; exit 1
fi
