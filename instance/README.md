# instance/ — 本地部署实例

本目录是**本机部署用**的运行实例配置,约定:

- `agent-os.toml` / `skills.yaml`:本机实际配置(模型、技能路径),git 跟踪了
  一份基线,本地改动按需、**不要随手提交**(可能含本机路径/实验配置);
- `run-web.sh`:Web 服务启动器(从 kimi-code CLI 的 OAuth 凭据库现取
  access_token 导出为 MOONSHOT_API_KEY 后启动;token 15 分钟过期,
  长时间使用请重启本脚本)。用法:`./run-web.sh [port] [config]`;
- `runs/` / `traces/`:每次运行的产物与遥测,gitignore,不入库。

新机器部署:复制本目录,改 `agent-os.toml` 里的技能路径与模型配置即可。
