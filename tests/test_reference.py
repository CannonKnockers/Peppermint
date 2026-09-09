"""The reference lookup is relevant, sourced, bounded and has no side effects."""

import builtins
import itertools
import json
from pathlib import Path
import socket
import subprocess
from urllib.parse import urlparse
import urllib.request

import pytest

from peppermint.daemon.tools.reference import (
    MAX_OUTPUT_CHARS, MAX_QUERY_CHARS, REFERENCES, REVIEWED, linux_reference,
)


@pytest.mark.parametrize(("query", "expected"), [
    ("hey this games not working, fix it", "steam_proton"),
    ("game won't start Steam Proton", "steam_proton"),
    ("Windows share access denied", "smb_sharing"),
    ("My NAS shared folders reject my login", "smb_sharing"),
    ("Windows NTFS drive read-only after dual boot", "ntfs_dual_boot"),
    ("Fast Startup and hibernation", "ntfs_dual_boot"),
    ("Windows applications through Wine", "wine_apps"),
    ("Why won't the .exe work on linux", "wine_apps"),
    ("WSL Windows path case sensitivity", "cross_os_paths"),
    ("bad interpreter after CRLF conversion", "cross_os_paths"),
    ("Flatpak app cannot find files with file chooser", "flatpak_apps"),
    ("apt dependency broken after adding PPA", "native_packages"),
    ("Vulkan DXVK GPU driver error", "gpu_runtime"),
    ("systemd daemon service is failing", "services_logs"),
    ("VPN DNS can't resolve hostname", "network_dns"),
    ("my computer is slow and running out of RAM", "resource_pressure"),
])
def test_natural_language_finds_the_relevant_reference(query, expected):
    result = json.loads(linux_reference(query))
    assert result["status"] == "ok"
    assert result["results"][0]["id"] == expected


@pytest.mark.parametrize("query", ["", "   ", "!!!", "banana bread recipe",
                                   "aptitude test", "broken", "Linux Windows"])
def test_unknown_or_underspecified_topic_requests_detail(query):
    result = json.loads(linux_reference(query))
    assert result["status"] == "no_match"
    assert result["results"] == []
    assert "exact symptom" in result["detail"]


@pytest.mark.parametrize("query", [None, 10, {}, [], "x" * (MAX_QUERY_CHARS + 1)])
def test_invalid_queries_are_bounded_and_not_reflected(query):
    result = json.loads(linux_reference(query))
    assert result["status"] == "invalid_query"
    assert result["results"] == []
    assert len(linux_reference(query)) <= MAX_OUTPUT_CHARS


def test_two_related_layers_and_order_are_deterministic():
    query = "Steam Proton graphics Vulkan DXVK driver"
    output = linux_reference(query)
    assert output == linux_reference(query.upper())
    assert output == linux_reference(query + " " + query)
    assert {r["id"] for r in json.loads(output)["results"]} == {"steam_proton", "gpu_runtime"}


def test_every_topic_pair_fits_complete_json_with_sources():
    for first, second in itertools.product(REFERENCES, repeat=2):
        query = " ".join(first.aliases + second.aliases)
        output = linux_reference(query)
        assert len(output) <= MAX_OUTPUT_CHARS
        result = json.loads(output)
        assert 1 <= len(result["results"]) <= 2
        for entry in result["results"]:
            assert entry["sources"]
            assert entry["verify"]
            assert entry["limitations"]


def test_sources_are_reviewed_primary_documentation():
    approved_hosts = {
        "github.com", "help.steampowered.com", "www.winehq.org", "www.samba.org",
        "learn.microsoft.com", "docs.kernel.org", "manpages.ubuntu.com",
        "docs.flatpak.org", "ubuntu.com", "networkmanager.dev", "www.kernel.org",
    }
    for reference in REFERENCES:
        entry = reference.result()
        assert entry["reviewed"] == REVIEWED
        for source in entry["sources"]:
            url = urlparse(source["url"])
            assert url.scheme == "https"
            assert url.hostname in approved_hosts
            assert source["title"]
            if url.hostname == "github.com":
                assert url.path.startswith("/ValveSoftware/")


def test_reference_lookup_does_not_read_execute_or_connect(monkeypatch, tmp_path):
    secret = tmp_path / "private.txt"
    secret.write_text("secret-not-for-output")

    def forbidden(*args, **kwargs):
        pytest.fail("Reference lookup attempted external I/O or execution")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "read_text", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(urllib.request, "urlopen", forbidden)
        query = f"Steam $(cat {secret}) https://example.invalid/steal"
        output = linux_reference(query)

    assert "secret-not-for-output" not in output
    assert str(secret) not in output
    assert "example.invalid" not in output
    assert json.loads(output)["scope"] == "bundled_reference_only"


def test_notes_keep_data_preservation_and_evidence_boundaries():
    entries = {reference.id: reference.result() for reference in REFERENCES}
    assert "Do not delete compatdata" in entries["steam_proton"]["limitations"]
    assert "do not force-mount" in entries["ntfs_dual_boot"]["limitations"].lower()
    assert "never request passwords" in entries["smb_sharing"]["recommend"]
    assert "blanket host" in entries["flatpak_apps"]["limitations"]
    for entry in entries.values():
        assert all(entry[key] for key in ("observe", "recommend", "verify", "limitations"))
