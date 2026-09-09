"""Built-in, searchable help. Only the Copy buttons change the clipboard."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402


PROMPT_EXAMPLES = (
    ("Sort downloads", "Sort ~/Downloads into folders by file type, keeping existing folders in place."),
    ("Find files", "Find PDF files with 'invoice' in their names under ~/Documents and show their locations."),
    ("Check disk space", "Show available disk space and the largest folders directly inside my home folder."),
    ("Check performance", "Check CPU and memory use, then explain what might be making my computer slow."),
    ("Install an app", "Check whether VLC is installed and install it from the software repositories if needed."),
    ("Change the theme", "Show the installed desktop themes, then ask which one I want to apply."),
    ("Add a shortcut", "Create a Ctrl+Alt+N keyboard shortcut that opens a new text editor window."),
    ("Summarize a file", "Summarize ~/Documents/notes.txt and list the action items in a short checklist."),
    ("Open websites", "Open youtube.com and wikipedia.org in my default browser."),
    ("Change the wallpaper", "Use ~/Pictures/wallpaper.jpg as my desktop background and scale it to fill the screen."),
)


MANUAL_SECTIONS = (
    ("Getting started", (
        "1. Click the peppermint picture in the top-left corner to open the sidebar, "
        "then choose New conversation. "
        "Describe what you want to do, or the problem you want to solve.",
        "2. Include the app name, file or folder path, and any error message. "
        "For troubleshooting, say what you expected and what happened instead.",
        "3. Choose New conversation or press Enter to submit. Read the reply, "
        "answer any questions, and review each requested action before allowing it.",
        "For a useful starting point, copy an example below and replace its paths "
        "with your own. Copying an example does not submit it.",
    )),
    ("Tasks and conversations", (
        "Tasks is your saved task board. The counters show active work, tasks that "
        "need you, completed tasks, and the total saved on this computer.",
        "Use the status filter and Search tasks to find earlier work. "
        "Previous and Next move through results, with the most recently updated first. "
        "Choose Open or Review to read a task's conversation.",
        "In Conversations, expand a conversation to read messages, its plan, and "
        "the Activity log. Expand Output within an activity step to see more detail. "
        "After a task finishes, use Continue this conversation to send a follow-up.",
        "A completed task or checked plan item records progress. Test the original "
        "problem to find out whether it is resolved. Draft replies stay in place "
        "when you switch pages or refresh.",
    )),
    ("Approvals, stopping and retests", (
        "When Permission required appears, read the proposed action and check "
        "its files, app, and scope. Allow once approves that request; Deny refuses it. "
        "Inspection of your computer can also require approval.",
        "When Peppermint asks a question, choose an offered answer or type details "
        "and select Reply. A task marked Needs an answer is waiting for this response.",
        "Stop requests cancellation of the conversation's current task. "
        "Work already performed remains in place; Stop does not close other "
        "applications or undo earlier changes.",
        "For a requested retest, perform the specific test and choose Passed, "
        "Still failing, or Not tested. These buttons record your reported outcome. "
        "Typing a reply adds details without recording a test result. If later "
        "changes affect the task, Peppermint may need you to test again.",
    )),
    ("Basic diagnostics", (
        "Open Diagnostics from the peppermint sidebar and choose Start monitoring. "
        "Basic shows CPU, memory, GPU, disk activity, network activity, and swap. "
        "Unavailable measurements appear as a dash or a gap.",
        "Choose Every 2 seconds or Every 5 seconds for the sampling interval. "
        "Last 60 seconds and Last 5 minutes change the visible history. Rates need "
        "two readings, so some values take another sample to appear.",
        "Pause monitoring stops new samples. Leaving Diagnostics or hiding the "
        "window also pauses collection. Returning resumes it if you previously "
        "chose Start and have not chosen Pause.",
        "History lasts for the current app session. A gap means no usable reading "
        "was recorded; it does not mean resource use was zero. Changing the sampling "
        "interval preserves earlier gaps.",
        "Choose Diagnostics on a task card to see its recorded activity times "
        "alongside measurements. Times may be outside the visible history or in "
        "a gap. This comparison alone does not identify the cause of a problem.",
    )),
    ("Advanced diagnostics", (
        "Choose Advanced to add process measurements, individual CPU cores, "
        "graphics details, temperatures, and memory or CPU pressure where available.",
        "Search sampled processes by name or PID. Click a column heading to sort "
        "and select a process for its latest details. The table contains a sampled "
        "subset, so an app missing from search may still be running.",
        "Process CPU uses one logical core as 100%; an app using several cores "
        "can exceed 100%. RSS MiB is the process's resident memory. Disk read and "
        "write columns show rates between readings; unavailable values remain blank.",
        "Use these measurements to inspect resource use. Process history and "
        "controls for closing applications are not available in this view. "
        "Press Ctrl+Alt+Delete to open the separate Recovery window for process controls.",
    )),
    ("Recovery: frozen applications", (
        "Press Ctrl+Alt+Delete to open Peppermint Recovery fullscreen. "
        "The original Linux Mint logout shortcut is moved to Ctrl+Alt+Shift+Delete. "
        "You can also run peppermint recover from a terminal.",
        "Search by process name or PID, select the exact process, and choose Request stop. "
        "Check the name and PID in the confirmation before continuing. "
        "Unsaved work in that process may be lost. The action applies to one process; "
        "it does not automatically close every process belonging to that application.",
        "If the selected process stays running after Request stop, Force stop becomes "
        "available. It requires a separate confirmation. Refresh processes updates "
        "the snapshot. Core display, session and system processes are protected. "
        "An exited process does not prove the original problem is fixed.",
        "Recovery runs separately from the main window, background service and AI. "
        "It can help when an application hangs, but Cinnamon's keyboard service, "
        "the display server and the kernel must still respond. It cannot guarantee "
        "screen takeover during a complete desktop or kernel freeze, even with "
        "administrator access.",
        "Choose Return to desktop or press Escape to close Recovery. "
        "Closing it does not undo a stop or session action already requested.",
    )),
    ("Recovery: session controls", (
        "At the bottom of Recovery, use Lock screen, Switch user, Log out, Restart "
        "or Shut down. Suspend and Hibernate appear only when supported by the "
        "current computer and session. Other unavailable actions are disabled.",
        "Log out, Restart and Shut down open Linux Mint's normal confirmation. "
        "Mint handles applications that need attention or block the action. "
        "Recovery moves out of the way for these dialogs.",
        "Lock screen, Switch user, Suspend and Hibernate ask you to confirm in "
        "Recovery before making the request. Switch user locks your current session "
        "before opening the login screen. Mint may request authentication.",
        "Requested means the desktop service received the request; it does not "
        "mean the session action finished. If no reply arrives in time, the request "
        "may still be pending. Check the desktop before trying again.",
    )),
    ("Recovery: administrator access and setup", (
        "You can stop your own eligible processes without administrator access. "
        "Stopping a process owned by another user requires the installed recovery "
        "helper and Linux Mint's administrator authentication. The Recovery window "
        "runs as your normal user. Peppermint stores no password and does not keep "
        "a permanent administrator session.",
        "Check administrator access runs an authenticated helper check without "
        "stopping any process. If the helper is unavailable, your own process "
        "controls remain usable.",
        "After installing Peppermint, open a terminal in its project directory. "
        "Run .venv/bin/python -m peppermint.recovery.shortcut --plan to inspect "
        "shortcut changes, then ./scripts/install-recovery.sh to configure the "
        "shortcut and helper. Mint asks for administrator authentication when "
        "installing the helper. To install or refresh only the helper, run "
        "./scripts/install-recovery-admin.sh.",
        "Shortcut setup preserves unrelated bindings and refuses conflicts. "
        "The original settings are backed up to "
        "~/.local/share/peppermint/recovery-shortcut-backup.json, or under "
        "XDG_DATA_HOME if configured. Run .venv/bin/python -m "
        "peppermint.recovery.shortcut --restore to restore settings that have "
        "not been changed since installation. Later user changes are preserved "
        "and reported. Restore affects shortcuts only; it leaves the helper installed.",
    )),
    ("Sidebar and window controls", (
        "The peppermint picture in the top-left corner opens a sidebar that slides "
        "in from the left. "
        "Use it to reach Tasks, Conversations, Diagnostics, and this User manual, "
        "or to start a New conversation. Choosing a destination closes the sidebar "
        "and opens that page. Click the peppermint picture again, the sidebar's "
        "close button, or the dimmed area outside it, or press Escape to close it. "
        "Use Tab to move between controls and Enter or Space "
        "to activate a button.",
        "Use Refresh from the sidebar to reload saved tasks and conversations. "
        "If the connection status shows Offline, the background service is "
        "unavailable; try Refresh after it is running again.",
        "The top-right controls minimize, maximize or restore, and close the "
        "window. Hide window in the sidebar also hides Peppermint. Its background service keeps "
        "working. Use Stop in a conversation when you need to cancel a task.",
    )),
)


def _text(text: str, style: str = "manual-body", *, selectable: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(76)
    label.set_selectable(selectable)
    label.get_style_context().add_class(style)
    return label


class UserManual(Gtk.Box):
    """A local reference page that filters sections and copies example text."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.get_style_context().add_class("user-manual")
        self.set_hexpand(True)
        self._load_css()
        self._sections: list[tuple[Gtk.Widget, str]] = []
        self.copy_buttons: list[Gtk.Button] = []

        self.pack_start(_text("User manual", "hero-title", selectable=False), False, False, 0)
        self.pack_start(_text(
            "Get started, find your work, and understand your computer.", "muted",
            selectable=False), False, False, 0)

        self.search = Gtk.SearchEntry(placeholder_text="Search the manual…")
        self.search.set_max_length(240)
        self.search.set_tooltip_text("Find sections and example prompts by their text")
        self.search.get_accessible().set_name("Search the user manual")
        self.search.connect("search-changed", self._filter)
        self.pack_start(self.search, False, False, 0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_overlay_scrolling(False)
        self.pack_start(self.scroller, True, True, 0)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.content.set_margin_end(10)
        self.scroller.add(self.content)

        self.empty_label = _text("No matching sections. Try another word or clear the search.", "muted")
        self.empty_label.set_no_show_all(True)
        self.content.pack_start(self.empty_label, False, False, 0)

        for title, paragraphs in MANUAL_SECTIONS:
            section = self._card(title)
            for paragraph in paragraphs:
                section.pack_start(_text(paragraph), False, False, 0)
            self._add_searchable(section, " ".join((title, *paragraphs)))

        self.examples = self._card("Example prompts")
        self.examples.pack_start(_text(
            "Copy an example, then paste it into a new conversation and edit it "
            "before submitting. You can also select the text directly.", "muted"), False, False, 0)
        self.content.pack_start(self.examples, False, False, 0)
        self._example_items: list[tuple[Gtk.Widget, str]] = []
        for title, prompt in PROMPT_EXAMPLES:
            item = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            item.get_style_context().add_class("manual-example")
            heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            heading.pack_start(_text(title, "task-title", selectable=False), True, True, 0)
            copy = Gtk.Button(label="Copy")
            copy.get_style_context().add_class("manual-copy")
            copy.set_tooltip_text(f"Copy example: {title}")
            copy.get_accessible().set_name(f"Copy example: {title}")
            copy.connect("clicked", self._copy_prompt, prompt)
            self.copy_buttons.append(copy)
            heading.pack_end(copy, False, False, 0)
            item.pack_start(heading, False, False, 0)
            item.pack_start(_text(prompt), False, False, 0)
            self.examples.pack_start(item, False, False, 0)
            item.show_all()
            item.set_no_show_all(True)
            self._example_items.append((item, f"Example prompts {title} {prompt}".casefold()))
        self.examples.show_all()
        self.examples.set_no_show_all(True)

    @staticmethod
    def _load_css():
        screen = Gdk.Screen.get_default()
        if screen:
            provider = Gtk.CssProvider()
            provider.load_from_path(str(Path(__file__).with_name("manual.css")))
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    @staticmethod
    def _card(title: str) -> Gtk.Box:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        section.get_style_context().add_class("manual-card")
        section.pack_start(_text(title, "manual-heading", selectable=False), False, False, 0)
        return section

    def _add_searchable(self, widget: Gtk.Widget, text: str):
        self.content.pack_start(widget, False, False, 0)
        widget.show_all()
        widget.set_no_show_all(True)
        self._sections.append((widget, text.casefold()))

    def _filter(self, *_):
        words = self.search.get_text().casefold().split()
        visible = 0
        for widget, text in self._sections:
            matches = all(word in text for word in words)
            widget.set_visible(matches)
            visible += matches
        examples_visible = 0
        for widget, text in self._example_items:
            matches = all(word in text for word in words)
            widget.set_visible(matches)
            examples_visible += matches
        self.examples.set_visible(bool(examples_visible))
        self.empty_label.set_visible(not (visible or examples_visible))
        self.scroller.get_vadjustment().set_value(0)

    def _copy_prompt(self, button: Gtk.Button, prompt: str):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(prompt, -1)
        clipboard.store()
        for copy in self.copy_buttons:
            copy.set_label("Copied" if copy is button else "Copy")
