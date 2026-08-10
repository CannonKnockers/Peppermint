# Peppermint — Architecture & Offline-Enforcement Research

**Phase:** Research and planning. No rewrite performed. No identifiers renamed.
**Repository inspected:** `/home/jesse/Documents/peppermint` (package still named `minty`, 7,221 lines across 51 files, not under version control).
**Baseline verified:** `727 passed in 1.00s`.

**Host measured for every empirical claim below:**

| Component | Measured value |
| --- | --- |
| Distribution | Linux Mint 22.3 "Zena" (Ubuntu 24.04 noble base) |
| Desktop | Cinnamon 6.6.9, X11 session |
| Kernel | 7.0.0-28-generic |
| systemd | 255 (255.4-1ubuntu8.16), cgroup v2 |
| LSMs active | `lockdown, capability, landlock, yama, apparmor, ima, evm` |
| Python | 3.12 |
| Sandbox tooling present | `bwrap`, `unshare`, `nft`, `iptables`, `systemd-run`, `flatpak` |
| GTK 3 + XApp typelibs | present |
| GTK 4 / libadwaita | runtime libs installed (`libgtk-4-1` 4.14.5, `libadwaita-1-0` 1.5.0), **GObject-introspection typelibs absent** (`gir1.2-gtk-4.0` not installed) |
| Ollama | 0.32.7, user service, `OLLAMA_HOST=127.0.0.1:11434`, model `qwen3:8b` |

**Evidence labels used throughout:**

- **[CONFIRMED]** — executed on this machine during this audit; command and result reproducible.
- **[CODE]** — read directly from the source; not executed.
- **[INFERRED]** — reasoned from code or documentation; not directly tested.
- **[RECOMMENDATION]** — proposed design, not current behaviour.

---

## 1. Executive summary

Peppermint today is a **local-first** application. It is **not** an offline-enforced one, and the gap between those two phrases is the central finding of this audit.

The existing safety architecture is genuinely good and should be preserved almost intact. Risk classification lives in the tool layer rather than in the model's judgement; approvals are bound to one exact call by a token, expire, and re-verify the on-disk target before acting; undo records are claimed atomically so a change cannot be reverted twice; crash recovery deliberately refuses to replay a mutation it cannot prove finished. A 727-case safety corpus encodes each of these as a regression test. That is a stronger foundation than most projects in this category have.

What does not hold is the README's first substantive claim: *"Minty runs fully on your computer. It sends nothing to the internet."* Nothing in the system enforces this. Five independent egress paths were confirmed by execution:

1. **`PEPPERMINT_OLLAMA_HOST` / `MINTY_OLLAMA_HOST` accepts any URL without validation** — setting it to `http://192.168.1.50:11434` silently redirects every prompt, file excerpt and tool output to another machine. **[CONFIRMED]**
2. **`HTTP_PROXY` diverts even loopback traffic off-box.** A listener on port 18899 received `GET http://127.0.0.1:11434/api/tags` from the Ollama client, because `httpx` runs with `trust_env=True`. Configuring Ollama as loopback-only does not prevent this. **[CONFIRMED]**
3. **`getent hosts <name>` is classified SAFE and runs without approval** — a DNS resolution is both network egress and a serviceable exfiltration channel. **[CONFIRMED]**
4. **`git remote update` is classified SAFE** — it fetches from every configured remote. `git remote add origin http://…` is also SAFE. **[CONFIRMED]**
5. **`nmcli device wifi connect SSID password …`, `nmcli connection add`, `nmcli connection up`, `nmcli radio wifi off` are all classified SAFE** — the classifier checks only the first subcommand word. These reconfigure networking without approval, and the Wi-Fi password is written verbatim into the step log and the database. **[CONFIRMED]**

Two further findings defeat the command allowlist as a category, independent of which commands are on it. `run_shell` executes through `/bin/bash -lc`, inheriting the daemon's full environment:

6. **An exported bash function overrides an allowlisted command.** With `BASH_FUNC_ls%%` set in the environment, `run_shell("ls")` — classified SAFE, no approval — executed attacker-controlled code. **[CONFIRMED]**
7. **`BASH_ENV` executes arbitrary code on every single invocation**, including every read-only one. `run_shell("echo hello")` ran the injected script first. **[CONFIRMED]**

The strategic conclusion: **a command allowlist is a usability filter, not a security boundary.** It classifies text that a different parser (bash, after expansion, profile sourcing and function import) subsequently re-interprets. Any parser differential is a bypass, and two were found without difficulty. Peppermint needs an OS-enforced boundary underneath the allowlist, and the allowlist should be retained above it for *approval ergonomics* rather than relied upon for containment.

**The good news is that a strong boundary is available, unprivileged, and was proven to work on this exact machine.** The recommended primitive is `RestrictAddressFamilies=AF_UNIX` on the daemon's systemd **user** unit:

- The daemon becomes **structurally incapable of creating an IPv4 or IPv6 socket** — `socket(AF_INET)` fails with `EAFNOSUPPORT` at the kernel. **[CONFIRMED]**
- The restriction is a seccomp filter, and **seccomp is inherited across `fork`/`exec`**. A grandchild launched through `bash -lc` also could not create an IP socket, and `getent hosts` DNS failed with exit 2. This closes the entire subprocess-egress class — `curl`, `wget`, `python`, `apt`, `ssh` — in one directive, including the two bash-environment escapes above. **[CONFIRMED]**
- **The daemon can still reach the local model.** With `RestrictAddressFamilies=AF_UNIX` and no IP stack whatsoever, a client using `ollama.Client(host=…, transport=httpx.HTTPTransport(uds=…))` over a Unix-domain socket successfully listed `['qwen3:8b']`. A deliberately hostile `HTTP_PROXY` set in the same process had no effect. **[CONFIRMED]**

One widely-recommended mechanism must be explicitly rejected: **`IPAddressDeny=any` is silently ignored in systemd user units on this system.** A probe unit with `IPAddressDeny=any IPAddressAllow=localhost` connected to `1.1.1.1:443` successfully, and systemd reported the unit as `success` with no warning. The user manager's delegated controllers are only `cpu memory pids` — no BPF attachment is possible unprivileged. Any design resting on `IPAddressDeny` in a user unit **fails open and says nothing**. **[CONFIRMED]**

The recommended first milestone (Section 11 / Milestone 0) is therefore not a rewrite. It is roughly 400 lines: a policy module, a Unix-socket model transport, a hardened unit file, environment sanitisation, closure of the five confirmed classifier holes, a visible policy indicator, and an adversarial test suite that proves IPv4, IPv6, DNS, proxy, subprocess and remote-model egress are all blocked. The existing application keeps working throughout.

---

## 2. Current architecture

### 2.1 Process and trust boundaries

```
 ┌──────────────────────── user session (uid 1000, no privilege separation) ────────────────────────┐
 │                                                                                                  │
 │   minty CLI            minty-window (GTK 3)            XApp.StatusIcon (panel)                   │
 │   cli.py               ui/app.py, window.py            ui/tray.py                                │
 │       │                        │                              │                                  │
 │       └────────────┬───────────┴──────────────┬───────────────┘                                  │
 │                    │  D-Bus SESSION bus, org.minty.Daemon                                        │
 │                    │  AddTask ListTasks GetTask Confirm Answer Chat Cancel Undo Revert Health    │
 │                    │  signal TaskUpdated(id, status)                                             │
 │                    │  ── NO caller authentication, NO polkit, NO rate limit ──                   │
 │                    v                                                                             │
 │   ┌──────────────────────────── minty-daemon (single process) ─────────────────────────────┐     │
 │   │  GLib main loop (D-Bus)          │  worker thread "minty-agent" (queue.Queue, depth 1) │     │
 │   │  main.py::Daemon                 │  main.py::_work -> agent.Agent.run                  │     │
 │   │                                                                                        │     │
 │   │   Agent loop (agent.py)   <-->  LLM (llm.py)  --HTTP/httpx-->  Ollama 127.0.0.1:11434   │     │
 │   │        │                                                                               │     │
 │   │        │ tools.call(name, args, Context)                                                │     │
 │   │        v                                                                               │     │
 │   │   Tool registry (tools/registry.py) — 21 tools                                          │     │
 │   │        │                                                                               │     │
 │   │        ├─ safety.classify_* ──> Verdict(SAFE|RISKY)   [the ONLY containment today]      │     │
 │   │        ├─ approval.make_token / verify                                                  │     │
 │   │        └─ subprocess.run(...)  ── inherits FULL os.environ, /bin/bash -lc ──> anything  │     │
 │   │                                                                                        │     │
 │   │   Database (db.py) — SQLite WAL, schema v3                                              │     │
 │   │   ~/.local/share/minty/minty.db   mode 0644   tasks, steps, messages, confirmations, undo│    │
 │   │   ~/.local/share/minty/minty.log  mode 0664                                             │     │
 │   └────────────────────────────────────────────────────────────────────────────────────────┘     │
 └──────────────────────────────────────────────────────────────────────────────────────────────────┘

 Trust boundary count today: 1 (the safety classifier, in-process, advisory).
 OS-enforced boundaries: 0.
```

