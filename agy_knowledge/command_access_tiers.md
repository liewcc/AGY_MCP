# Antigravity CLI 指令外部接入分层（Command Access Tiers）

> **目的**：记录如何让外部程序（Claude / MCP 客户端）操控 agy CLI 的内部斜线指令。
> agy 的 31 条斜线指令大多只存在于交互式 TUI 中，本文档把它们按"外部如何接入"
> 分成 6 个层级，并记录每条指令当前的实现状态。

相关文档：[slash_commands.md](slash_commands.md)（31 条指令清单）、
[delegation.md](delegation.md)（gemi/agy 委托策略）。

---

## 核心发现

agy 是一个编译后的 Go 单体可执行文件（`%LOCALAPPDATA%\agy\bin\agy.exe`）。
它的斜线指令在源码里通过 `registerCommand(name, handler)` 注册到主循环，
**不对外暴露 API**。但通过分析其本地存储和进程行为，我们找到了 6 条接入路径：

| 层级 | 机制 | 难度 | 实现状态 |
|------|------|------|----------|
| **A** | `agy.exe` CLI flags | ✅ 原生支持 | 已完成 |
| **B** | 直接读写 SQLite 会话 DB | 低 | ✅ 已实现 3 条 |
| **C** | 读写配置文件（JSON/YAML） | 低 | ✅ 已实现 6 类 |
| **D** | shell 子命令 / 文件操作 | 低 | ✅ 已实现 4 条 |
| **E** | 本地 gRPC 语言服务器 | 中 | ✅ 3/3（usage + tasks + agents） |
| **F** | ConPTY 伪终端注入 | 高 | ⏳ 未实现 |

---

## Tier A — CLI Flags（原生支持）

agy 只把少数指令暴露为命令行参数。`agy.exe --help` 完整输出：

| Flag | 等价斜线指令 | 说明 |
|------|--------------|------|
| `--model <name>` | `/model` | 设置会话模型 |
| `--add-dir <path>` | `/add-dir` | 把目录加入工作区（可重复） |
| `--conversation <id>` | `/resume` `/switch` | 恢复指定会话 |
| `-c` / `--continue` | — | 继续最近的会话 |
| `--dangerously-skip-permissions` | `/permissions` | 自动批准所有工具权限 |
| `--sandbox` | `/permissions` | 沙盒受限模式 |
| `--print` / `-p` / `--prompt` | — | 非交互单次执行并打印 |
| `--prompt-interactive` / `-i` | — | 执行初始 prompt 后继续交互 |

**子命令**（`agy <subcommand>`）：`changelog`、`install`、`models`、
`plugin`（install/uninstall/list/enable/disable）、`update`。

实现位置：`agy_client.py::ask_agy()` 组装这些 flag。

---

## Tier B — SQLite 会话 DB（已实现）

会话存储在 `~/.gemini/antigravity-cli/conversations/<id>.db`（SQLite）。
表 `steps` 保存逐轮轨迹：`step_type=14` 是用户 prompt，`step_type=15` 是模型回复。
`trajectory_meta.cascade_id` 必须等于文件名（agy 据此查找会话）。

| 斜线指令 | MCP 工具 | 机制 | 实现 |
|----------|----------|------|------|
| `/fork` `/branch` | `fork_conversation` | 复制 .db → 新 UUID，更新 cascade_id | ✅ |
| `/rewind` `/undo` | `rewind_conversation` | 删除最后 N 轮的所有 step 行 | ✅ |
| `/export` | `export_conversation` | 把轨迹格式化为 markdown 写盘 | ✅ |
| `/rename` | — | 标题来源不明（疑似内存/history.jsonl），跳过 | ⏭️ |
| `/artifact` | — | artifact 存储结构未明，跳过 | ⏭️ |

实现位置：`conversations.py`（函数 `fork/rewind/export_conversation`）。

⚠️ **警告**：agy 运行时对 .db 持有 WAL 锁。外部写入应在 agy 空闲时进行，
避免数据库损坏。读取用 `mode=ro` URI 始终安全。

---

## Tier C — 配置文件（已实现）

