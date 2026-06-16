# AGY_MCP — Handoff (TUI work)

This handoff outlines the current progress and specifies the exact tasks and details for the next agent to continue the work on the terminal control panel (`tui.py`).

---

## 1. What was completed in this session

1. **Fixed ModelButton Line Wrapping Bug:**
   - **Problem:** `ptg.break_line` was counting raw TIM markup characters (like `[120]●[/] [247]...`) when calculating line length. This caused strings like `Gemini 3.5 Flash (Low)` (length 37 with markup) to exceed the container width limit and wrap the closing parenthesis `)` to the next line.
   - **Fix:** Modified `ModelButton.get_lines()` to parse the TIM markup to ANSI first, and then run `ptg.break_line` on the ANSI representation (where escape sequences are properly measured and not counted as display characters).
2. **Fixed Highlight/Hover Background Reset Bug:**
   - **Problem:** When hovered or selected, the white-background/black-text style (`black @255`) was applied. However, the internal colors of the dot `●` and grey text embedded a reset code `\x1b[0m` that reset the background color, leading to a broken and partial hover highlight.
   - **Fix:** Used a boolean `is_highlighted` to check if a button is hovered or selected. If highlighted, we strip all internal ANSI escape sequences first using a regex, then apply `self.styles.value` to the clean plain text to ensure a solid, uniform white-background highlights across the entire width.

## 2. Research Findings on Quota Retrieval

We researched how to implement the `Quota` feature and execute the `/quota` command:
- **Direct CLI Execution Fails:** Running `agy --print "/quota"` directly causes the Gemini Coder agent backend to treat `/quota` as a developer prompt. It starts analyzing the local project workspace (performing `list_dir`, `view_file` on `package.json`, and searching the codebase), which hangs or times out in print mode.
- **REST API Solution Found:** We found a previous test script `test_retrieve_quota.py` inside `.gemini/antigravity/brain/.../scratch/`. It retrieves the user's quota using a POST request to:
  `https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`
  with the OAuth `access_token` and project `"phat-concept-j2hpz"` or `"app"`.
- **API Response:** We tested this API via the command line with the user's current token from Windows Credential Manager and verified it successfully and instantly returns the list of model quota buckets and their remaining fraction:
  ```json
  {
    "buckets": [
      {
        "resetTime": "2026-06-17T06:30:52Z",
        "tokenType": "REQUESTS",
        "modelId": "gemini-2.5-flash",
        "remainingFraction": 1
      },
      ...
    ]
  }
  ```

## 3. Pending Tasks (To Be Completed Next)

The owner requested the following UI redesign and functionality:
1. **Remove Old Interface Elements:** Remove all the elements inside the red circle `A` in the mock image:
   - The split panel between `Models` and `MCP Tools` (meaning `MCP Tools` list is removed completely).
   - The `Ask` input field box.
   - The `Send` button.
2. **Implement Left Sidebar:**
   - Add a sidebar container on the left of the main window layout.
   - The sidebar should contain two selectable items: `Models` and `Quota`.
3. **Implement Right Content Area:**
   - Place a content panel on the right of the sidebar.
   - If `Models` is selected in the sidebar, show the list of models in the content panel.
   - If `Quota` is selected in the sidebar, trigger the `retrieveUserQuota` API and present the formatted quota information (model name, remaining percentage, reset time, etc.) in the content panel.

## 4. Already-done styling decisions (don't redo)

- **All frames are uniform grey** `240` (`BORDER = "240"`): every box, splitter divider, and the Window border.
- **Account card**: the `current log in profile: ...` line is located inside the card, below the flat `log in` / `log out` buttons.
- **Flat buttons/labels**: grey rectangular frame + title, no color at rest, title/label turns accent `72 bold` or `black @255` on hover/select.

## 5. pytermgui gotchas

- **Console encoding**: printing box-drawing chars under Windows cp1252 crashes. For any test that prints rendered lines, run with `PYTHONIOENCODING=utf-8`.
- **Smoke test without a TTY**: `build_window()` is pure construction, so you can validate API usage with:
  `PYTHONIOENCODING=utf-8 python -c "import tui; tui.build_window()"`
- **Accessing Credentials**: `check_email_now` in `tui.py` demonstrates how to read the active OAuth credentials from the Windows Credential Manager. Use this token to query the `retrieveUserQuota` API.

## 6. How to run / test

```bat
setup.bat            REM install deps
run.bat              REM launch the TUI
```