### 2.2 Control flow for a risky action

```
model emits tool_call
  └─> agent._execute -> tools.call -> tool fn -> returns Confirm(description, reason)      [tools/*.py]
        └─> agent._pause_for_confirm                                                        [agent.py:380]
              ├─ db.add_step(..., risk="risky", status="pending", mutating=True)
              ├─ db.add_confirmation(token=sha256(task_id|tool|args|POLICY_VERSION)[:32],
              │                      expires_at=now+900s,
              │                      fingerprint=lstat(resolved paths))
              └─ status = awaiting-confirmation, notify user
   user clicks Allow (UI / CLI / panel menu)
        └─> D-Bus Confirm(id, true) -> job queue -> agent.resume_after_confirm              [agent.py:317]
              ├─ db.resolve_confirmation(...)  atomic UPDATE ... WHERE resolved=0   → first click only
              ├─ approval.verify: token match, not expired, target unchanged        → else refuse
              ├─ db.update_step(status="executing")                                 → crash-visible
              └─ tools.call(..., approved=True)  → real side effect
```

This design is sound and is the part of the system most worth preserving.

### 2.3 Storage

| Artefact | Location | Mode | Contents |
| --- | --- | --- | --- |
| Task DB | `~/.local/share/minty/minty.db` | **0644** | ideas, **full model message history**, tool args, tool output (file contents, command output), approvals, undo values incl. **previous file contents** |
| Log | `~/.local/share/minty/minty.log` | **0664** | task ideas, errors, approval refusals |
| Config | none — environment variables only | — | `MINTY_*` |
| Undo values | `undo` table, inline | 0644 | prior gsettings values, prior file bodies, prior paths |

Both files are **world-readable**. On a multi-user machine every local account can read the user's prompts, the file contents Peppermint has read, and the pre-change bodies of edited files. **[CONFIRMED]** — `ls -l` output above.

---

## 3. Offline threat model

### 3.1 Assets

| Asset | Where | Sensitivity |
| --- | --- | --- |
| User intent / prompts | `messages`, `tasks` | high — reveals activity, names, projects |
| File contents pulled into context | `steps.output`, `messages` | high — arbitrary home-directory data |
| Command output | `steps.output` | high — may include `journalctl`, process lists |
| Undo payloads | `undo.old_value` | high — prior file bodies verbatim |
| Credentials adjacent to the workflow | Wi-Fi passwords via `nmcli`, secrets in read files | critical |
| The fact of usage | any outbound packet | moderate — timing/metadata |

### 3.2 Adversaries

| # | Adversary | Capability | Currently mitigated? |
| --- | --- | --- | --- |
| A1 | **Prompt injection via processed content** — a file, filename, or command output that contains instructions | Steers tool selection; can request egress commands | Partially. Classifier gates *most* egress commands behind approval; **`getent`, `git remote update`, `nmcli` are not gated**. **[CONFIRMED]** |
| A2 | **Model error / non-adversarial misfire** | Wrong tool, repeated tool, unrelated tool | Yes — loop guard `MAX_SAME_CALL`, undo records, README documents observed cases |
| A3 | **Local unprivileged user on the same host** | Reads 0644 DB/log; calls the D-Bus API with no authentication | **No.** Any session process can `AddTask`, `Confirm`, `Revert` |
| A4 | **Malicious/compromised environment** — anything that can set env vars for the user service | `BASH_ENV`, `BASH_FUNC_*`, `HTTP_PROXY`, `MINTY_OLLAMA_HOST` | **No.** All four confirmed exploitable |
| A5 | **Supply chain** — `ollama` PyPI package, its `httpx` dependency, Ollama server itself | Arbitrary outbound HTTP from inside the daemon | **No.** Nothing constrains the daemon's own sockets |
| A6 | **Curious/naive user** | Follows a blog post, sets a remote host, believes README | **No.** No validation, no warning, no indicator |
| A7 | **Physical/offline attacker with disk access** | Reads DB at rest | No — no encryption (arguably out of scope; note explicitly) |

### 3.3 The trust-boundary error

The current model is:

> *The model proposes; the classifier disposes; bash executes.*

The flaw is that **the classifier and bash are different parsers over the same string**. `safety.classify_command` tokenises with `shlex` and reasons about pre-expansion text. `/bin/bash -lc` then sources profile files, imports exported functions from the environment, expands variables, and executes. Anything the classifier cannot see — an exported function named `ls`, a `BASH_ENV` file, a second-level subcommand it does not parse — is outside its authority but inside bash's. Both classes were exploited during this audit.

**Design implication:** containment must be enforced by a component that does not parse strings. The kernel, via seccomp and namespaces, is such a component.

---

## 4. Network-egress inventory

Every path by which data can leave the machine during **normal runtime operation**. Setup-time downloads are inventoried separately in §4.2.

| # | Path | Trigger | Gate today | Status |
| --- | --- | --- | --- | --- |
| E1 | `MINTY_OLLAMA_HOST` / future `PEPPERMINT_OLLAMA_HOST` → remote Ollama | env var at daemon start | **none — no validation** | **[CONFIRMED]** `config.OLLAMA_HOST = http://192.168.1.50:11434` accepted |
| E2 | `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` → httpx proxies **loopback** requests | env var | **none** — `trust_env=True` | **[CONFIRMED]** proxy received `GET http://127.0.0.1:11434/api/tags` |
| E3 | `getent hosts` / `getent ahosts` / `ahostsv6` → DNS query | model tool call | **classified SAFE, no approval** | **[CONFIRMED]** |
| E4 | `git remote update` → fetches all remotes | model tool call | **classified SAFE** | **[CONFIRMED]** |
| E5 | `nmcli device wifi connect` / `connection add` / `connection up` / `radio` | model tool call | **classified SAFE** | **[CONFIRMED]** — also writes Wi-Fi password to DB |
| E6 | Exported bash function overriding an allowlisted name | env var + any `run_shell` | **none** — bypasses classifier entirely | **[CONFIRMED]** |
| E7 | `BASH_ENV` script sourced on every `-lc` | env var + any `run_shell` | **none** | **[CONFIRMED]** |
| E8 | `run_shell` with `curl`/`wget`/`ssh`/`scp`/`rsync`/`nc`/`aria2c`/`yt-dlp` | model tool call | approval only | **[CODE]** by design; approval is the only barrier |
| E9 | `apt_install` → `pkexec apt-get install` → repository fetch | model tool call | approval + polkit password | **[CODE]** |
| E10 | Ollama server's own egress (`pull`, `push`, `signin`, version check) | outside daemon control | **none** | **[INFERRED]** — server runs unrestricted in its own unit |
| E11 | `journalctl` / `ps` output → into model context → to E1/E2 if model is remote | model tool call | classified SAFE | **[CONFIRMED]** classification; egress conditional |
| E12 | Notification body / D-Bus → local only | — | n/a | **[CODE]** no egress |
| E13 | `xdg-mime default …` — not egress, but an unapproved persistent mutation | model tool call | **classified SAFE** | **[CONFIRMED]** |

**`RestrictAddressFamilies=AF_UNIX` closes E1–E8 and E11 in one move** (E1 because a remote host cannot be dialled at all; E2 because there is no IP socket to proxy through). E9 becomes structurally impossible in Strict Offline, which is the required behaviour. E10 requires a separate unit-level control (§5.4). E13 is a classifier fix.

### 4.2 Setup-time network use (must remain separable)

| Script | Network action | Assessment |
| --- | --- | --- |
| `scripts/setup-ollama.sh` | `curl api.github.com` for latest tag; downloads `ollama-linux-amd64.tar.zst` from GitHub; `ollama pull qwen3:8b` (~5 GB) | Legitimate setup-time use. **Must not** run under the normal runtime policy, and must not be reachable from the agent loop. |
| `scripts/install.sh` | `pip install -e .` (fetches `ollama` from PyPI unless cached); `curl 127.0.0.1:11434` health check | Same. `pip install` is the only genuinely remote step. |

Neither script pins a version or verifies a checksum/signature of the Ollama tarball. **[CODE]** — release blocker for a security-positioned product (§10, B4).

---

## 5. Recommended offline enforcement design

### 5.1 Mechanism evaluation — measured, not assumed

