# 助理委托策略指南（供 Claude 自用）

> **背景**：用两个 MCP 助理节省 Claude 的 token 消耗，各司其职：
> - **gemi-mcp** — 控制 Gemini 网页 UI，主力负责上网搜索与图片生成
> - **agy-mcp** — 无头 CLI，主力负责代码执行、文件修改、git 操作

---

## 1. 三方分工总览

| 任务类型 | 谁来做 | 理由 |
|----------|--------|------|
| 上网搜索 / 资料研究 | **gemi-mcp** | 原生 Google Search，信息质量最高 |
| 图片生成 | **gemi-mcp** | Gemini Image Generation 工具 |
| 多模态文件分析 | **gemi-mcp** | 可 attach 本地文件直接提问 |
| 写代码 / 修改文件 | **agy-mcp** | 真实执行，跳过权限确认 |
| git commit / push | **agy-mcp** | 在 trusted CWD 里真正执行命令 |
| 样板代码 / 独立模块 | **agy-mcp** | 边界清晰，一条 prompt 即可完成 |
| 设计决策 / 复杂推理 | **Claude** | 需要对话上下文，无法序列化 |
| 探索性调试 | **Claude** | 需要多轮迭代，助理无法胜任 |
| 关键改动验证 | **Claude** | 最终责任在 Claude，必须亲自确认 |

---

## 2. gemi-mcp — 搜索与多模态助理

### 工具清单

| 工具 | 用途 |
|------|------|
| `apply_settings` | 切换模型或启用工具（如 Google Search、Image Generation） |
| `send_chat` | 发送 prompt，等待并返回文字回复（最常用） |
| `set_prompt` + `submit_response` | 需要图片输出时分两步执行 |
| `attach_files` | 附加本地文件供 Gemini 分析 |
| `download_images` | 把 Gemini 生成的图片下载到本地 |
| `redo_response` | 对不满意的回答重新生成 |

### 使用方式

**上网搜索（最常用场景）：**
```
1. apply_settings(tool="Google Search")
2. send_chat("你想搜索的问题")
```

**图片生成：**
```
1. apply_settings(tool="Image generation")
2. set_prompt("图片描述")
3. submit_response()
4. download_images(save_dir="D:\\AI\\AGY_MCP\\output")
```

**分析本地文件：**
```
1. attach_files(["D:\\AI\\AGY_MCP\\server.py"])
2. send_chat("请分析这个文件并…")
```

### 注意事项

- gemi-mcp 依赖 `engine_service.py`（port 18800）和浏览器内已登录的 Gemini 会话，若未启动则工具报错
- 搜索前**必须** `apply_settings` 切换到 Google Search，否则 Gemini 不联网
- 不适合代码执行或文件写入（它只能聊天，不能改你的项目文件）

---

## 3. agy-mcp — 代码执行助理

### 工具清单

| 工具 | 用途 |
|------|------|
| `ask_antigravity` | 发 prompt 给 agy CLI 执行，可修改文件、跑命令 |
| `list_conversations` | 列出历史对话 |
| `read_conversation` | 读取历史对话完整记录 |
| `list_models` | 查看可用模型 |
| `list/install/uninstall/enable/disable_plugin` | 插件管理 |
| `get_changelog` | 查看更新日志 |

### 使用方式

```python
# 委托 agy 执行文件修改 + commit
ask_antigravity(
    prompt="""
    工作目录：D:\\AI\\AGY_MCP
    任务：把 server.py 第 X 行的 Y 改成 Z，然后 git commit -m "fix: ..."
    完成后报告每步结果。
    """,
    add_dirs=["D:\\AI\\AGY_MCP"],
    timeout=180
)
```

### 关键约束

- agy 在 `trustedFolders.json` 中第一个 trusted 目录运行。若目标项目不是该目录，需在 prompt 里明确 `cd <项目路径>` 后再执行命令
- `--dangerously-skip-permissions`：agy 会**真实执行**所有操作，不弹确认，委托前须确认 prompt 无误
- 默认超时 120s，复杂任务传 `timeout=300`

---

## 4. 决策树

```
收到任务
  │
  ├─ 需要上网查资料？ ──────────────────── → gemi-mcp（Google Search）
  │
  ├─ 需要生成图片？ ─────────────────────── → gemi-mcp（Image generation）
  │
  ├─ 需要分析本地文件但不改它？ ─────────── → gemi-mcp（attach_files）
  │
  ├─ 独立的代码写入 / 文件修改 / git 操作？
  │   ├─ 边界清晰、可写成一条 prompt？ ── → agy-mcp
  │   └─ 需要探索或多轮迭代？ ──────────── → Claude 自己做
  │
  └─ 设计决策 / 复杂推理 / 需对话上下文？ → Claude 自己做
```

---

## 5. token 节省估算

| 场景 | Claude 自己做 | 委托助理 |
|------|---------------|----------|
| 搜索一个技术问题 | ~2 000 tokens | ~300 tokens（prompt + 结果摘要） |
| 写一个 100 行模块 | ~3 000 tokens | ~500 tokens（agy 执行 + Claude 验证读文件） |
| git commit + push | ~800 tokens | ~200 tokens（agy 执行） |
| 图片生成 | 无法做 | gemi-mcp 负责 |
| 探索性 bug 调试 | ~5 000 tokens | **不适合委托** |

---

## 6. 验证原则

无论委托给哪个助理，Claude 仍需：
1. 读关键文件确认修改正确（`Read` 工具）
2. 向用户简报结果和任何异常
3. 搜索结果需判断来源可信度，不能直接照单全收