配置全部在 `~/.gemini/antigravity-cli/` 下。agy 通常通过 inotify 热重载这些文件，
**修改即生效**。建议外部程序用原子写入（写临时文件再 rename）避免读到半成品。

| 斜线指令 | 配置文件 | 格式 | MCP 工具 |
|----------|----------|------|----------|
| `/config` `/settings` | `settings.json` | JSON | `read_settings` / `write_settings` |
| `/keybindings` | `keybindings.json` | JSON | `read_keybindings` / `write_keybindings` |
| `/mcp` | `mcp.json` | JSON | `read_mcp_config` / `write_mcp_config` |
| `/statusline` | `statusline.yaml` | YAML | `read_statusline_config` / `write_statusline_config` |
| `/hooks` | `hooks/` 目录 | 可执行脚本 | `list_hooks` / `read_hook_script` / `write_hook_script` |
| `/skills` | `**/*.md` | Markdown | `list_skills` |

综合查询：`get_config_info` 一次性返回全部配置状态。

实现位置：`tier_c_commands.py`。

### 配置文件结构示例

**mcp.json**（标准 MCP 格式）：
```json
{
  "mcpServers": {
    "local-fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
    }
  }
}
```

**statusline.yaml**：
```yaml
layout:
  left:  [mode_indicator, current_model]
  right:
    - component: shell_command
      command: "free -m | awk 'NR==2{print $3\"MB\"}'"
      refresh_ms: 2000
```

