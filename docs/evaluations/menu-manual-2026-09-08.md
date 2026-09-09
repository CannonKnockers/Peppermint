# Peppermint menu and user manual — September 8, 2026

The former prompt-guide button is now a wordless peppermint emblem with mint
green and cream swirls. Its SVG stays crisp at desktop scale and ships with the
application. The button has an accessible name and tooltip.

The full-height sliding left sidebar contains Tasks, Conversations, Diagnostics, User manual,
New conversation, Refresh and Hide window. It replaces the separate navigation
tabs and header refresh control. The selected page appears beneath Peppermint's
title. Native minimize/maximize/close controls retain their corner positions.
The peppermint icon, close arrow, Escape or a click in the dimmed workspace
dismisses the sidebar; Escape again hides the window. Selecting a destination
also closes it. The 280ms slide respects the desktop's animation preference.

The manual provides searchable instructions for getting started, saved work,
questions and approvals, cancellation and retests, basic and advanced monitoring,
and window controls. It includes the ten existing examples with Copy buttons.
Reading, searching or copying never submits a task. Menu navigation preserves
drafts and follows the existing diagnostics pause/resume behavior.

## Validation

- Full suite: `.venv/bin/python -m pytest tests -q` — **1,207 passed in 10.09s**.
  The **14 workspace tests** cover full-height sidebar geometry, closing via the
  dimmed area and close arrow, disabled animations, native GDK Escape
  delivery, menu focus, draft retention, manual navigation, Refresh reads and
  opening the composer without submitting its existing draft.
- Manual smoke checks covered search, clearing, no matches, example filtering,
  filtered visibility across `show_all()`, and Copy feedback with a mocked
  clipboard. No model or real computer action was invoked.
- GTK fixture previews were reviewed at 1000×900 and 660×560 requested content
  sizes. The sidebar overlays content without squeezing it; the manual scrolls
  within the existing window. Captured images also include GTK decorations.
- Python compilation and whitespace checks passed. An offline wheel build
  confirmed the menu icon, manual, menu component and styles are packaged.
- The sidebar UI was reloaded at 20:56 America/Chicago, PID 355986, and presented.
  The daemon remained running as PID 350835. Read-only hashes of all nine DB
  tables matched across UI reload; all 35 saved tasks remain intact.

Previews use illustrative fixtures:

- [Main menu](peppermint-menu.png)
- [User manual](peppermint-manual.png)
