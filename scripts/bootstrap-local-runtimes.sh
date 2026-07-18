#!/usr/bin/env bash
# Provision the two local model runtimes required by this project on macOS or
# Linux. It leaves both HTTP APIs bound to loopback and records a redacted,
# reproducible initial inventory in var/provisioning/.

set -euo pipefail
IFS=$'\n\t'
umask 077

readonly OLLAMA_URL='http://127.0.0.1:11434'
readonly LM_STUDIO_URL='http://127.0.0.1:1234'
readonly OUTPUT_DIR='var/provisioning'
readonly OUTPUT_FILE="$OUTPUT_DIR/initial-runtime-inventory.json"
readonly LOG_DIR="$OUTPUT_DIR/logs"
install_missing=false

usage() {
  cat <<'EOF'
Usage: scripts/bootstrap-local-runtimes.sh [--install]

Starts Ollama and LM Studio's supported headless runtime on localhost, then
writes a redacted native-inventory artifact to
var/provisioning/initial-runtime-inventory.json.

  --install  Install a missing runtime using its vendor's official installer.
             Without this flag, a missing runtime is reported and nothing is
             downloaded.

The script never downloads or loads a model, opens a public listener, writes a
credential, or prints a credential. If LM Studio needs an API token, provide
it only through the target host's secret mechanism as LM_STUDIO_API_TOKEN.
EOF
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }

wait_for_http() {
  local url="$1" attempts=20
  while (( attempts > 0 )); do
    if curl --fail --silent --show-error --connect-timeout 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  return 1
}

find_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    command -v ollama
  elif [[ -x '/Applications/Ollama.app/Contents/Resources/ollama' ]]; then
    printf '%s\n' '/Applications/Ollama.app/Contents/Resources/ollama'
  fi
}

install_ollama() {
  local installer
  installer="$(mktemp)"
  trap 'rm -f "$installer"' RETURN
  note 'Installing Ollama from its official installer…'
  curl --fail --silent --show-error --location https://ollama.com/install.sh --output "$installer"
  sh "$installer"
}

install_lm_studio() {
  local installer
  installer="$(mktemp)"
  trap 'rm -f "$installer"' RETURN
  note 'Installing LM Studio headless runtime from its official installer…'
  curl --fail --silent --show-error --location https://lmstudio.ai/install.sh --output "$installer"
  bash "$installer"
}

start_ollama() {
  local ollama_bin
  if wait_for_http "$OLLAMA_URL/api/version"; then
    note 'Ollama is already responding on loopback.'
    return
  fi
  ollama_bin="$(find_ollama || true)"
  if [[ -z "$ollama_bin" ]]; then
    if ! "$install_missing"; then
      fail 'Ollama is not installed. Re-run with --install.'
    fi
    install_ollama
    ollama_bin="$(find_ollama || true)"
    [[ -n "$ollama_bin" ]] || fail 'Ollama installation completed but its CLI was not found. Open Ollama once, then re-run this script.'
  fi
  if [[ "$(uname -s)" == 'Linux' ]] && command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files ollama.service >/dev/null 2>&1; then
    note 'Starting the installed Ollama system service…'
    sudo systemctl enable --now ollama
  else
    note 'Starting Ollama on 127.0.0.1:11434…'
    OLLAMA_HOST='127.0.0.1:11434' nohup "$ollama_bin" serve >"$LOG_DIR/ollama.log" 2>&1 &
  fi
  wait_for_http "$OLLAMA_URL/api/version" || fail 'Ollama did not become reachable at 127.0.0.1:11434.'
}

find_lms() { command -v lms 2>/dev/null || true; }

start_lm_studio() {
  local lms_bin
  lms_bin="$(find_lms)"
  if [[ -z "$lms_bin" ]]; then
    if ! "$install_missing"; then
      fail 'LM Studio headless runtime is not installed. Re-run with --install.'
    fi
    install_lm_studio
    lms_bin="$(find_lms)"
    [[ -n "$lms_bin" ]] || fail 'LM Studio installation completed but `lms` is not on PATH. Start a new shell, then re-run this script.'
  fi
  if wait_for_http "$LM_STUDIO_URL/api/v1/models"; then
    note 'LM Studio is already responding on loopback.'
    return
  fi
  note 'Starting the LM Studio headless daemon and loopback API…'
  "$lms_bin" daemon up
  "$lms_bin" server start --bind 127.0.0.1 --port 1234
  wait_for_http "$LM_STUDIO_URL/api/v1/models" || fail 'LM Studio did not become reachable at 127.0.0.1:1234.'
}

