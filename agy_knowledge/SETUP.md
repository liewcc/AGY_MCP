# agy-mcp 安装与验证指南

安装完 agy-mcp 后，请按以下步骤验证集成是否正常。

## 前提条件

- ✅ Antigravity CLI (`agy.exe`) 已安装并登录
- ✅ Python 3.10+ 已安装
- ✅ agy-mcp 已安装：`pip install -r requirements.txt`
- ✅ agy-mcp 已添加到 Claude Code: `claude mcp add agy-mcp -- python <path>\server.py`

## 第一步：运行本地工具测试

这一步验证所有 39 个工具的**功能层**是否正常工作（不涉及 MCP 通信）。

### 方式 A：命令行运行（推荐）

在项目根目录执行：

```bash
python test_tools.py
```

你会看到类似这样的输出：

```
🔍 agy-mcp 工具测试报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ Tier A — 核心问答
  ✓ ask_antigravity       (0.5s)
  ✓ ask_btw              (0.3s)

✅ Tier B — 对话管理
  ✓ list_conversations   (0.1s)
  ✓ read_conversation    (0.2s)
  ... (更多工具)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
总计：39 个工具，35 个 ✅，4 个 ⚠️

✅ 所有核心工具正常！
```

**含义：**
- ✅ 绿色：工具工作正常
- ⚠️ 黄色：工具响应但可能有警告（如无运行的 agy 会话）
- ❌ 红色：工具出错，需要修复

### 方式 B：在 TUI 中运行（可选）

打开 TUI：
```bash
run.bat
```

在 Quota 面板下方可以看到 **[运行工具测试]** 按钮。点击后会在 Main Panel 中显示实时测试进度和结果。

### 如果有红色错误

请查看 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 对应的错误代码。常见问题：

| 错误 | 原因 | 解决 |
|------|------|------|
| `agy.exe not found` | agy 未装或路径不对 | 检查 `%LOCALAPPDATA%\agy\bin\agy.exe` |
| `No trusted folder` | 没有添加信任文件夹 | 在 agy 中运行一次 `/trust-this-folder` |
| `Permission denied` | Python 权限不足 | 用管理员权限打开 PowerShell |

---

## 第二步：验证 Claude Code 集成

这一步验证 agy-mcp 与 Claude Code 的 **MCP 通信层**是否正常（stdio, 权限, 协议）。

### 在 Claude Code 中执行

打开 Claude Code 对话框，**复制并粘贴以下内容**：

```
Run SETUP.md step 2 integration test
```

Claude 会自动识别这条指令，然后：

1. 调用 5 个关键工具验证通信
2. 显示每个工具的成功/失败状态
3. 生成集成报告

**预期输出：**

```
🔗 agy-mcp MCP 集成测试
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣  ask_antigravity
   ✅ 通信正常 (0.6s)
   问题：What is 2+2?
   回答：4

2️⃣  list_models
   ✅ 返回模型列表 (0.1s)

3️⃣  read_settings
   ✅ 读取配置成功 (0.1s)

4️⃣  list_tasks
   ⚠️  没有运行中的 agy 会话（预期行为）

5️⃣  show_diff
   ✅ git diff 正常 (0.2s)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 集成测试通过！agy-mcp 已就位。
```

**如果失败：**

| 症状 | 原因 | 解决 |
|------|------|------|
| `Tool not found` | MCP 服务器未连接 | 运行 `claude mcp list` 检查 agy-mcp 是否注册 |
| `Permission denied` | Claude Code 权限不足 | 检查 `~/.claude/settings.json` 的 permissions |
| `Timeout` | agy 响应慢或网络问题 | 等待 5 秒后重试 |

---

## 第三步：开始使用

一旦两步测试都通过，agy-mcp 就完全就位了。

### 在 Claude Code 中使用示例

```
帮我分析 src/main.py 的代码结构，用 agy 来查看 git diff
```

Claude 会自动调用 `show_diff` 和 `ask_antigravity` 来完成任务。

### 常用场景

| 需求 | 调用的工具 |
|------|-----------|
| 问 agy 问题 | `ask_antigravity` |
| 后台问一个问题 | `ask_btw` |
| 查看改动的代码 | `show_diff` |
| 读取配置文件 | `read_settings`, `read_keybindings` 等 |
| 管理对话历史 | `list_conversations`, `read_conversation`, `fork_conversation` |

---

## 常见问题

### Q: 第一步测试失败，但第二步通过了，这是否正常？

**A:** 不正常。如果本地测试失败但 MCP 测试通过，说明通信层掩盖了潜在问题。建议：

1. 重新运行 `python test_tools.py` 看具体错误
2. 查看 `_startup.log` 检查服务器启动日志
3. 在 Claude Code 中要求 "诊断 agy-mcp" 看完整错误堆栈

### Q: 可以重复运行这些测试吗？

**A:** 完全可以。任何时候想验证 agy-mcp 是否还正常运行，都可以：

- 运行 `python test_tools.py`
- 或在 Claude Code 中说 "再运行一遍集成测试"

### Q: 为什么需要两步验证？

**A:** 
- **第一步** — 快速验证工具代码本身
- **第二步** — 验证 MCP 通信协议、权限、超时等
- 两者都通过才能保证完整的集成

---

## 下一步

- 阅读 [architecture.md](architecture.md) 了解 39 个工具的完整清单和设计
- 查看 [command_access_tiers.md](command_access_tiers.md) 理解工具的访问权限模型
- 在 Claude Code 中探索工具的各种用法

---

**需要帮助？**

- 遇到问题请查看 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- 或在 Claude Code 中说 "诊断 agy-mcp 错误：<具体错误信息>"
