"""scripts/download_models.py — the Python equivalent of download-models.ps1.

Loaded from its file path (the same technique tests/test_check_models.py
uses), since scripts/ is not an importable package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_module(name: str, filename: str):
    path = Path(__file__).resolve().parent.parent / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # download_models.py's @dataclass needs its own module registered in
    # sys.modules *before* exec (it resolves `from __future__ import
    # annotations` string annotations via sys.modules[cls.__module__]) -
    # check_models.py's test doesn't need this since it has no dataclass.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


download_models = _load_module("download_models", "download_models.py")
check_models = _load_module("check_models", "check_models.py")


def _run(args: list[str]) -> tuple[int, list[str]]:
    """Runs main() with snapshot_download mocked out, returning (exit_code,
    repo_ids actually requested) - no test here touches the network."""
    with patch("huggingface_hub.snapshot_download") as mock_download:
        mock_download.return_value = "/tmp/fake"
        code = download_models.main(args)
    return code, [call.kwargs.get("repo_id") for call in mock_download.call_args_list]


def test_default_run_fetches_video_and_lipsync_exactly_once(tmp_path) -> None:
    code, repos = _run(["--models-root", str(tmp_path)])
    assert code == 0
    assert repos.count("Wan-AI/Wan2.2-TI2V-5B") == 1
    assert "ByteDance/LatentSync-1.5" in repos
    assert "TMElyralab/MuseTalk" in repos
    assert "vinthony/SadTalker" in repos


def test_skip_video_actually_skips_it(tmp_path) -> None:
    """Regression test: build_jobs() used to append the video job
    unconditionally *and* main() appended it again unless --skip-video was
    set, so passing --skip-video still downloaded it anyway."""
    code, repos = _run(["--models-root", str(tmp_path), "--skip-video"])
    assert code == 0
    assert "Wan-AI/Wan2.2-TI2V-5B" not in repos


def test_skip_lipsync_actually_skips_it(tmp_path) -> None:
    code, repos = _run(["--models-root", str(tmp_path), "--skip-lipsync"])
    assert code == 0
    assert "ByteDance/LatentSync-1.5" not in repos
    assert "TMElyralab/MuseTalk" not in repos
    assert "vinthony/SadTalker" not in repos


@pytest.mark.parametrize(
    ("variant", "expected_repo"),
    [
        ("qwen3-30b-a3b", "Qwen/Qwen3-30B-A3B-Instruct-2507"),
        ("qwen3-30b-a3b-awq", "RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16"),
        ("qwen3-14b", "Qwen/Qwen3-14B"),
        ("gemma-3-12b", "google/gemma-3-12b-it"),
    ],
)
def test_llm_variant_selects_the_right_repo(tmp_path, variant, expected_repo) -> None:
    code, repos = _run(
        [
            "--models-root",
            str(tmp_path),
            "--llm-variant",
            variant,
            "--skip-video",
            "--skip-lipsync",
        ]
    )
    assert code == 0
    assert expected_repo in repos
    # Exactly one LLM repo per run - the other variants' repos are absent.
    all_llm_repos = {
        "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16",
        "Qwen/Qwen3-14B",
        "google/gemma-3-12b-it",
    }
    assert set(repos) & all_llm_repos == {expected_repo}


def test_piper_voice_fa_all_uses_a_wildcard_pattern(tmp_path) -> None:
    with patch("huggingface_hub.snapshot_download") as mock_download:
        mock_download.return_value = "/tmp/fake"
        download_models.main(
            [
                "--models-root",
                str(tmp_path),
                "--piper-voice-fa",
                "all",
                "--skip-video",
                "--skip-lipsync",
            ]
        )
        piper_calls = [
            c
            for c in mock_download.call_args_list
            if c.kwargs.get("repo_id") == "rhasspy/piper-voices"
        ]
    assert len(piper_calls) == 1
    assert piper_calls[0].kwargs["allow_patterns"] == ["fa/**"]


def test_a_download_failure_is_recorded_and_does_not_crash_the_run(tmp_path) -> None:
    """Any exception during one repo's download (HTTP error, proxy error,
    timeout, ...) must be caught and recorded as a failure, not propagate -
    a real network hiccup on repo N should not lose the results of repos
    1..N-1 or skip N+1..end."""
    with patch("huggingface_hub.snapshot_download", side_effect=RuntimeError("boom")):
        code = download_models.main(
            ["--models-root", str(tmp_path), "--skip-video", "--skip-lipsync"]
        )
    assert code == 1


def test_output_layout_matches_what_check_models_py_reads(tmp_path) -> None:
    """The folder layout this script produces under --models-root must be
    exactly what scripts/check_models.py looks for under MODELS_DIR."""
    code, _ = _run(["--models-root", str(tmp_path)])
    assert code == 0
    for category in check_models._CATEGORIES:
        assert (tmp_path / category).is_dir(), f"{category!r} was never created under --models-root"


@pytest.mark.parametrize("category", list(check_models._CATEGORIES))
def test_every_documented_category_maps_to_a_real_destination(category: str) -> None:
    """Cross-check against this script's own job destinations, so it and
    check_models.py cannot silently drift apart (same intent as
    test_check_models.py's equivalent check against download-models.ps1)."""
    jobs = download_models.build_all_jobs(
        "qwen3-30b-a3b", "amir-medium", skip_video=False, skip_lipsync=False
    )
    assert any(job.dest.startswith(f"{category}/") for job in jobs), (
        f"{category!r} has no matching job destination in scripts/download_models.py"
    )
