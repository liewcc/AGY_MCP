# AGY_MCP — 服务器架构与通信机制

> 读这份文档可以完整理解 `server.py` 的设计，以及它用哪 6 种机制与 `agy` CLI 对话。

---

## 1. 项目定位

```
MCP 客户端 (Claude Code / Cursor / ...)
        │ stdio (JSON-RPC)
        ▼
   server.py  ← FastMCP，39 个工具
        │
        ├─ agy --print ...       (Tier A/D headless 子进程)
        ├─ ~/.gemini/.../*.db    (Tier B SQLite 直接读写)
        ├─ ~/.gemini/.../*.json  (Tier C 配置文件读写)
        ├─ git / os.startfile    (Tier D shell 操作)
        ├─ gRPC 127.0.0.1:PORT   (Tier E 寄生到已运行的 agy)
        └─ Windows ConPTY        (Tier F 伪终端注入)
```

`agy`（Antigravity CLI）是 Google 的 Gemini CLI，编译好的 Go 单体可执行文件。
它**不对外暴露 HTTP/RPC API**，所有功能只在交互式 TUI 或 `--print` 模式下可用。
`AGY_MCP` 通过 6 种机制绕过这一限制，把 agy 的全部能力暴露为 MCP 工具。

---

## 2. 模块分工

| 文件 | 职责 | 调用的 Tier |
|------|------|-------------|
| `server.py` | FastMCP 入口，注册全部 39 个工具，把同步函数包进 `asyncio.to_thread` | A–F |
| `agy_client.py` | headless `agy --print` 调用 + SQLite 回读 + protobuf 解码 | A |
| `conversations.py` | 会话列表 / 读取 / fork / rewind / export | B |
| `tier_c_commands.py` | 配置文件 read/write（settings/keybindings/mcp/statusline/hooks/skills） | C |
| `tier_d_commands.py` | git diff / open / logout | D |
| `tier_e_commands.py` | gRPC attach — list_tasks / agent_session_state | E |
| `tier_f_commands.py` | ConPTY 伪终端注入 — goal/planning/fast/schedule/btw/grill-me/teamwork | F |
| `agy_models.py` | 模型列表（ConPTY）+ 配额（gRPC） + REST quota；供 TUI 使用 | B/E |

`server.py` 只做"注册"和"async 包装"，没有业务逻辑。全部逻辑在上面各模块。

---

## 3. 启动与生命周期

```
MCP 客户端 → 启动 python server.py (stdio)
                │
                └─ FastMCP 建立 JSON-RPC 会话
                   每次工具调用 → asyncio.to_thread(同步函数)
                   → 返回 JSON 结果
```

- `server.py` 顶部立即写 `_startup.log`，早于 MCP 握手，用于诊断"进程有没有起来"。
- 服务器本身是**无状态**的——每次工具调用独立，不保留跨调用的 Python 内存（SQLite / 文件系统是持久化层）。
- `agy --print` 子进程在工具调用期间创建，调用结束后子进程已退出；gRPC 连接在每次调用内建立和关闭。

---

## 4. Tier A — headless `agy --print`（主要通信路径）

**用途**：`ask_antigravity`、`ask_btw`（btw 借用此机制）

### 数据流

```
ask_agy(prompt, model, ...)
  │
  ├─ 1. 组装 CLI 参数
  │      agy.exe --model <label> --dangerously-skip-permissions
  │              [--conversation <id>] [--add-dir <dir>] --print <prompt>
  │
  ├─ 2. subprocess.run(...)
  │      cwd = trustedFolders.json 里的第一个 TRUST_FOLDER
  │      stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL
  │      env: GEMINI_CLI_TRUST_WORKSPACE=true
  │      creationflags: CREATE_NO_WINDOW (0x08000000)
  │
  ├─ 3. agy 把答案写入 SQLite
  │      ~/.gemini/antigravity-cli/conversations/<uuid>.db
  │      (agy 不写 stdout — 答案只在 DB 里)
  │
  └─ 4. 找到刚写的 DB，回读答案
         glob(CONV_DIR/*.db) 过滤 mtime >= 调用开始时间
         _answer_from_db(path) 解析 steps 表：
           step_type=14 → 用户 prompt
           step_type=15 → 模型回复（protobuf field 1，取最长串）
```

### protobuf 解码（无 .proto 文件）

`agy_client.py::_strings()` 手写 varint 解析器，递归遍历 wire-type-2 块，
取可打印率 > 85% 的 UTF-8 片段。`field_number=1` 的最长串即为模型回复正文。

