"""Mask credential replies using the prompt, never by inspecting typed secrets."""
import re

from gi.repository import Gtk

_CREDENTIAL = re.compile(r'\b(?:pass\s*word|passwd|passphrase|passcode|pin|api[ _-]?key|access[ _-]?token|secret[ _-]?key)\b', re.I)


def configure_secret_entry(entry, prompt):
    secret = bool(_CREDENTIAL.search(prompt or ''))
    entry.set_visibility(not secret)
    entry.set_input_purpose(Gtk.InputPurpose.PASSWORD if secret else Gtk.InputPurpose.FREE_FORM)
    entry.set_invisible_char('*')
    return secret
