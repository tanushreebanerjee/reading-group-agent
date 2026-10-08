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
ACCOUNT="${RGA_NEXUS_ACCOUNT:-vulcan-zwicker}"  # vulcan-ampere only allows lab accounts
PARTITION="${RGA_NEXUS_PARTITION:-vulcan-ampere}"
QOS="${RGA_NEXUS_QOS:-vulcan-default}"           # vulcan-default-h200 for the H200 node
GRES="${RGA_NEXUS_GRES:-gpu:rtxa6000:1}"         # 48 GB: 32B model + whole paper in context
TIME="${RGA_NEXUS_TIME:-04:00:00}"
MODELS="${RGA_NEXUS_MODELS:-qwen3:32b}"
NUM_CTX="${RGA_NEXUS_NUM_CTX:-32768}"            # must match num_ctx in the Nexus preset
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
job_state() { remote "squeue -h -j $1 -o '%T %N %r'" 2>/dev/null || true; }
ready_line() { remote "grep -h RGA_READY ~/rga/rga-ollama-$1.log | tail -1" 2>/dev/null || true; }
tunnel_ok() { curl -fs -m 3 "http://127.0.0.1:$LOCAL_PORT/api/version" >/dev/null 2>&1; }

if [ "${1:-}" = "--check" ]; then
  JOB=$(cat "$STATE/job" 2>/dev/null || true)
  echo "job: ${JOB:-none}"
  if "${SSH[@]}" -O check "$HOST" 2>/dev/null; then
    echo "ssh: connected"
    if [ -n "$JOB" ]; then
      echo "state: $(job_state "$JOB")"
      echo "server: $(ready_line "$JOB")"
    fi
  else
    echo "ssh: not connected (run scripts/nexus_up.sh)"
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
  # one-time: install Ollama into scratch from the login node (it has zstd; batch nodes may not)
  remote "D=/fs/nexus-scratch/\$USER/rga-ollama; [ -x \$D/bin/ollama ] && exit 0; mkdir -p \$D && echo 'installing ollama into '\$D && \
          curl -fsSL https://ollama.com/download/ollama-linux-amd64.tar.zst | zstd -d | tar -x -C \$D && \$D/bin/ollama --version"
  if ! JOB=$(remote "cd ~/rga && sbatch --parsable --account=$ACCOUNT --partition=$PARTITION --qos=$QOS \
         --gres=$GRES --time=$TIME --export=ALL,RGA_MODELS='$MODELS',RGA_NUM_CTX=$NUM_CTX serve_ollama.sbatch"); then
    echo "sbatch rejected $ACCOUNT / $PARTITION / $QOS / $GRES. What $PARTITION allows, and your accounts:"
    remote "scontrol show partition $PARTITION -o | tr ' ' '\n' | grep -E '^Allow(Accounts|Qos)='; \
            sacctmgr -nP show assoc user=\$USER format=account,qos" || true
    echo "Re-run with RGA_NEXUS_ACCOUNT=... RGA_NEXUS_QOS=... scripts/nexus_up.sh"
    exit 1
  fi
  JOB=$(echo "$JOB" | tail -1)
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

# (re)open the tunnel through the existing connection: no new Duo prompt. Close the tunnel
# this script opened last time first (it may point at an old job's node and port).
for spec in "$(cat "$STATE/forward" 2>/dev/null)" "$LOCAL_PORT:$NODE:$PORT"; do
  [ -n "$spec" ] && "${SSH[@]}" -O cancel -L "$spec" "$HOST" 2>/dev/null || true
done
"${SSH[@]}" -O forward -L "$LOCAL_PORT:$NODE:$PORT" "$HOST"
echo "$LOCAL_PORT:$NODE:$PORT" > "$STATE/forward"
for _ in 1 2 3 4 5; do tunnel_ok && break; sleep 1; done
if tunnel_ok; then
  echo "ready: $MODELS on $NODE, at http://127.0.0.1:$LOCAL_PORT"
  echo "start the assistant with --profile nexus (see README > Meeting day)"
else
  echo "tunnel did not come up (node $NODE port $PORT)"; exit 1
fi