### 多轮对话

`--conversation <uuid>` 让 agy 追加到同一个 .db，`ask_agy` 返回的 `conv_id`
就是 .db 文件名（不含 .db 后缀），传回 `ask_antigravity(conversation=...)` 即可续轮。

---

## 5. Tier B — SQLite 直接读写（会话管理）

**用途**：`list_conversations`、`read_conversation`、`fork_conversation`、`rewind_conversation`、`export_conversation`

```
~/.gemini/antigravity-cli/conversations/
    <uuid1>.db
    <uuid2>.db   ← 每个文件一个独立会话
```

### 关键表结构

```sql
-- steps 表（轨迹）
idx          INTEGER   -- 行序号
step_type    INTEGER   -- 14=用户turn, 15=模型回复
step_payload BLOB      -- protobuf 编码内容

-- trajectory_meta 表
cascade_id   TEXT      -- 必须等于文件名（agy 用此查找会话）
```

### fork 机制

```python
shutil.copy2(src_db, new_uuid + ".db")   # 复制整个 .db
# 更新 trajectory_meta.cascade_id = new_uuid
# 返回 new_uuid
```

### rewind 机制

```python
# 找到最后 N 个 step_type=14 的 idx
# DELETE FROM steps WHERE idx >= 第N个用户turn的idx
```

⚠️ agy 运行时对 .db 持有 WAL 锁，外部写操作（fork/rewind）应在 agy 空闲时进行。
读操作用 `file:path?mode=ro` URI 始终安全。

---

## 6. Tier C — 配置文件读写

**用途**：settings / keybindings / mcp / statusline / hooks / skills

所有配置在 `~/.gemini/antigravity-cli/`，agy 通过 inotify 热重载，**修改即生效**。

```
antigravity-cli/
  settings.json       — 当前模型、权限、telemetry
  keybindings.json    — 快捷键映射
  mcp.json            — 外部 MCP server 定义
  statusline.yaml     — TUI 状态栏布局
  hooks/              — pre-prompt / post-response 可执行脚本
  **/*.md             — skill 文件
```

`tier_c_commands.py` 的全部函数都是同步 JSON/YAML/文件读写，无需启动 agy 进程。
`get_config_info()` 一次性返回全部配置快照，便于调试。

---

## 7. Tier D — shell 子命令 / 系统调用

**用途**：`show_diff`、`open_path`、`logout`、插件管理

| 工具 | 实现 |
|------|------|
| `show_diff` | `subprocess.run(["git", "diff", ...])` |
| `open_path` | `os.startfile(path)`（Windows）|
| `logout` | 删除 `~/.gemini/antigravity-cli/` 下的 OAuth token 文件 |
| 插件系列 | `subprocess.run([agy.exe, "plugin", "list/install/..."])` |

---

## 8. Tier E — gRPC 寄生到运行中的 agy

**用途**：`list_tasks`、`agent_session_state`

这是最复杂的通信路径。`/tasks` 和 `/agents` 的数据存在于**正在运行的 agy 进程内存**中，
只有用户已开着交互式 agy 时才可访问。

### 完整流程

```
tier_e_commands.py
  │
  ├─ 1. 找运行中的 agy 进程
  │      Get-Process agy → PIDs
  │      Get-NetTCPConnection -OwningProcess <pid> → 监听端口列表
  │      lower_port = gRPC/TLS, higher_port = LSP
  │
  ├─ 2. 实时提取 TLS 证书
  │      ssl.create_default_context(CERT_NONE)
  │      → socket.connect(127.0.0.1:lower_port)
  │      → ts.getpeercert(binary_form=True) → PEM
  │      (本地服务器不需要 auth header，比预想简单)
  │
  ├─ 3. 建立 gRPC channel
  │      grpc.ssl_channel_credentials(root_certificates=cert_pem)
  │      grpc.secure_channel("127.0.0.1:<port>", creds,
  │        options=[("grpc.ssl_target_name_override","localhost")])
  │
  ├─ 4. 取活跃 conversation_id
  │      GetAllCascadeTrajectories (空请求) → trajectory ids
  │
  └─ 5. 订阅 agent 状态流
         StreamAgentStateUpdates(conversation_id) → server-streaming
         读首条完整快照即 cancel
         tool action 参数是扁平 JSON，正则 + json.loads 提取
```

### 每进程独立架构

agy 是"每进程一个语言服务器"——每个 agy 实例监听**随机**的两个 127.0.0.1 端口，
状态不共享、不落磁盘。所以必须寄生到**用户正在运行的那个**实例，而不能 spawn 新的。