| Mechanism | Works in a **user** unit, unprivileged? | Verdict |
| --- | --- | --- |
| **`RestrictAddressFamilies=AF_UNIX`** (seccomp) | **Yes** — `AF_INET`/`AF_INET6` fail `EAFNOSUPPORT`; **inherited by all descendants incl. `bash -lc` grandchildren**; `getent` DNS fails | **[CONFIRMED] — PRIMARY MECHANISM** |
| **`PrivateNetwork=yes`** (netns) | **Yes** — external IPv4, IPv6 and DNS all blocked; but also cuts host loopback, so Ollama-over-TCP is unreachable | **[CONFIRMED] — secondary layer** |
| **`IPAddressDeny=any` / `IPAddressAllow`** (BPF) | **NO — silently ignored.** Probe reached `1.1.1.1:443`; unit reported `success`; delegated controllers are only `cpu memory pids` | **[CONFIRMED] — DO NOT RELY ON. Fails open, silently** |
| **`JoinsNamespaceOf=`** | Not settable via `systemd-run -p` (silently drops `PrivateNetwork` with it); `[Unit]`-section only | **[CONFIRMED]** — usable only in real unit files; treat as unproven for this design and prefer the UDS bridge |
| **bubblewrap `--unshare-net`** | **Yes** — blocks external IPv4/IPv6/DNS **and** host loopback (fresh `lo`); `bwrap` present | **[CONFIRMED] — per-subprocess layer** |
| **Landlock** | Kernel supports it (`landlock` in active LSM list). ABI varies by Mint release — filesystem rules on all supported versions; **network (TCP connect/bind) rules require kernel ≥ 6.7**, i.e. Mint 22.x only | **[CONFIRMED]** presence; **[INFERRED]** ABI mapping — use for *filesystem* scoping, do not depend on for network |
| **seccomp via `SystemCallFilter=`** | Yes | Useful supplement (`~@mount @swap @reboot`) |
| **AppArmor profile** | Enforcing, profiles present. Requires root to install | **[INFERRED]** — good for packaged releases, not for a no-root install |
| **nftables / firewall** | Requires root; machine-wide; hostile to a user-scoped app | Reject for default install |
| **`unshare -rn`** | Works here (`kernel.apparmor_restrict_unprivileged_userns = 0`) — **but Ubuntu 24.04 ships this sysctl as `1`, which blocks unprivileged userns**; this host has been changed from the default | **[CONFIRMED]** on this host; **must not be assumed portable**. Prefer `bwrap` (ships an AppArmor profile permitting userns) |
| **Capability removal** (`CapabilityBoundingSet=`, `NoNewPrivileges=yes`) | Yes | Cheap, include it |
| **Environment sanitisation** | Application-level | **Mandatory** — the only defence against E6/E7 that also protects non-sandboxed paths |

### 5.2 The architecture

The insight that makes strict offline compatible with a working local model: **Unix-domain sockets are filesystem objects and are unaffected by network namespaces or by `RestrictAddressFamilies=AF_UNIX`.** The daemon can therefore have *no IP stack at all* and still talk to Ollama.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ peppermint-model-bridge.service        (user unit)                              │
│   systemd-socket-proxyd  %t/peppermint/ollama.sock  ->  127.0.0.1:11434         │
│   (or ollama.service directly, if a future Ollama gains native UDS support)     │
└─────────────────────────────────────────────────────────────────────────────────┘
                                   ▲  AF_UNIX only, mode 0600, in %t (XDG_RUNTIME_DIR)
                                   │
┌──────────────────────────────────┴──────────────────────────────────────────────┐
│ peppermint-daemon.service         (user unit)   ── STRICT OFFLINE ──             │
│   RestrictAddressFamilies=AF_UNIX AF_NETLINK    <-- no IPv4/IPv6 socket exists   │
│   NoNewPrivileges=yes                                                            │
│   CapabilityBoundingSet=                                                         │
│   SystemCallFilter=@system-service ~@mount ~@swap ~@reboot ~@module              │
│   SystemCallArchitectures=native                                                 │
│   ProtectSystem=strict  PrivateTmp=yes                                           │
│   ReadOnlyPaths=%h   ReadWritePaths=%h/.local/share/peppermint                   │
│   # CORRECTED: `ProtectHome=read-write` is NOT a valid systemd value — systemd    │
│   # rejects the unit ("Invalid ProtectHome setting"). And ProtectSystem=strict    │
│   # does NOT make $HOME read-only in a USER unit (measured: home stays rw), so    │
│   # ReadOnlyPaths=%h is required for real confinement. See install-path-audit.md │
│   ProtectKernelTunables=yes ProtectKernelModules=yes ProtectControlGroups=yes    │
│   LockPersonality=yes  MemoryDenyWriteExecute=no   (llama needs JIT pages)       │
│   Environment=PEPPERMINT_MODE=strict-offline                                     │
│                                                                                  │
│   ┌── Layer 1: unit sandbox (above) — inherited by EVERY subprocess ──┐          │
│   │  ┌── Layer 2: policy module — validates host, rejects non-UDS ──┐ │          │
│   │  │  ┌── Layer 3: env sanitiser — strips BASH_ENV, BASH_FUNC_*, │ │ │          │
│   │  │  │      *_PROXY, LD_*, PYTHON*; run_shell uses bash --noprofile│ │        │
│   │  │  │      --norc, never -l                                    │ │ │          │
│   │  │  │  ┌── Layer 4: bwrap --unshare-net per subprocess ──┐     │ │ │          │
│   │  │  │  │   ┌── Layer 5: safety classifier (approval UX) ─┐│     │ │ │          │
└───┴──┴──┴──┴───┴─────────────────────────────────────────────┴┴─────┴─┴─┴────────┘
```

Layer 5 — the existing classifier — is **demoted from "the boundary" to "the approval-ergonomics layer"**. It keeps its full test corpus and its job of deciding what to ask the user about. It is simply no longer the thing standing between the model and the network.

### 5.3 Operating modes

Modes are a **property of the systemd unit**, not a runtime variable. Changing mode requires writing a drop-in and restarting the unit — an explicit, auditable, user-initiated act that the daemon cannot perform on itself.

| Mode | `RestrictAddressFamilies` | Network reach | apt / network shell | Default? |
| --- | --- | --- | --- | --- |
| **Strict Offline** | `AF_UNIX AF_NETLINK` | Unix sockets only; model via UDS bridge | **Unavailable — tools not registered** | **Yes, after install** |
| **Local Network** | `+ AF_INET AF_INET6`, plus `bwrap` egress filter and an explicit CIDR allowlist checked in-process | Approved LAN CIDRs only (e.g. a LAN Ollama, a NAS) | apt still unavailable | No |
| **Maintenance** | full | Full, **time-boxed** (default 30 min, auto-reverts via systemd timer) | Available, approval-gated | No |
| **Developer** | full | Full, persistent | Available | No — requires a `--i-understand` flag and shows a permanent red banner |

Required properties:

- Strict Offline is the post-install default, written by the installer.
- Ollama is loopback-only in Strict Offline; the daemon never dials TCP at all.
- **A non-loopback `PEPPERMINT_OLLAMA_HOST` (or legacy `MINTY_OLLAMA_HOST`) is rejected at startup in Strict Offline** — the daemon refuses to start and states why, rather than silently degrading.
- In Strict Offline, `apt_install` and network-capable shell verbs are **not registered in the tool registry at all** — the model is not shown a capability that cannot exist. This is materially different from confirmation-gating: the model cannot propose it, so the user is never asked to approve something the system would refuse.
- Every mode change writes an append-only audit record (`mode_changes` table: from, to, timestamp, actor, reason, unit-file hash).
- The effective policy is displayed continuously in the UI, sourced from **a live probe** (`socket(AF_INET)` attempt at startup + a readback of the unit's effective properties), not from a config variable that could disagree with reality.

### 5.4 Ollama's own egress

The daemon being sealed does not seal the model server. `ollama.service` should receive its own drop-in in Strict Offline:

```
# ~/.config/systemd/user/ollama.service.d/peppermint-offline.conf   [RECOMMENDATION]
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_NOPRUNE=1"
RestrictAddressFamilies=AF_UNIX AF_INET       # loopback listener only
IPAddressDeny=any                              # documented as non-enforcing in user units;
                                               # present for defence-in-depth if run system-wide
