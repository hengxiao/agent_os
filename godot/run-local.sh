#!/usr/bin/env bash
# Agent OS Godot 本地启动器:X11 动态库在用户态 sysroot(无 root 环境),
# 用 LD_LIBRARY_PATH 补齐后启动 Editor 跑 Doc Editor。
#
# 用法:godot/run-local.sh            # 开 Doc Editor 窗口
#       godot/run-local.sh --headless ...  # 透传其他参数
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GODOT_BIN="${GODOT_BIN:-$HOME/godot/Godot_v4.7.1-stable_linux.x86_64}"
SYSROOT_LIBS="${AGENT_OS_SYSROOT:-$HOME/unity-editor/sysroot}/usr/lib64"

export LD_LIBRARY_PATH="$SYSROOT_LIBS:$SYSROOT_LIBS/pulseaudio${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$GODOT_BIN" --path "$ROOT" "$@"
