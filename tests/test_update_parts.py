from __future__ import annotations

import hashlib
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mercury_app.update_checker import UpdateInfo, UpdatePart, _info_from_manifest
from mercury_app import updater


def test_manifest_parses_gitee_split_parts() -> None:
    payload = {
        "version": "v1.2.3",
        "asset_name": "MIP-DragonScience-v1.2.3.exe",
        "gitee_download_parts": [
            {
                "name": "MIP-DragonScience-v1.2.3.exe.part01",
                "url": "https://gitee.com/example/releases/download/v1.2.3/part01",
                "size": 10,
                "sha256": "a" * 64,
            },
            {
                "name": "MIP-DragonScience-v1.2.3.exe.part02",
                "download_url": "https://gitee.com/example/releases/download/v1.2.3/part02",
                "bytes": 20,
                "checksum": "b" * 64,
            },
        ],
    }

    info = _info_from_manifest(
        payload,
        current_version="1.0.0",
        source_url="https://gitee.com/dragonMalong/mercury-smp-analyzer/raw/main/updates/latest.json",
    )

    assert info.update_available is True
    assert info.asset_name == "MIP-DragonScience-v1.2.3.exe"
    assert [part.name for part in info.download_parts] == [
        "MIP-DragonScience-v1.2.3.exe.part01",
        "MIP-DragonScience-v1.2.3.exe.part02",
    ]
    assert [part.size for part in info.download_parts] == [10, 20]
    assert info.download_parts[1].sha256 == "b" * 64


def test_download_update_merges_split_parts(tmp_path, monkeypatch) -> None:
    content = b"first chunk" + b"second chunk"
    first = tmp_path / "part01"
    second = tmp_path / "part02"
    first.write_bytes(b"first chunk")
    second.write_bytes(b"second chunk")
    download_dir = tmp_path / "downloads"
    progress_events: list[tuple[int, int]] = []

    monkeypatch.setattr(updater, "_download_dir", lambda: download_dir)

    info = UpdateInfo(
        current_version="1.0.0",
        latest_version="1.2.3",
        update_available=True,
        release_url="",
        download_url="",
        asset_name="MIP-DragonScience-v1.2.3.exe",
        release_name="v1.2.3",
        release_notes="",
        published_at="",
        sha256=hashlib.sha256(content).hexdigest(),
        download_parts=(
            UpdatePart(
                name="part01",
                url=first.as_uri(),
                size=first.stat().st_size,
                sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
            ),
            UpdatePart(
                name="part02",
                url=second.as_uri(),
                size=second.stat().st_size,
                sha256=hashlib.sha256(second.read_bytes()).hexdigest(),
            ),
        ),
    )

    output = updater.download_update(info, progress_callback=lambda done, total: progress_events.append((done, total)))

    assert output.name == "Mercury-SMP-Analyzer-zh-CN-v1.2.3.exe"
    assert output.read_bytes() == content
    assert progress_events
    assert progress_events[-1] == (len(content), len(content))
    assert not list(download_dir.glob("*.part??"))