```

Because `IPAddressDeny` does not enforce here, the honest control for `ollama pull` reaching the registry is **Maintenance mode plus user intent** — the report must not claim otherwise. A stronger option for Strict Offline is to run Ollama itself with `PrivateNetwork=yes` and reach it exclusively through the UDS bridge; this was proven viable (**[CONFIRMED]** bwrap netns test shows loopback inside a private netns is fully functional and isolated), at the cost of making `ollama pull` impossible without a mode change — which is the correct trade for Strict Offline.

### 5.5 Language: "local-first" vs "OS-enforced offline"

The README must stop saying *"It sends nothing to the internet."* Proposed replacements:

- **Local-first** — "Peppermint uses a model running on your computer. No feature is designed to contact a remote service." (Describes intent. True today.)
- **OS-enforced offline (Strict Offline mode)** — "In Strict Offline mode the Linux kernel refuses to create any internet socket for Peppermint or for any program it starts. This is enforced by the operating system, not by Peppermint's own checks." (Describes a mechanism. True only after Milestone 0.)
- **Never claim "100% offline."** State the residual risks explicitly: the Ollama server is a separate process under its own policy; a user with a shell can change the mode; kernel and systemd bugs are out of scope; nothing here protects data at rest.

### 5.6 Offline installation bundle

```
peppermint-offline-bundle-<version>/
  peppermint-<version>-py3-none-any.whl
  wheels/                      # ollama, httpx, h11, certifi, … full transitive closure
  ollama-linux-amd64.tar.zst   # pinned version + .sha256 + upstream signature
  models/
    qwen3-8b.gguf              # or an `ollama create`-able Modelfile + blobs
    MANIFEST.json              # name, digest, params, licence, provenance
  install-offline.sh           # --no-index --find-links=wheels ; verifies every digest
  CHECKSUMS.sha256
```

Model import workflow: `peppermint model import <path>` verifies the digest against `MANIFEST.json`, copies into `OLLAMA_MODELS`, registers via the local Ollama API over the UDS bridge, and records the import in the audit log. The bundle build itself is an online, developer-side operation, entirely outside the runtime policy.

### 5.7 Required adversarial tests

New file `tests/test_offline_enforcement.py`. Tests marked `@pytest.mark.enforcement` run only under the hardened unit (in CI via `systemd-run --user -p RestrictAddressFamilies=…`), and **must fail loudly if the sandbox is absent** rather than skip silently.

| Test | Asserts |
| --- | --- |
| `test_ipv4_socket_cannot_be_created` | `socket(AF_INET)` raises `EAFNOSUPPORT` |
| `test_ipv6_socket_cannot_be_created` | `socket(AF_INET6)` raises `EAFNOSUPPORT` |
| `test_dns_resolution_fails` | `getaddrinfo` and `getent hosts` both fail |
| `test_proxy_env_cannot_divert_model_traffic` | with `HTTP_PROXY` set to a live listener, listener receives nothing |
| `test_subprocess_inherits_the_restriction` | `bash -lc 'python3 -c "socket(AF_INET)"'` fails |
| `test_curl_and_wget_cannot_reach_the_network` | non-zero exit, no bytes transferred |
| `test_remote_ollama_host_is_rejected_at_startup` | non-loopback host → daemon refuses to start, names the variable |
| `test_loopback_ollama_host_is_accepted` | `127.0.0.1`, `::1`, `localhost`, UDS path all accepted |
| `test_apt_install_is_not_registered_in_strict_offline` | tool name absent from `tools.tool_names()` |
| `test_network_shell_verbs_are_not_offered_in_strict_offline` | schema/description advertises the restriction |
| `test_exported_bash_function_cannot_override_a_safe_command` | env sanitiser strips `BASH_FUNC_*`; `run_shell("ls")` lists files |
| `test_bash_env_is_not_honoured` | injected script does not execute |
| `test_mode_change_requires_explicit_action_and_is_audited` | daemon cannot self-promote; `mode_changes` row written |
| `test_ui_policy_indicator_matches_a_live_probe` | displayed state derives from an actual socket attempt |

Plus classifier regressions in the existing corpus for every confirmed hole: `getent hosts`, `getent ahosts*`, `git remote update`, `git remote add`, `nmcli device wifi connect`, `nmcli connection add|up`, `nmcli radio`, `xdg-mime default`.

---

## 6. Linux Mint capability map and risk matrix

Classification: **R** read-only · **RM** reversible mutation (undo record required) · **C** confirmation-required · **P** privileged (polkit) · **X** prohibited.

| Subsystem | Native interface (preferred over shell) | Class | Notes / offline compatibility |
| --- | --- | --- | --- |
| **Cinnamon settings** | GSettings via `Gio.Settings` (in-process, typed) | RM | Replaces `subprocess gsettings`. Typed get/set, schema+key validation, automatic old-value capture. Offline-safe. |
| Cinnamon theme / applets | `org.Cinnamon` D-Bus | RM | Applet enable/disable reversible; applet *install* is a download → not in Strict Offline |
| **Nemo file ops** | `org.freedesktop.FileManager1` (`ShowItems`, `ShowFolders`) | R | Present on this host **[CONFIRMED]**. Opening a folder is read-only from Peppermint's view |
| File deletion | `gio trash` (current) → `Gio.File.trash()` | C | Already correct: never unlinks. Keep. |
| **XApp status icon** | `XApp.StatusIcon` | R | Already used. Extend to carry the policy indicator |
| **Notifications** | `org.freedesktop.Notifications` | R | Present. Add *actionable* buttons (Approve / Deny / Open) — currently only "Open" |
| **Portals** | `org.freedesktop.portal.Desktop`, `…portal.Documents`, `impl.portal.xapp` | R/C | Present **[CONFIRMED]**. Use `Screenshot`, `FileChooser`, `Background` portals instead of ad-hoc commands |
| **Keyboard shortcuts** | Cinnamon custom-keybinding GSettings (existing `keybinding.py`) | C | Already confirmation-gated. Add conflict detection before writing |
| **Audio** | `org.cinnamon.SettingsDaemon` / PulseAudio via `pactl` | RM | Volume/default-sink reversible. `pactl set-*` currently correctly RISKY |
| **Display** | `org.cinnamon.Muffin.DisplayConfig` | C | Present **[CONFIRMED]**. Typed adapter far safer than `xrandr`; resolution changes need confirm + auto-revert timer |
| **Power** | `org.cinnamon.SettingsDaemon.Power`, UPower | R / RM | Battery read R; power-profile change RM |
| **Bluetooth** | BlueZ `org.bluez` | C | Pairing is C; **not** offline-relevant but is a radio |
| **Printers** | CUPS via `cups` D-Bus / IPP on loopback | R / C | Printer *discovery* is network activity — restrict to R in Strict Offline |
| **Storage** | UDisks2 `org.freedesktop.UDisks2` | R / C / **X** | Mount/unmount C; **format/partition X** |
| **Network status** | NetworkManager D-Bus, read-only properties | R | Read state R. **All `nmcli` mutations must move to C — currently SAFE [CONFIRMED HOLE]** |
| **Applications / MIME** | `Gio.AppInfo`, `xdg-mime` | R / **RM** | Listing R. **`xdg-mime default` must become RM with undo — currently SAFE [CONFIRMED HOLE]** |
| **Flatpak metadata** | `flatpak list/info` (local), `org.freedesktop.Flatpak` | R | Read-only local queries fine offline; `flatpak install` is a download → X in Strict Offline |
| **apt metadata** | `apt-cache`, `dpkg-query` (local index) | R | Offline-safe; no network |
| **apt install** | `pkexec apt-get` | **P / X** | P in Maintenance; **X in Strict Offline (unregistered)** |
| **systemd user services/timers** | `org.freedesktop.systemd1` (session) | R / C | List/status R; enable/start C. **`peppermint-*` units themselves must be X** (no self-modification) |
| **Scheduled tasks** | migrate crontab → systemd user timers | C | Timers are introspectable, undoable, and don't require crontab parsing |
| **Accessibility** | GSettings `org.cinnamon.desktop.a11y.*` | RM | Reversible |
| **Backup / restore** | Timeshift, Déjà Dup | R / **X** | Read status R; **triggering or deleting snapshots X** for now |
| **Logs / diagnostics** | `journalctl` (already SAFE) | R | **Secret-leak vector into model context** — needs redaction before context insertion |
| **Session / lock / suspend / shutdown** | `org.cinnamon.ScreenSaver`, `logind` | C | Lock C (low risk); **suspend/shutdown C with a countdown + cancel**; never automatic |
| **Clipboard** | GTK clipboard | R / **C** | Reading the clipboard is a **privacy-sensitive read** — treat as C, never silent |
| **Screenshot** | `org.freedesktop.portal.Desktop` Screenshot | C | Portal shows its own consent UI — good |
| **Workspaces / windows / panels** | `org.Cinnamon` D-Bus, Muffin | RM | Reversible; good demo surface |
| **Themes / backgrounds** | GSettings | RM | Already implemented and undoable |

**Prohibited (X) set — never exposed regardless of mode:** disk formatting/partitioning; user account and password management; `sudoers`/polkit rule edits; firewall rule changes; kernel module load/unload; anything writing `~/.ssh`, `~/.gnupg`, `~/.local/share/keyrings`; modification of Peppermint's own units, policy files, or audit tables; deletion of undo records.

---

## 7. Proposed plugin / tool architecture

The current registry is a decorator carrying `name`, `description`, `parameters`, and a function. Everything else — risk, reversibility, timeout, offline compatibility — is decided inside each function body, ad hoc. That is why the classifier ended up as the single point of truth and why holes appear per-tool.

**[RECOMMENDATION]** Make the contract declarative, so that policy is a property of the tool's *declaration* and can be enforced centrally and tested exhaustively.

```python
@dataclass(frozen=True)
class ToolContract:
    name: str
    summary: str                      # one line, shown in approval cards
    parameters: dict                  # JSON Schema, given to the model

    # --- policy: declared, not decided in the body ---
    effects: frozenset[Effect]        # READ | WRITE_FILE | WRITE_SETTING | PROCESS
                                      # | NETWORK | PRIVILEGED | DESTRUCTIVE
    permissions: frozenset[Permission]  # FILES_HOME | SETTINGS_DESKTOP | PACKAGES
                                        # | SCHEDULING | NETWORK_LAN | CLIPBOARD | …
    reversibility: Reversibility      # NONE | UNDO_RECORD | TRASH | SNAPSHOT
    approval: ApprovalPolicy          # NEVER | WHEN_UNSAFE | ALWAYS
    modes: frozenset[Mode]            # which operating modes register this tool
    timeout_s: int
    validate: Callable[[dict], None]  # raises ToolError before ANY side effect
    verify: Callable[[dict, Any], VerifyResult] | None  # re-reads state, proves it happened
    undo: Callable[[UndoRecord], Result] | None
    dry_run: Callable[[dict], Preview] | None  # powers the plan-preview UI
