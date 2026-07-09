#!/usr/bin/env bash
# devkit_lock.sh — cross-agent advisory lock for the physical AXC3000 devkit (JTAG/board).
#
# Multiple Claude Code agents share ONE board. Programming, jtagconfig, system-console, quartus_pgm,
# usbipd attach/detach, dla_benchmark-on-board, etc. must NOT run concurrently. Any agent that touches
# the board MUST hold this lock for the duration and release it after.
#
# The lock is a directory (atomic `mkdir`) at a FIXED machine-wide path so it is shared across
# sessions / worktrees / checkouts. Design choices for SAFETY (the whole point is to avoid collisions):
#   * acquire NEVER auto-steals another agent's lock — if busy it refuses (exit 1). You will not
#     surprise-grab the board out from under another agent.
#   * re-acquiring by the SAME <who> succeeds and refreshes the timestamp (use it as a heartbeat during
#     a long board session so the lock doesn't look stale).
#   * staleness is TIME-based and ADVISORY: `status` flags a lock older than STALE_MINUTES; a stuck
#     lock from a crashed agent is cleared only by an EXPLICIT `acquire --steal-if-stale` or
#     `release --force` — never silently.
#
# Usage:
#   scripts/devkit_lock.sh acquire "<who>" "<reason>" [--wait SECONDS] [--steal-if-stale]
#   scripts/devkit_lock.sh release ["<who>"|--force]
#   scripts/devkit_lock.sh status                                   # exit 0 free, 1 held-fresh, 3 held-stale
#   scripts/devkit_lock.sh with "<who>" "<reason>" -- <command...>  # acquire, run, always release
#
# Recommended <who>: a stable per-agent id (e.g. "hyperram-agent", "coredla-agent"). Board example:
#   scripts/devkit_lock.sh with "coredla-agent" "program + jtag readback" -- \
#       bash -lc 'source scripts/env.sh && quartus_pgm -c 1 -m jtag -o "p;out.sof"'
set -euo pipefail

LOCK_DIR="${DEVKIT_LOCK_DIR:-/tmp/axc3000_devkit.lock.d}"
HOLDER_FILE="$LOCK_DIR/holder"
STALE_MINUTES="${DEVKIT_LOCK_STALE_MINUTES:-45}"

_now()     { date +%s; }
_read_kv() { [ -f "$HOLDER_FILE" ] && sed -n "s/^$1=//p" "$HOLDER_FILE" || true; }

_write_holder() {
    { echo "who=$1"; echo "reason=$2"; echo "pid=$$"; echo "host=$(hostname)";
      echo "epoch=$(_now)"; echo "time=$(date -Is)"; } > "$HOLDER_FILE"
}

# 0 == time-stale (age >= STALE_MINUTES), 1 == fresh. Advisory only.
_is_stale() {
    [ -f "$HOLDER_FILE" ] || return 0
    local acquired; acquired="$(_read_kv epoch)"; [ -n "$acquired" ] || return 0
    [ $(( ( $(_now) - acquired ) / 60 )) -ge "$STALE_MINUTES" ]
}

cmd_status() {
    if [ -d "$LOCK_DIR" ] && [ -f "$HOLDER_FILE" ]; then
        if _is_stale; then echo "LOCKED (STALE >= ${STALE_MINUTES}m — steal only if you are SURE the holder is dead):"; sed 's/^/  /' "$HOLDER_FILE"; return 3
        else echo "LOCKED by:"; sed 's/^/  /' "$HOLDER_FILE"; return 1; fi
    fi
    echo "FREE"; return 0
}

cmd_acquire() {
    local who="${1:?who required}" reason="${2:-}"; shift 2 || true
    local wait_s=0 steal=0
    while [ $# -gt 0 ]; do case "$1" in
        --wait) wait_s="${2:?seconds}"; shift 2;;
        --steal-if-stale) steal=1; shift;;
        *) shift;;
    esac; done
    local deadline=$(( $(_now) + wait_s ))
    while :; do
        if mkdir "$LOCK_DIR" 2>/dev/null; then _write_holder "$who" "$reason"; echo "ACQUIRED devkit lock for '$who'"; return 0; fi
        # already held
        if [ "$(_read_kv who)" = "$who" ]; then _write_holder "$who" "$reason"; echo "REFRESHED devkit lock (already held by '$who')"; return 0; fi
        if [ "$steal" = 1 ] && _is_stale; then
            echo "WARN: --steal-if-stale: taking over a lock older than ${STALE_MINUTES}m:" >&2; sed 's/^/  /' "$HOLDER_FILE" >&2 || true
            rm -rf "$LOCK_DIR"; continue
        fi
        if [ "$(_now)" -lt "$deadline" ]; then sleep 3; continue; fi
        echo "BUSY: devkit held by another agent — NOT touching the board:" >&2; sed 's/^/  /' "$HOLDER_FILE" >&2 || true
        return 1
    done
}

cmd_release() {
    local who="${1:-}"
    [ -d "$LOCK_DIR" ] || { echo "already free"; return 0; }
    local cur; cur="$(_read_kv who)"
    if [ "$who" != "--force" ] && [ -n "$who" ] && [ "$who" != "$cur" ]; then
        echo "REFUSING release: lock held by '$cur', not '$who' (use --force to override)" >&2; return 1
    fi
    rm -rf "$LOCK_DIR"; echo "RELEASED devkit lock (was '$cur')"; return 0
}

cmd_with() {
    local who="${1:?who}" reason="${2:?reason}"; shift 2
    [ "${1:-}" = "--" ] && shift
    cmd_acquire "$who" "$reason" || return 1
    trap 'cmd_release "$who" >/dev/null 2>&1 || true' EXIT
    local rc=0; "$@" || rc=$?
    trap - EXIT; cmd_release "$who" >/dev/null 2>&1 || true
    return $rc
}

case "${1:-status}" in
    acquire) shift; cmd_acquire "$@";;
    release) shift; cmd_release "$@";;
    status)  cmd_status;;
    with)    shift; cmd_with "$@";;
    *) echo "usage: $0 {acquire <who> <reason> [--wait N] [--steal-if-stale]|release [<who>|--force]|status|with <who> <reason> -- <cmd...>}" >&2; exit 2;;
esac
