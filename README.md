# Ratatosk

Platform session runtime for the Willow fleet — the process that runs agent
turns on any node (desk, laptop, Termux phone) and speaks MCP to `willow-mcp`.

## Role

Ratatosk is **not** memory (Nestor), **not** the desk (Grove). It is the
**session process**: prompt → tools → loop, Grove lifecycle events, JSONL
working set, tier-0 deposit sync.

## Install

```bash
pip install "willow-ratatosk[mcp,cloud,local]"
# development
pip install -e ".[dev]"
```

## Run

```bash
ratatosk --local
ratatosk --mcp
ratatosk --mcp --listen
ratatosk --mcp --deposit
```

## Environment

| Var | Default | Purpose |
|-----|---------|---------|
| `RATATOSK_GROVE_CHANNEL` | _(disabled)_ | Grove channel for session events |
| `RATATOSK_MCP_COMMAND` | `python -m willow_mcp` | MCP server command |
| `RATATOSK_APP_ID` | `ratatosk` | MCP `app_id` |
| `WILLOW_HOME` | `$WILLOW_HOME` or XDG share | Session data under `$WILLOW_HOME/ratatosk/` |
| `OLLAMA_URL` | `http://localhost:11434` | Local inference |
| `ANTHROPIC_API_KEY` | — | Cloud mode |

## willow-mcp registration

Ratatosk is a **platform session identity**, not a dispatch specialist. Install
its manifest under `$WILLOW_HOME/mcp_apps/ratatosk/manifest.json` (example in
`docs/mcp-manifest.example.json`). Without this, Grove tools deny with
`app_id=ratatosk`.

## Org home

Canonical repo: `willow-memory/ratatosk` (Platform face, sibling to
`willow-mcp` and `kartikeya`).

Playground launcher: `safe-app-store-public/apps/ratatosk`.
