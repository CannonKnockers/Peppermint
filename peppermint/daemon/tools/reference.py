"""Bounded, offline troubleshooting notes; lookup never inspects or changes a machine."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from peppermint.daemon.tools.registry import tool

REVIEWED = "2026-09-08"
MAX_QUERY_CHARS = 1000
MAX_OUTPUT_CHARS = 4000
MAX_RESULTS = 2


@dataclass(frozen=True)
class Reference:
    id: str
    title: str
    aliases: tuple[str, ...]
    observe: str
    recommend: str
    verify: str
    limitations: str
    sources: tuple[tuple[str, str], ...]

    def result(self) -> dict:
        return {
            "id": self.id, "title": self.title,
            "observe": self.observe, "recommend": self.recommend,
            "verify": self.verify, "limitations": self.limitations,
            "sources": [{"title": title, "url": url} for title, url in self.sources],
            "reviewed": REVIEWED,
        }


# Deliberately small reviewed corpus. References describe possible next steps,
# never executable tool arguments or a diagnosis of the user's current machine.
REFERENCES = (
    Reference(
        "steam_proton", "Steam / Proton game launch failures",
        ("steam", "proton", "game", "gaming", "compatdata", "steam play"),
        "Identify the game and AppID, launch symptom, native versus Proton build, "
        "Steam package type, compatibility version, and library filesystem.",
        "After identifying the game, collect a fresh Proton log with the temporary "
        "Steam launch option PROTON_LOG=1 %command%; preserve existing options. "
        "The default log is ~/steam-<AppID>.log unless PROTON_LOG_DIR overrides it. "
        "Consider Steam Properties > Installed Files > Verify integrity after "
        "protecting mods. Record each change and change one thing at a time.",
        "Relaunch the same game, compare the new error/log, and check that saved "
        "progress loads. Remove the temporary logging option afterward.",
        "No guarantee of game compatibility. Do not delete compatdata or prefixes: "
        "they can contain saves and configuration. Verification can replace modified files.",
        (("Valve Proton runtime options", "https://github.com/ValveSoftware/Proton"),
         ("Steam file verification", "https://help.steampowered.com/en/faqs/view/0C48-FCBD-DA71-93EB")),
    ),
    Reference(
        "wine_apps", "Windows applications through Wine",
        ("wine", "wineprefix", "winecfg", "windows application", "windows app",
         "windows program", "exe", "msi", "bottles", "lutris"),
        "Identify the exact Windows application/version, installer architecture, "
        "Wine version, launcher, and existing prefix before changing compatibility settings.",
        "Wine implements Windows APIs on Linux; it is not a complete Windows "
        "installation. Check the application's WineHQ compatibility/help records "
        "and launcher documentation. Preserve its existing prefix and user data; "
        "test any proposed configuration change with a recorded rollback.",
        "Retest the same app action and compare its launch output; opening the "
        "main window alone does not verify printing, plugins or file access.",
        "Compatibility varies by application and version. Do not run installers "
        "as root or assume Windows device drivers work through Wine.",
        (("WineHQ About Wine", "https://www.winehq.org/about"),
         ("WineHQ Getting Help", "https://www.winehq.org/help")),
    ),
    Reference(
        "smb_sharing", "Windows / Linux / NAS SMB file sharing",
        ("smb", "samba", "cifs", "nas", "windows share", "network share",
         "shared folder", "shared drive", "access denied", "guest access"),
        "Identify client and server OS, share name, account/domain, and exact "
        "error. Separate server discovery, authentication, share access and file permissions.",
        "Use an authenticated account with the intended share permissions. "
        "Samba clients can prompt locally for credentials; never request passwords "
        "in chat or put them in command arguments/logs. Preserve existing credentials. "
        "Check server SMB signing and authentication requirements before proposing changes.",
        "Retry the exact share with the intended account; confirm listing and "
        "opening an existing permitted file before separately testing a requested write.",
        "Access denied is not proof of a network fault. Do not enable SMB1, "
        "insecure guest access or disable signing as a blanket workaround.",
        (("Samba smbclient manual", "https://www.samba.org/samba/docs/current/man-html/smbclient.1.html"),
         ("Microsoft SMB guest authentication", "https://learn.microsoft.com/en-us/windows-server/storage/file-server/enable-insecure-guest-logons-smb2-and-smb3")),
    ),
    Reference(
        "ntfs_dual_boot", "NTFS and Windows dual boot storage",
        ("ntfs", "dual boot", "dualboot", "hibernation", "hibernated", "fast startup",
         "windows disk", "windows drive", "read only drive", "read only partition"),
        "Identify the affected mount, filesystem driver (ntfs3 versus ntfs-3g), "
        "mount options and exact error. Check whether Windows was hibernated or used Fast Startup.",
        "Keep an unsafe or dirty volume read-only. Resume Windows, save open work "
        "and fully shut down before attempting shared writes; discuss disabling "
        "Fast Startup for repeated dual boot use. Back up before any filesystem repair.",
        "Recheck the volume state and mount error after a clean Windows shutdown; "
        "test writes only when the filesystem is healthy and the user requests them.",
        "Do not force-mount dirty NTFS, remove the hibernation file, format, or "
        "treat Linux ntfsfix as Windows chkdsk. NTFS driver behavior differs.",
        (("Kernel NTFS3 mount options", "https://docs.kernel.org/filesystems/ntfs3.html"),
         ("Ubuntu ntfs-3g manual", "https://manpages.ubuntu.com/manpages/jammy/man8/mount.ntfs.8.html"),
         ("Ubuntu ntfsfix manual", "https://manpages.ubuntu.com/manpages/jammy/man8/ntfsfix.8.html")),
    ),
    Reference(
        "cross_os_paths", "Paths, filenames and scripts across operating systems",
        ("wsl", "crlf", "line ending", "case sensitive", "case sensitivity",
         "filename", "windows path", "exec format", "bad interpreter", "bin bash m"),
        "Identify which OS and shell run the command, the actual file format and "
        "CPU architecture, filename case, path syntax, and text line endings.",
        "Translate paths for the receiving environment; do not assume a Windows "
        "drive letter works in Linux. Check case collisions before copying files. "
        "For text scripts, review LF/CRLF and interpreter differences; prefer "
        "project-specific line-ending rules with a previewed diff.",
        "Run the intended workflow in its actual environment and inspect the diff "
        "for unintended content changes or renamed files.",
        "WSL interoperability is specific to WSL, not ordinary Linux. Linux ELF "
        "and Windows PE executables are different formats; chmod alone cannot "
        "resolve an incompatible binary. Never normalize binary files as text.",
        (("Microsoft WSL filesystem interoperability", "https://learn.microsoft.com/en-us/windows/wsl/filesystems"),
         ("Microsoft WSL Git line endings", "https://learn.microsoft.com/en-us/windows/wsl/tutorials/wsl-git"),
         ("Microsoft PE executable format", "https://learn.microsoft.com/en-us/windows/win32/debug/pe-format"),
         ("Ubuntu ELF format manual", "https://manpages.ubuntu.com/manpages/noble/man5/elf.5.html")),
    ),
    Reference(
        "flatpak_apps", "Flatpak application and sandbox troubleshooting",
        ("flatpak", "flathub", "flatseal", "sandbox", "portal", "file chooser"),
        "Identify the exact Flatpak app ID, runtime and user/system installation. "
        "Review app permissions and overrides before treating a hidden path as missing.",
        "Prefer the app's file chooser/portal for access. If necessary, propose "
        "the smallest app-specific permission change and record its previous "
        "value. Inspect app/runtime update availability before proposing updates.",
        "Repeat the failed action inside that Flatpak and confirm both the "
        "requested access and ordinary app behavior after any change.",
        "Host package installs do not necessarily supply sandbox dependencies. "
        "Avoid blanket host filesystem/device access and global permission resets.",
        (("Flatpak sandbox permissions", "https://docs.flatpak.org/en/latest/sandbox-permissions.html"),
         ("Using Flatpak", "https://docs.flatpak.org/en/latest/using-flatpak.html")),
    ),
    Reference(
        "native_packages", "Native Linux applications and APT packages",
        ("apt", "dpkg", "deb", "dependency", "dependencies", "package", "repository",
         "repositories", "ppa", "native app", "linux app"),
        "Identify distro/release and whether the app is a deb, Flatpak or another "
        "format. Check the installed package/version, repository origin and exact error.",
        "Use the distro's package metadata to resolve the correct package. "
        "Separate refreshing package indexes from installing/upgrading software. "
        "Review a proposed transaction, including removals, before applying it.",
        "Check the resulting package version and rerun the original app action. "
        "A successful installation is not evidence that the original failure is fixed.",
        "APT guidance applies to Debian/Ubuntu-family installations, not every "
        "Linux distro. Do not mix release repositories or add unverified PPAs.",
        (("Ubuntu package management", "https://ubuntu.com/server/docs/how-to/software/package-management/"),),
    ),
    Reference(
        "gpu_runtime", "Graphics, Vulkan and host versus runtime drivers",
        ("gpu", "vulkan", "dxvk", "vkd3d", "opengl", "nvidia", "mesa", "graphics",
         "black screen", "driver", "drivers"),
        "Identify GPU(s), active driver, session type, and affected app runtime. "
        "Compare host diagnostics with the Steam/Flatpak environment; host success "
        "does not establish sandbox or 32-bit graphics support.",
        "Check whether the failing layer is display access, driver/runtime "
        "matching, or the game's DirectX translation. Flatpak supplies GL driver "
        "extensions; Proton uses components such as DXVK. Propose a targeted "
        "runtime/driver correction only after evidence identifies that layer.",
        "Repeat the same rendering workload and compare the new graphics error "
        "or log with the original symptom.",
        "A missing optional diagnostic command is not a broken GPU. Avoid "
        "universal driver replacements or graphics launch flags for every game.",
        (("Flatpak runtime extensions", "https://docs.flatpak.org/en/latest/extension.html"),
         ("Valve Proton", "https://github.com/ValveSoftware/Proton")),
    ),
    Reference(
        "services_logs", "Linux service failures and focused logs",
        ("systemd", "systemctl", "journalctl", "journal", "service", "daemon",
         "unit failed", "boot error"),
        "Identify the exact unit, whether it belongs to the user or system "
        "manager, and the failure time. Inspect unit status and a bounded log "
        "window for that unit/boot rather than collecting unrelated logs.",
        "Connect the earliest relevant error to a concrete dependency or "
        "configuration problem. Preserve the current configuration and explain "
        "the impact before proposing a targeted edit or service restart.",
        "Check unit state and new logs, then test the service's actual function; "
        "an active unit alone does not prove the application works.",
        "Requires systemd; user and system services differ. Logs can contain "
        "private data. A restart can interrupt active work and is not diagnosis.",
        (("Ubuntu systemctl manual", "https://manpages.ubuntu.com/manpages/noble/man1/systemctl.1.html"),
         ("Ubuntu journalctl manual", "https://manpages.ubuntu.com/manpages/noble/man1/journalctl.1.html")),
    ),
    Reference(
        "network_dns", "Network connectivity, DNS, VPN and application access",
        ("network", "internet", "wifi", "wi fi", "dns", "vpn", "proxy", "connection",
         "website", "offline", "hostname", "resolve", "resolving"),
        "Separate link/IP connectivity, route, DNS lookup, TLS and application "
        "authentication. Identify affected apps and whether a VPN, proxy or "
        "sandbox changes their network path; compare another app on the same host.",
        "On NetworkManager systems inspect connection/device status. Inspect "
        "the active resolver and per-link DNS before suggesting DNS changes; "
        "systemd-resolved systems can use resolvectl status. Test only the "
        "relevant destination with the appropriate diagnostic tools.",
        "Repeat the original connection and confirm name resolution and app "
        "access while any required VPN/proxy remains in use.",
        "Not every distro uses NetworkManager or systemd-resolved. Do not "
        "disable the firewall, VPN or TLS verification as a blanket fix.",
        (("NetworkManager nmcli manual", "https://networkmanager.dev/docs/api/latest/nmcli.html"),
         ("Ubuntu resolvectl manual", "https://manpages.ubuntu.com/manpages/jammy/man1/resolvectl.1.html")),
    ),
    Reference(
        "resource_pressure", "Slowness, memory pressure and storage stalls",
        ("slow", "slowness", "lag", "lagging", "stutter", "stuttering", "freeze",
         "freezing", "memory", "ram", "oom", "disk full", "disk space", "cpu"),
        "Measure while the symptom occurs: CPU, memory/swap pressure, disk "
        "space and I/O waits. Kernel PSI, when available, distinguishes CPU, "
        "memory and I/O stalls; a single utilization snapshot is not a diagnosis.",
        "Tie the observed pressure to the affected process or workload. "
        "Propose a reversible, targeted reduction in contention, preserving "
        "unsaved work. Use separate measurements for storage capacity and latency.",
        "Repeat a comparable workload and compare responsiveness and pressure "
        "over time; report whether the user's symptom actually improved.",
        "PSI availability depends on kernel configuration. Software cannot "
        "create physical RAM or GPU capability; cache clearing and forced "
        "process killing are not universal performance fixes.",
        (("Linux kernel pressure stall information", "https://www.kernel.org/doc/html/latest/accounting/psi.html"),),
    ),
)


def _normalize(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    plurals = {"games": "game", "apps": "app", "programs": "program",
               "applications": "application", "folders": "folder", "shares": "share"}
    return " ".join(plurals.get(word, word) for word in words)


def _score(query: str, reference: Reference) -> int:
    # Whole terms/phrases only: e.g. 'aptitude' does not match 'apt'. Each alias
    # counts once, so repeating a word does not swamp other relevant evidence.
    padded = f" {query} "
    return sum(7 if " " in alias else 3 for alias in reference.aliases
               if f" {_normalize(alias)} " in padded)


def _serialize(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


@tool(
    name="linux_reference",
    description=(
        "Look up bundled Linux and cross-OS troubleshooting notes (Steam/Proton, "
        "Wine, SMB, NTFS, WSL, Flatpak, packages, graphics, services, DNS, resources). "
        "Internal reference lookup only: no computer inspection, network, or actions. "
        "Returns up to two sourced notes, not a diagnosis; gather actual evidence "
        "with separate tools and apply normal approval rules to any proposed action."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {
            "type": "string", "maxLength": MAX_QUERY_CHARS,
            "description": "Short symptom/topic and relevant app or operating systems; no secrets.",
        }},
        "required": ["query"],
        "additionalProperties": False,
    },
)
def linux_reference(query: str) -> str:
    """Return complete JSON within the budget without reflecting query data."""
    payload = {
        "status": "no_match", "scope": "bundled_reference_only", "reviewed": REVIEWED,
        "limitations": "Reviewed notes can age; confirm installed versions and actual evidence. "
                        "Recommendations do not execute or authorize actions.",
        "results": [],
    }
    if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
        payload.update(status="invalid_query", detail="Use a text query of at most 1000 characters.")
        return _serialize(payload)
    normalized = _normalize(query)
    ranked = sorted(((_score(normalized, ref), ref) for ref in REFERENCES),
                    key=lambda item: (-item[0], item[1].id))
    for score, reference in ranked:
        if score < 3 or len(payload["results"]) == MAX_RESULTS:
            break
        payload["results"].append(reference.result())
        if len(_serialize(payload)) > MAX_OUTPUT_CHARS:
            payload["results"].pop()
            break
    if payload["results"]:
        payload["status"] = "ok"
    else:
        payload["detail"] = "No matching bundled note. Ask for the app, operating systems and exact symptom."
    return _serialize(payload)
