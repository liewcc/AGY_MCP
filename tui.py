"""AGY MCP — Textual TUI (replacing pytermgui version)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request

import win32cred
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Container, VerticalScroll
from textual.widgets import Header, Footer, Static, ListView, ListItem, Label, Button, ContentSwitcher, OptionList, Input
from textual.widgets.option_list import Option
from textual.reactive import reactive
from textual.events import Click

import datetime

from agy_core import list_models, get_quota_summary, get_context_stats, list_conversations as _list_conversations, CONV_DIR, read_conversation

STATS_FILE = Path(__file__).parent / "data" / "profile_stats.json"

# Configuration
AGY_BIN = Path(
    os.environ.get("AGY_BIN")
    or Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
)


def get_agy_version() -> str:
    """Return the agy CLI version string, or '(unknown)' on failure."""
    try:
        result = subprocess.run(
            [str(AGY_BIN), "--version"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,
        )
        line = (result.stdout or result.stderr or "").strip().splitlines()
        return line[0] if line else "(unknown)"
    except Exception:
        return "(unknown)"


def _get_agy_status() -> str:
    """Check if agy/Antigravity processes are running (blocking, run in thread)."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-Process -Name agy,Antigravity -ErrorAction SilentlyContinue "
             "| Select-Object Id,Name,@{N='MB';E={[int]($_.WorkingSet/1MB)}} "
             "| ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000,
        )
        raw = r.stdout.strip()
        if not raw:
            return "[dim]no agy processes[/dim]"
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        lines = ["[green]● agy processes[/green]"]
        for p in data:
            lines.append(f"  [dim]PID {p['Id']}[/dim]  {p['Name']}  {p['MB']} MB")
        return "\n".join(lines)
    except Exception:
        return "[dim]agy status unknown[/dim]"

class ProfileCard(Static):
    """Displays login profile information."""

    email = reactive("(not signed in)")

    def render(self) -> str:
        return f"Profile: {self.email}"


class ModelsPanel(Static):
    """Models view content."""

    models = reactive([])
    loading = reactive(False)
    selected_model = reactive("(none)")

    def compose(self) -> ComposeResult:
        """Compose models panel."""
        with Horizontal(id="models-header-row"):
            yield Static("[bold cyan]Available Models[/bold cyan]", id="models-header")
            yield Button("↻ Reload", id="btn-reload-models")
        yield Static("[dim]Current: (none)[/dim]", id="models-selected")
        yield OptionList(id="models-optionlist")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-reload-models":
            self.app._load_models_async()
            event.stop()

    def watch_loading(self, _=None) -> None:
        self._rebuild_list()

    def watch_models(self, _=None) -> None:
        self._rebuild_list()

    def watch_selected_model(self, model: str) -> None:
        try:
            self.query_one("#models-selected", Static).update(f"[dim]Current: {model}[/dim]")
        except Exception:
            pass
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        """Rebuild the OptionList with current models."""
        try:
            ol = self.query_one("#models-optionlist", OptionList)
            ol.clear_options()
            if self.loading:
                ol.add_option(Option("Loading models...", disabled=True))
                return
            if not self.models:
                ol.add_option(Option("(no models loaded)", disabled=True))
                return
            for model in self.models:
                marker = "●" if model == self.selected_model else " "
                ol.add_option(Option(f"{marker} {model}"))
        except Exception:
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle model selection."""
        if self.loading:
            return
        idx = event.option_index
        if 0 <= idx < len(self.models):
            model_name = self.models[idx]
            self.selected_model = model_name
            self.app._set_selected_model(model_name)


class QuotaPanel(Static):
    """Quota view content."""

    quota_data = reactive("")

    def compose(self) -> ComposeResult:
        """Compose quota panel with reload button."""
        yield Button("↻ Reload", id="btn-reload-quota")
        with VerticalScroll(id="quota-scroll"):
            yield Static("[bold cyan]Quota Information[/bold cyan]\n\n[dim]Loading quota data...[/dim]",
                         id="quota-content")
        # Auto-load quota on mount
        self._load_quota_async()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle reload button."""
        if event.button.id == "btn-reload-quota":
            self._reload_quota()

    def _reload_quota(self) -> None:
        """Reload quota data."""
        try:
            content = self.query_one("#quota-content", Static)
            content.update("[dim]Reloading quota data...[/dim]")
        except Exception:
            pass
        self._load_quota_async()

    def _load_quota_async(self) -> None:
        """Load quota data asynchronously, push result back to main thread."""
        def work():
            try:
                result = get_quota_summary()
                quota_text = "[bold cyan]Quota Information[/bold cyan]\n\n"
                if result:
                    quota_text += "[bold]Gemini Group Limits[/bold]\n"
                    if g := result.get("gemini"):
                        quota_text += f"  Weekly: {self._pct(g.get('weekly_pct')):.1f}%\n"
                        if weekly_reset := g.get("weekly_reset_ts"):
                            quota_text += f"    {self._format_countdown(weekly_reset)}\n"
                        quota_text += f"  Five-Hour: {self._pct(g.get('fiveh_pct')):.1f}%\n"
                        if fiveh_reset := g.get("fiveh_reset_ts"):
                            quota_text += f"    {self._format_countdown(fiveh_reset)}\n"
                    quota_text += "\n[bold]Claude & GPT Group Limits[/bold]\n"
                    if c := result.get("claude_gpt"):
                        quota_text += f"  Weekly: {self._pct(c.get('weekly_pct')):.1f}%\n"
                        if weekly_reset := c.get("weekly_reset_ts"):
                            quota_text += f"    {self._format_countdown(weekly_reset)}\n"
                        quota_text += f"  Five-Hour: {self._pct(c.get('fiveh_pct')):.1f}%\n"
                        if fiveh_reset := c.get("fiveh_reset_ts"):
                            quota_text += f"    {self._format_countdown(fiveh_reset)}\n"
                else:
                    quota_text += "[dim](no quota data available)[/dim]"
            except Exception as e:
                quota_text = f"[dim]Error loading quota: {e}[/dim]"
            self.app.call_from_thread(self._apply_quota, quota_text)

        threading.Thread(target=work, daemon=True).start()

    def _apply_quota(self, quota_text: str) -> None:
        """Apply quota text to the widget (runs on main thread)."""
        self.quota_data = quota_text
        try:
            self.query_one("#quota-content", Static).update(quota_text)
        except Exception:
            pass

    @staticmethod
    def _pct(v) -> float:
        """Percentage of quota remaining. A missing bucket (None) means that
        limit hasn't been tracked/hit yet → full quota (100%). A real 0.0 is a
        genuinely-exhausted limit and must stay 0%."""
        return 100.0 if v is None else v

    def _format_countdown(self, timestamp: int) -> str:
        """Format countdown time until reset."""
        if not timestamp:
            return ""
        secs = max(0, timestamp - int(time.time()))
        d, rem = divmod(secs, 86400)
        h, rem2 = divmod(rem, 3600)
        m = rem2 // 60
        if d > 0:
            return f"[dim]Refreshes in {d}d {h}h[/dim]"
        if h > 0:
            return f"[dim]Refreshes in {h}h {m}m[/dim]"
        return f"[dim]Refreshes in {m}m[/dim]"