无运行中的 agy 时，工具返回 `{"status": "no running agy session found"}`。

---

## 9. Tier F — Windows ConPTY 伪终端注入

**用途**：`run_goal`、`start_planning`、`start_schedule`、`start_grill_me`、
`start_teamwork_preview`、`toggle_fast_mode`、`ask_btw`（btw 例外用 --print）

这些斜线命令只存在于 agy 的交互式 TUI 中，没有对应的 CLI flag 或文件落点。
唯一的接入方式是伪装成一个真实终端。

### 为何不能用普通管道

agy 启动时检测 `isatty(stdin)`。非 TTY 时直接进入 `--print` 模式，把输入当作 prompt 发给模型，不解析斜线命令。

### 实现流程

```
tier_f_commands.py::_run_conpty_command(slash_cmd, wait_s)
  │
  ├─ 1. CreatePseudoConsole (Windows ConPTY API)
  │      ctypes 调用 kernel32.CreatePseudoConsole
  │      建立 read_pipe / write_pipe 对
  │
  ├─ 2. CreateProcessW with PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
  │      启动 agy.exe 交互模式（无 --print flag）
  │      agy 认为自己在真实终端里
  │
  ├─ 3. 等待 TUI 初始化 (3.5 s)
  │      agy 需要时间完成 OAuth 认证 + 界面渲染
  │
  ├─ 4. 注入斜线命令
  │      write_pipe.write(b"/goal description\x1b\r")
  │      ^^^                              ^^^^
  │      斜线命令文本                ESC 关掉自动补全下拉框
  │                                       ^^
  │                                    Enter 执行
  │
  ├─ 5. 读取 N 秒 stdout（在独立线程）
  │      去除 ANSI/VT 转义序列
  │      提取可读文本
  │
  └─ 6. 清理
         taskkill /F /T /PID <agy_pid>  (包括子进程树)
         关闭所有 handle
```

### 关键陷阱

- **autocomplete 吞 Enter**：agy TUI 里斜线命令有自动补全。首个裸 `\r` 会选中补全项而不执行命令。解决：先发 `\x1b`（ESC）关掉补全面板，再发 `\r`。
- **各命令等待时间不同**：`/planning` 需要 12 s，`/fast` 只需 3 s。`wait_response_s` 参数按命令分别调整。
- **进程树清理**：agy 会 spawn 语言服务器子进程，必须用 `/T`（终止树）而不只是 kill 主 PID。

---

## 10. 配置与环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGY_BIN` | `%LOCALAPPDATA%\agy\bin\agy.exe` | agy 可执行路径 |
| `AGY_CONV_DIR` | `~/.gemini/antigravity-cli/conversations` | 会话 DB 目录 |
| `AGY_TRUSTED_CWD` | trustedFolders.json 第一条 | headless 调用的工作目录 |
| `AGY_DEFAULT_MODEL` | `Gemini 3 Pro` | 默认模型显示名 |
| `AGY_TIMEOUT` | `120` | headless 调用超时秒数 |

---

## 11. 工具总览（39 个）

| Tier | 工具 |
|------|------|
| A | `ask_antigravity`, `ask_btw` |
| B | `list_conversations`, `read_conversation`, `fork_conversation`, `rewind_conversation`, `export_conversation` |
| C | `read_settings`, `write_settings`, `read_keybindings`, `write_keybindings`, `read_mcp_config`, `write_mcp_config`, `read_statusline_config`, `write_statusline_config`, `list_hooks`, `read_hook_script`, `write_hook_script`, `list_skills`, `get_config_info` |
| D | `show_diff`, `open_path`, `logout`, `list_plugins`, `install_plugin`, `uninstall_plugin`, `enable_plugin`, `disable_plugin`, `import_plugins`, `get_changelog` |
| E | `list_tasks`, `agent_session_state` |
| F | `run_goal`, `start_planning`, `start_schedule`, `start_grill_me`, `start_teamwork_preview`, `toggle_fast_mode`, `list_models` |

---

## 相关文档

- [`command_access_tiers.md`](command_access_tiers.md) — 每条斜线指令的接入机制详表
- [`slash_commands.md`](slash_commands.md) — agy 全部斜线指令清单
- [`delegation.md`](delegation.md) — Claude / agy / gemi 三方分工策略
- [`../HANDOFF.md`](../HANDOFF.md) — 调试细节、API 发现记录、pytermgui 踩坑
- [`../AGENTS.md`](../AGENTS.md) — gRPC 技术细节与配额解码
