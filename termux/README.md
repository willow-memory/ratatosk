# Termux lane (tier 0)

Phone-side Ratatosk runs on Termux with loopback Ollama and USB/adb deposit sync.

## Boot

```bash
termux/boot/ratatosk-listen.sh   # when RATATOSK_GROVE_CHANNEL and MCP are configured
python -m ratatosk.crown --local --deposit
```

## Sync home

Session deposits land in `~/.ratatosk/deposits/`. Pull to desk with adb:

```bash
adb pull /data/data/com.termux/files/home/.ratatosk/deposits ./phone-deposits/
```

Full Termux UI port from archived `willow-2.0/apps/ratatosk/termux/` is follow-on work.
