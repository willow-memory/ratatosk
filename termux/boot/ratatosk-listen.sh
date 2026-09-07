#!/data/data/com.termux/files/usr/bin/bash
# Ratatosk tier-0 listener — requires MCP + Grove channel on phone.
set -euo pipefail
if [ -z "${RATATOSK_GROVE_CHANNEL:-}" ]; then
  echo "ratatosk-listen: RATATOSK_GROVE_CHANNEL unset — refusing to boot." >&2
  echo "  Set it to this node's channel; there is no safe default." >&2
  exit 1
fi
exec python -m ratatosk.crown --mcp --listen