**hooks/**：`pre-prompt`、`post-response` 等无后缀脚本，需可执行权限。
`pre-prompt` 在 prompt 发给模型前执行（可收集系统状态隐式附加）；
`post-response` 在模型返回后通过 stdin 接收输出做后处理。

### 注意：默认配置 vs 用户配置

agy 首次运行会自动生成 `settings.json` 和 `keybindings.json`（默认值）。
`mcp.json` / `statusline.yaml` / `hooks/` 默认**不存在**——这些是高级配置，
我们的工具相当于赋予外部程序从零创建它们的能力。

---

## Tier D — Shell 子命令 / 文件操作（已实现）

| 斜线指令 | MCP 工具 | 机制 |
|----------|----------|------|
| `/diff` | `show_diff` | 运行 `git diff` 子进程 |
| `/open <path>` | `open_path` | `os.startfile`（Win）/ `open` / `xdg-open` |
| `/logout` | `logout` | 删除本地 OAuth token 文件 |
| `/plugin ...` | `list/install/uninstall/enable/disable_plugin` | `agy plugin` 子命令 |

实现位置：`tier_d_commands.py`（diff/open/logout）+ `server.py`（plugin）。

---

## Tier E — 本地 gRPC 语言服务器（部分实现）

agy 启动时会拉起一个本地语言服务器，绑定两个 127.0.0.1 端口：
- 低端口：TLS 加密的 gRPC 服务 `/exa.language_server_pb.LanguageServerService`
- 高端口：LSP 连接

| 斜线指令 | gRPC 方法 | MCP 工具 | 状态 |
|----------|-----------|----------|------|
| `/usage` | `RetrieveUserQuotaSummary` | （TUI 配额视图） | ✅ |
| `/tasks` | `StreamAgentStateUpdates` → 解析内嵌 JSON | `list_tasks` | ✅ |
| `/agents` | `StreamAgentStateUpdates` 首条快照 | `agent_session_state` | ✅ |

完整技术细节（端口发现、TLS 证书提取、protobuf 解码）见
[../AGENTS.md](../AGENTS.md) 的 "How the gRPC quota method works"。
实现位置：`agy_models.get_quota_summary()`（usage）、`tier_e_commands.py`（tasks/agents）。

### ⚠️ 关键架构：tasks/agents 必须"寄生"到正在运行的 agy 会话

`/usage` 是**用户级全局配额**，临时 spawn 一个 headless agy 发空请求就能拿到。
但 `/tasks`（后台 shell 命令）和 `/agents`（子代理状态）是**某个活跃会话进程内存里
的运行时状态**——agy 是"每进程独立"架构（每个 agy 拉起自己的语言服务器，监听两个
随机 127.0.0.1 端口），状态不落磁盘、不共享。新 spawn 的 headless 实例里 sidecar
manager 根本没初始化。

所以 `tier_e_commands.py` 的做法是**寄生**到用户已经在跑的交互式 agy：

1. `Get-Process agy` 找出运行中的 PID，`Get-NetTCPConnection -OwningProcess` 拿到它们
   监听的端口（**不写死**，低端口=gRPC，高端口=LSP）。
2. 连上去**实时提取**自签 TLS 证书并临时信任——**本地服务器不需要任何 auth header**
   （这一点比预想的简单，无需复用证书或注入 token）。
3. `GetAllCascadeTrajectories` → 取活跃 conversation_id。
4. `StreamAgentStateUpdates(conversation_id)`（server-streaming）→ 读首条完整快照即
   cancel。快照里 tool action 的参数是**扁平 JSON**
   （`{"CommandLine":"ping -t 127.0.0.1","Cwd":...,"WaitMsBeforeAsync":500,
   "toolAction":...,"toolSummary":...}`），正则 + `json.loads` 直接抽出后台命令。

无运行中的 agy 时，两个工具返回 `{"status": "no running agy session found"}`，
让调用方能区分"没在跑"和"连不上"。

**已验证**：用户在终端跑 `agy` 并让它"在后台运行 ping -t 127.0.0.1"，`list_tasks`
正确返回该命令（background=true），`agent_session_state` 列出活跃会话 + 全部已加载
trajectory id + 在跑的 tool action。

### 其他可用 gRPC 方法（已实证，备用）

二进制里 `LanguageServerService` 暴露 ~250 个方法。空请求即可用的：`GetUserStatus`
（4.7KB 账户信息）、`GetWorkspaceInfos`、`GetAllCascadeTrajectories`。需要参数的：
`GetCascadeTrajectory`/`GetCascadeTrajectorySteps`/`GetConversationMetadata`
（field1 = conversation_id 字符串）、`GetAgentTeamMetadata`（field1 = `file://`
项目 URI）。`GetSlashCommands` 需要嵌套 model 子消息 + 真实 trajectory id，较麻烦，
暂未封装。`RequestAgentStatePageUpdate` 是推送模型，必须先有 `StreamAgentStateUpdates`
订阅者，单独调会报 `subscriber not found`。

---

## Tier F — ConPTY 伪终端注入（未实现）

这些是纯会话内指令，无文件/DB/gRPC 落点，只能通过伪终端模拟交互输入：

| 斜线指令 | 说明 | 备注 |
|----------|------|------|
| `/goal` | 长期自主执行循环 | 需 PTY |
| `/schedule` | 定时/cron 任务 | 需 PTY |
| `/grill-me` | 交互式问答对齐计划 | 需 PTY |
| `/planning` | 多轮计划生成 | 需 PTY |
| `/fast` | 切换快速模式（session 内状态） | 需 PTY |
| `/teamwork-preview` | 多代理协作预览 | 需 PTY |
| `/btw <query>` | 后台侧问 | **可用 `--print` 单次调用模拟** |

**技术路线**：复用 `agy_models.py` 已有的 Windows ConPTY 代码，
启动 agy 交互模式 → 向 stdin 写入 `/goal ...` → 读取 stdout（去除 ANSI 转义）。

⚠️ 已知陷阱：直接管道注入斜线指令会被当作 prompt 发给模型（agy 检测到非 TTY
即进入 print 模式，不解析本地指令）。必须用真 PTY 才能触发指令解析。
`/usage` 的 ConPTY 注入实验失败过（autocomplete 吞掉第一个 Enter），需注意。

---

## 实现进度汇总

- **已实现工具：24 个**（Tier A 原有 + B/C/D 新增 19 个 + Tier E 的 tasks/agents 2 个）
- **已验证**：MCP server 重启后正确加载，真实调用读写均正常；Tier E 对活跃会话实测通过
- **剩余**：Tier F 全部（需 PTY 封装），`/btw` 例外可用 `--print` 模拟

代码文件：`agy_client.py`、`conversations.py`、`tier_c_commands.py`、
`tier_d_commands.py`、`tier_e_commands.py`、`agy_models.py`，统一在 `server.py`
注册为 MCP 工具。
