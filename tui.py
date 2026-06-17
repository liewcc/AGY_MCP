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
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import Header, Footer, Static, ListView, ListItem, Label, Button, ContentSwitcher, OptionList
from textual.widgets.option_list import Option
from textual.reactive import reactive

from agy_core import list_models, get_quota_summary, get_context_stats

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

class ProfileCard(Static):
    """Displays login profile information."""

    email = reactive("(not signed in)")

    def render(self) -> str:
        return f"""[bold]Profile[/bold]
Current: {self.email}"""


class ModelsPanel(Static):
    """Models view content."""

    models = reactive([])
    loading = reactive(False)
    selected_model = reactive("(none)")

    def compose(self) -> ComposeResult:
        """Compose models panel."""
        yield Static("[bold cyan]Available Models[/bold cyan]", id="models-header")
        yield Static("[dim]Current: (none)[/dim]", id="models-selected")
        yield OptionList(id="models-optionlist")

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
        yield Static("""[bold cyan]Quota Information[/bold cyan]

[dim]Loading quota data...[/dim]

Features to implement:
  • Weekly/Five-Hour limits
  • Individual model quotas
  • Session usage tracking""", id="quota-content")
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
                        quota_text += f"  Weekly: {g.get('weekly_pct', 0):.1f}%\n"
                        if weekly_reset := g.get("weekly_reset_ts"):
                            quota_text += f"    {self._format_countdown(weekly_reset)}\n"
                        quota_text += f"  Five-Hour: {g.get('fiveh_pct', 0):.1f}%\n"
                        if fiveh_reset := g.get("fiveh_reset_ts"):
                            quota_text += f"    {self._format_countdown(fiveh_reset)}\n"
                    quota_text += "\n[bold]Claude & GPT Group Limits[/bold]\n"
                    if c := result.get("claude_gpt"):
                        quota_text += f"  Weekly: {c.get('weekly_pct', 0):.1f}%\n"
                        if weekly_reset := c.get("weekly_reset_ts"):
                            quota_text += f"    {self._format_countdown(weekly_reset)}\n"
                        quota_text += f"  Five-Hour: {c.get('fiveh_pct', 0):.1f}%\n"
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


class CredentialPanel(Static):
    """Credential view: log in / log out buttons and CLI info."""

    def compose(self) -> ComposeResult:
        yield Static("[bold cyan]Credential[/bold cyan]", id="cred-header")
        with Horizontal(id="cred-buttons"):
            yield Button("Log In", id="btn-login", variant="primary")
            yield Button("Log Out", id="btn-logout", variant="default")
        yield Static("", id="cred-info")

    def on_mount(self) -> None:
        self.refresh_info()

    def refresh_info(self) -> None:
        version = get_agy_version()
        model = self.app._get_selected_model() or "(none)"
        workspace = os.getcwd()
        self.query_one("#cred-info", Static).update(
            f"\n"
            f"  [dim]Version:    [/dim]{version}\n"
            f"  [dim]Model:      [/dim]{model}\n"
            f"  [dim]Workspace:  [/dim]{workspace}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-login":
            self.app._do_login()
        elif event.button.id == "btn-logout":
            self.app._do_logout()
            self.refresh_info()
        event.stop()


class AGYMCPApp(App):
    """AGY MCP Control Panel - Textual version."""

    CSS = """
    Screen {
        background: $surface;
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
        height: 5;
        border-bottom: solid $accent;
        padding: 1;
        background: $surface;
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
        padding: 0 1;
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

    #models-header {
        padding: 0 1;
    }

    #models-selected {
        padding: 0 1 1 1;
    }

    #models-optionlist {
        height: 1fr;
        border: solid $accent-darken-1;
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
                yield ListItem(Label("🔑 Credential"), id="nav-credential")
                yield ListItem(Label("📊 Models"), id="nav-models")
                yield ListItem(Label("📈 Quota"), id="nav-quota")
                yield ListItem(Label("📝 Content"), id="nav-content")

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

        yield Footer()

    def on_mount(self) -> None:
        """Initialize when app starts."""
        self._load_models_async()
        self._update_profile()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle sidebar navigation."""
        switcher = self.query_one(ContentSwitcher)

        if event.item.id == "nav-credential":
            switcher.current = "credential-view"
        elif event.item.id == "nav-models":
            switcher.current = "models-view"
        elif event.item.id == "nav-quota":
            switcher.current = "quota-view"
        elif event.item.id == "nav-content":
            switcher.current = "content-view"

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

    def _do_login(self) -> None:
        """Trigger login."""
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


if __name__ == "__main__":
    app = AGYMCPApp()
    app.run()