assert_loopback_listener() {
  local port="$1" report
  if command -v lsof >/dev/null 2>&1; then
    report="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v ss >/dev/null 2>&1; then
    report="$(ss -ltn "sport = :$port" 2>/dev/null || true)"
  else
    note "WARNING: cannot inspect the listener for port $port (no lsof or ss)."
    return
  fi
  [[ -n "$report" ]] || fail "No listening process found for port $port after its health check."
  if grep -Eq '(^|[[:space:]])(\*|0\.0\.0\.0|\[::\]|:::)' <<<"$report"; then
    fail "Port $port is publicly bound. Stop that runtime and re-run; this bootstrap requires loopback-only listeners."
  fi
}

capture_inventory() {
  OLLAMA_URL="$OLLAMA_URL" LM_STUDIO_URL="$LM_STUDIO_URL" LM_STUDIO_API_TOKEN="${LM_STUDIO_API_TOKEN:-}" OUTPUT_FILE="$OUTPUT_FILE" python3 - <<'PY'
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OLLAMA_URL = os.environ['OLLAMA_URL']
LM_STUDIO_URL = os.environ['LM_STUDIO_URL']
OUTPUT_FILE = os.environ['OUTPUT_FILE']
secret_key = re.compile(r'(api.?key|authorization|credential|password|secret|token)', re.I)
home_path = re.compile(r'(?:(?:/Users|/home)/[^/\\\"]+|[A-Za-z]:\\\\Users\\\\[^\\\"]+)', re.I)

def get_json(url, *, headers=None, method='GET', body=None):
    request = Request(url, data=body, headers=headers or {}, method=method)
    with urlopen(request, timeout=10) as response:
        return json.load(response)

def scrub(value, key=''):
    if secret_key.search(key): return '[redacted]'
    if isinstance(value, dict): return {str(k): scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, list): return [scrub(item, key) for item in value]
    if isinstance(value, str): return home_path.sub('[redacted-home]', value)
    return value

try:
    ollama_version = get_json(f'{OLLAMA_URL}/api/version')
    ollama_tags = get_json(f'{OLLAMA_URL}/api/tags')
    ollama_models = []
    for model in ollama_tags.get('models', []):
        name = model.get('name')
        if isinstance(name, str):
            details = get_json(f'{OLLAMA_URL}/api/show', headers={'Content-Type': 'application/json'}, method='POST', body=json.dumps({'name': name}).encode())
            ollama_models.append({'tag': model, 'show': details})
    lm_headers = {}
    if os.environ.get('LM_STUDIO_API_TOKEN'):
        lm_headers['Authorization'] = f"Bearer {os.environ['LM_STUDIO_API_TOKEN']}"
    lm_models = get_json(f'{LM_STUDIO_URL}/api/v1/models', headers=lm_headers)
except (HTTPError, URLError, OSError, ValueError) as error:
    sys.exit(f'Could not capture native runtime inventory: {error}')

artifact = scrub({'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(), 'endpoints': {'ollama': OLLAMA_URL, 'lm_studio': LM_STUDIO_URL}, 'ollama': {'version': ollama_version, 'models': ollama_models}, 'lm_studio': {'models': lm_models}})
payload = json.dumps(artifact, indent=2, sort_keys=True) + '\n'
with open(OUTPUT_FILE, 'w', encoding='utf-8') as handle: handle.write(payload)
print(hashlib.sha256(payload.encode()).hexdigest())
PY
}

while (( $# > 0 )); do
  case "$1" in
    --install) install_missing=true ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
  shift
done

command -v curl >/dev/null 2>&1 || fail 'curl is required.'
command -v python3 >/dev/null 2>&1 || fail 'python3 is required to create the redacted inventory artifact.'
mkdir -p "$LOG_DIR"
start_ollama
start_lm_studio
assert_loopback_listener 11434
assert_loopback_listener 1234
inventory_sha256="$(capture_inventory)"
note "Wrote redacted inventory: $OUTPUT_FILE"
note "SHA-256: $inventory_sha256"
note 'Both runtime endpoints are loopback-only. Configure the future service with exactly these endpoints; do not enable port discovery.'
note 'No model was downloaded or selected. The model-assessment ticket decides which installed models may receive specialist work.'
