# Gemi MCP Usage Guidelines

## 1. Service Switching
Always use the `switch_service` tool to switch the active AI provider (e.g. `gemini` or `deepseek`) before performing actions on that service.

## 2. Alignment of Provider State
If the backend engine service (TUI/FastAPI) is restarted, the in-memory provider state resets to `gemini` by default. 
If the browser window is already on DeepSeek, you MUST run `switch_service(service="deepseek")` first to align the backend's provider state with the browser page before calling other browser tools (like `new_chat` or `send_chat`). Otherwise, the backend will run Gemini DOM selectors on the DeepSeek page, causing 500 errors.

## 3. Manual Login/Verification (DeepSeek WAF)
DeepSeek web UI frequently requires manual login or WAF CAPTCHA verification. When `new_chat` or `send_chat` returns a `login_required` status, a headed browser window will be opened on the desktop. The user must manually solve the challenge or log in. Once completed, call `new_chat()` again to verify and transition back.

## 4. Configuration Safety
Never modify `"active_user"` in `data/config.json`. The user profiles (like `ccliew.blog`) already contain the necessary cookies for both Gemini and DeepSeek.

## 5. Command Shortcuts (User Intent)
When the user says "叫 agy 做某件事" (tell agy to do X) or "叫 gemi 做某件事" (tell gemi to do Y), they want you to call the respective `agy-mcp` or `gemi` MCP tools to perform the work (e.g., using `ask_antigravity` for `agy` analysis/execution, or `gemi` tools for browser operations) rather than performing it yourself through local file reads/grep or custom terminal scripts.
