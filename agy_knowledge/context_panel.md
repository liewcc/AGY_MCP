# TUI Content Panel — 实现笔记

> 对应 `tui.py::ContentPanel` + `agy_core.py::get_context_stats()`

## 功能概述

TUI 左侧导航增加了 **📝 Content** 项，点击后右侧主面板显示当前 agy 会话的
Context Window 使用情况，效果类似 agy CLI 的 `/context` 斜线指令。

---

## 数据来源

### 为什么不用 gRPC？

调查了语言服务器的 gRPC 方法：

- `GetConversationMetadata` — 只有 workspace 路径、branch、会话 ID，无 token 数
- `GetCascadeTrajectory` — 同上，无 token 数
- `GetCascadeTrajectorySteps` — 返回空（0 bytes）

gRPC 对 token 计数无用，但仍用于**发现当前活跃会话的 ID**（见下）。

### 真正的 token 数据在哪里？

每次 agy 生成完一步，会把 token 统计写入 SQLite 会话库的 `gen_metadata` 表。
路径：`~/.gemini/antigravity-cli/conversations/<uuid>.db`

Protobuf 解析路径：`top.field1.field4` → token 结构体：

| 字段 | 含义 |
|------|------|
| `f2` | prompt tokens（未命中缓存，实际计算的部分） |
| `f3` | 本步骤的 token 数（用于按类型分类统计） |
| `f5` | cached tokens（命中缓存的部分） |

**总 context 用量 = 最新一行的 `f2 + f5`**（Gemini Context Caching 机制）

字段语义由 gemi-mcp 协助确认（参见当时的对话）。

### 按类型分类

遍历所有 `gen_metadata` 行，按对应 `steps.step_type` 累加 `f3`：

| step_type | 类别 |
|-----------|------|
| 14 | User messages |
| 15 | Agent responses |
| 33 | Tool calls |
| 其他（23/98）| 忽略 |

最终按比例把分类数字缩放到 `f2+f5` 的总量上，保证数字一致。

---

## 会话定位策略（`get_live_conversation_id()`）

```
1. 遍历所有运行中 agy 进程的 gRPC 端口
   → 找到有 active conversation 且 .db 里有 gen_metadata 的会话
   → 优先：交互式用户 session（有真实对话历史）

2. fallback：按 mtime 倒序扫 conversations/*.db
   → 取最近修改、且有 gen_metadata 的那个
   → 覆盖：agy-mcp headless 会话（--print 模式）

返回 None → get_context_stats() 报错
```

返回结果携带 `live` 布尔标志，TUI 据此显示 `● live` 或 `○ last session`。

---

## Headless 模式的限制

`agy --print`（agy-mcp 所有工具调用走的模式）只会写一行 gen_metadata（对应用户步骤 idx=0）：

- `f2` = 整个请求的 prompt tokens → **总量准确**
- `f3` 只有用户步的大小 → **分类不完整**，model_tokens / tool_tokens 显示 0

交互式 agy session（用户在终端直接对话）每一步都有 gen_metadata，分类才会完整。

---

## 相关文件

| 文件 | 内容 |
|------|------|
| `agy_core.py` | `_parse_gen_metadata_tokens()` — protobuf 解析 |
| `agy_core.py` | `get_live_conversation_id()` — gRPC + db 双路查找 |
| `agy_core.py` | `get_context_stats()` — 汇总返回 dict |
| `tui.py` | `ContentPanel` — Textual 面板，含网格可视化 |
