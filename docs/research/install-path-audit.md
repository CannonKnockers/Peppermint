# Install-Path & Template Consistency Audit

**Scope:** the lane Codex declared — path materialization, service/template alignment, evidence-based reporting. No rename, no parameter-selection changes.
**Method:** 10 agents (5 read-only dimension audits, each re-checked by an adversarial verifier told to refute it and regenerate the evidence). 67 findings, 66 verified: **60 CONFIRMED, 5 PLAUSIBLE, 2 REFUTED**. Every blocker below was then re-reproduced by hand before being written down.
**Repo state:** unmodified. `727 passed`. Agents wrote only to `/tmp` scratch.

---

## 1. Headline: the installation is already broken

Codex's lane is not cosmetic. Renaming the checkout `Documents/Minty` → `Documents/peppermint` **broke the installed product**, and the breakage is mostly silent.

| Severity | Count |
| --- | --- |
| **blocker** | **10** |
| high | 5 |
| medium | 26 |
| low | 25 |
| non-issue (refuted) | 1 |

Independently reproduced:

```
$ head -1 .venv/bin/minty
#!/home/jesse/Documents/Minty/.venv/bin/python3        <-- interpreter no longer exists

$ .venv/bin/minty --help ; echo $?
127

$ grep MAPPING .venv/lib/python3.12/site-packages/__editable___minty_0_1_0_finder.py
MAPPING: dict[str, str] = {'minty': '/home/jesse/Documents/Minty/minty'}   <-- deleted path

$ cd /home/jesse && .../peppermint/.venv/bin/python -c "import minty"
ModuleNotFoundError: No module named 'minty'
```

All 9 console scripts in `.venv/bin` (`minty`, `minty-daemon`, `minty-window`, `pip`, `pytest`, …) exit 127. The installed unit at `~/.config/systemd/user/minty-daemon.service` still reads `ExecStart=%h/Documents/Minty/.venv/bin/minty-daemon`; under systemd this surfaces as `203/EXEC`. Both installed `.desktop` files point at the same dead path — GIO refuses to load them, so the menu entry and the autostart applet fail **silently**.

**The live daemon is a ghost.** PID 192893 started at 12:36, before the rename, and is executing from deleted inodes. It works now and will never come back after a restart.

### Why this was not noticed — and a correction to my earlier report

I previously cited `727 passed` as evidence the baseline was healthy. That was misleading. `pytest` inserts its rootdir into `sys.path`, so the suite passes **from any directory** while the actual installation is dead:

```
$ cd /home/jesse && .../peppermint/.venv/bin/python -m pytest .../tests/ -q
727 passed          <-- green

$ cd /home/jesse && .../peppermint/.venv/bin/python -c "import minty"
ModuleNotFoundError  <-- the install is broken
```

The test suite validates the **source**. It says nothing about the **install**. Treat them as separate gates from now on.

### install.sh cannot repair this

`[ -d "$VENV" ] || $PY -m venv …` — the venv directory exists, so the rebuild is skipped, the stale venv is reused, and the script then dies on the broken `pip`. Re-running the installer does not recover the system; it has to be rebuilt.

---

## 2. The ten blockers

| # | File | What is broken | Silent? |
| --- | --- | --- | --- |
| B1 | `.venv/bin/*` | 9 console scripts have shebangs pointing at the deleted interpreter → exit 127 | loud |
| B2 | `.venv/` (15 files) | `Documents/Minty` hardcoded throughout; venvs are not relocatable | loud |
| B3 | `__editable___minty_0_1_0_finder.py:9` | `MAPPING` points at the deleted source dir; `import minty` works only by cwd accident | **SILENT** |
| B4 | `~/.config/systemd/user/minty-daemon.service` | `ExecStart=%h/Documents/Minty/…` → `203/EXEC` on next restart | loud |
| B5 | `~/.local/share/applications/minty.desktop` | dead `Exec=`; GIO returns NULL, entry vanishes | **SILENT** |
| B6 | `data/minty-daemon.service` | template still carries the old path convention | **SILENT** |
| B7 | `data/minty-daemon.service` | `ExecStart` target's interpreter is gone | loud |
| B8 | `scripts/install.sh` | skips venv rebuild because `.venv/` exists → cannot self-heal | loud |
| B9 | `scripts/install.sh` | aborts at step 2 on the stale venv's broken `pip` | loud |
| B10 | `scripts/install.sh` | writes `.desktop` files that GLib refuses to load | **SILENT** |

**Recovery is one command**, and it must not be a shebang patch — `pyvenv.cfg`, the `activate*` scripts, the editable finder and `dist-info` all carry the old path:

```bash
rm -rf .venv && /usr/bin/python3 -m venv --system-site-packages .venv && .venv/bin/pip install -e .
```