```

Enforced centrally by the registry, not by tool authors:

1. **Mode filtering at registration.** `Effect.NETWORK` or `Effect.PRIVILEGED` in `effects` → the tool is not registered in Strict Offline, and never reaches the model's schema list. This is what makes "unavailable, not merely confirmation-gated" true.
2. **Reversibility invariant.** A tool declaring `WRITE_FILE`/`WRITE_SETTING` with `reversibility=NONE` and `approval=NEVER` is a **contract violation that fails at import time**, i.e. a failing test — not a runtime surprise.
3. **Mandatory `validate` before side effects**, so argument errors never leave partial state.
4. **`verify` closes the "reported work it did not do" failure** the README documents: the framework re-reads the world and appends ground truth to the tool result, so the model cannot claim success the system did not observe.
5. **`dry_run` powers plan preview and before/after diffs** — a real capability behind a real UI control.
6. **Typed native adapters** (`Gio.Settings`, D-Bus proxies) replace `subprocess` wherever a native API exists, removing a shell parse from the path entirely.

Plugin loading: local directory `~/.local/share/peppermint/plugins/`, each declaring its contract; **no network install path**; plugins run under the same unit sandbox (they cannot widen it — seccomp is one-way); a plugin declaring effects beyond its granted permission set is refused at load with a visible error. Plugins may never register `Effect.PRIVILEGED` in any mode below Maintenance.

---

## 8. Visual design system and text wireframes

### 8.1 Toolkit recommendation: **stay on GTK 3**

| Criterion | GTK 3 | GTK 4 + libadwaita |
| --- | --- | --- |
| Native Cinnamon appearance | **Exact** — Mint-Y themes are GTK3-native | Adwaita-styled; visibly foreign on Cinnamon |
| Typelibs on this host | **Present** `Gtk-3.0`, `XApp-1.0`, `Notify-0.7` **[CONFIRMED]** | **`gir1.2-gtk-4.0` NOT installed** — adds a dependency **[CONFIRMED]** |
| `XApp.StatusIcon` | **Supported** — the panel-icon design depends on it | XApp is a GTK3 library; the panel integration would have to be abandoned or split |
| Mint 21.x back-compat | Yes | libadwaita 1.5 present on 22.x; 21.x is older |
| Migration cost | **Zero** — existing UI keeps working | Full rewrite of `window.py`, `task_row.py`, `app.py`, `tray.py` |

**Recommendation:** GTK 3 for the shipping product. The visual ambition of Section 4 is achievable in GTK 3 with a disciplined CSS design system plus custom `Gtk.DrawingArea` widgets for meters and timelines. Revisit only when Mint's default session itself moves. **[CONFIRMED]** basis: XApp is GTK3-only and the panel indicator is a core product feature.

### 8.2 Design system

- **Tokens, not ad-hoc colours.** The existing `CSS` blob in `window.py` hardcodes `#3584e4`, `#f5c211`, `#33d17a`, `#e01b24`. Move to named tokens resolved from the GTK theme (`@theme_fg_color`, `@theme_selected_bg_color`) with the hardcoded values as fallback only, so Mint-Y / Mint-Y-Dark / high-contrast all work.
- **Semantic status colour is never the sole signal** — always paired with an icon and a word (accessibility; also colour-blind safety). The current pill system already does this; keep it.
- **Three surfaces:** *Composer* (fast, keyboard-first, Super+Space), *Console* (dashboard, timeline, controls), *Cards* (approval, plan preview, incident) which appear in both.
- **Beginner / Expert toggle** is a real filter over the control set, persisted, defaulting to Beginner. Expert reveals: raw tool args, JSON payloads, policy internals, sandbox diagnostics, model parameters.
- **Every control maps to a named capability, policy field, resource limit, or observed state.** No control ships without a backing field. Meters read real values (`/proc`, `nvidia-smi`, Ollama `/api/ps`); sliders write real config keys; the emergency stop cancels real work.

### 8.3 Wireframes

**(1) Dashboard**

```
┌─ Peppermint ────────────────────────────────────────── [Beginner|Expert] ─ ─ □ ✕ ┐
│ ┌──────────────────────────────────────────────────────────────────────────────┐ │
│ │  ⌨  Tell Peppermint what you want…                                    [ Go ] │ │
│ └──────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  🔒 STRICT OFFLINE   no internet socket can be created      [ Change… ]          │
│     ● model  qwen3:8b via unix socket   ● database   ● D-Bus   ● sandbox         │
│  ─────────────────────────────────────────────────────────────────────────────   │
│  CPU  ▓▓▓▓●░░░░░ 41%   RAM ▓▓▓▓▓▓▓●░░ 68% (10.9/16 GB)                           │
│  VRAM ▓▓▓▓▓▓▓●░ 79% (6.3/8 GB)   context ▓▓▓░░░░░░░ 4.1k/16k tokens             │
│                                                                                  │
│  ACTIVE ─────────────────────────────────────────────────────────────────────    │
│   ⣾ Sort ~/Downloads by file type          step 3/6   running   00:14  [ Stop ]  │
│                                                                                  │
│  NEEDS YOU ──────────────────────────────────────────────────────────────────    │
│   ⚠ Install packages: gimp                 waiting 2m         [ Review… ]        │
│                                                                                  │
│  RECENT ─────────────────────────────────────────────────────────────────────    │
│   ✓ Make desktop theme light               done  12:31   3 changes  [ Undo ]     │
│   ✓ How much disk space is left?           done  12:19   read-only               │
│   ✕ Bind Ctrl+Alt+T to terminal            failed 11:58              [ Details ] │
│                                                                                  │
│  [ Automations 2 ]  [ Permissions ]  [ Undo centre 4 ]  [ Tools 21 ]  [ ⚙ ]      │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**(2) Task execution view** — live timeline, expandable pipeline

```
┌─ Sort ~/Downloads by file type ───────────────────── running · 00:14 ─ [ Stop ] ┐
│  ●────────●────────●────────◍- - - - -○- - - - -○                               │
│  plan     list     make     move      verify    report                          │
│                    dirs     files                                               │
│                                                                                 │
│  ▾ 12:44:02  list_dir  ~/Downloads                       read-only     ✓ 0.03s  │
│      41 files, 3 folders                                                        │
│  ▾ 12:44:03  make_dir  ~/Downloads/Images                reversible   ✓ 0.01s  │
│      created · undo recorded #118                                               │
│  ▾ 12:44:04  sort_folder  ~/Downloads  {Images:[jpg,png], Docs:[pdf]}           │
│      ⣾ moving 23 of 41…                                    [ live output ▾ ]    │
│                                                                                 │
│  subsystems touched:  ▣ files   ▢ settings   ▢ packages   ▢ network(blocked)     │
│  undo records for this task: 24                            [ Undo whole task ]  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**(3) Approval dialog** — exact consequences, never a bare yes/no

