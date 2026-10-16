#!/usr/bin/env bash
# common_env.sh - Shared environment variables for demo scripts

RYU_REST_HOST="${RYU_REST_HOST:-127.0.0.1}"
RYU_REST_PORT="${RYU_REST_PORT:-8080}"
RYU_REST_BASE="http://${RYU_REST_HOST}:${RYU_REST_PORT}"

# Fixed topology bindings are generated from the same inventory as Mininet.
# Fail visibly if Python/configuration is unavailable; do not keep stale copies.
_LAB_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LAB_BINDINGS="$(python3 "${_LAB_ENV_DIR}/../config/lab.py" shell)" || return 1
eval "${_LAB_BINDINGS}"
unset _LAB_ENV_DIR _LAB_BINDINGS
