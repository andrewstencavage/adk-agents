# Target-host local runtime bootstrap

After cloning this repository on the target host, run:

```bash
chmod +x scripts/bootstrap-local-runtimes.sh
scripts/bootstrap-local-runtimes.sh --install
```

The script checks for Ollama first. It installs a missing Ollama or LM Studio
headless runtime only when `--install` is supplied, starts both APIs on loopback
only, and writes a redacted native-inventory artifact to
`var/provisioning/initial-runtime-inventory.json`. `var/` is deliberately
ignored so target-host inventories, logs, and machine-specific details cannot
enter Git.

It never downloads, loads, or chooses a model. That decision remains with the
model capability assessment. If a runtime needs an API token, expose it only
through the target host's secret mechanism as `LM_STUDIO_API_TOKEN`; do not put
it in a repository file or pass it as a command-line argument.

Run the script again without `--install` to start already installed runtimes and
refresh the artifact. It rejects a listener bound publicly instead of trying to
reconfigure it automatically.

LM Studio's supported `lms` CLI starts its headless daemon and server; no GUI
is required. See [LM Studio headless operation](https://lmstudio.ai/docs/developer/core/headless)
and [Ollama Linux installation](https://docs.ollama.com/linux).
