#!/usr/bin/env bash
# Exposure test against a real ComfyUI (make exposure-test). Not in CI: it needs a
# ComfyUI checkout. Starts a throwaway ComfyUI on 127.0.0.1 with only this node,
# then checks that none of the harness's files or secrets are reachable, and that the
# canvas bridge endpoint accepts only the session's agent (the fake CLI is on PATH).
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
# Not "comfyui-harness": the author's ComfyUI has its own custom_nodes/comfyui-harness
# (the main checkout), and a whitelist of that name would load it as well.
NODE_NAME="comfyui-harness-under-test"

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

CONTROL_NAME="harness_control.txt"
CONTROL_TEXT=""
KEY_HEX=""
INSTANCE_ID=""
COOKIE_PAIR=""
BRIDGE_SECRET=""
BODY=""
PLANTED_MARKER=""

# fetch <curl args...>: writes the body to $BODY (set in start_server) and prints the HTTP status. 000 means curl failed.
fetch() {
  curl -s -o "$BODY" -w '%{http_code}' --max-time 20 "$@" || true
}

leaks_secret() {
  [[ -s "$BODY" ]] || return 1
  grep -qF -e "$KEY_HEX" -e "$INSTANCE_ID" ${BRIDGE_SECRET:+-e "$BRIDGE_SECRET"} \
    ${PLANTED_MARKER:+-e "$PLANTED_MARKER"} "$BODY"
}

# expect_refused <name> <curl args...>: a real 4xx answer whose body holds no secret.
# Status 000 (no answer), 200 and 5xx all fail, so a dead server cannot pass.
expect_refused() {
  local name="$1" code; shift
  code="$(fetch "$@")"
  if leaks_secret; then fail "$name (body holds secret material, status $code)"; return; fi
  if [[ "$code" =~ ^4[0-9][0-9]$ ]]; then pass "$name (status $code)"; else fail "$name (status $code)"; fi
}

# expect_control <name> <curl args...>: 200 and the control file's text.
expect_control() {
  local name="$1" code; shift
  code="$(fetch "$@")"
  if [[ "$code" == "200" && "$(cat "$BODY")" == "$CONTROL_TEXT" ]]; then
    pass "control: $name"
  else
    fail "control: $name (status $code)"
  fi
}

# list_names <curl args...>: prints "<status> <names...>" parsed from a JSON array.
list_names() {
  local code
  code="$(fetch "$@")"
  "$PY" -I -c '
import json, sys
code, path = sys.argv[1:3]
try:
    names = json.load(open(path))
    assert isinstance(names, list) and all(isinstance(n, str) for n in names)
except Exception:
    print(code, "UNPARSEABLE"); sys.exit(0)
print(code, *names)
' "$code" "$BODY"
}

