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
NUM_CTX="${RGA_NUM_CTX:-32768}"     # answers: whole paper + brief + transcript
HANDS_CTX="${RGA_HANDS_CTX:-8192}"  # hand checks and hand text: brief + excerpts + transcript
mkdir -p "$SCRATCH/bin" "$SCRATCH/models"

if [ ! -x "$SCRATCH/bin/ollama" ]; then
  echo "ollama is not installed in $SCRATCH (scripts/nexus_up.sh installs it from the login node)"; exit 1
fi

# Two Ollama servers on this GPU, each on a free port. The servers listen on the node's cluster
# address so the login node can forward to them; they serve only model inference.
#   answers: one slot, only ever the whole-paper answer prompt, so its cached paper is never
#            displaced (qwen3.x models can't reuse a cached prefix once another prompt type used it)
#   hands:   hand checks and hand text, short prompts (brief + excerpts), 2 slots
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("",0)); print(s.getsockname()[1])'; }
PORT_ANSWERS=$(free_port); PORT_HANDS=$(free_port)
export OLLAMA_MODELS="$SCRATCH/models"
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_KEEP_ALIVE=24h
export OLLAMA_FLASH_ATTENTION=1
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

OLLAMA_HOST="0.0.0.0:$PORT_ANSWERS" OLLAMA_NUM_PARALLEL=1 "$SCRATCH/bin/ollama" serve > answers-server.log 2>&1 &
A=$!
OLLAMA_HOST="0.0.0.0:$PORT_HANDS" OLLAMA_NUM_PARALLEL=2 "$SCRATCH/bin/ollama" serve > hands-server.log 2>&1 &
H=$!
for port in $PORT_ANSWERS $PORT_HANDS; do
  for _ in $(seq 60); do curl -fs "http://127.0.0.1:$port/api/version" >/dev/null && break; sleep 1; done
done

for m in $MODELS; do
  OLLAMA_HOST="127.0.0.1:$PORT_ANSWERS" "$SCRATCH/bin/ollama" pull "$m"
  # load onto the GPU now so the first meeting request is fast (answers: long context; hands: short)
  curl -fs "http://127.0.0.1:$PORT_ANSWERS/api/generate" -d "{\"model\":\"$m\",\"prompt\":\"hi\",\"stream\":false,\"think\":false,\"options\":{\"num_ctx\":$NUM_CTX,\"num_predict\":1}}" >/dev/null
  curl -fs "http://127.0.0.1:$PORT_HANDS/api/generate" -d "{\"model\":\"$m\",\"prompt\":\"hi\",\"stream\":false,\"think\":false,\"options\":{\"num_ctx\":$HANDS_CTX,\"num_predict\":1}}" >/dev/null
done

# nexus_up.sh waits for this line
echo "RGA_READY node=$(hostname -s) port=$PORT_ANSWERS hands_port=$PORT_HANDS models=$MODELS"
wait $A $H