class ContentPanel(Static):
    """Context usage panel — mirrors /context command output."""

    def compose(self) -> ComposeResult:
        yield Button("↻ Reload", id="btn-reload-context")
        with VerticalScroll(id="context-scroll"):
            yield Static("[dim]Loading context stats...[/dim]", id="context-content")

    def on_mount(self) -> None:
        self._load_async()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-reload-context":
            try:
                self.query_one("#context-content", Static).update("[dim]Loading...[/dim]")
            except Exception:
                pass
            self._load_async()

    def _load_async(self) -> None:
        def work():
            result = get_context_stats()
            self.app.call_from_thread(self._apply, result)
        threading.Thread(target=work, daemon=True).start()

    def _apply(self, data: dict) -> None:
        text = f"[red]{data['error']}[/red]" if "error" in data else self._build_text(data)
        try:
            self.query_one("#context-content", Static).update(text)
        except Exception:
            pass

    @staticmethod
    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.0f}K"
        return str(n)

    def _build_text(self, d: dict) -> str:
        limit = d["context_limit"]
        total = d["total_tokens"]
        pct = d["pct_used"]
        user, model_t, tool = d["user_tokens"], d["model_tokens"], d["tool_tokens"]
        free = max(0, limit - total)

        # Grid: 40 cols × 5 rows = 200 cells
        COLS, ROWS = 30, 5
        cells = COLS * ROWS
        filled = min(cells, int(pct / 100 * cells))
        rows = []
        for r in range(ROWS):
            row = []
            for c in range(COLS):
                idx = r * COLS + c
                row.append("[cyan]■[/cyan]" if idx < filled else "[dim]□[/dim]")
            rows.append(" ".join(row))
        grid = "\n".join(rows)

        conv_id = d.get("conversation_id", "")
        short_id = conv_id[:8] + "…" if len(conv_id) > 8 else conv_id
        source = "[green]● live[/green]" if d.get("live") else "[dim]○ last session[/dim]"

        f = self._fmt
        p = lambda n: f"{n / max(limit, 1) * 100:.1f}%"

        return (
            f"[bold cyan]Context Usage[/bold cyan]  {source}\n\n"
            f"[bold]{d['model']}[/bold] · {f(total)}/{f(limit)} tokens ({pct:.1f}%)\n\n"
            f"{grid}\n\n"
            f"[dim]Conversation:[/dim] {short_id}\n\n"
            f"[cyan]●[/cyan] User messages:   {f(user):>6} tokens ({p(user)})\n"
            f"[green]●[/green] Agent responses: {f(model_t):>6} tokens ({p(model_t)})\n"
            f"[yellow]●[/yellow] Tool calls:      {f(tool):>6} tokens ({p(tool)})\n"
            f"[dim]□  Free space:     {f(free):>6} ({p(free)})[/dim]"
        )


