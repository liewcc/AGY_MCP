"""AGY MCP — terminal control panel.

A small pytermgui skeleton. Frame only; real functionality (sending prompts to
`agy`, listing/reading conversations, real OAuth login) is wired in
incrementally on top of it.

Run it with `run.bat`, or directly:

    python tui.py
"""

from __future__ import annotations

import json
import re
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytermgui as ptg
import win32cred

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

# Reference to the status panel container.
STATUS_PANEL: ptg.Container | None = None

# The active WindowManager instance.
ACTIVE_MANAGER: ptg.WindowManager | None = None

# One uniform grey for every frame / divider.
BORDER = "240"


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
        
    global PROFILE_LABEL
    if PROFILE_LABEL:
        PROFILE_LABEL.value = "[dim]current log in profile:  [247](not signed in)"
        if ACTIVE_MANAGER:
            try:
                ACTIVE_MANAGER.compositor.redraw()
            except Exception:
                pass


def _update_profile_loop(manager: ptg.WindowManager) -> None:
    """Background loop to poll login status and update the UI in real-time."""
    last_email = None
    last_selected_model = None
    
    while True:
        try:
            email = check_email_now()
        except Exception:
            email = None
            
        display_email = email if email else "(not signed in)"
        selected_model = get_selected_model() if email else None
        
        if display_email != last_email or selected_model != last_selected_model:
            last_email = display_email
            last_selected_model = selected_model
            global PROFILE_LABEL
            if PROFILE_LABEL:
                PROFILE_LABEL.value = f"[dim]current log in profile:  [247]{display_email}"
            
            update_status_ui(email)
            
            if manager:
                try:
                    manager.compositor.redraw()
                except Exception:
                    pass
        time.sleep(3)


def _framed(*widgets) -> ptg.Container:
    """A single-line box with a uniform grey border."""
    box = ptg.Container(*widgets, box="SINGLE")
    box.styles.border = BORDER
    box.styles.corner = BORDER
    return box


def _split(*widgets) -> ptg.Splitter:
    """A horizontal splitter whose divider matches the grey frames."""
    sp = ptg.Splitter(*widgets)
    sp.styles.separator = BORDER
    sp.styles.fill = lambda depth, item: item  # prevent pytermgui from parsing/corrupting ANSI codes
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
        elif self._is_hovered or self.selected_index is not None:
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


def _status_box(*widgets) -> ptg.Container:
    """A borderless container for sub-panels within the main splitter."""
    c = ptg.Container(*widgets)
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


def _status_panel() -> ptg.Container:
    """Build the status panel container."""
    global STATUS_PANEL
    STATUS_PANEL = _status_box(
        ptg.Label("[72 bold]Status"),
        ptg.Label(""),
        ptg.Label(f"{_dot(AGY_BIN.exists())} [247]agy.exe"),
        ptg.Label(""),
        ptg.Label(f"{_dot(True)} [247]default model"),
        ptg.Label(f"   [dim]{DEFAULT_MODEL}"),
    )
    return STATUS_PANEL


def update_status_ui(email: str | None = None) -> None:
    """Update the status panel widgets."""
    global STATUS_PANEL
    if not STATUS_PANEL:
        return
    agy_ok = AGY_BIN.exists()
    model = DEFAULT_MODEL
    if email is None:
        email = check_email_now()
    if email:
        model = get_selected_model() or DEFAULT_MODEL
        
    widgets = [
        ptg.Label("[72 bold]Status"),
        ptg.Label(""),
        ptg.Label(f"{_dot(agy_ok)} [247]agy.exe"),
        ptg.Label(""),
        ptg.Label(f"{_dot(True)} [247]default model"),
        ptg.Label(f"   [dim]{model}"),
    ]
    STATUS_PANEL.set_widgets(widgets)


def _tools_panel() -> ptg.Container:
    return _status_box(
        "[72 bold]MCP Tools",
        "",
        "[247]ask_antigravity",
        "[dim]   send one prompt, get the answer",
        "[247]list_conversations",
        "[dim]   recent sessions, newest first",
        "[247]read_conversation",
        "[dim]   full transcript by id",
    )


def _window_width() -> int:
    """Fixed default width of 80, capped to the terminal width."""
    term_w = ptg.terminal.width
    return min(80, term_w - 2)


def build_window() -> ptg.Window:
    """Build the main window. Pure construction — no terminal required,
    so it can be smoke-imported without a live TTY."""
    ask_box = _framed(ptg.InputField("", prompt="[72]Ask  [/]"))

    status_box = _status_panel()

    panels = _split(status_box, _tools_panel())
    panels.chars["separator"] = "│"   # single connected line, NO spaces
    panels_box = _framed(panels)      # one grey outer box around both columns

    win = (
        ptg.Window(
            _account_card(),
            "",
            panels_box,
            "",
            ask_box,
            # Placeholder action — wired to ask_antigravity in a later step.
            FlatButtonContainer("Send", lambda *_: None, box="EMPTY"),
            "",
            "[dim]ctrl+c quit   ·   tab to move   ·   skeleton — features coming",
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
    with ptg.WindowManager() as manager:
        ACTIVE_MANAGER = manager
        manager.add(build_window())
        
        # Start background profile update thread
        threading.Thread(target=_update_profile_loop, args=(manager,), daemon=True).start()
        
        manager.run()


if __name__ == "__main__":
    main()
