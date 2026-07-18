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

## Captured inventory summary

- Ollama: `0.31.1`
  - `deepseek-64k:latest`
  - `deepseek-r1:7b`
  - `llama3.1:8b`
- LM Studio: `9902c3a`
  - `text-embedding-nomic-embed-text-v1.5`

The detailed native API capture remains at
`var/provisioning/initial-runtime-inventory.json` on the target host. It is
intentionally ignored because it is a host-specific operational record. This
summary contains only the safe model identifiers and runtime versions needed to
reproduce the next capability-assessment step.

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
