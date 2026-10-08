#!/bin/bash
# Ollama model server for the reading group assistant, on one Nexus GPU.
# Submitted by scripts/nexus_up.sh (which fills the #SBATCH options from the command line).
# Installs Ollama into scratch on first run; models are cached in scratch too.
#SBATCH --job-name=rga-ollama
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --output=%x-%j.log

set -euo pipefail
SCRATCH="${RGA_SCRATCH:-/fs/nexus-scratch/$USER}/rga-ollama"
MODELS="${RGA_MODELS:-qwen3.8:27b}"
NUM_CTX="${RGA_NUM_CTX:-32768}"
mkdir -p "$SCRATCH/bin" "$SCRATCH/models"

if [ ! -x "$SCRATCH/bin/ollama" ]; then
  echo "ollama is not installed in $SCRATCH (scripts/nexus_up.sh installs it from the login node)"; exit 1
fi

# A free port on this node. The server listens on the node's cluster address so the login
# node can forward to it; it serves only model inference, no files or shell.
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("",0)); print(s.getsockname()[1])')
export OLLAMA_HOST="0.0.0.0:$PORT"
export OLLAMA_MODELS="$SCRATCH/models"
# answers, hand checks (every few seconds) and hand text (with thinking) overlap; with too few
# slots an answer waits behind them. The model uses ~18 of 48 GB, so 4 slots fit.
export OLLAMA_NUM_PARALLEL="${RGA_NUM_PARALLEL:-4}"
export OLLAMA_KEEP_ALIVE=24h
export OLLAMA_FLASH_ATTENTION=1

nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
"$SCRATCH/bin/ollama" serve &
SERVER=$!
for _ in $(seq 60); do curl -fs "http://127.0.0.1:$PORT/api/version" >/dev/null && break; sleep 1; done

for m in $MODELS; do
  "$SCRATCH/bin/ollama" pull "$m"
  # load it onto the GPU now so the first meeting request is fast
  curl -fs "http://127.0.0.1:$PORT/api/generate" -d "{\"model\":\"$m\",\"prompt\":\"hi\",\"stream\":false,\"think\":false,\"options\":{\"num_ctx\":$NUM_CTX,\"num_predict\":1}}" >/dev/null
done

# nexus_up.sh waits for this line
echo "RGA_READY node=$(hostname -s) port=$PORT models=$MODELS"
wait $SERVER