---

## 3. Corrections to Milestone 0 — four errors in my own design

The verifiers correctly filed these as *forward-looking design conflicts*, not present repo defects (the hardening stanza does not exist in the tree yet). All four mechanisms reproduce.

**C1 — `ProtectHome=read-write` is not a valid systemd value.** My §5.2 stanza would fail to start the unit outright:

```
$ systemd-run --user -p ProtectHome=read-write /bin/true
Failed to start transient service unit: Invalid ProtectHome setting: read-write
```

Valid values are `yes`, `no`, `read-only`, `tmpfs`. **Remove the directive.**

**C2 — `ProtectSystem=strict` does not protect `$HOME` in a user unit.** Measured with the exact S1 combination:

```
$ systemd-run --user -p ProtectSystem=strict -p ReadWritePaths=~/.local/share/minty \
    /bin/bash -c 'touch ~/.pmaudit && echo HOME-WRITE-OK'
HOME-WRITE-OK
/home/jesse   /home   rw,relatime      <-- home stays writable
```

Every persistence vector in the threat model — `~/.bashrc`, `~/.config/autostart`, `~/.config/systemd/user` — remains writable by the agent and by every `bash -lc` it spawns. The directive reads like confinement and provides none. Real confinement needs `ReadOnlyPaths=%h` + explicit `ReadWritePaths=`, or `TemporaryFileSystem=%h:ro` + `BindPaths=`.

**C3 — `NoNewPrivileges=yes` breaks `apt_install` in *every* mode, not just Strict Offline.**

```
$ systemd-run --user -p NoNewPrivileges=yes /bin/bash -c '/usr/bin/pkexec --disable-internal-agent /bin/true'
pkexec rc=127 out=[pkexec must be setuid root]
```

`NoNewPrivs` is inherited by all descendants, so the kernel ignores pkexec's setuid bit. `packages.py:97` turns rc=127 into `ToolError("The install failed: pkexec must be setuid root")` — a confusing internal message rather than "installation is disabled in this mode". S7 unregisters `apt_install` in Strict Offline, but **S1 alone breaks it in Maintenance mode too.**

**C4 — S1 without S2 is a total outage that reports itself healthy.** `RestrictAddressFamilies=AF_UNIX AF_NETLINK` severs TCP to Ollama. `main.py:87-90` catches `LLMError` and only *logs a warning*, so the unit comes up `active (running)`, owns its D-Bus name, accepts tasks — and every task fails. My roadmap listed S1 and S2 as separate rows; **they must land in the same change.** Add a startup self-test that refuses to start on `EAFNOSUPPORT` so the failure is loud rather than per-task.

## 3b. Correction to the research report's Open Question 4

I flagged `kernel.apparmor_restrict_unprivileged_userns = 0` on this host as a **divergence from the Ubuntu 24.04 default** that must not be assumed portable. That was wrong, and the verifier refuted it with better evidence than the original finding:

```
$ cat /etc/sysctl.d/20-apparmor-mint.conf
# Allow the use of unprivileged user namespaces
# See https://github.com/linuxmint/mint22-beta/issues/82.
kernel.apparmor_restrict_unprivileged_userns = 0

$ dpkg -S /etc/sysctl.d/20-apparmor-mint.conf
ubuntu-system-adjustments: /etc/sysctl.d/20-apparmor-mint.conf
$ dpkg -V ubuntu-system-adjustments      # no output — package unmodified
```

**Mint 22.x ships this override deliberately**, in a packaged file, overriding Ubuntu's `1`. The host is stock. Since Peppermint targets Mint specifically, bubblewrap/userns layers are portable on the target platform. Open Question 4 is answered, not open — though `PrivateUsers=true` and an install-time probe remain sensible belt-and-braces.

---

## 4. Lane ownership and collision map

> The synthesis agent that was to produce this stalled twice (a hung API call, ~15 min of zero output). Written by hand from the verified findings.

| File | Codex (install-path) | Milestone 0 (policy) | Status |
| --- | --- | --- | --- |
| `.venv/` | **owns** — rebuild | — | clean |
| `__editable__*_finder.py` | **owns** | — | clean |
| `data/minty.desktop`, `data/minty-applet.desktop` | **owns** | — | clean |
| `~/.local/share/applications/`, `~/.config/autostart/` | **owns** | — | clean |
| `pyproject.toml` | **owns** — packaging | — | clean |
| `minty.egg-info/` | **owns** — delete | — | clean |
| `data/minty-daemon.service` | `ExecStart` path, dead `After=`/`PartOf=` | sandbox stanza, `Environment=` | **COLLISION** |
| `scripts/install.sh` | sed materialization, venv rebuild, step-1 `$1` bug | Ollama probe, Strict-Offline default, tarball pinning | **COLLISION** |
| `scripts/setup-ollama.sh` | unit heredoc, `unzstd` check, tag parsing | pin + checksum + signature | **COLLISION** |
| `minty/*.py`, `tests/` | — | **owns** | clean |

