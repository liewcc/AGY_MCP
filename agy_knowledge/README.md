# agy_knowledge — Antigravity CLI 知识库

本文件夹集中存放关于 Google Antigravity CLI (`agy`) 的所有研究与实现知识。
其他智能体应从项目根目录的 **HANDOFF.md / CLAUDE.md / AGENTS.md** 进入，
再根据指引来此深入理解 agy 的指令结构与外部接入方式。

## 文档导航

| 文档 | 内容 |
|------|------|
| [slash_commands.md](slash_commands.md) | agy 互动模式全部 **31 条斜线指令**清单（指令名、别名、说明） |
| [command_access_tiers.md](command_access_tiers.md) | **核心文档**：如何让外部程序操控这些指令，按接入难度分 6 层（A–F），含每条指令的实现状态、文件路径、机制 |
| [delegation.md](delegation.md) | gemi-mcp / agy-mcp / Claude 三方委托分工策略（节省 token） |

## 快速理解路径

1. 想知道 agy **有哪些指令** → `slash_commands.md`
2. 想知道**外部如何调用**这些指令、**已实现哪些** → `command_access_tiers.md`
3. 想知道**何时该委托** agy 而非 Claude 自己做 → `delegation.md`

## 实现代码位置（在项目根目录，非本文件夹）

| 代码文件 | 对应层级 |
|----------|----------|
| `agy_client.py` | Tier A（CLI flags）+ SQLite 读取基础设施 |
| `conversations.py` | Tier B（fork/rewind/export） |
| `tier_c_commands.py` | Tier C（配置文件读写） |
| `tier_d_commands.py` | Tier D（diff/open/logout） |
| `agy_models.py` | Tier E（gRPC quota） |
| `server.py` | 统一 MCP 工具注册入口 |
