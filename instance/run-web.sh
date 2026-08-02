#!/usr/bin/env bash
# agent-os-web 启动器:从 kimi-code CLI 的 OAuth 凭据库取最新 access_token,
# 导出为 MOONSHOT_API_KEY 后启动 Web 服务。
#
# 背景:api.kimi.com/coding 用的是 kimi-code 订阅的 OAuth token,
# 有效期只有 15 分钟,不能静态写死。kimi-code CLI 每次运行都会刷新
# ~/.kimi-code/credentials/kimi-code.json,所以启动时现取即可。
# 注意:服务进程内 token 过期(约 15 分钟)后 LLM 调用会开始 401,
# 长时间调试请重启本脚本(或在 agent_os 里换用长期 API key)。
set -euo pipefail

CRED_FILE="${KIMI_CODE_CREDENTIALS:-$HOME/.kimi-code/credentials/kimi-code.json}"
PORT="${1:-8391}"
CONFIG="${2:-/home/hengxiao/agent_os/instance/agent-os.toml}"

export MOONSHOT_API_KEY="$(python3 -c "import json; print(json.load(open('$CRED_FILE'))['access_token'])")"
echo "MOONSHOT_API_KEY 已从 $CRED_FILE 加载(15 分钟内有效)"
exec /home/hengxiao/agent_os/agent_os/.venv/bin/agent-os-web --config "$CONFIG" --port "$PORT"
