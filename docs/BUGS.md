# Known Bugs

- `--local` mode does not yet use the policy/hook pipeline for model-side tool calls because local mode currently has no tool loop.
- `/resume` currently picks the first best match from local sqlite search and does not present an interactive chooser.
- `/export` writes plain text transcript only; markdown formatting and redaction presets are not yet implemented.

