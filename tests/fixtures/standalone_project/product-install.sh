#!/usr/bin/env bash
set -euo pipefail
# Copy this witness-only hook into the autonomous project's ops/postgres directory.
exec python3 ops/postgres/tools_operator.py install "$1"