```
┌─ Peppermint needs your approval ───────────────────────────────────────────────┐
│  ⚠  Install system packages                          expires in 13:42          │
│                                                                                │
│  WHAT WILL HAPPEN                                                              │
│    pkexec apt-get install -y gimp                                              │
│    → asks for your password (polkit)                                           │
│    → downloads ~210 MB · installs 14 packages · needs Maintenance mode         │
│                                                                                │
│  WHY  "You asked for an image editor; gimp is in the Mint repositories."       │
│                                                                                │
│  REVERSIBILITY   ⚠ Not automatically reversible.                               │
│                    Removal afterwards: apt-get remove gimp                     │
│                                                                                │
│  BLOCKED BY POLICY                                                             │
│    🔒 Strict Offline forbids package installation.                             │
│       Approving requires switching to Maintenance mode (30 min, audited).      │
│                                                                                │
│  This approval covers this exact action only. Token 9f3a…c1, target verified.  │
│                                                                                │
│              [ Deny ]   [ Switch to Maintenance & Allow… ]   [ Always ask ]    │
└────────────────────────────────────────────────────────────────────────────────┘
```

**(4) Automation builder**

```
┌─ Automations ────────────────────────────────────── [ + New ] ─────────────────┐
│  ▣ Tidy Downloads      every day 18:00     next in 5h 12m   [ Run now ][ ⏸ ]   │
│  ▣ Weekly theme check  Mondays 09:00       next in 3d       [ Run now ][ ⏸ ]   │
│  ──────────────────────────────────────────────────────────────────────────────│
│  EDIT: Tidy Downloads                                                          │
│   WHEN  ( ● schedule  ○ on login  ○ manual )   [18:00] [daily      ▾]          │
│         backend: systemd user timer  peppermint-tidy.timer         [ show ]    │
│   DO    ┌────────────────────────────────────────────────────────────────┐     │
│         │ 1  list_dir   ~/Downloads                        read-only     │     │
│         │ 2  sort_folder rules {Images:[jpg,png], Docs:[pdf]}  reversible│     │
│         │ 3  notify_user "Downloads tidied"                  read-only   │     │
│         └───────────────────── drag to reorder · [+ add step] ───────────┘     │
│   IF A STEP NEEDS APPROVAL   ( ● pause and ask   ○ skip step   ○ abort )       │
│   AUTOMATION TOLERANCE  reversible-only ▓▓▓▓●░░░░  ask-for-everything          │
│   ⓘ This automation may only use tools allowed in Strict Offline.              │
│                                                       [ Cancel ]  [ Save ]     │
└────────────────────────────────────────────────────────────────────────────────┘
```

**(5) Permissions / offline control centre**

```
┌─ Permissions & Network Policy ─────────────────────────────────────────────────┐
│  MODE                                                                          │
│   ● Strict Offline    no internet socket can be created        (recommended)   │
│   ○ Local Network     approved LAN addresses only              [ edit list ]   │
│   ○ Maintenance       temporary full access · auto-reverts in [30 min ▾]       │
│   ○ Developer         full access, persistent            ⚠ shows a red banner  │
│   Changing mode rewrites a systemd drop-in and restarts the daemon.            │
│   It is recorded in the audit log. Peppermint cannot change it by itself.      │
│                                                                                │
│  LIVE PROOF          last probed 12:58:04            [ Re-run probe ]          │
│   IPv4 socket  ✕ blocked (EAFNOSUPPORT)     DNS       ✕ blocked                │
│   IPv6 socket  ✕ blocked (EAFNOSUPPORT)     Proxy env ✕ stripped               │
│   Model        ✓ unix:%t/peppermint/ollama.sock                                │
│   Subprocesses ✕ inherit the restriction (verified)                            │
│                                                                                │
│  PERMISSION MATRIX                    read   change   needs approval   off     │
│   Files (home)                          ●      ●            ●          ○       │
│   Desktop settings                      ●      ●            ○          ○       │
│   Keyboard shortcuts                    ●      ●            ●          ○       │
│   Packages                              ●      ✕ blocked by Strict Offline     │
│   Scheduling                            ●      ●            ●          ○       │
│   Clipboard                             ○      —            ●          ●       │
│   Network configuration                 ●      ✕ blocked                       │
│                                                                                │
│  DATA & RETENTION   history [90 days ▾]  [ Export… ] [ Delete all history ]    │
│  AUDIT LOG  append-only · 412 entries                          [ View… ]       │
│                                            [ 🛑 EMERGENCY STOP & LOCK DOWN ]   │
└────────────────────────────────────────────────────────────────────────────────┘
```

**(6) Model and resource tuning**

```
┌─ Model & Resources ────────────────────────────────────────────────────────────┐
│  MODEL   qwen3:8b  ▾        loaded · 6.3 GB VRAM · via unix socket             │
│          fallback qwen2.5:7b-instruct-q4_K_M        [ Import model… ]          │
│                                                                                │
│  ┌ VRAM ────────┐ ┌ RAM ─────────┐ ┌ CPU ─────────┐   context window           │
│  │   ╭─────╮    │ │   ╭─────╮    │ │   ╭─────╮    │   ▓▓▓░░░░░░░ 4.1k / 16k    │
│  │   │ 79% │    │ │   │ 68% │    │ │   │ 41% │    │   tokens this task         │
│  │   ╰─────╯    │ │   ╰─────╯    │ │   ╰─────╯    │                            │
│  │  6.3/8.0 GB  │ │ 10.9/16 GB   │ │  8 cores     │                            │
│  └──────────────┘ └──────────────┘ └──────────────┘                            │
│                                                                                │
│  Creativity (temperature)   precise ▓●░░░░░░░ creative        0.20             │
│  Context length             4k ▓▓▓▓▓●░░░ 32k                  16384  ⚠ VRAM    │
│  Steps per task             5 ▓▓▓▓●░░░░ 60                    30               │
│  Task time limit            1m ▓▓●░░░░░ 30m                   10m              │
│  Keep model loaded          0 ▓▓●░░░░░ 60m                    10m              │
│                                                                                │
│  ⓘ Raising context or keep-alive increases VRAM. Peppermint warns before       │
│    a value would exceed the free VRAM shown above.                             │
│                              [ Reset to defaults ]  [ Benchmark locally… ]     │
└────────────────────────────────────────────────────────────────────────────────┘
```

**(7) Undo and incident recovery**