class ProfileStatsPanel(Vertical):
    """Quota stats panel — hand-drawn 2-level header, auto-sizing columns."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cache: dict = {}
        self._timer = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="ps-header-row"):
            yield Label("[bold cyan]Profile Stats[/bold cyan]")
            yield Static(id="ps-spacer")
            yield Label("auto-refresh every")
            yield Input(value="30", id="ps-interval")
            yield Label("min")
            yield Button("↻", id="btn-ps-refresh")
        yield Static("", id="ps-table-header")
        with VerticalScroll(id="ps-scroll"):
            yield Static("", id="ps-content")

    def on_mount(self) -> None:
        self._load_cache_and_refresh()
        self._setup_timer()

    def on_resize(self) -> None:
        self._render_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ps-refresh":
            self._refresh()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "ps-interval":
            self._setup_timer()

    def _get_profiles(self) -> list[str]:
        current = self.app.profile_email
        active = current if current and current != "(not signed in)" else None
        profiles = ([active] if active else []) + [e for e in self._cache if e != active]
        return profiles

    def _col_width(self) -> int:
        try:
            w = self.query_one("#ps-scroll").content_size.width
        except Exception:
            w = 88
        PROF_W = 20
        return max(15, (w - 6 - PROF_W) // 4), PROF_W

    @staticmethod
    def _center(s: str, w: int) -> str:
        if len(s) >= w:
            return s[:w]
        left = (w - len(s)) // 2
        return " " * left + s + " " * (w - len(s) - left)

    def _cell(self, pct: float | None, reset_ts: int | None, col_w: int) -> str:
        def pad(s: str) -> str:
            return s[:col_w] if len(s) >= col_w else s + " " * (col_w - len(s))
        if pct is None:
            return pad(" —")
        if reset_ts:
            secs = max(0, reset_ts - int(time.time()))
            d, rem = divmod(secs, 86400)
            h, m = rem // 3600, (rem % 3600) // 60
            countdown = f"{d}d {h}h" if d > 0 else f"{h}h {m}m"
            return pad(f" {pct:.1f}% ({countdown})")
        return pad(f" {pct:.1f}%")

    @staticmethod
    def _trunc(email: str, w: int) -> str:
        user = email.split("@")[0]
        if len(user) <= w:
            return user.ljust(w)
        return user[:w - 1] + "…"

    def _render_table(self) -> None:
        col_w, prof_w = self._col_width()
        span = col_w * 2 + 1
        s = "─"

        def hline(l, i0, i1, i2, i3, r) -> str:
            return l + s*prof_w + i0 + s*col_w + i1 + s*col_w + i2 + s*col_w + i3 + s*col_w + r

        def border(t): return f"[dim]{t}[/dim]"
        def sep(): return border("│")

        profiles = self._get_profiles()
        lines = [
            border(hline("┌", "┬", "─", "┬", "─", "┐")),
            sep() + f"[dim]{' '*prof_w}[/dim]" + sep() + f"[bold cyan]{self._center('Gemini Group', span)}[/bold cyan]" + sep() + f"[bold cyan]{self._center('Claude & GPT Group', span)}[/bold cyan]" + sep(),
            border(hline("├", "┼", "┬", "┼", "┬", "┤")),
            sep() + f"[dim]{self._center('Profile', prof_w)}[/dim]" + sep() + f"[dim]{self._center('Weekly', col_w)}[/dim]" + sep() + f"[dim]{self._center('5Hr Limit', col_w)}[/dim]" + sep() + f"[dim]{self._center('Weekly', col_w)}[/dim]" + sep() + f"[dim]{self._center('5Hr Limit', col_w)}[/dim]" + sep(),
            border(hline("├", "┼", "┼", "┼", "┼", "┤")),
        ]

        if not profiles:
            lines.append(sep() + f"[dim]{self._center('(not signed in)', prof_w + span*2 + 2)}[/dim]" + sep())
        else:
            current = self.app.profile_email
            for i, p in enumerate(profiles):
                e = self._cache.get(p, {})
                is_active = (p == current)
                raw = ("*" if is_active else " ") + self._trunc(p, prof_w - 1)
                prof = raw[:prof_w].ljust(prof_w)
                prof_color = "green" if is_active else "dim"
                data_color = "white" if is_active else "dim"
                gw = self._cell(e.get("gemini_weekly_pct"),  e.get("gemini_weekly_reset_ts"),  col_w)
                g5 = self._cell(e.get("gemini_fiveh_pct"),   e.get("gemini_fiveh_reset_ts"),   col_w)
                cw = self._cell(e.get("claude_weekly_pct"),  e.get("claude_weekly_reset_ts"),  col_w)
                c5 = self._cell(e.get("claude_fiveh_pct"),   e.get("claude_fiveh_reset_ts"),   col_w)
                lines.append(sep() + f"[{prof_color}]{prof}[/{prof_color}]" + sep() + f"[{data_color}]{gw}[/{data_color}]" + sep() + f"[{data_color}]{g5}[/{data_color}]" + sep() + f"[{data_color}]{cw}[/{data_color}]" + sep() + f"[{data_color}]{c5}[/{data_color}]" + sep())
                if i < len(profiles) - 1:
                    lines.append(border(hline("├", "┼", "┼", "┼", "┼", "┤")))

        lines.append(border(hline("└", "┴", "┴", "┴", "┴", "┘")))

        try:
            self.query_one("#ps-table-header", Static).update("\n".join(lines[:5]))
        except Exception:
            pass
        try:
            self.query_one("#ps-content", Static).update("\n".join(lines[5:]))
        except Exception:
            pass


    def _load_cache_and_refresh(self) -> None:
        def work():
            cache = {}
            try:
                if STATS_FILE.exists():
                    with open(STATS_FILE, "r", encoding="utf-8") as f:
                        cache = json.load(f)
            except Exception:
                pass
            self.app.call_from_thread(self._apply_cache, cache)
        threading.Thread(target=work, daemon=True).start()

    def _apply_cache(self, cache: dict) -> None:
        self._cache = cache
        self._render_table()
        self._refresh_live_quota()

    def _refresh_live_quota(self) -> None:
        current_email = self.app.profile_email
        if not current_email or current_email == "(not signed in)":
            return
        def work():
            try:
                result = get_quota_summary()
                self.app.call_from_thread(self._apply_live_quota, current_email, result)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _apply_live_quota(self, email: str, result: dict | None) -> None:
        if not result:
            return
        g = result.get("gemini") or {}
        c = result.get("claude_gpt") or {}
        self._cache[email] = {
            "last_updated": int(time.time()),
            "gemini_weekly_pct":      100.0 if g.get("weekly_pct") is None else g.get("weekly_pct"),
            "gemini_weekly_reset_ts": g.get("weekly_reset_ts"),
            "gemini_fiveh_pct":       g.get("fiveh_pct"),
            "gemini_fiveh_reset_ts":  g.get("fiveh_reset_ts"),
            "claude_weekly_pct":      100.0 if c.get("weekly_pct") is None else c.get("weekly_pct"),
            "claude_weekly_reset_ts": c.get("weekly_reset_ts"),
            "claude_fiveh_pct":       c.get("fiveh_pct"),
            "claude_fiveh_reset_ts":  c.get("fiveh_reset_ts"),
        }
        def save():
            try:
                STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(STATS_FILE, "w", encoding="utf-8") as f:
                    json.dump(self._cache, f, indent=2)
            except Exception:
                pass
        threading.Thread(target=save, daemon=True).start()
        self._render_table()

    def _refresh(self) -> None:
        self._load_cache_and_refresh()

    def _setup_timer(self) -> None:
        if self._timer:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None
        try:
            minutes = float(self.query_one("#ps-interval", Input).value.strip())
            if minutes > 0:
                self._timer = self.set_interval(minutes * 60.0, self._refresh)
        except Exception:
            pass


class ChatHistoryPanel(Vertical):
    """Chat History panel — list and delete past agy conversations."""

    def compose(self) -> ComposeResult:
        with ContentSwitcher(id="chats-switcher", initial="chats-list-view"):
            with Vertical(id="chats-list-view"):
                yield OptionList(id="chats-list")
                yield Static(id="chat-detail")
                with Horizontal(id="chats-actions"):
                    yield Button("↻ Reload", id="btn-reload-chats")
                    yield Button("🗑 Delete", id="btn-delete-chat", variant="error", disabled=True)
                    yield Button("🗑 Delete All", id="btn-delete-all", variant="error")

            with Vertical(id="chats-detail-view"):
                with VerticalScroll(id="chats-scroll-area"):
                    yield Static("", id="chats-full-text", markup=False)
                with Horizontal(id="chats-detail-actions"):
                    yield Button("🗑 Delete", id="btn-detail-delete-chat", variant="error")
                    yield Button("🗑 Delete All", id="btn-detail-delete-all", variant="error")
                    yield Button("←", id="btn-prev-chat")
                    yield Button("→", id="btn-next-chat")
                    yield Button("🏠 Home", id="btn-home")

    def on_mount(self) -> None:
        self._conversations: list = []
        self._selected_idx: int | None = None
        self._last_click_time: float = 0.0
        self._last_click_idx: int | None = None
        self._load_async()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        try:
            if event.button.id == "btn-reload-chats":
                self._load_async()
            elif event.button.id in ("btn-delete-chat", "btn-detail-delete-chat"):
                self._delete_selected()
                self._go_home()
            elif event.button.id in ("btn-delete-all", "btn-detail-delete-all"):
                self._delete_all()
                self._go_home()
            elif event.button.id == "btn-prev-chat":
                self._select_prev_chat()
            elif event.button.id == "btn-next-chat":
                self._select_next_chat()
            elif event.button.id == "btn-home":
                self._go_home()
            event.stop()
        except Exception:
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        try:
            idx = event.option_index
            if idx is None or not self._conversations or idx < 0 or idx >= len(self._conversations):
                return

            now = time.time()
            last_time = getattr(self, "_last_click_time", 0.0)
            last_idx = getattr(self, "_last_click_idx", None)

            if now - last_time <= 0.35 and last_idx == idx:
                # Double click
                self._selected_idx = idx
                self._show_conversation(idx)
                try:
                    switcher = self.query_one("#chats-switcher", ContentSwitcher)
                    switcher.current = "chats-detail-view"
                except Exception:
                    pass
                self._last_click_time = 0.0
                self._last_click_idx = None
            else:
                # Single click
                self._last_click_time = now
                self._last_click_idx = idx
                self._selected_idx = idx
                self._update_detail(idx)
                try:
                    self.query_one("#btn-delete-chat", Button).disabled = False
                except Exception:
                    pass
        except Exception:
            pass

    def _show_conversation(self, idx: int | None) -> None:
        try:
            if idx is None or idx < 0 or not self._conversations or idx >= len(self._conversations):
                try:
                    self.query_one("#chats-full-text", Static).update("")
                except Exception:
                    pass
                return

            c = self._conversations[idx]
            conv_id = c["id"]

            def work():
                try:
                    turns = read_conversation(conv_id)
                    formatted_turns = []
                    for t in turns:
                        who = "USER" if t["role"] == "user" else "MODEL"
                        formatted_turns.append(f"### {who}\n{t['text']}")
                    text = "\n\n".join(formatted_turns) if formatted_turns else "(empty conversation)"
                except Exception as e:
                    text = f"Error reading conversation {conv_id}: {e}"
                self.app.call_from_thread(self._apply_conversation_text, idx, text)

            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def _apply_conversation_text(self, idx: int, text: str) -> None:
        try:
            if self._selected_idx != idx:
                return
            try:
                self.query_one("#chats-full-text", Static).update(text)
                self.query_one("#chats-scroll-area", VerticalScroll).scroll_to(y=0, animate=False)
            except Exception:
                pass

            try:
                prev_btn = self.query_one("#btn-prev-chat", Button)
                next_btn = self.query_one("#btn-next-chat", Button)
                prev_btn.disabled = (idx == 0)
                next_btn.disabled = (idx >= len(self._conversations) - 1)
            except Exception:
                pass
        except Exception:
            pass

    def _select_prev_chat(self) -> None:
        try:
            if not self._conversations:
                return
            if self._selected_idx is None:
                self._selected_idx = 0
            elif self._selected_idx > 0:
                self._selected_idx -= 1
            self._show_conversation(self._selected_idx)
            self._update_detail(self._selected_idx)
            try:
                ol = self.query_one("#chats-list", OptionList)
                ol.highlighted = self._selected_idx
            except Exception:
                pass
        except Exception:
            pass

    def _select_next_chat(self) -> None:
        try:
            if not self._conversations:
                return
            if self._selected_idx is None:
                self._selected_idx = 0
            elif self._selected_idx < len(self._conversations) - 1:
                self._selected_idx += 1
            self._show_conversation(self._selected_idx)
            self._update_detail(self._selected_idx)
            try:
                ol = self.query_one("#chats-list", OptionList)
                ol.highlighted = self._selected_idx
            except Exception:
                pass
        except Exception:
            pass

    def _go_home(self) -> None:
        try:
            switcher = self.query_one("#chats-switcher", ContentSwitcher)
            switcher.current = "chats-list-view"
        except Exception:
            pass

    def _load_async(self) -> None:
        try:
            ol = self.query_one("#chats-list", OptionList)
            ol.clear_options()
            ol.add_option(Option("Loading...", disabled=True))
            self.query_one("#btn-delete-chat", Button).disabled = True
            self.query_one("#chats-full-text", Static).update("")
            self.query_one("#chat-detail", Static).update("")
        except Exception:
            pass
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            convs = _list_conversations(limit=50)
            self.app.call_from_thread(self._apply, convs)
        except Exception:
            pass

    def _apply(self, convs: list) -> None:
        try:
            self._conversations = convs
            self._selected_idx = None
            try:
                ol = self.query_one("#chats-list", OptionList)
                ol.clear_options()
                if not convs:
                    ol.add_option(Option("(no conversations)", disabled=True))
                    return
                for c in convs:
                    date = datetime.datetime.fromtimestamp(c["modified"]).strftime("%m-%d %H:%M")
                    title = c["title"][:55].replace("\n", " ").replace("\x1a", "").replace("\x14", "")
                    turns = c["user_turns"]
                    ol.add_option(Option(f"[{date}] ({turns}t) {title}"))
            except Exception:
                pass
        except Exception:
            pass

    def _update_detail(self, idx: int | None) -> None:
        try:
            if idx is None or idx < 0 or not self._conversations or idx >= len(self._conversations):
                try:
                    self.query_one("#chat-detail", Static).update("")
                except Exception:
                    pass
                return
            c = self._conversations[idx]
            conv_id = c["id"]
            kb = c["db_bytes"] // 1024
            date = datetime.datetime.fromtimestamp(c["modified"]).strftime("%Y-%m-%d %H:%M")
            detail = (
                f"[dim]ID:[/dim]    {conv_id}\n"
                f"[dim]Date:[/dim]  {date}  "
                f"[dim]Turns:[/dim] {c['user_turns']}  [dim]Size:[/dim] {kb} KB\n"
                f"[dim]Tokens:[/dim] Loading..."
            )
            try:
                self.query_one("#chat-detail", Static).update(detail)
            except Exception:
                pass

            def work():
                try:
                    stats = get_context_stats(conv_id)
                except Exception as e:
                    stats = {"error": str(e)}
                self.app.call_from_thread(self._apply_token_stats, idx, stats)

            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def _apply_token_stats(self, idx: int, stats: dict) -> None:
        try:
            if self._selected_idx != idx:
                return
            if idx < 0 or not self._conversations or idx >= len(self._conversations):
                return
            c = self._conversations[idx]
            conv_id = c["id"]
            kb = c["db_bytes"] // 1024
            date = datetime.datetime.fromtimestamp(c["modified"]).strftime("%Y-%m-%d %H:%M")

            if "error" in stats:
                token_str = f"[red]Error: {stats['error']}[/red]"
            else:
                total = stats.get("total_tokens", 0)
                user = stats.get("user_tokens", 0)
                model = stats.get("model_tokens", 0)
                tool = stats.get("tool_tokens", 0)
                token_str = f"{total} (User: {user}, Model: {model}, Tool: {tool})"

            detail = (
                f"[dim]ID:[/dim]    {conv_id}\n"
                f"[dim]Date:[/dim]  {date}  "
                f"[dim]Turns:[/dim] {c['user_turns']}  [dim]Size:[/dim] {kb} KB\n"
                f"[dim]Tokens:[/dim] {token_str}"
            )
            try:
                self.query_one("#chat-detail", Static).update(detail)
            except Exception:
                pass
        except Exception:
            pass

    def _delete_selected(self) -> None:
        try:
            if self._selected_idx is None or not self._conversations or self._selected_idx >= len(self._conversations):
                return
            c = self._conversations[self._selected_idx]
            path = os.path.join(CONV_DIR, c["id"] + ".db")
            
            def work():
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
                self.app.call_from_thread(self._load_async)
                
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def _delete_all(self) -> None:
        try:
            convs = list(self._conversations)
            
            def work():
                for c in convs:
                    path = os.path.join(CONV_DIR, c["id"] + ".db")
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except Exception:
                        pass
                self.app.call_from_thread(self._load_async)
                
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass


class WorkspaceItem(Horizontal):
    """Single trusted-workspace row with a delete button."""

    def __init__(self, path: str, index: int) -> None:
        super().__init__(classes="ws-item")
        self._path = path
        self._index = index

    def compose(self) -> ComposeResult:
        yield Static(self._path, classes="ws-path")
        yield Button("✕", id=f"del-ws-{self._index}", classes="ws-del-btn")


class CredentialPanel(Static):
    """Credential view: log in / log out, workspace manager, agy status."""

    _SETTINGS = Path(os.path.expanduser("~")) / ".gemini" / "antigravity-cli" / "settings.json"

    def _read_settings(self) -> dict:
        if self._SETTINGS.exists():
            with open(self._SETTINGS, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _write_settings(self, data: dict) -> None:
        self._SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        with open(self._SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def compose(self) -> ComposeResult:
        yield Static("[bold cyan]Credential[/bold cyan]", id="cred-header")
        with Horizontal(id="cred-buttons"):
            yield Button("Log In", id="btn-login", variant="primary")
            yield Button("Log Out", id="btn-logout", variant="default")
            yield Button("Console", id="btn-console", variant="success")
            yield Button("Shut Down", id="btn-shutdown", variant="error")
        yield Static("", id="cred-info")
        yield Static("", id="oauth-status")
        yield Static("[bold]Trusted Workspaces[/bold]", id="ws-header")
        with VerticalScroll(id="ws-list"):
            pass
        with Horizontal(id="ws-add-row"):
            yield Input(placeholder="Add workspace path…", id="ws-input")
            yield Button("+", id="btn-add-ws", variant="success")
        yield Static("[dim]○ agy status unknown[/dim]", id="agy-status")

    _oauth_running = False

    def on_mount(self) -> None:
        self.refresh_info()
        self._reload_workspaces()
        self.set_interval(2, self._poll_status)

    def _run_oauth_thread(self) -> None:
        """Run the clipboard-bridge login in-process; report status to the TUI."""
        from oauth_login import run_login

        def status(msg: str) -> None:
            self.app.call_from_thread(self._set_oauth_status, msg)

        try:
            run_login(status)
        except Exception as e:
            self.app.call_from_thread(self._set_oauth_status, f"[red]Error: {e}[/red]")
        finally:
            self.app.call_from_thread(self._oauth_finished)

    def _set_oauth_status(self, msg: str) -> None:
        try:
            self.query_one("#oauth-status", Static).update(f"  {msg}")
        except Exception:
            pass

    def _oauth_finished(self) -> None:
        self._oauth_running = False
        try:
            self.query_one("#btn-login", Button).disabled = False
        except Exception:
            pass
        self.refresh_info()
        try:
            self.app._update_profile()
            self.app._load_models_async()
        except Exception:
            pass

    def _reload_workspaces(self) -> None:
        data = self._read_settings()
        workspaces = data.get("trustedWorkspaces", [])
        ws_list = self.query_one("#ws-list", VerticalScroll)
        ws_list.remove_children()
        for i, path in enumerate(workspaces):
            ws_list.mount(WorkspaceItem(path, i))

    def _poll_status(self) -> None:
        threading.Thread(target=self._check_status, daemon=True).start()

    def _check_status(self) -> None:
        status = _get_agy_status()
        self.app.call_from_thread(self._apply_status, status)

    def _apply_status(self, status: str) -> None:
        try:
            self.query_one("#agy-status", Static).update(status)
        except Exception:
            pass

    def refresh_info(self) -> None:
        version = get_agy_version()
        model = self.app._get_selected_model() or "(none)"
        self.query_one("#cred-info", Static).update(
            f"  [dim]Version:    [/dim]{version}\n"
            f"  [dim]Model:      [/dim]{model}"
        )
        threading.Thread(target=self._check_status, daemon=True).start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-login":
            # Run the clipboard-bridge OAuth login in-process (background thread),
            # streaming status to #oauth-status and refreshing on completion.
            if self._oauth_running:
                return
            self._oauth_running = True
            event.button.disabled = True
            self._set_oauth_status("Starting …")
            threading.Thread(target=self._run_oauth_thread, daemon=True).start()
        elif bid == "btn-console":
            self.app._launch_agy_console()
        elif bid == "btn-logout":
            self.app._do_logout()
            self.refresh_info()
        elif bid == "btn-shutdown":
            self.app._do_shutdown()
        elif bid == "btn-add-ws":
            inp = self.query_one("#ws-input", Input)
            path = inp.value.strip()
            if path:
                data = self._read_settings()
                ws = data.get("trustedWorkspaces", [])
                if path not in ws:
                    ws.append(path)
                    data["trustedWorkspaces"] = ws
                    self._write_settings(data)
                    self._reload_workspaces()
                inp.value = ""
        elif bid and bid.startswith("del-ws-"):
            idx = int(bid.split("-")[-1])
            data = self._read_settings()
            ws = data.get("trustedWorkspaces", [])
            if 0 <= idx < len(ws):
                ws.pop(idx)
                data["trustedWorkspaces"] = ws
                self._write_settings(data)
                self._reload_workspaces()
        event.stop()


class AGYMCPApp(App):
    """AGY MCP Control Panel - Textual version."""

    CSS = """
    Screen {
        background: $surface;
        height: 50vh;
    }

    #main-container {
        layout: horizontal;
        height: 1fr;
    }

    #sidebar {
        width: 18;
        height: 100%;
        border-right: solid $accent;
        background: $boost;
    }

    #content-area {
        width: 1fr;
        height: 100%;
        layout: vertical;
    }

    #profile-section {
        width: 100%;
        height: 4;
        border-bottom: solid $accent;
        padding: 1 1 0 1;
        background: $boost;
    }

    #cred-header {
        padding: 0 1 1 1;
    }

    #cred-buttons {
        height: auto;
        padding: 0 1 1 1;
    }

    #cred-buttons Button {
        width: auto;
        margin: 0 1 0 0;
    }

    #cred-info {
        padding: 0 1 1 1;
    }

    #ws-header {
        padding: 0 1 0 1;
    }

    #ws-list {
        height: auto;
        max-height: 6;
        padding: 0 1;
    }

    .ws-item {
        height: 1;
        width: 100%;
    }

    .ws-path {
        width: 1fr;
    }

    .ws-del-btn {
        width: 3;
        min-width: 3;
        height: 1;
        min-height: 1;
        border: none;
        margin: 0;
    }

    #ws-add-row {
        height: 3;
        padding: 0 1;
        margin-top: 1;
    }

    #ws-input {
        width: 1fr;
    }

    #btn-add-ws {
        width: 5;
        margin: 0 0 0 1;
    }

    #agy-status {
        padding: 1 1 0 1;
    }

    #content-switcher {
        width: 100%;
        height: 1fr;
        padding: 1;
    }

    .panel-content {
        width: 100%;
        height: 100%;
    }

    ListView {
        width: 100%;
    }

    ListItem {
        padding: 0 1;
    }

    #models-header-row {
        height: auto;
        padding: 0 0 0 1;
        align: left middle;
    }

    #models-header {
        width: 1fr;
        padding: 0;
    }

    #btn-reload-models {
        width: auto;
        height: 1;
        min-height: 1;
        border: none;
        margin: 0 1 0 0;
    }

    #models-selected {
        padding: 0 1 1 1;
    }

    #models-optionlist {
        height: 1fr;
        border: solid $accent-darken-1;
    }

    #quota-scroll, #context-scroll {
        height: 1fr;
        border: solid $accent-darken-1;
        padding: 1;
    }

    #chats-switcher {
        width: 100%;
        height: 1fr;
    }

    #chats-list-view {
        width: 100%;
        height: 100%;
        layout: vertical;
    }

    #chats-detail-view {
        width: 100%;
        height: 100%;
        layout: vertical;
    }

    #chats-list {
        height: 1fr;
        border: solid $accent-darken-1;
    }

    #chats-scroll-area {
        height: 1fr;
        border: solid $accent-darken-1;
        padding: 1 2;
    }

    #chats-full-text {
        width: 100%;
        height: auto;
    }

    #chat-detail {
        height: auto;
        padding: 0 1;
    }

    #chats-actions, #chats-detail-actions {
        height: auto;
        padding: 0;
        margin-top: 1;
    }

    #chats-actions Button, #chats-detail-actions Button {
        width: auto;
        margin: 0 1 0 0;
        height: 1;
        min-height: 1;
        border: none;
    }

    OptionList > .option--option {
        padding: 0 1;
    }

    #ps-header-row {
        height: auto;
        padding: 0 1;
        align: left middle;
    }
    #ps-spacer {
        width: 1fr;
    }
    #ps-interval {
        height: 3;
        width: 10;
        margin: 0 1;
        padding: 0 1;
        border: solid $accent;
        background: $boost;
    }
    #btn-ps-refresh {
        width: auto;
        height: 1;
        min-height: 1;
        border: none;
    }
    #ps-table-header {
        height: auto;
        padding: 0 1;
    }
    #ps-scroll {
        height: 1fr;
        padding: 0 1;
    }
    #ps-content {
        width: auto;
    }
    """

    TITLE = "AGY MCP - Control Panel"
    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.models_data = []
        self.profile_email = "(not signed in)"

    def compose(self) -> ComposeResult:
        """Compose the main layout."""
        yield Header()

        with Horizontal(id="main-container"):
            # Left sidebar - Navigation
            with ListView(id="sidebar"):
                yield ListItem(Label("🏠 Home"), id="nav-credential")
                yield ListItem(Label("📊 Models"), id="nav-models")
                yield ListItem(Label("📈 Quota"), id="nav-quota")
                yield ListItem(Label("📝 Content"), id="nav-content")
                yield ListItem(Label("💬 Chat History"), id="nav-chats")
                yield ListItem(Label("👤 Profile Stats"), id="nav-profile-stats")

            # Right content area
            with Vertical(id="content-area"):
                # Profile section
                yield ProfileCard(id="profile-section")

                # Content switcher
                with ContentSwitcher(id="content-switcher", initial="credential-view"):
                    yield CredentialPanel(id="credential-view", classes="panel-content")
                    yield ModelsPanel(id="models-view", classes="panel-content")
                    yield QuotaPanel(id="quota-view", classes="panel-content")
                    yield ContentPanel(id="content-view", classes="panel-content")
                    yield ChatHistoryPanel(id="chat-history-view", classes="panel-content")
                    yield ProfileStatsPanel(id="profile-stats-view", classes="panel-content")

        yield Footer()

    def on_mount(self) -> None:
        """Initialize when app starts."""
        self._load_models_async()
        self._update_profile()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle sidebar navigation — always reload on entry."""
        switcher = self.query_one(ContentSwitcher)

        if event.item.id == "nav-credential":
            switcher.current = "credential-view"
        elif event.item.id == "nav-models":
            switcher.current = "models-view"
            self._load_models_async()
        elif event.item.id == "nav-quota":
            switcher.current = "quota-view"
            self.query_one(QuotaPanel)._reload_quota()
        elif event.item.id == "nav-content":
            switcher.current = "content-view"
            self.query_one(ContentPanel)._load_async()
        elif event.item.id == "nav-chats":
            switcher.current = "chat-history-view"
            self.query_one(ChatHistoryPanel)._load_async()
        elif event.item.id == "nav-profile-stats":
            switcher.current = "profile-stats-view"
            self.query_one(ProfileStatsPanel)._refresh()

    def _update_profile(self) -> None:
        """Update profile card with current email."""
        try:
            cred = win32cred.CredRead('gemini:antigravity', win32cred.CRED_TYPE_GENERIC)
            blob = json.loads(cred['CredentialBlob'].decode('utf-8'))
            access_token = blob['token']['access_token']

            req = urllib.request.Request(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {access_token}'}
            )
            with urllib.request.urlopen(req, timeout=5) as res:
                info = json.loads(res.read().decode('utf-8'))
                self.profile_email = info.get("email", "(not signed in)")
        except Exception:
            self.profile_email = "(not signed in)"

        profile_card = self.query_one(ProfileCard)
        profile_card.email = self.profile_email

    def _load_models_async(self) -> None:
        """Load models in background thread, then push results to main thread."""
        def work():
            try:
                models = list_models()
                selected = self._get_selected_model()
                self.call_from_thread(self._apply_models, models, selected)
            except Exception as e:
                self.call_from_thread(self._apply_models, [], None)

        panel = self.query_one(ModelsPanel)
        panel.loading = True
        threading.Thread(target=work, daemon=True).start()

    def _apply_models(self, models: list, selected: str | None) -> None:
        """Apply loaded model list to the panel (runs on main thread)."""
        self.models_data = models
        panel = self.query_one(ModelsPanel)
        panel.models = models
        panel.loading = False
        if selected:
            panel.selected_model = selected

    def _get_selected_model(self) -> str | None:
        """Read currently selected model from settings."""
        settings_path = Path(os.path.expanduser("~")) / ".gemini" / "antigravity-cli" / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("model")
            except Exception:
                pass
        return None

    def _set_selected_model(self, model_name: str) -> None:
        """Write selected model to settings."""
        settings_path = Path(os.path.expanduser("~")) / ".gemini" / "antigravity-cli" / "settings.json"
        try:
            data = {}
            if settings_path.exists():
                with open(settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["model"] = model_name
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            # Update UI
            panel = self.query_one(ModelsPanel)
            panel.selected_model = model_name
        except Exception:
            pass

    def _launch_agy_console(self) -> None:
        """Open agy.exe in its own console window for interactive command testing."""
        try:
            subprocess.Popen([str(AGY_BIN)], creationflags=0x00000010)
        except Exception:
            pass

    def _do_logout(self) -> None:
        """Trigger logout."""
        try:
            win32cred.CredDelete(TargetName='gemini:antigravity', Type=win32cred.CRED_TYPE_GENERIC)
            self.models_data = []
            self.profile_email = "(not signed in)"
            self._update_profile()
            panel = self.query_one(ModelsPanel)
            panel.models = []
        except Exception:
            pass

    def _do_shutdown(self) -> None:
        """Kill all agy/Antigravity processes."""
        for name in ("agy.exe", "Antigravity.exe"):
            subprocess.run(
                ["taskkill", "/F", "/IM", name, "/T"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000,
            )


if __name__ == "__main__":
    app = AGYMCPApp()
    app.run()
