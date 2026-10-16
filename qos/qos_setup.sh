#!/usr/bin/env bash
# Shared inventory, complete validation and scoped cleanup live in manage.py.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/manage.py" setup
