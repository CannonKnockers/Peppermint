# Bundled Linux and cross-OS troubleshooting references

Reviewed September 8, 2026. The initial `linux_reference` tool contains eleven
small notes, with Steam/Proton as the first game workflow. They help Peppermint
choose evidence to collect and distinguish the host, application, compatibility
layer, sandbox and remote operating system. They do not confer universal Linux
or application expertise and cannot establish what is wrong on a user's computer.

The tool performs a deterministic lookup over bundled Python data, with no file
reads, network requests, subprocesses or repairs. It returns at most two complete
notes, each separating observation, recommendation, verification, limitations
and sources. Input is limited to 1,000 characters; output is valid JSON bounded
to 4,000 characters. Unmatched or vague queries request the app, operating
systems and exact symptom. The query is not reflected into the result.

## Primary sources and scope

| Note | Official documentation consulted | Supported distinction |
| --- | --- | --- |
| Steam / Proton | [Valve Proton runtime options](https://github.com/ValveSoftware/Proton), [Valve Proton FAQ](https://github.com/ValveSoftware/Proton/wiki/Proton-FAQ), [Steam file verification](https://help.steampowered.com/en/faqs/view/0C48-FCBD-DA71-93EB) | Proton logging is a temporary launch option, with a default per-AppID home-directory log and an override directory. Each game has its own compatibility prefix; file verification is a Steam operation. |
| Wine applications | [WineHQ About](https://www.winehq.org/about), [WineHQ Getting Help](https://www.winehq.org/help) | Wine provides Windows API compatibility on Unix-like systems; app-specific compatibility information belongs in WineHQ's application/help resources. |
| SMB sharing | [Samba smbclient](https://www.samba.org/samba/docs/current/man-html/smbclient.1.html), [Microsoft SMB guest authentication](https://learn.microsoft.com/en-us/windows-server/storage/file-server/enable-insecure-guest-logons-smb2-and-smb3) | Share identity and account/domain matter. Samba documents local password prompting and credential-file protection. Microsoft discourages insecure guest access. |
| NTFS / dual boot | [Kernel NTFS3](https://docs.kernel.org/filesystems/ntfs3.html), [Ubuntu ntfs-3g](https://manpages.ubuntu.com/manpages/jammy/man8/mount.ntfs.8.html), [Ubuntu ntfsfix](https://manpages.ubuntu.com/manpages/jammy/man8/ntfsfix.8.html) | NTFS3 discourages forced dirty-volume mounts. ntfs-3g documents Windows hibernation/fast restart and read-only mounts. ntfsfix has narrower capabilities than Windows chkdsk. |
| Paths / formats | [Microsoft WSL filesystems](https://learn.microsoft.com/en-us/windows/wsl/filesystems), [case sensitivity](https://learn.microsoft.com/en-us/windows/wsl/case-sensitivity), [WSL Git line endings](https://learn.microsoft.com/en-us/windows/wsl/tutorials/wsl-git), [Windows PE format](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format), [Ubuntu ELF manual](https://manpages.ubuntu.com/manpages/noble/man5/elf.5.html) | Windows/Linux path and filename behavior differs; WSL has specific interoperability features. Text line-ending policy and executable architecture/format are separate issues. |
| Flatpak apps | [Flatpak sandbox permissions](https://docs.flatpak.org/en/latest/sandbox-permissions.html), [using Flatpak](https://docs.flatpak.org/en/latest/using-flatpak.html) | Apps have isolated runtimes and explicit permissions; portals can supply requested file access. Host filesystem exposure is not a default repair. |
| Native packages | [Ubuntu package management](https://ubuntu.com/server/docs/how-to/software/package-management/) | APT index refresh, installation and upgrade are distinct operations; distro and package origin determine applicable instructions. |
| Graphics runtime | [Flatpak runtime extensions](https://docs.flatpak.org/en/latest/extension.html), [Valve Proton](https://github.com/ValveSoftware/Proton) | Flatpak graphics extensions and Proton translation components add layers beyond the host driver. A host test alone cannot verify the app's environment. |
| Services / logs | [Ubuntu systemctl](https://manpages.ubuntu.com/manpages/noble/man1/systemctl.1.html), [Ubuntu journalctl](https://manpages.ubuntu.com/manpages/noble/man1/journalctl.1.html) | Service manager scope, unit state and targeted journal filtering support focused diagnosis. |
| Network / DNS | [NetworkManager nmcli](https://networkmanager.dev/docs/api/latest/nmcli.html), [Ubuntu resolvectl](https://manpages.ubuntu.com/manpages/jammy/man1/resolvectl.1.html) | NetworkManager connection state and resolver/per-link DNS state are distinct; these tools apply only where those components are in use. |
| Resource pressure | [Kernel PSI](https://www.kernel.org/doc/html/latest/accounting/psi.html) | CPU, memory and I/O contention cause measurable stall time. Trends over a workload carry more information than one utilization sample. |

The preservation of saves, credentials, mods and unsaved work, the instruction
to change one thing at a time, and the need to verify the original user workflow
are Peppermint's troubleshooting design choices inferred from these mechanisms.
They are not claims that upstream documentation prescribes this exact workflow.
Recommendations require separate live evidence and the usual action approvals.

Some official sites were only partly accessible during review: WineHQ's current
FAQ redirected to a bot challenge, several WineHQ/systemd manual requests returned
403, and Steam's English help page did not expose its full body to the browser.
WineHQ's indexed official About/Help text, Valve's repository, indexed Steam
support text (including its localized page), and Ubuntu's packaged systemd manuals
provided the narrower facts used here. No third-party forum workaround was used
as an authoritative repair instruction. The notes deliberately avoid versions,
game compatibility promises or commands that these sources could not establish.

## Validation

`python -m pytest -q tests/test_reference.py`: 33 tests passed. Coverage includes
natural language game/SMB/NTFS/Wine/WSL/Flatpak/package/graphics/service/DNS/resource
queries; absent matches and oversized inputs; deterministic ranking; all 121
pairs of topics remaining within the JSON budget with sources intact; primary
source metadata; and failure traps for file access, subprocesses and network
connections. A query containing shell syntax, a local file path and a URL stays
plain lookup text and is not reflected or executed.

This verifies lookup behavior. It does not benchmark the local model's reasoning,
validate fixes on every application, or prove a particular game now launches.
