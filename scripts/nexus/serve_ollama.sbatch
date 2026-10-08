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
MODELS="${RGA_MODELS:-qwen2.5:32b}"
mkdir -p "$SCRATCH/bin" "$SCRATCH/models"

if [ ! -x "$SCRATCH/bin/ollama" ]; then
  echo "installing ollama into $SCRATCH"
  curl -fsSL https://ollama.com/download/ollama-linux-amd64.tgz | tar -xz -C "$SCRATCH"
fi

# A free port on this node. The server listens on the node's cluster address so the login
# node can forward to it; it serves only model inference, no files or shell.
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("",0)); print(s.getsockname()[1])')
export OLLAMA_HOST="0.0.0.0:$PORT"
export OLLAMA_MODELS="$SCRATCH/models"
export OLLAMA_NUM_PARALLEL=2          # an answer can run while a trigger check is in flight
export OLLAMA_KEEP_ALIVE=24h
export OLLAMA_FLASH_ATTENTION=1

nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
"$SCRATCH/bin/ollama" serve &
SERVER=$!
for _ in $(seq 60); do curl -fs "http://127.0.0.1:$PORT/api/version" >/dev/null && break; sleep 1; done

for m in $MODELS; do
  "$SCRATCH/bin/ollama" pull "$m"
  # load it onto the GPU now so the first meeting request is fast
  curl -fs "http://127.0.0.1:$PORT/api/generate" -d "{\"model\":\"$m\",\"prompt\":\"hi\",\"stream\":false,\"options\":{\"num_ctx\":8192,\"num_predict\":1}}" >/dev/null
done

# nexus_up.sh waits for this line
echo "RGA_READY node=$(hostname -s) port=$PORT models=$MODELS"
wait $SERVER