**The two collisions are orthogonal but order-dependent.** In `minty-daemon.service` Codex edits `ExecStart=` and the `[Unit]` dependency lines; M0 adds `[Service]` hardening directives. Different lines, mergeable — *provided Codex lands first*, because M0's stanza is written against a unit whose `ExecStart` actually resolves.

In `install.sh` the same holds with one exception: **step 3's Ollama probe belongs to M0, not Codex.** It hard-codes `curl 127.0.0.1:11434`, which becomes unsatisfiable under the UDS design. If Codex "aligns" it, that is a policy change wearing a cleanup costume.

### Sequencing

**Codex's lane must land first — it is the critical path, not a follow-up.** Milestone 0's acceptance gate is "14 enforcement tests green under the hardened unit." That gate is unreachable today: the unit cannot start, so offline enforcement cannot be validated at all. Order:

1. **Codex:** rebuild venv → repair templates/installer → verify daemon starts and `minty health` responds
2. **Checkpoint:** `.venv/bin/minty --help` exits 0; unit reaches `active`; `import minty` works from `$HOME`
3. **M0:** hardening stanza (with C1–C4 applied) on a unit that is known-good

Doing M0 first means debugging seccomp against a daemon that was never going to start.

### Boundary risks — edits that look like cleanup but are policy

For Codex, these are **out of lane** despite appearing in the files it owns:

- Adding, removing or reordering `Environment=` lines in either unit
- Changing `ExecStart` to anything but a path fix (e.g. inserting a wrapper)
- `RestrictAddressFamilies`, `NoNewPrivileges`, `ProtectSystem`, `CapabilityBoundingSet`, `PrivateTmp`
- Touching `After=`/`Wants=`/`PartOf=` on **ollama.service** (M0 may move it into a private netns)
- Step 3's Ollama reachability probe in `install.sh`
- Ollama tarball pinning/checksums — supply-chain policy, M0 owns it

**In lane and safe:** the `ExecStart` path, the sed expressions and their quoting, venv rebuild, `.desktop` correctness, the step-1 `$1` bug, `unzstd` preflight, backup-before-overwrite, idempotency, uninstall script.

### Rename adjacency

One item cannot be fixed without touching identity, and should be **deferred**: the Cinnamon keybinding is matched by exact command string (`keybinding.py`, `install.sh` step 7), so any path change creates a *second* shortcut slot rather than updating the first. The fix that avoids duplicate slots is entangled with the eventual `minty`→`peppermint` command rename. Codex should fix the path and **note the orphaned slot**, not redesign the matching.

Same for `~/.local/share/applications/minty.desktop` and `~/.config/autostart/minty-applet.desktop`: install.sh never removes old-id files, so the rename milestone must delete them explicitly or users get two menu entries, one dead.

---

## 5. Notable non-blockers worth Codex's attention

- **`install.sh:14` — `missing+=("$1")` references the script's `$1`, not the loop variable.** Under `set -u` the missing-typelib branch dies on `unbound variable`, so the remediation line (`sudo apt install …`) is dead code. Fix: `missing+=("$typelib")`.
- **Unquoted `Exec=`/`ExecStart=`** — a space in the checkout path produces a broken unit and a `.desktop` that silently vanishes.
- **sed metacharacters** — `&`, `|`, `\` in the repo path corrupt the substitution; the `>` redirect has already truncated the destination when sed fails.
- **Installer clobbers manual edits** — the installed `ollama.service` carries an `OLLAMA_MODELS=` line the script never emits, proving hand-edits exist that a re-run silently reverts. Back up or use drop-ins.
- **No `.git`, no `.gitignore`** — the rename has no rollback, and `.venv/`, `minty.egg-info/`, `__pycache__/` are untracked clutter. **Initialise version control before any of this work starts.**
- **`pyproject.toml` `include = ["minty*"]`** — produces an empty wheel the moment the package directory is renamed. A rename-milestone landmine.
- **~43 test fixtures hardcode `/home/jesse`** — the suite is not portable to another developer.

---

## 6. Method note

Two synthesis agents stalled on hung API calls (~15 min of zero writes). A workflow resume did **not** replay from cache as documented — it relaunched 5 fresh audit agents (`started` 12 → 17, `result` stayed 10), so it was stopped and the banked results were read directly from `journal.jsonl`. The audit and verification stages completed normally and are the basis of everything above; only the two synthesis agents were replaced by hand.
