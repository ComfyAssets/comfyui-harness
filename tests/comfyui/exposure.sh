#!/usr/bin/env bash
# Exposure test against a real ComfyUI (make exposure-test). Not in CI: it needs a
# ComfyUI checkout. Starts a throwaway ComfyUI on 127.0.0.1 with only this node,
# then checks that none of the harness's files or secrets are reachable.
#
#   COMFYUI_DIR                    ComfyUI checkout (default ~/comfy/ComfyUI)
#   HARNESS_EXPOSURE_PORT          port (default 8775)
#   HARNESS_EXPOSURE_WITH_AGENT=1  also run with --enable-agent. Off by default: it
#                                  starts ComfyUI's own agent, which may contact its cloud.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMFYUI_DIR="${COMFYUI_DIR:-$HOME/comfy/ComfyUI}"
PORT="${HARNESS_EXPOSURE_PORT:-8775}"
BASE="http://127.0.0.1:${PORT}"
PY="$REPO/.venv/bin/python"

SCRATCH=""
SERVER_PID=""
FAILURES=0

stop_server() {
  if [[ -n "$SERVER_PID" ]]; then
    kill -INT "$SERVER_PID" 2>/dev/null || true
    for _ in $(seq 1 50); do
      kill -0 "$SERVER_PID" 2>/dev/null || break
      sleep 0.2
    done
    kill -KILL "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}

cleanup() {
  stop_server
  if [[ -n "$SCRATCH" && -d "$SCRATCH" ]]; then
    rm -rf "$SCRATCH"
  fi
}
trap cleanup EXIT

pass() { echo "  ok   $1"; }
fail() { echo "  FAIL $1" >&2; FAILURES=$((FAILURES + 1)); }

# status_of <curl args...>: prints the HTTP status only.
status_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@"; }

# expect_refused <name> <curl args...>: the status must not be 200.
expect_refused() {
  local name="$1"; shift
  local code
  code="$(status_of "$@")"
  if [[ "$code" != "200" ]]; then pass "$name (status $code)"; else fail "$name (status 200)"; fi
}

start_server() {
  local label="$1"; shift
  SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/harness-exposure-XXXXXX")"
  mkdir -p "$SCRATCH/nodes" "$SCRATCH/user" "$SCRATCH/temp"
  ln -s "$REPO" "$SCRATCH/nodes/comfyui-harness"
  printf 'harness_check:\n  base_path: %s\n  custom_nodes: nodes\n' "$SCRATCH" >"$SCRATCH/extra.yaml"
  (
    cd "$COMFYUI_DIR"
    exec venv/bin/python main.py --listen 127.0.0.1 --port "$PORT" --cpu \
      --disable-all-custom-nodes --whitelist-custom-nodes comfyui-harness \
      --extra-model-paths-config "$SCRATCH/extra.yaml" \
      --user-directory "$SCRATCH/user" --temp-directory "$SCRATCH/temp" \
      --disable-auto-launch "$@"
  ) >"$SCRATCH/server.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 240); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      echo "ComfyUI exited early ($label):" >&2; tail -n 30 "$SCRATCH/server.log" >&2; exit 1
    fi
    if [[ "$(status_of "$BASE/system_stats")" == "200" ]]; then return 0; fi
    sleep 0.5
  done
  echo "ComfyUI did not start ($label)" >&2; tail -n 30 "$SCRATCH/server.log" >&2; exit 1
}

check_files_not_served() {
  local data="$SCRATCH/user/__harness"
  [[ -f "$data/key" ]] && pass "key exists on disk" || fail "key exists on disk (the node did not start)"
  expect_refused "userdata traversal to key" --path-as-is "$BASE/userdata/..%2F__harness%2Fkey"
  expect_refused "userdata traversal (raw ..) to key" --path-as-is "$BASE/userdata/../__harness/key"
  local name
  for name in key state.json pair mcp.json; do
    expect_refused "GET /userdata/__harness/$name" "$BASE/userdata/__harness%2F$name"
    expect_refused "GET /api/userdata/__harness/$name" "$BASE/api/userdata/__harness%2F$name"
    expect_refused "comfy-user header, /userdata/$name" -H "comfy-user: __harness" "$BASE/userdata/$name"
    expect_refused "comfy-user header, /api/userdata/$name" -H "comfy-user: __harness" "$BASE/api/userdata/$name"
  done
  local listing
  listing="$(curl -s --max-time 20 "$BASE/api/userdata?dir=__harness" || true)"
  if [[ "$listing" != *key* && "$listing" != *state.json* && "$listing" != *mcp.json* ]]; then
    pass "userdata listing of __harness shows none of our files"
  else
    fail "userdata listing of __harness: $listing"
  fi
  listing="$(curl -s --max-time 20 -H "comfy-user: __harness" "$BASE/api/userdata?dir=." || true)"
  if [[ "$listing" != *state.json* && "$listing" != *mcp.json* ]]; then
    pass "userdata listing as comfy-user __harness shows none of our files"
  else
    fail "userdata listing as comfy-user __harness: $listing"
  fi
}

pair_and_check_logs() {
  local origin="$BASE" body code secret poll headers cookie_line cookie_value state
  body="$(curl -s --max-time 20 -X POST -H "Origin: $origin" "$BASE/harness/pair/request")"
  code="$("$PY" -I -c 'import json,sys; print(json.load(sys.stdin)["code"])' <<<"$body")"
  secret="$("$PY" -I -c 'import json,sys; print(json.load(sys.stdin)["poll_secret"])' <<<"$body")"
  [[ -n "$code" && -n "$secret" ]] && pass "pair request returned a code" || { fail "pair request"; return; }

  "$PY" "$REPO/pair.py" "$code" --data-dir "$SCRATCH/user/__harness" >/dev/null \
    && pass "pair.py accepted the code" || { fail "pair.py"; return; }

  headers="$SCRATCH/poll.headers"
  poll="$(curl -s --max-time 20 -D "$headers" -X POST -H "Origin: $origin" \
    -H "X-Harness-Poll: $secret" "$BASE/harness/pair/poll")"
  state="$("$PY" -I -c 'import json,sys; print(json.load(sys.stdin)["state"])' <<<"$poll")"
  cookie_line="$(grep -i '^set-cookie:' "$headers" | head -n 1 | tr -d '\r' || true)"
  [[ "$state" == "paired" && -n "$cookie_line" ]] \
    && pass "poll returned paired with Set-Cookie" || { fail "poll state '$state', cookie '$cookie_line'"; return; }
  cookie_value="${cookie_line#*=}"; cookie_value="${cookie_value%%;*}"
  local cookie_pair="${cookie_line#*: }"; cookie_pair="${cookie_pair%%;*}"

  local logs
  logs="$(curl -s --max-time 20 "$BASE/internal/logs/raw")"
  if [[ -n "$cookie_value" && "$logs" != *"$cookie_value"* ]]; then pass "logs hold no cookie value"; else fail "logs hold the cookie value"; fi
  if [[ "$logs" != *"$secret"* ]]; then pass "logs hold no poll secret"; else fail "logs hold the poll secret"; fi
  if grep -qF -e "$cookie_value" -e "$secret" "$SCRATCH/server.log"; then fail "server output holds a secret"; else pass "server output holds no secret"; fi

  # The key and state still unreachable after pairing created a record.
  expect_refused "state.json after pairing" "$BASE/userdata/__harness%2Fstate.json"

  local a b
  a="$(curl -s --max-time 20 -H "Cookie: $cookie_pair" -H "Sec-Fetch-Site: same-origin" "$BASE/harness/status")"
  b="$(curl -s --max-time 20 -H "Cookie: $cookie_pair" -H "Sec-Fetch-Site: same-origin" "$BASE/api/harness/status")"
  if [[ -n "$a" && "$a" == "$b" && "$a" == *'"paired": true'* ]]; then
    pass "/harness/status and /api/harness/status match for a paired caller"
  else
    fail "status bodies differ or not paired: [$a] [$b]"
  fi
  a="$(curl -s --max-time 20 "$BASE/harness/status")"
  b="$(curl -s --max-time 20 "$BASE/api/harness/status")"
  if [[ -n "$a" && "$a" == "$b" && "$a" == *'"paired": false'* ]]; then
    pass "/harness/status and /api/harness/status match for an unpaired caller"
  else
    fail "unpaired status bodies differ: [$a] [$b]"
  fi
  local unpaired_ws
  unpaired_ws="$(status_of -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
    -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Origin: $origin" "$BASE/harness/terminal/ws")"
  if [[ "$unpaired_ws" == "401" ]]; then pass "terminal socket refuses an unpaired caller"; else fail "terminal socket without cookie: $unpaired_ws"; fi
}

run_suite() {
  local label="$1"; shift
  echo "== $label =="
  start_server "$label" "$@"
  check_files_not_served
  pair_and_check_logs
  stop_server
  rm -rf "$SCRATCH"; SCRATCH=""
}

[[ -x "$COMFYUI_DIR/venv/bin/python" ]] || { echo "No ComfyUI venv in $COMFYUI_DIR (set COMFYUI_DIR)" >&2; exit 2; }
[[ -x "$PY" ]] || { echo "No $PY (create .venv first)" >&2; exit 2; }
if [[ "$(status_of "$BASE/system_stats")" != "000" ]]; then echo "Port $PORT is in use" >&2; exit 2; fi

run_suite "default"
run_suite "--enable-cors-header" --enable-cors-header
if [[ "${HARNESS_EXPOSURE_WITH_AGENT:-}" == "1" ]]; then
  run_suite "--enable-agent" --enable-agent
else
  echo "== --enable-agent: skipped (set HARNESS_EXPOSURE_WITH_AGENT=1 to run) =="
fi

if (( FAILURES > 0 )); then echo "$FAILURES assertion(s) failed" >&2; exit 1; fi
echo "all assertions passed"
