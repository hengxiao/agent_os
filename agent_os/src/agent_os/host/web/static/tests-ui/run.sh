#!/usr/bin/env bash
# UI 测试入口(项目内隔离:.venv-ui / .browsers / .syslibs 均在本目录,不入库)
set -euo pipefail
cd "$(dirname "$0")"
export LD_LIBRARY_PATH="$PWD/.syslibs/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers"
exec .venv-ui/bin/python run.py "$@"
