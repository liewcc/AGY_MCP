"""AGY MCP — terminal control panel.

A small pytermgui skeleton. Frame only; real functionality (sending prompts to
`agy`, listing/reading conversations, real OAuth login) is wired in
incrementally on top of it.

Run it with `run.bat`, or directly:

    python tui.py
"""

from __future__ import annotations

import datetime
import json
import re
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

import pytermgui as ptg
import win32cred

from agy_models import list_models, get_quota_summary

# Where the Antigravity CLI lives. Mirrors AGY_BIN in agy_client.py so the
# panel reports the same path the server actually uses.
AGY_BIN = Path(
    os.environ.get("AGY_BIN")
    or Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
)
DEFAULT_MODEL = os.environ.get("AGY_DEFAULT_MODEL", "Gemini 3 Pro")

# Shown inside the account card. Wired to the real OAuth profile in a later step.
PROFILE_EMAIL = "(not signed in)"

# Reference to the profile label widget for real-time updates.
PROFILE_LABEL: ptg.Label | None = None

# Reference to the right-hand content panel container.
CONTENT_PANEL: ptg.Container | None = None

# Sidebar item widgets, keyed by view name ("Models" / "Quota").
SIDEBAR_ITEMS: dict[str, "FlatButtonContainer"] = {}

# The sidebar container (left column).
SIDEBAR_BOX: ptg.Container | None = None

# Which sidebar view is currently active.
ACTIVE_VIEW: str = "Models"

# Live model list (fetched from `agy models`), and whether a fetch is in flight.
# Never persisted — refreshed from agy each session.
MODELS_CACHE: list[str] = []
MODELS_LOADING: bool = False

# Live quota list (fetched from cloudcode-pa REST API), and whether a fetch is in flight.
# Never persisted — refreshed from agy each session.
QUOTA_CACHE: list[dict] = []
QUOTA_LOADING: bool = False

# gRPC-based group quota summary (Weekly / Five-Hour limits for Gemini and Claude/GPT).
QUOTA_SUMMARY_CACHE: dict | None = None
QUOTA_SUMMARY_LOADING: bool = False

# Current scroll offset (in rows) for the content panel.  Reset to 0 on view change.
CONTENT_SCROLL: int = 0

# Rendered height (rows) of the status panel C — measured at build time so the
# left rail B and the main panel D can be padded to the same total height.
STATUS_H: int = 7

# The active WindowManager instance.
ACTIVE_MANAGER: ptg.WindowManager | None = None

# One uniform grey for every frame / divider.
BORDER = "240"

# Uncaught exceptions (any thread) are appended here so crashes can be diagnosed
# even though the TUI runs on the alternate screen buffer.
CRASH_LOG = Path(__file__).with_name("tui_crash.log")


def _log_exc(where: str, exc: BaseException | None = None) -> None:
    """Append a traceback to CRASH_LOG."""
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n==== {datetime.datetime.now().isoformat()} [{where}] ====\n")
            if exc is not None:
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
            else:
                traceback.print_exc(file=f)
    except Exception:
        pass


def _install_crash_logging() -> None:
    """Route uncaught exceptions from every thread into CRASH_LOG."""
    sys.excepthook = lambda t, v, tb: _log_exc("main", v)
    threading.excepthook = lambda args: _log_exc(
        f"thread:{args.thread_name}", args.exc_value
    )


class FlatLabel(ptg.Label):
    """A label that avoids pytermgui's break_line color-corruption bug
    by breaking the plain text first and applying styles afterwards.
    """
    def get_lines(self) -> list[str]:
        lines = []
        limit = self.width - self.padding
        broken = ptg.break_line(
            self.value,
            limit=limit,
            non_first_limit=limit - self.non_first_padding,
        )
        for i, line in enumerate(broken):
            styled_line = self.styles.value(line)
            if i == 0:
                lines.append(self.padding * " " + styled_line)
                continue
            lines.append(self.padding * " " + self.non_first_padding * " " + styled_line)
        return lines or [""]


