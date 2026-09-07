#!/data/data/com.termux/files/usr/bin/bash
# Ratatosk tier-0 listener — requires MCP + Grove channel on phone.
set -euo pipefail
export RATATOSK_GROVE_CHANNEL="${RATATOSK_GROVE_CHANNEL:-general}"
exec python -m ratatosk.crown --mcp --listen
