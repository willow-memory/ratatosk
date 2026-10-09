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

## `--onescript` — the one script's model seat

```bash
ratatosk --onescript --served served.json --out proposals.jsonl \
    [--model gemma3:4b] [--rung ollama] [--ctx 4096] "<task>"

# the desk's cloud turn on a card the chain could not close
ratatosk --onescript --class flowering --served piece.json --out proposals.jsonl "<task>"
```

One local model turn over a document serve wrote. It is a different program
sharing the entry point: no MCP, no Grove events, no session JSONL, no hooks,
no `CLAUDE.md` or repo file in the prompt, none of the built-in tools. The
model sees one fixed system prompt, the served document as framed data in the
user turn, and one tool.

| Rule | What it means |
|------|---------------|
| Local rungs only | A rung is local when its `base_url` is loopback and its dialect is `ollama` or `openai` (an OpenAI-compatible server such as llama.cpp). A cloud rung, a cloud model name, or a ladder with no local rung is refused with exit 2 before any request. |
| `--class flowering` | The one way past local-only: the ladder's `flowering` class names the rungs, models and order (free tiers only). A rate limit, overload, timeout or transport failure before any row is written steps to the next rung; the summary line gains `stepped=rung:end,…`. `--rung` and `--model` are refused beside it. Every other rule here holds. |
| One tool, one write | `propose(path, data, cites, claim)` appends one line `{"path", "data", "cites", "claim"}` to `--out`. That file is the only thing written. Any other tool name is refused and recorded in the transcript, never run. A malformed call (absolute or `..` path, non-list `cites`, over-size field) is handed back as `refused:` and not written. At most 16 proposals per run. |
| Three states | `populated`, `empty` and `unreachable` reach the model as themselves. A missing, unreadable or non-served file is `unreachable`; serve's own `empty` stays `empty`. `<` is escaped so served text cannot close the frame, and serve's `return` line is carried through. |
| Budget | The served block is sized from the model's context: `--ctx`, else the rung's own answer (Ollama `/api/show` `num_ctx`, llama.cpp `/props`), else 4096 (the phone budget), minus the frame, the task and an answer reserve. Over budget the block becomes `empty` ("narrow the stack"). It is never truncated. For Ollama the measured window is sent as `num_ctx`, so the budget is the window the model runs with. |
| Bounded | `--max-turns` (default 4) and `--wall-clock` seconds (default 180). A cap or a timeout ends the turn with a recorded reason. |
| No path | The served file's path is read by code and goes nowhere else: not into the prompt, not into the summary line. |

The last line printed is the summary: `[onescript] model=… rung=… ctx=… (flag|rung|default) served=… proposals=N turns=T end=…`.
`-v` prints the transcript (including refused tool calls) to stderr; nothing is written for it.
End reasons: `done`, `turn_cap`, `wall_clock`, `timeout`, `provider_error:<kind>`, `crash:<Class>`.
Exit status: 0 the model finished, 1 the turn ended early, 2 refused before any request.
`--out` is created empty when the run starts, so "ran, proposed nothing" differs from "never ran".

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