```
┌─ Undo & Recovery ──────────────────────────────────────────────────────────────┐
│  ⚠ INCIDENT  Task 41 stopped part-way through a change                         │
│     apt_install(gimp) began at 12:03:11 and never reported an outcome.         │
│     Peppermint did NOT retry it — it cannot prove whether it finished.         │
│     [ Check current state ]   [ Mark resolved ]   [ Show full step log ]       │
│  ──────────────────────────────────────────────────────────────────────────────│
│  TIMELINE   ◀ ══════════╤═══════════╤══════════╤═════════▶   [today ▾]         │
│                       11:58       12:19      12:31      now                    │
│                                                                                │
│  REVERSIBLE CHANGES                                            [ Undo all ]    │
│   #124  12:31  setting  org.cinnamon.desktop.interface gtk-theme               │
│                Mint-Y-Dark → Mint-Y                    [ diff ] [ Undo ]       │
│   #123  12:31  setting  …background picture-uri                                │
│                before ▓ after ▓  side-by-side preview  [ diff ] [ Undo ]       │
│   #118  12:44  move     ~/Downloads/a.pdf → ~/Downloads/Docs/a.pdf             │
│                                                        [ Undo ]                │
│   #101  11:40  file     ~/notes.txt (412 bytes replaced)                       │
│                ─ old ─────────── │ ─ new ───────────    [ diff ] [ Undo ]      │
│                                                                                │
│  ⓘ A change can be put back once. Peppermint refuses to overwrite anything     │
│    new that appeared in the meantime.                                          │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 8.4 Controls — each backed by a real field

| Control | Backing | Real effect |
| --- | --- | --- |
| Creativity slider | `TEMPERATURE` | Ollama option |
| Context slider | `NUM_CTX` | Ollama option; warns against measured free VRAM |
| Steps / time-limit sliders | `MAX_ITERATIONS`, `TASK_TIMEOUT_S` | Agent loop bounds |
| Automation-tolerance slider | approval policy threshold | Which reversibility classes may run unattended |
| CPU/RAM/VRAM/context meters | `/proc`, `nvidia-smi`, Ollama `/api/ps`, token count | Live telemetry |
| Offline-mode radio | systemd drop-in + restart | Actual kernel policy |
| Subsystem toggles | permission set per `ToolContract.permissions` | Tool registration |
| Permission matrix | same | Per-capability read/change/approve/off |
| Status lights | live probes of model UDS, SQLite `integrity_check`, D-Bus name ownership, `socket(AF_INET)` attempt | Real health |
| Emergency stop / lock down | cancel all tasks + drop to Strict Offline + revoke pending approvals | Real |
| Timeline scrubber | `steps.ts`, `undo.ts` | Real history navigation |
| Diff viewer | `undo.old_value` vs current | Real before/after |

---

## 9. Safe adaptive learning

**Invariant, enforced structurally:** the adaptation subsystem may write to *one* table (`preferences`) and *one* file (a local eval set). It has no code path to safety policy, approval policy, network mode, audit history, or evaluation thresholds. Those live in a separate module with no write API reachable from the learning code, and a test asserts the import graph (`no module under peppermint.adapt may import peppermint.policy.write`).

| Capability | Design | Guard |
| --- | --- | --- |
| Preference learning | Key/value with provenance: `{key, value, source: explicit|inferred, confidence, task_id, ts}` | Only affects *defaults and phrasing* — never risk class. Inferred prefs need 3 consistent observations |
| Correction capture | User edits a plan / denies with a reason → stored as a labelled pair | Denial reasons never auto-generate permissions |
| Task metrics | Local counters: steps-per-task, approval rate, undo rate, verify-failure rate. No content, no paths | Aggregate-only table; exportable; deletable |
| Local eval set | Frozen JSONL of (idea → expected tool sequence), seeded from the README's observed failures | Version-controlled with the code; changes are code review, not runtime |
| Shadow mode | New prompt/schema version runs alongside the active one, **executes nothing**, only logs divergence | Shadow results can never be promoted automatically |
| Prompt / schema versioning | `prompt_version`, `tool_schema_version` recorded per task alongside the existing `POLICY_VERSION` | Enables reading old logs under old rules |
| Model benchmarking | Local runs against the eval set; report accuracy, steps, latency, VRAM | Manual promotion only |
| Regression detection | Compare candidate to active on the eval set; block promotion on any safety-relevant regression | Threshold is config-as-code, not runtime-writable |
| Staged upgrades | shadow → opt-in → default, each stage user-confirmed and audited | Never automatic |
| Export / delete | `peppermint prefs export|delete`, mirrored in the Privacy panel | One click, complete |

**The rule to state in the product:** *Peppermint can learn what you like. It cannot learn to ask you less.*

---

## 10. Minty → Peppermint migration plan

A blind global replace would break the installed system: it would orphan the running systemd unit, abandon the D-Bus name the CLI and panel icon talk to, and strand `~/.local/share/minty/minty.db` containing real task history and undo records. Sequence it in five reviewable stages.

**Inventory**

| Layer | Today | Target | Compatibility need |
| --- | --- | --- | --- |
| Python package | `minty/` | `peppermint/` | `minty` shim re-exporting, `DeprecationWarning`, one release |
| Distribution | `minty` 0.1.0 | `peppermint` 0.2.0 | `Obsoletes`/conflict note; uninstall guidance |
| Console scripts | `minty`, `minty-daemon`, `minty-window` | `peppermint`, `peppermint-daemon`, `peppermint-window` | Keep old names as wrappers that warn |
| D-Bus names/paths | `org.minty.Daemon`, `/org/minty/Daemon`, `org.minty.Window`, `org.minty.App` | `org.peppermint.*` | **Daemon owns BOTH names for one release**; clients try new then old |
| Error domain | `org.minty.Error` | `org.peppermint.Error` | Client accepts both |
| systemd units | `minty-daemon.service` | `peppermint-daemon.service` | Migration step disables+removes old unit **after** the new one is active |
| Desktop files | `minty.desktop`, `minty-applet.desktop` | `peppermint*.desktop` | Remove old autostart to avoid two panel icons |
| Data dir | `~/.local/share/minty/` | `~/.local/share/peppermint/` | **Copy, verify, then leave the original in place** as `minty.db.pre-peppermint` |
| DB file | `minty.db` | `peppermint.db` | Schema v3 → v4 migration adds `policy_version` write, `mode_changes`, `preferences` |
| Env vars | `MINTY_*` | `PEPPERMINT_*` | Read new, fall back to old, warn once, document removal release |
| Log | `minty.log` | `peppermint.log` | New file; old retained |
| Keybinding | slot with command `…/minty toggle` | `…/peppermint toggle` | Find by old command, rewrite in place (don't create a second slot) |
| Undo temp suffix | `.minty-undo` | `.peppermint-undo` | Cosmetic; handle both when cleaning |
| UI strings / icons | "Minty" | "Peppermint" | Last stage, single pass |
| Tests / docs | `minty` | `peppermint` | With the code move |

**Sequence**

1. **Compatibility layer first** — env-var fallback, dual D-Bus name ownership, dual-name client lookup, data-dir resolution preferring new and falling back to old. No renames yet. Ship and verify.
2. **Data migration** — `peppermint migrate` (also run automatically once, guarded by a marker file): copy DB, `PRAGMA integrity_check` on the copy, run schema v4, write `migrated_from` provenance row, keep the original untouched. Abort loudly on any failure; never delete.
3. **Package/module rename** — `git mv`-equivalent move, `minty/__init__.py` shim, entry points added (not replaced).
4. **Units, desktop files, keybinding** — install new, start new, verify healthy, then disable/remove old. Order matters: never leave the user without a running daemon.
5. **Strings, icons, docs, old-name removal** — after one release of overlap.

**Rule:** do not rename any internal identifier until this plan is reviewed and approved (per the research standards for this phase). Nothing in this report has renamed anything.

---

## 11. Prioritised roadmap

### Milestone 0 — Immediate safety blockers (~400 LOC, no rewrite)

| # | Change | Files |
| --- | --- | --- |
| S1 | `RestrictAddressFamilies=AF_UNIX AF_NETLINK` + `CapabilityBoundingSet=` + `ProtectSystem=strict` + `ReadOnlyPaths=%h` + `ReadWritePaths` in the daemon unit. **Must land in the SAME change as S2** — alone it severs Ollama while the unit still reports `active`, because `main.py:87-90` only logs `LLMError`. **`NoNewPrivileges=yes` is deferred to S7**: it breaks `pkexec` (`rc=127, "pkexec must be setuid root"`) and so disables `apt_install` in *every* mode, not just Strict Offline. | `data/minty-daemon.service` |
| S2 | UDS model transport: `systemd-socket-proxyd` bridge unit + `ollama.Client(transport=httpx.HTTPTransport(uds=…))`. Add a startup self-test that refuses to start on `EAFNOSUPPORT`, so a severed model is a loud failure, not a per-task one. | new unit, `daemon/llm.py` |
| S0 | **PREREQUISITE — not this lane.** The installation is currently broken by the directory rename (venv shebangs, editable finder, installed unit and `.desktop` files all point at `Documents/Minty`). M0's acceptance gate is unreachable until it is repaired. See `install-path-audit.md`. | `.venv/`, `data/`, `scripts/` |
| S3 | Startup validation: reject non-loopback `*_OLLAMA_HOST` in Strict Offline; refuse to start with a clear message | `config.py`, new `policy.py` |
| S4 | Environment sanitiser: strip `BASH_ENV`, `BASH_FUNC_*`, `LD_*`, `*_PROXY`, `PYTHONPATH`, `PYTHONSTARTUP` for **all** subprocesses; `httpx` client with `trust_env=False` | new `daemon/env.py`, `llm.py`, all tools |
| S5 | `run_shell` uses `bash --noprofile --norc -c`, never `-lc` | `tools/shell.py` |
| S6 | Close confirmed classifier holes: `getent`→RISKY; `git remote` second-level parse; `nmcli` second-level parse; `xdg-mime default`→RISKY; drop `git remote` from safe subcommands | `daemon/safety.py`, `POLICY_VERSION`→3 |
| S7 | Mode-aware registration: `apt_install` and network verbs unregistered in Strict Offline | `tools/registry.py`, `policy.py` |
| S8 | Tighten data-at-rest: `0700` data dir, `0600` DB and log | `db.py`, `main.py` |
| S9 | Policy indicator in window header + panel tooltip, sourced from a live probe | `ui/window.py`, `ui/tray.py` |
| S10 | `tests/test_offline_enforcement.py` (§5.7) + corpus regressions for every S6 hole | `tests/` |
| S11 | README correction: remove "sends nothing to the internet"; adopt §5.5 wording | `README.md` |

**Acceptance:** all 727 existing tests still pass; every §5.7 test passes under the hardened unit and *fails* without it; a manual attempt to reach the internet from a `run_shell` task fails; the daemon refuses to start with a remote model host; the window shows 🔒 Strict Offline derived from a live probe.

### Milestone 1 — First polished prototype

Declarative `ToolContract` (§7) with import-time invariant tests; `Gio.Settings` native adapter replacing subprocess `gsettings`; `verify` hooks on mutating tools; mode switching with drop-in rewrite + audit table; Dashboard, Task view, Approval card, Permissions centre wireframes implemented; design-token CSS; policy_version actually written per task.

**Acceptance:** no mutating tool lacks `verify` or `undo`; mode change is impossible without user action and always audited; every UI control maps to a backing field (enumerated in a test); approval cards show consequences, reversibility and expiry.

### Milestone 2 — Beta readiness

Undo/recovery centre with diffs and timeline; automation builder on systemd user timers (migrating off crontab); tool/plugin manager; model & resource panel with live meters; offline bundle + `model import`; Local Network and Maintenance modes with auto-revert; redaction before journal/log output enters model context; D-Bus caller authorisation.

**Acceptance:** every automation is introspectable and revertible; Maintenance auto-reverts and is audited; a fresh machine can install and run with zero network access using the bundle; no secret-shaped string reaches the model context in a fuzz corpus.

### Milestone 3 — Production readiness

Peppermint rename completed through §10; adaptive layer with shadow mode and import-graph guard; packaging (`.deb`, AppArmor profile for system installs); accessibility pass (keyboard-only, screen reader, contrast); performance budget for the UI; incident playbooks; signed/pinned Ollama and model artefacts.

**Acceptance:** upgrade from a Minty install preserves all history, undo records and configuration with a verified copy; adaptive layer provably cannot modify policy (graph test + attempted-write test); `.deb` installs Strict Offline by default; full keyboard navigation.

---

## 12. Acceptance criteria summary

| Phase | Gate |
| --- | --- |
| M0 | 727 baseline tests green · 14 enforcement tests green under sandbox, red without · remote host rejected · proxy cannot divert · subprocess egress blocked · README claim corrected |
| M1 | Contract invariants enforced at import · every mode change audited · every control backed by a field · approval cards show consequence + reversibility + expiry |
| M2 | Offline bundle installs on an air-gapped machine · automations revertible · Maintenance auto-reverts · redaction fuzz corpus clean · D-Bus callers authorised |
| M3 | Migration preserves 100% of history/undo/config · adaptation cannot touch policy (proven by test) · packaged install defaults to Strict Offline · accessibility pass complete |

---

## 13. Open questions and explicit assumptions

**Open questions**

1. **Ollama UDS support.** The bridge (`systemd-socket-proxyd`) is proven, but does a current Ollama accept a native `unix://` listener? If yes, the bridge unit disappears. *Needs a version check against upstream.*
2. **Is the Ollama server in scope for Strict Offline?** Sealing it (`PrivateNetwork=yes`) makes `ollama pull` impossible without a mode change. Correct for the threat model; a product decision about friction.
3. **Mint 21.x support?** Landlock ABI and systemd 249 differ materially. `RestrictAddressFamilies` is old enough to be safe there **[INFERRED]**, but this was measured only on 22.3.
4. ~~**`kernel.apparmor_restrict_unprivileged_userns = 0` on this host** diverges from the Ubuntu 24.04 default of `1`.~~ **ANSWERED — this was wrong.** Mint 22.x ships the override deliberately in a packaged file: `/etc/sysctl.d/20-apparmor-mint.conf` (owned by `ubuntu-system-adjustments`, `dpkg -V` clean) sets it to `0`, citing linuxmint/mint22-beta#82. The host is stock; `1` is the *Ubuntu* default that Mint intentionally overrides. Since Peppermint targets Mint, bwrap/userns layers are portable on the target platform. Still add `PrivateUsers=true` and an install-time probe as belt-and-braces.
5. **D-Bus caller authorisation model** — polkit, or a peer-credential check against the owning uid? Affects adversary A3.
6. **Multi-user / shared machines** — is that a supported configuration at all? Changes the data-at-rest requirements.
7. **Undo for `apt_install`** — currently none. Is `apt-get remove` an acceptable inverse, or should installs remain explicitly irreversible in the UI?
8. **8 GB VRAM floor** — the README requires it; the resource panel should degrade gracefully below it rather than fail.