start_server() {
  local label="$1"; shift
  SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/harness-exposure-XXXXXX")"
  BODY="$SCRATCH/body.out"
  mkdir -p "$SCRATCH/nodes" "$SCRATCH/user/default" "$SCRATCH/temp" "$SCRATCH/input" "$SCRATCH/output" "$SCRATCH/bin"
  CONTROL_TEXT="control-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
  printf '%s' "$CONTROL_TEXT" >"$SCRATCH/user/default/$CONTROL_NAME"
  printf '%s' "$CONTROL_TEXT" >"$SCRATCH/input/$CONTROL_NAME"
  ln -s "$REPO" "$SCRATCH/nodes/$NODE_NAME"
  printf 'harness_check:\n  base_path: %s\n  custom_nodes: nodes\n' "$SCRATCH" >"$SCRATCH/extra.yaml"
  # The bridge checks start a session: the fake CLI must win the lookup, never the real one.
  printf '#!/bin/sh\nFAKE_CLI_ARGV0="$0" exec %q %q "$@"\n' "$PY" "$REPO/tests/browser/fake_cli.py" >"$SCRATCH/bin/claude"
  chmod 700 "$SCRATCH/bin/claude"
  local server_path="$SCRATCH/bin:$PATH" found
  found="$(PATH="$server_path" command -v claude)"
  if [[ "$(realpath "$found")" != "$(realpath "$SCRATCH/bin/claude")" ]]; then
    echo "claude resolves to $found, not the fake CLI" >&2; exit 1
  fi
  (
    cd "$COMFYUI_DIR"
    export PATH="$server_path" FAKE_CLI_LOG="$SCRATCH/fake_cli.log"
    exec venv/bin/python main.py --listen 127.0.0.1 --port "$PORT" --cpu \
      --disable-all-custom-nodes --whitelist-custom-nodes "$NODE_NAME" \
      --extra-model-paths-config "$SCRATCH/extra.yaml" \
      --user-directory "$SCRATCH/user" --temp-directory "$SCRATCH/temp" \
      --input-directory "$SCRATCH/input" --output-directory "$SCRATCH/output" \
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

load_secrets() {
  local data="$SCRATCH/user/__harness"
  [[ -f "$data/key" && -f "$data/state.json" ]] || { fail "the node did not create key and state.json"; return 1; }
  KEY_HEX="$(od -An -v -tx1 "$data/key" | tr -d ' \n')"
  INSTANCE_ID="$("$PY" -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["instance_id"])' "$data/state.json")"
  if [[ ${#KEY_HEX} -eq 64 && -n "$INSTANCE_ID" ]]; then pass "secret material read (key hex, instance id)"; else fail "secret material unreadable"; return 1; fi
}

check_files_not_served() {
  local name listing
  # Positive controls first: the URL shapes used below do reach files in ComfyUI's
  # normal user area. Without them a refusal could just be a wrongly shaped URL.
  expect_control "direct read /userdata/$CONTROL_NAME" "$BASE/userdata/$CONTROL_NAME"
  expect_control "direct read /api/userdata/$CONTROL_NAME" "$BASE/api/userdata/$CONTROL_NAME"
  expect_control "traversal form ..%2Fdefault%2F$CONTROL_NAME" --path-as-is "$BASE/userdata/..%2Fdefault%2F$CONTROL_NAME"
  expect_control "header comfy-user: default" -H "comfy-user: default" "$BASE/userdata/$CONTROL_NAME"
  # ComfyUI (multi-user off) ignores the header: the default user's file is served for any value.
  expect_control "header comfy-user: __harness is ignored" -H "comfy-user: __harness" "$BASE/userdata/$CONTROL_NAME"

  # Asked first: later file requests make ComfyUI create an empty default/__harness
  # directory, after which a listing answers 200 with [] (a different directory).
  listing="$(list_names "$BASE/api/userdata?dir=__harness")"
  if [[ "$listing" =~ ^4[0-9][0-9]\  ]]; then
    pass "listing dir=__harness refused ($listing)"
  else
    fail "listing dir=__harness: $listing"
  fi
  if leaks_secret; then fail "listing dir=__harness body holds secret material"; fi
  expect_refused "userdata traversal to key (..%2F__harness%2Fkey)" --path-as-is "$BASE/userdata/..%2F__harness%2Fkey"
  expect_refused "userdata traversal (raw ..) to key" --path-as-is "$BASE/userdata/../__harness/key"
  expect_refused "api userdata traversal to key" --path-as-is "$BASE/api/userdata/..%2F__harness%2Fkey"
  # agent.json and copilot-mcp.json may not exist yet: plant them (owner-only, a
  # marker the body check looks for) so a refusal is not just a missing file.
  local data="$SCRATCH/user/__harness" planted=()
  PLANTED_MARKER="planted-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
  for name in agent.json copilot-mcp.json; do
    if [[ ! -e "$data/$name" ]]; then
      (umask 077; printf '{"marker": "%s"}\n' "$PLANTED_MARKER" > "$data/$name")
      planted+=("$data/$name")
    fi
  done
  for name in key state.json pair mcp.json agent.json copilot-mcp.json; do
    expect_refused "GET /userdata/__harness/$name" "$BASE/userdata/__harness%2F$name"
    expect_refused "GET /api/userdata/__harness/$name" "$BASE/api/userdata/__harness%2F$name"
    expect_refused "comfy-user header, /userdata/$name" -H "comfy-user: __harness" "$BASE/userdata/$name"
    expect_refused "comfy-user header, /api/userdata/$name" -H "comfy-user: __harness" "$BASE/api/userdata/$name"
  done
  rm -f "${planted[@]}"

  # Listings: parsed JSON. The control must be listed (the form works); ours must not.
  listing="$(list_names "$BASE/api/userdata?dir=.")"
  if [[ "$listing" == "200 "* && " $listing " == *" $CONTROL_NAME "* ]]; then pass "control: listing dir=. shows $CONTROL_NAME"; else fail "control: listing dir=.: $listing"; fi
  if [[ " $listing " != *" key "* && " $listing " != *" state.json "* && " $listing " != *" __harness "* ]]; then
    pass "listing dir=. shows none of our files"
  else
    fail "listing dir=. shows our files: $listing"
  fi
  listing="$(list_names -H "comfy-user: __harness" "$BASE/api/userdata?dir=.")"
  if [[ "$listing" == "200 "* && " $listing " == *" $CONTROL_NAME "* && " $listing " != *" key "* && " $listing " != *" state.json "* ]]; then
    pass "listing with comfy-user __harness is the default user's, none of our files"
  else
    fail "listing with comfy-user __harness: $listing"
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
  COOKIE_PAIR="$cookie_pair"

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

# --- the canvas bridge's endpoint, with a session running and the bridge on ---

MCP_LIST='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# bridge_switch <on|off>: the Canvas bridge switch, set as the paired page sets it.
bridge_switch() {
  local value=false; [[ "$1" == "on" ]] && value=true
  status_of -X PUT -H "Cookie: $COOKIE_PAIR" -H "Origin: $BASE" -H "Content-Type: application/json" \
    -d "{\"enabled\": $value}" "$BASE/harness/bridge"
}

# session_secret: the bearer secret in the session's mcp.json canvas entry, or nothing.
session_secret() {
  "$PY" -I -c '
import json, sys
try:
    entry = json.load(open(sys.argv[1]))["mcpServers"]["canvas"]
    print(entry["headers"]["Authorization"].removeprefix("Bearer "))
except Exception:
    pass
' "$SCRATCH/user/__harness/mcp.json"
}

# wait_secret <not this one>: waits for a canvas entry whose secret differs.
wait_secret() {
  local secret=""
  for _ in $(seq 1 40); do
    secret="$(session_secret)"
    if [[ -n "$secret" && "$secret" != "$1" ]]; then printf '%s' "$secret"; return 0; fi
    sleep 0.25
  done
  return 1
}

mcp_post() {
  local path="$1"; shift
  fetch -X POST -H "Content-Type: application/json" -H "Accept: application/json" -d "$MCP_LIST" "$@" "$BASE$path"
}

# expect_mcp_ok <name> <path> <curl args...>: 200 with the tool list (the control).
expect_mcp_ok() {
  local name="$1" path="$2" code; shift 2
  code="$(mcp_post "$path" "$@")"
  if [[ "$code" == "200" ]] && grep -qF '"get_outline"' "$BODY"; then pass "control: $name"; else fail "control: $name (status $code)"; fi
}

# expect_bridge_refused <name> <path> <curl args...>: our own 403 bridge_refused,
# so the refusal is the endpoint's and not ComfyUI's middleware's; no secret in it.
expect_bridge_refused() {
  local name="$1" path="$2" code; shift 2
  code="$(mcp_post "$path" "$@")"
  if leaks_secret; then fail "$name (body holds secret material, status $code)"; return; fi
  if [[ "$code" == "403" ]] && grep -qF '"bridge_refused"' "$BODY"; then pass "$name (403 bridge_refused)"; else fail "$name (status $code)"; fi
}

check_bridge() {
  local code path name old status
  [[ -n "$COOKIE_PAIR" ]] || { fail "bridge checks need the paired cookie"; return; }
  code="$(bridge_switch on)"
  [[ "$code" == "204" ]] && pass "bridge switched on by the paired caller" || { fail "bridge switch on: $code"; return; }
  "$PY" "$REPO/tests/comfyui/start_session.py" "$BASE" "$COOKIE_PAIR" start \
    && pass "session started with the fake CLI" || { fail "session start"; return; }
  BRIDGE_SECRET="$(wait_secret "")" && pass "mcp.json has a canvas entry with a secret" || { fail "no canvas entry in mcp.json"; return; }
  status="$(curl -s --max-time 20 -H "Cookie: $COOKIE_PAIR" -H "Sec-Fetch-Site: same-origin" "$BASE/harness/status")"
  [[ "$status" == *'"bridge_live": true'* ]] && pass "status: bridge_live" || fail "status: bridge not live"

  for path in /harness/mcp /api/harness/mcp; do
    expect_mcp_ok "$path with the secret and no browser headers" "$path" -H "Authorization: Bearer $BRIDGE_SECRET"
    expect_bridge_refused "$path with the secret and a Cookie" "$path" -H "Authorization: Bearer $BRIDGE_SECRET" -H "Cookie: $COOKIE_PAIR"
    expect_bridge_refused "$path with the secret and an Origin" "$path" -H "Authorization: Bearer $BRIDGE_SECRET" -H "Origin: $BASE"
    expect_bridge_refused "$path with the secret and Sec-Fetch-Site" "$path" -H "Authorization: Bearer $BRIDGE_SECRET" -H "Sec-Fetch-Site: same-origin"
    expect_bridge_refused "$path with the paired cookie and no secret" "$path" -H "Cookie: $COOKIE_PAIR"
    expect_bridge_refused "$path with no secret" "$path"
    expect_bridge_refused "$path with a wrong secret" "$path" -H "Authorization: Bearer $(printf '0%.0s' $(seq 1 64))"
  done

  # mcp.json and the secret are not reachable through ComfyUI's own routes.
  expect_control "/view of a file in the input directory" "$BASE/view?filename=$CONTROL_NAME&type=input"
  for name in "userdata/__harness%2Fmcp.json" "api/userdata/__harness%2Fmcp.json" \
    "userdata/..%2F__harness%2Fmcp.json" "api/userdata/..%2F__harness%2Fmcp.json" \
    "view?filename=mcp.json&subfolder=..%2Fuser%2F__harness&type=input" \
    "view?filename=..%2Fuser%2F__harness%2Fmcp.json&type=input" \
    "view?filename=mcp.json&subfolder=..%2F..%2Fuser%2F__harness&type=temp" \
    "view?filename=$(printf '%s' "$SCRATCH/user/__harness/mcp.json" | sed 's#/#%2F#g')&type=input" \
    "api/view?filename=mcp.json&subfolder=..%2Fuser%2F__harness&type=output"; do
    expect_refused "GET /$name" --path-as-is "$BASE/$name"
  done
  expect_refused "comfy-user header, /userdata/mcp.json" -H "comfy-user: __harness" "$BASE/userdata/mcp.json"
  local logs
  logs="$(curl -s --max-time 20 "$BASE/internal/logs/raw")"
  if [[ -n "$logs" && "$logs" != *"$BRIDGE_SECRET"* ]]; then pass "logs hold no bridge secret"; else fail "logs hold the bridge secret (or none returned)"; fi
  if grep -qF "$BRIDGE_SECRET" "$SCRATCH/server.log"; then fail "server output holds the bridge secret"; else pass "server output holds no bridge secret"; fi
  # The fake CLI logs its argv (secrets masked) and env names; neither may hold the secret.
  if [[ ! -s "$SCRATCH/fake_cli.log" ]]; then fail "the fake CLI log is missing or empty"
  elif grep -qF "$BRIDGE_SECRET" "$SCRATCH/fake_cli.log"; then fail "the agent's argv or env names hold the bridge secret"
  else pass "the agent's argv and env names hold no bridge secret"; fi

  # A secret from an older session opens nothing.
  old="$BRIDGE_SECRET"
  "$PY" "$REPO/tests/comfyui/start_session.py" "$BASE" "$COOKIE_PAIR" restart \
    && pass "session started again" || { fail "session restart"; return; }
  BRIDGE_SECRET="$(wait_secret "$old")" && pass "the new session has a new secret" || { fail "no new secret"; return; }
  expect_bridge_refused "/harness/mcp with the old session's secret" /harness/mcp -H "Authorization: Bearer $old"
  expect_mcp_ok "/harness/mcp with the new secret" /harness/mcp -H "Authorization: Bearer $BRIDGE_SECRET"

  # Switched off, the current secret opens nothing either.
  code="$(bridge_switch off)"
  [[ "$code" == "204" ]] && pass "bridge switched off" || fail "bridge switch off: $code"
  expect_bridge_refused "/harness/mcp with the bridge off" /harness/mcp -H "Authorization: Bearer $BRIDGE_SECRET"
  expect_bridge_refused "/api/harness/mcp with the bridge off" /api/harness/mcp -H "Authorization: Bearer $BRIDGE_SECRET"
}

run_suite() {
  local label="$1"; shift
  echo "== $label =="
  COOKIE_PAIR=""; BRIDGE_SECRET=""
  start_server "$label" "$@"
  load_secrets || return 0
  check_files_not_served
  pair_and_check_logs
  check_bridge
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
