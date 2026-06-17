# agy_knowledge — Antigravity CLI 知识库

本文件夹集中存放关于 Google Antigravity CLI (`agy`) 的所有研究与实现知识。
其他智能体应从项目根目录的 **HANDOFF.md / CLAUDE.md / AGENTS.md** 进入，
再根据指引来此深入理解 agy 的指令结构与外部接入方式。

## 文档导航

| 文档 | 内容 |
|------|------|
| [architecture.md](architecture.md) | **入口文档**：MCP server 整体架构、模块分工、与 agy 的 6 种通信机制（含数据流图） |
| [command_access_tiers.md](command_access_tiers.md) | 每条斜线指令的接入机制详表，按层级 A–F 列出实现状态与代码位置 |
| [slash_commands.md](slash_commands.md) | agy 互动模式全部斜线指令清单（指令名、别名、说明） |
| [delegation.md](delegation.md) | gemi-mcp / agy-mcp / Claude 三方委托分工策略（节省 token） |

## 快速理解路径

1. 想了解**整体架构 / server 如何与 agy 通信** → `architecture.md`
2. 想知道 agy **有哪些指令** → `slash_commands.md`
3. 想知道**每条指令怎么接入、已实现哪些** → `command_access_tiers.md`
4. 想知道**何时委托 agy 而非 Claude 自己做** → `delegation.md`

## 实现代码位置（在项目根目录，非本文件夹）

| 代码文件 | 对应层级 |
|----------|----------|
| `agy_client.py` | Tier A（CLI flags + headless --print + SQLite 读取） |
| `conversations.py` | Tier B（fork/rewind/export） |
| `tier_c_commands.py` | Tier C（配置文件读写） |
| `tier_d_commands.py` | Tier D（diff/open/logout） |
| `tier_e_commands.py` | Tier E（gRPC attach — tasks/agents） |
| `tier_f_commands.py` | Tier F（ConPTY 伪终端注入） |
| `agy_models.py` | Tier E（gRPC quota）+ ConPTY 模型列表 |
| `server.py` | 统一 MCP 工具注册入口（39 个工具） |