**Assumptions**

- Single-user desktop, X11 Cinnamon session, no root at install time.
- The user's own shell is outside the threat model: someone who can run commands can change the mode. Peppermint defends against the *model*, against *content it processes*, and against *accidental* egress — not against the machine's owner.
- Data at rest is unencrypted; disk encryption is the OS's responsibility.
- Kernel, systemd and Ollama are trusted to implement their documented behaviour.
- The local model remains small and error-prone; the README's documented failure modes are permanent design constraints, not bugs to be fixed by a better prompt.

---

## 14. Proposed source changes (no implementation performed)

| File | Change | Milestone |
| --- | --- | --- |
| `data/minty-daemon.service` | Full sandbox stanza (S1); template the path instead of hardcoding `%h/Documents/Minty` | M0 |
| *new* `data/peppermint-model-bridge.socket` / `.service` | UDS → Ollama bridge | M0 |
| *new* `minty/policy.py` | Mode definitions, host validation, live probe, mode-change audit | M0 |
| *new* `minty/daemon/env.py` | Environment sanitiser for every subprocess | M0 |
| `minty/config.py` | `PEPPERMINT_*` with `MINTY_*` fallback; validated `OLLAMA_HOST`; mode field | M0 |
| `minty/daemon/llm.py` | UDS transport; `trust_env=False`; reject non-loopback in Strict Offline | M0 |
| `minty/daemon/tools/shell.py` | `bash --noprofile --norc -c`; sanitised env; mode-aware description | M0 |
| `minty/daemon/safety.py` | S6 hole fixes; second-level subcommand parsing; `POLICY_VERSION`→3; fix the `.strip()` leaking into the user-visible reason string at line 226; remove the duplicated `if not tokens` at lines 200–202 | M0 |
| `minty/daemon/tools/registry.py` | Mode-aware registration; later, `ToolContract` | M0/M1 |
| `minty/daemon/db.py` | `0600` files; write `policy_version` (column exists, **never written** — the docstring claim is false); `mode_changes` table; populate `undo.step_id` (column exists, never written); wire up or remove the unused `expired_confirmations` | M0/M1 |
| `minty/daemon/main.py` | `0700` data dir; policy probe at startup; expose `Policy` over D-Bus | M0 |
| `minty/ui/window.py`, `ui/tray.py` | Policy indicator from live probe; design tokens replacing hardcoded hex | M0 |
| `minty/daemon/agent.py` | Context/history trimming (none today — history grows unbounded and is silently truncated by Ollama); cancellation checked between tool calls, not only per iteration | M1 |
| *new* `minty/daemon/adapters/settings.py` | `Gio.Settings` native adapter | M1 |
| `tests/test_offline_enforcement.py` *(new)*, `tests/corpus_safety.py` | §5.7 suite + regressions for every confirmed hole | M0 |
| `README.md` | §5.5 wording; document modes | M0 |
| `scripts/install.sh`, `setup-ollama.sh` | Pin + verify the Ollama tarball; install Strict Offline by default; separate setup-time network use explicitly | M0/M2 |

**Known defects worth recording** (found while reading, not yet fixed):

- `safety.py:226` — `f"\`{name} {first or ''}\`.strip() can change the system"` puts the literal text `.strip()` into a user-visible approval reason. **[CONFIRMED]** in output: ``` `git fetch`.strip() can change the system ```.
- `safety.py:200–202` — `if not tokens: return …` duplicated verbatim.
- `db.py` — `tasks.policy_version` and `undo.step_id` columns exist but are never written; `expired_confirmations()` is dead code.
- `packages.py:92` — `pkexec` is invoked with an env containing only `DEBIAN_FRONTEND` and `PATH`; without `DISPLAY`/`XAUTHORITY` the graphical polkit prompt may not appear. **[INFERRED]** — needs a live test before M1.
- `agent.py` — `Cancel` is only observed at the top of an iteration, so a task inside a 600 s shell call cannot be stopped. **[CODE]**

---

## Recommended first implementation milestone

**Ship Milestone 0, and nothing else, first.**

The smallest change set that converts "we intend to be offline" into "the kernel will not let us be otherwise", while the existing application keeps running exactly as it does today:

1. **`RestrictAddressFamilies=AF_UNIX AF_NETLINK` on the daemon unit.** One directive. Proven on this machine to block IPv4, IPv6 and DNS, for the daemon *and every process it spawns*, including through `bash -lc`.
2. **Talk to Ollama over a Unix socket.** Proven: `['qwen3:8b']` listed from a process with no IP stack, with a hostile `HTTP_PROXY` set and ignored.
3. **Sanitise the environment and stop using a login shell.** Closes the two confirmed bash escapes that the classifier cannot see.
4. **Reject non-loopback model hosts at startup, and unregister `apt_install` and network verbs in Strict Offline** — unavailable, not merely gated.
5. **Fix the five confirmed classifier holes**, add their regressions to the corpus, and raise `POLICY_VERSION`.
6. **Show the policy in the UI from a live probe, and correct the README.**

This is roughly 400 lines across nine files plus one new unit. It touches no tool semantics, no approval logic, no undo logic, and no UI structure — so all 727 existing tests keep their meaning. After it lands, "Strict Offline" is a statement about the operating system rather than about Peppermint's intentions, and every subsequent milestone builds on a boundary that has been demonstrated rather than assumed.