def check_email_now() -> str | None:
    """Read Windows Credential Manager and query Google's userinfo API.
    If the access token is expired (401), runs agy.exe models headless
    to refresh the token using its refresh token, then retries once.
    """
    try:
        cred = win32cred.CredRead('gemini:antigravity', win32cred.CRED_TYPE_GENERIC)
        blob = json.loads(cred['CredentialBlob'].decode('utf-8'))
        access_token = blob['token']['access_token']
        
        req = urllib.request.Request(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                info = json.loads(res.read().decode('utf-8'))
                return info.get("email")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                # Token expired, run agy.exe models headless to refresh it
                try:
                    subprocess.run(
                        [str(AGY_BIN), "models"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=0x08000000,
                        timeout=10
                    )
                    # Retry reading the refreshed credential
                    cred = win32cred.CredRead('gemini:antigravity', win32cred.CRED_TYPE_GENERIC)
                    blob = json.loads(cred['CredentialBlob'].decode('utf-8'))
                    access_token = blob['token']['access_token']
                    req = urllib.request.Request(
                        'https://www.googleapis.com/oauth2/v3/userinfo',
                        headers={'Authorization': f'Bearer {access_token}'}
                    )
                    with urllib.request.urlopen(req, timeout=5) as res:
                        info = json.loads(res.read().decode('utf-8'))
                        return info.get("email")
                except Exception:
                    pass
            return None
    except Exception:
        return None


def get_selected_model() -> str | None:
    """Read the currently selected model from settings.json."""
    settings_path = Path(os.path.expanduser("~")) / ".gemini" / "antigravity-cli" / "settings.json"
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("model")
        except Exception:
            pass
    return None


def set_selected_model(model_name: str) -> bool:
    """Write the selected model to settings.json."""
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
        return True
    except Exception:
        return False


# Model selection code removed in favor of Status panel.


def on_login_click(button: ptg.Widget) -> None:
    """Trigger the interactive OAuth sign-in flow and launch the native interactive CLI
    by launching agy.exe in a new Command Prompt window.
    """
    try:
        # Spawn the native interactive shell of agy.exe in a new console window.
        # This will trigger the OAuth flow if not logged in, and then remain open
        # as a chat session.
        subprocess.Popen([str(AGY_BIN)], creationflags=0x00000010)
    except Exception:
        pass


def on_logout_click(button: ptg.Widget) -> None:
    """Log out by deleting the generic credential from Windows Credential Manager.
    Immediately updates the UI.
    """
    try:
        win32cred.CredDelete(TargetName='gemini:antigravity', Type=win32cred.CRED_TYPE_GENERIC)
    except Exception:
        pass
        
    global MODELS_CACHE, QUOTA_CACHE, QUOTA_SUMMARY_CACHE
    MODELS_CACHE = []
    QUOTA_CACHE = []
    QUOTA_SUMMARY_CACHE = None

    global PROFILE_LABEL
    if PROFILE_LABEL:
        PROFILE_LABEL.value = "[dim]current log in profile:  [247](not signed in)"
        if ACTIVE_VIEW == "Models":
            update_content_ui("Models")
        elif ACTIVE_VIEW == "Quota":
            update_content_ui("Quota")
        if ACTIVE_MANAGER:
            try:
                ACTIVE_MANAGER.compositor.redraw()
            except Exception:
                pass


def _update_profile_loop(manager: ptg.WindowManager) -> None:
    """Background loop: poll login status and the selected model so the UI
    reflects external changes (e.g. running `/model` in a separate terminal)."""
    last_email = None
    last_model = None

    while True:
        try:
            email = check_email_now()
        except Exception:
            email = None

        display_email = email if email else "(not signed in)"
        selected_model = get_selected_model()
        dirty = False

        if display_email != last_email:
            last_email = display_email
            global PROFILE_LABEL, MODELS_CACHE, QUOTA_CACHE, QUOTA_SUMMARY_CACHE
            if PROFILE_LABEL:
                PROFILE_LABEL.value = f"[dim]current log in profile:  [247]{display_email}"
            MODELS_CACHE = []
            QUOTA_CACHE = []
            QUOTA_SUMMARY_CACHE = None
            if ACTIVE_VIEW == "Models":
                _fetch_models_async()
            elif ACTIVE_VIEW == "Quota":
                _fetch_quota_async()
                _fetch_quota_summary_async()
            dirty = True

        # External model switch → move the selection dot in the Models view.
        if selected_model != last_model:
            last_model = selected_model
            if ACTIVE_VIEW == "Models" and MODELS_CACHE:
                update_content_ui("Models")
            dirty = True

        if dirty and manager:
            try:
                manager.compositor.redraw()
            except Exception:
                pass
        time.sleep(3)


class _Frame(ptg.Container):
    """A bordered box. Subclassed so pytermgui's Splitter doesn't apply its
    name-based ``+1`` row fudge (which only triggers for the exact type
    "Container") when this box is used as a direct Splitter child."""


def _framed(*widgets) -> ptg.Container:
    """A single-line box with a uniform grey border."""
    box = _Frame(*widgets, box="SINGLE")
    box.styles.border = BORDER
    box.styles.corner = BORDER
    return box


def _split(*widgets) -> ptg.Splitter:
    """A horizontal splitter whose divider matches the grey frames."""
    sp = ptg.Splitter(*widgets)
    sp.styles.separator = BORDER
    sp.styles.fill = lambda depth, item: item  # prevent pytermgui from parsing/corrupting ANSI codes
    # pytermgui's Splitter.keys omits scroll bindings, yet it inherits
    # Container.handle_key which *unconditionally* reads keys["scroll_down"/"up"]
    # at the top of every keypress → KeyError (crash) whenever a key is routed to
    # the splitter (e.g. a mouse-wheel scroll over a selected model row). Give it
    # empty scroll sets so the lookup succeeds and the branch is simply skipped.
    sp.keys = {**sp.keys, "scroll_down": set(), "scroll_up": set()}
    return sp


def _dot(ok: bool) -> str:
    """Green dot when healthy, red dot when not."""
    return "[120]●[/]" if ok else "[210]●[/]"





class FlatButtonContainer(ptg.Container):
    """A container-based button with three visual states:
    - Normal (rest): grey text, black background (no color fill)
    - Hover / keyboard focus: grey background, black text for the whole box
    - Clicked (moment of click): yellow background, black text for the whole box
    """
    def __init__(self, label: str, onclick, **attrs):
        self._is_pressed = False
        self._is_hovered = False
        self.active = False
        self.label_widget = FlatLabel(label)
        self.label_widget.parent_align = ptg.HorizontalAlignment.CENTER
        
        # Default box to SINGLE if not specified in attrs
        attrs.setdefault("box", "SINGLE")
        
        super().__init__(self.label_widget, **attrs)
        self.onclick = onclick
    @property
    def is_selectable(self) -> bool:
        return True

    @property
    def selectables(self) -> list[tuple[ptg.Widget, int]]:
        return [(self, 0)]

    @property
    def selectables_length(self) -> int:
        return 1

    def select(self, index: int | None = None) -> None:
        """Override Container.select to prevent infinite recursion."""
        ptg.Widget.select(self, index)

    def set_state(self, state: str):
        if state == "normal":
            self.styles.border = BORDER
            self.styles.corner = BORDER
            self.styles.fill = ""
            self.label_widget.styles.value = "247"
        elif state == "hover":
            self.styles.border = "240 @247"
            self.styles.corner = "240 @247"
            self.styles.fill = "@247"
            self.label_widget.styles.value = "0 @247"
        elif state == "clicked":
            self.styles.border = "0 @220"
            self.styles.corner = "0 @220"
            self.styles.fill = "@220"
            self.label_widget.styles.value = "0 @220"

    def handle_mouse(self, event: ptg.MouseEvent) -> bool:
        if event.action == ptg.MouseAction.LEFT_CLICK:
            self._is_pressed = True
            if self.onclick is not None:
                self.onclick(self)
            return True
        elif event.action == ptg.MouseAction.RELEASE:
            self._is_pressed = False
        
        # Do not delegate to the label; check hover/containment directly.
        if event.action in (ptg.MouseAction.HOVER, ptg.MouseAction.RELEASE):
            self._is_hovered = self.contains(event.position)
            
        return False

    def handle_key(self, key: str) -> bool:
        if key in (ptg.keys.RETURN, ptg.keys.CARRIAGE_RETURN) and self.onclick is not None:
            self.onclick(self)
            return True
        return ptg.Widget.handle_key(self, key)

    def get_lines(self) -> list[str]:
        if self._is_pressed:
            self.set_state("clicked")
        elif self.active or self._is_hovered or self.selected_index is not None:
            self.set_state("hover")
        else:
            self.set_state("normal")
            
        return super().get_lines()


def _flat_button(label: str, onclick) -> FlatButtonContainer:
    """Flat button: a grey rectangular frame with the title inside.
    Normal: grey text, black background.
    Hover: grey background, black text.
    Clicked: yellow background, black text."""
    return FlatButtonContainer(label, onclick)


class _Column(ptg.Container):
    """A borderless column for use inside the main Splitter.

    Subclassing matters for two reasons:

    1. pytermgui's Splitter adds a +1 row to the stored position of any child
       whose ``type(...).__name__ == "Container"`` (a fudge meant for *bordered*
       containers). For our EMPTY-box columns that +1 makes hover/click hit the
       neighbouring row. A subclass dodges that name check, so a widget's stored
       position matches where it is actually drawn.

    2. The Compositor draws on its own thread, while click/background handlers
       call ``set_widgets`` on the main/worker threads. ``set_widgets`` does
       ``self._widgets = []`` then appends one by one, so the draw thread can be
       iterating the list mid-rebuild → ``RuntimeError: list changed size during
       iteration`` → the TUI dies. A per-column re-entrant lock serialises
       ``set_widgets`` against ``get_lines`` to prevent that.
    """

    def __init__(self, *args, **kwargs) -> None:
        self._mutate_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def set_widgets(self, new: list[ptg.Widget]) -> None:
        with self._mutate_lock:
            super().set_widgets(new)

    def get_lines(self) -> list[str]:
        with self._mutate_lock:
            return super().get_lines()


def _status_box(*widgets) -> ptg.Container:
    """A borderless container for sub-panels within the main splitter."""
    c = _Column(*widgets)
    c.box = "EMPTY"
    return c


def _account_card() -> ptg.Container:
    global PROFILE_LABEL
    buttons = _split(
        _flat_button("log in", on_login_click),
        _flat_button("log out", on_logout_click),
    )
    buttons.chars["separator"] = " "  # delete the | divider line, leaving a space
    PROFILE_LABEL = ptg.Label(f"[dim]current log in profile:  [247]{PROFILE_EMAIL}")
    return _framed(
        buttons,
        "",
        PROFILE_LABEL,
    )


# Fixed width (in columns) of the left rail B, including its frame.
SIDEBAR_WIDTH = 18


def _sidebar() -> ptg.Container:
    """Left navigation: selectable 'Models' and 'Quota' items."""
    global SIDEBAR_BOX
    SIDEBAR_ITEMS.clear()
    for name in ("Models", "Quota"):
        btn = FlatButtonContainer(name, lambda _b, n=name: select_view(n), box="EMPTY")
        btn.label_widget.parent_align = ptg.HorizontalAlignment.LEFT
        SIDEBAR_ITEMS[name] = btn
    SIDEBAR_BOX = _Column(*SIDEBAR_ITEMS.values(), box="EMPTY")
    return SIDEBAR_BOX


def _content_panel() -> ptg.Container:
    """Right content area; populated by `select_view`."""
    global CONTENT_PANEL
    CONTENT_PANEL = _status_box(ptg.Label(""))
    return CONTENT_PANEL


def on_model_click(name: str) -> None:
    """Select a model: write it to agy's settings.json and refresh the dots."""
    try:
        set_selected_model(name)
        if ACTIVE_VIEW == "Models":
            update_content_ui("Models")
            if ACTIVE_MANAGER:
                try:
                    ACTIVE_MANAGER.compositor.redraw()
                except Exception:
                    pass
    except Exception as exc:
        _log_exc("on_model_click", exc)


def _model_row(name: str, selected: bool) -> FlatButtonContainer:
    """One clickable model line; a dot marks the active selection."""
    dot = "[120]● [/]" if selected else "  "
    row = FlatButtonContainer(dot + name, lambda _b, n=name: on_model_click(n), box="EMPTY")
    row.label_widget.parent_align = ptg.HorizontalAlignment.LEFT
    return row


def _fetch_models_async() -> None:
    """Fetch the live model list off the UI thread, then re-render Models."""
    global MODELS_LOADING
    if MODELS_LOADING:
        return
    MODELS_LOADING = True

    def work() -> None:
        global MODELS_CACHE, MODELS_LOADING
        try:
            models = list_models()
        except Exception:
            models = []
        if models:
            MODELS_CACHE = models
        MODELS_LOADING = False
        if ACTIVE_VIEW == "Models":
            update_content_ui("Models")
            if ACTIVE_MANAGER:
                try:
                    ACTIVE_MANAGER.compositor.redraw()
                except Exception:
                    pass

    threading.Thread(target=work, daemon=True).start()


def _get_valid_token() -> str | None:
    """Ensure token is fresh by running check_email_now(), then read credential."""
    try:
        email = check_email_now()
        if not email:
            return None
        cred = win32cred.CredRead('gemini:antigravity', win32cred.CRED_TYPE_GENERIC)
        blob = json.loads(cred['CredentialBlob'].decode('utf-8'))
        return blob['token']['access_token']
    except Exception:
        return None


def _get_quota_data() -> list[dict] | None:
    """Query the CloudCode APIs for loadCodeAssist (project) and retrieveUserQuota."""
    token = _get_valid_token()
    if not token:
        return None

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'Go-http-client/1.1'
    }

    project_id = "app"
    try:
        req = urllib.request.Request(
            'https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist',
            data=b'{}',
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            data = json.loads(res.read().decode('utf-8'))
            proj = data.get("cloudaicompanionProject")
            if proj:
                project_id = proj
    except Exception as e:
        _log_exc("_get_quota_data:loadCodeAssist", e)

    try:
        req = urllib.request.Request(
            'https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota',
            data=json.dumps({"project": project_id}).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            data = json.loads(res.read().decode('utf-8'))
            return data.get("buckets", [])
    except Exception as e:
        _log_exc("_get_quota_data:retrieveUserQuota", e)
        return None


def _format_quota_row(bucket: dict) -> str:
    """Format one quota row with aligned columns and color coding."""
    model_id = bucket.get("modelId", "")
    rem = bucket.get("remainingFraction", 0.0)
    pct = int(rem * 100)

    model_padded = model_id.ljust(28)
    pct_val_str = f"{pct}%"
    pct_padded = pct_val_str.rjust(5)

    if pct == 100:
        pct_styled = f"[120]{pct_padded}[/]"
    elif pct < 30:
        pct_styled = f"[210]{pct_padded}[/]"
    else:
        pct_styled = f"[220]{pct_padded}[/]"

    reset_time_str = bucket.get("resetTime", "")
    reset_display = ""
    if reset_time_str:
        try:
            clean_str = reset_time_str.split(".")[0].replace("Z", "")
            utc_dt = datetime.datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            if utc_dt.year >= 2000:
                local_dt = utc_dt.astimezone()
                reset_display = f" [dim](reset: {local_dt.strftime('%m-%d %H:%M')})[/]"
        except Exception:
            pass

    return f"  {model_padded} {pct_styled} {reset_display}"


def _redraw_quota() -> None:
    """Re-render the Quota view and flush the compositor."""
    if ACTIVE_VIEW == "Quota":
        update_content_ui("Quota")
        if ACTIVE_MANAGER:
            try:
                ACTIVE_MANAGER.compositor.redraw()
            except Exception:
                pass


def _fetch_quota_async() -> None:
    """Fetch individual model daily-request quotas from cloudcode-pa REST API off the UI thread."""
    global QUOTA_LOADING
    if QUOTA_LOADING:
        return
    QUOTA_LOADING = True

    def _work() -> None:
        global QUOTA_CACHE, QUOTA_LOADING
        try:
            buckets = _get_quota_data()
        except Exception:
            buckets = None
        if buckets is not None:
            QUOTA_CACHE = buckets
        QUOTA_LOADING = False
        _redraw_quota()

    threading.Thread(target=_work, daemon=True).start()


def _fetch_quota_summary_async() -> None:
    """Fetch group quota from agy's gRPC language server off the UI thread (~15 s)."""
    global QUOTA_SUMMARY_LOADING
    if QUOTA_SUMMARY_LOADING:
        return
    QUOTA_SUMMARY_LOADING = True

    def _work() -> None:
        global QUOTA_SUMMARY_CACHE, QUOTA_SUMMARY_LOADING
        try:
            result = get_quota_summary()
        except Exception as exc:
            _log_exc("_fetch_quota_summary_async", exc)
            result = None
        if result is not None:
            QUOTA_SUMMARY_CACHE = result
        QUOTA_SUMMARY_LOADING = False
        _redraw_quota()

    threading.Thread(target=_work, daemon=True).start()


TUI_START_TIME = time.time()


def _get_session_usage() -> dict:
    """Calculate dynamic session usage based on active model, time, and conversation DB."""
    active_model = get_selected_model() or "Unknown"
    elapsed_sec = int(time.time() - TUI_START_TIME)
    h = elapsed_sec // 3600
    m = (elapsed_sec % 3600) // 60
    s = elapsed_sec % 60
    elapsed_str = f"{h}h {m}m {s}s"
    
    tokens_used = 0
    conv_dir = Path(os.path.expanduser("~")) / ".gemini" / "antigravity-cli" / "conversations"
    if conv_dir.exists():
        dbs = list(conv_dir.glob("*.db"))
        if dbs:
            try:
                newest_db = max(dbs, key=lambda p: p.stat().st_mtime)
                import sqlite3
                con = sqlite3.connect(f"file:{newest_db}?mode=ro", uri=True)
                rows = con.execute("SELECT step_payload FROM steps").fetchall()
                total_chars = 0
                for row in rows:
                    if row[0]:
                        total_chars += len(row[0])
                con.close()
                tokens_used = total_chars // 3
            except Exception:
                pass
                
    workspace = os.getcwd()
    
    return {
        "model": active_model,
        "elapsed": elapsed_str,
        "tokens": f"{tokens_used:,}",
        "workspace": workspace
    }


def _content_widgets(view: str) -> list[ptg.Widget]:
    """Widgets shown in the content panel for a given view."""
    if view == "Quota":
        def _bar(pct: float) -> str:
            filled = int(round(pct * 15 / 100))
            return "█" * filled + "░" * (15 - filled)

        def _countdown(ts: int | None) -> str:
            if ts is None:
                return ""
            secs = max(0, ts - int(time.time()))
            d, rem = divmod(secs, 86400)
            h, rem2 = divmod(rem, 3600)
            m = rem2 // 60
            if d > 0: return f"{d}d {h}h"
            if h > 0: return f"{h}h {m}m"
            return f"{m}m"

        def _color(pct: float) -> str:
            return "210" if pct < 10 else ("220" if pct < 40 else "120")

        rows: list[ptg.Widget] = [
            ptg.Label("[72 bold]Account Group Limits"),
            ptg.Label(""),
        ]

        if QUOTA_SUMMARY_CACHE:
            for key, header in (
                ("gemini",    "Gemini Group Limits"),
                ("claude_gpt", "Claude & GPT Group Limits"),
            ):
                g = QUOTA_SUMMARY_CACHE.get(key)
                rows.append(ptg.Label(f"  [bold]{header}[/]"))
                if g:
                    for lk, label in (("weekly", "Weekly Limit"), ("fiveh", "Five-Hour Limit")):
                        pct = g.get(f"{lk}_pct")
                        if pct is not None:
                            c = _color(pct)
                            rows.append(ptg.Label(
                                f"    {label:<17}  {_bar(pct)} [{c}]{pct:>6.2f}%[/]"
                            ))
                            cd = _countdown(g.get(f"{lk}_reset_ts"))
                            if cd:
                                rows.append(ptg.Label(f"      [dim]Refreshes in {cd}[/]"))
                rows.append(ptg.Label(""))
        elif QUOTA_SUMMARY_LOADING:
            rows += [ptg.Label("    [dim]Loading… (~15 s)[/]"), ptg.Label("")]
        else:
            rows += [ptg.Label("    [dim](sign in to view limits)[/]"), ptg.Label("")]

        # Session Usage Summary
        usage = _get_session_usage()
        rows.extend([
            ptg.Label("[72 bold]Session Usage Summary"),
            ptg.Label(""),
            ptg.Label(f"  Active Model:      [247]{usage['model']}[/]"),
            ptg.Label(f"  Session Elapsed:   [247]{usage['elapsed']}[/]"),
            ptg.Label(f"  Est. Tokens Used:  [247]{usage['tokens']}[/]"),
            ptg.Label(f"  Workspace:         [247]{usage['workspace']}[/]"),
            ptg.Label(""),
        ])

        # Individual Model Quotas (daily request limits from REST API)
        rows += [ptg.Label("[72 bold]Individual Model Quotas"), ptg.Label("")]
        if QUOTA_CACHE:
            for bucket in QUOTA_CACHE:
                rows.append(ptg.Label(_format_quota_row(bucket)))
        elif QUOTA_LOADING:
            rows.append(ptg.Label("    [dim]Loading…[/]"))
        else:
            rows.append(ptg.Label("    [dim](sign in to view quotas)[/]"))
        return rows


    # Models view.
    if not MODELS_CACHE:
        msg = "Loading models…" if MODELS_LOADING else "(no models — signed in?)"
        return [ptg.Label("[72 bold]Models"), ptg.Label(""), ptg.Label(f"[dim]{msg}")]
    selected = get_selected_model()
    rows: list[ptg.Widget] = [ptg.Label("[72 bold]Models"), ptg.Label("")]
    for name in MODELS_CACHE:
        rows.append(_model_row(name, name == selected))
    return rows


def update_content_ui(view: str) -> None:
    """Render the panel content for the active sidebar view, padding the left
    rail B and the main panel D so both Splitter columns are the same height.

    Layout per column (lines):
        B  = frame(2) + sidebar rows
        right = status C + blank(1) + frame(2) + content rows
    We make both equal to ``body_h`` (fills the terminal height, or grows to fit
    the content on a short terminal). The Splitter mis-pads unequal columns, so
    equal heights are required.
    """
    if not (CONTENT_PANEL and SIDEBAR_BOX):
        return
    content_all = _content_widgets(view)
    total_rows = len(content_all)
    # Visible terminal height available for content panel body.
    vis_h = max(ptg.terminal.height - 6 - STATUS_H - 3, 5)

    # Clamp scroll offset so the last page always fills the viewport.
    global CONTENT_SCROLL
    CONTENT_SCROLL = max(0, min(CONTENT_SCROLL, total_rows - vis_h))

    # Slice the visible window.
    visible = content_all[CONTENT_SCROLL: CONTENT_SCROLL + vis_h]

    # Scroll indicator on the last visible row when content overflows.
    if total_rows > vis_h:
        end = CONTENT_SCROLL + len(visible)
        indicator = ptg.Label(
            f"[dim]  ↑/↓  pgup/pgdn  ·  {CONTENT_SCROLL + 1}-{end} of {total_rows}[/]"
        )
        visible[-1] = indicator

    visible += [ptg.Label("") for _ in range(vis_h - len(visible))]

    # Both Splitter columns must be the same height.
    body_h = vis_h + STATUS_H + 3
    sidebar_h = body_h - 2
    sidebar: list[ptg.Widget] = list(SIDEBAR_ITEMS.values())
    sidebar += [ptg.Label("") for _ in range(sidebar_h - len(sidebar))]
    CONTENT_PANEL.set_widgets(visible)
    SIDEBAR_BOX.set_widgets(sidebar)


def _scroll(delta: int) -> None:
    """Shift the content panel scroll offset by delta rows and redraw."""
    global CONTENT_SCROLL
    CONTENT_SCROLL += delta
    update_content_ui(ACTIVE_VIEW)
    if ACTIVE_MANAGER:
        try:
            ACTIVE_MANAGER.compositor.redraw()
        except Exception:
            pass


def select_view(view: str) -> None:
    """Activate a sidebar view and refresh the content panel."""
    global ACTIVE_VIEW, CONTENT_SCROLL
    ACTIVE_VIEW = view
    CONTENT_SCROLL = 0
    for name, btn in SIDEBAR_ITEMS.items():
        btn.active = (name == view)
    # Fetch the model list on demand (off the UI thread). Gated on a running
    # manager so build_window() stays pure / TTY-free for smoke tests.
    if view == "Models" and not MODELS_CACHE and ACTIVE_MANAGER:
        _fetch_models_async()
    elif view == "Quota" and ACTIVE_MANAGER:
        if not QUOTA_CACHE:
            _fetch_quota_async()
        if QUOTA_SUMMARY_CACHE is None and not QUOTA_SUMMARY_LOADING:
            _fetch_quota_summary_async()
    update_content_ui(view)
    if ACTIVE_MANAGER:
        try:
            ACTIVE_MANAGER.compositor.redraw()
        except Exception:
            pass


def _window_width() -> int:
    """Fill the terminal width (minus a small margin) so the main panel is wide
    enough to show full model names. Floored so a tiny terminal still renders."""
    return max(60, ptg.terminal.width - 2)


def build_window() -> ptg.Window:
    """Build the main window. Pure construction — no terminal required,
    so it can be smoke-imported without a live TTY.

    Layout: a full-height left rail B (sidebar) beside a right column that
    stacks the status panel C over the main panel D.
    """
    global STATUS_H

    b_panel = _framed(_sidebar())          # B — full-height left rail
    b_panel.size_policy = ptg.SizePolicy.STATIC
    b_panel.width = SIDEBAR_WIDTH

    status = _account_card()               # C — status panel (top-right)
    status.width = 50
    STATUS_H = len(status.get_lines())     # measure so B/D pad to matching height

    d_panel = _framed(_content_panel())    # D — main panel (bottom-right)
    right = _Column(status, "", d_panel, box="EMPTY")

    body = _split(b_panel, right)
    body.chars["separator"] = " "          # frames draw the borders; just a gap

    # Render the default view and mark its sidebar item active (also pads heights).
    select_view(ACTIVE_VIEW)

    win = (
        ptg.Window(
            body,
            "",
            "[dim]ctrl+c quit   ·   tab to move",
            width=_window_width(),
            box="DOUBLE",
        )
        .set_title("[210 bold] AGY MCP ")
        .center()
    )
    win.styles.border = BORDER
    win.styles.corner = BORDER
    return win


def main() -> None:
    global ACTIVE_MANAGER
    _install_crash_logging()
    with ptg.WindowManager() as manager:
        ACTIVE_MANAGER = manager
        manager.add(build_window())

        # Arrow keys / page keys scroll the content panel.
        manager.bind("\x1b[A", lambda *_: _scroll(-1))   # ↑
        manager.bind("\x1b[B", lambda *_: _scroll(1))    # ↓
        manager.bind("\x1b[5~", lambda *_: _scroll(-10)) # Page Up
        manager.bind("\x1b[6~", lambda *_: _scroll(10))  # Page Down

        # Preload the live model list (default view is Models).
        _fetch_models_async()

        # Start background profile update thread
        threading.Thread(target=_update_profile_loop, args=(manager,), daemon=True).start()

        manager.run()


if __name__ == "__main__":
    main()
