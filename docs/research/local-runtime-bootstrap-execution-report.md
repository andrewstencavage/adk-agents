# Local runtime bootstrap execution report

## Scope

Executed `scripts/bootstrap-local-runtimes.sh --install` on a Linux target host
on 2026-07-17.

## Result

- Ollama was already installed and responding at its configured loopback
  endpoint, `127.0.0.1:11434`.
- The LM Studio headless runtime was installed and its API started at
  `127.0.0.1:1234`.
- Both health endpoints responded successfully, and host listener inspection
  showed Ollama bound to `127.0.0.1` only.

## Corrections validated

The original bootstrap exposed two portability defects during this run:

1. Listener validation either selected a process-inspection tool without
   visibility into the service or treated `ss`'s peer-address column as a
   listener address.
2. Inventory capture attempted to assign shell variables declared `readonly`.

The script now prefers `ss` when available, validates only local listener
addresses, and uses `env` to pass its immutable configuration into Python.
This preserves the loopback-only requirement while allowing the redacted
inventory capture to execute.

The generated `var/provisioning/` inventory and logs remain untracked by
design; they are target-host operational records, not repository content.
