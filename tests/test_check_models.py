"""scripts/check_models.py — reports weight presence, never downloads.

Loaded from its file path (the same technique
``tests/test_tenant_isolation.py`` uses for a migration file), since
``scripts/`` is not an importable package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).resolve().parent.parent / "scripts" / "check_models.py"
    spec = importlib.util.spec_from_file_location("check_models", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_models = _load_module()


def test_an_empty_or_missing_directory_is_not_a_blocking_failure(tmp_path) -> None:
    """The local-dev default (TTS_BACKEND unset -> espeak) needs no weights
    at all, so a bare-metal checkout with no models/ directory yet must not
    fail this check."""
    missing_dir = tmp_path / "does-not-exist"
    code = check_models.main(["--models-dir", str(missing_dir)], env={})
    assert code == 0


def test_every_category_is_reported_missing_for_an_empty_root(tmp_path, capsys) -> None:
    code = check_models.main(["--models-dir", str(tmp_path)], env={})
    assert code == 0
    output = capsys.readouterr().out
    for category in check_models._CATEGORIES:
        assert f"[missing] {category:10}" in output


def test_tts_production_blocks_when_the_tts_directory_is_empty(tmp_path) -> None:
    code = check_models.main(
        ["--models-dir", str(tmp_path)], env={"TTS_BACKEND": "production"}
    )
    assert code == 1


def test_tts_production_passes_once_a_voice_file_exists(tmp_path) -> None:
    tts_dir = tmp_path / "tts"
    tts_dir.mkdir()
    (tts_dir / "voice.onnx").write_bytes(b"fake")

    code = check_models.main(
        ["--models-dir", str(tmp_path)], env={"TTS_BACKEND": "production"}
    )
    assert code == 0


def test_an_empty_directory_still_counts_as_missing(tmp_path) -> None:
    """A category folder that exists but has nothing in it (e.g. created by
    a half-finished copy) must not read as 'present'."""
    (tmp_path / "tts").mkdir()
    code = check_models.main(
        ["--models-dir", str(tmp_path)], env={"TTS_BACKEND": "production"}
    )
    assert code == 1


def test_other_real_backends_never_block_startup(tmp_path) -> None:
    """vLLM and ComfyUI are separate services this repo's compose file does
    not run — their weights are reported on for convenience, but a missing
    llm/ or image/ directory must never fail this container's own
    pre-flight check."""
    code = check_models.main(
        ["--models-dir", str(tmp_path)],
        env={"LLM_CLIENT": "vllm", "IMAGE_BACKEND": "comfyui", "GPU_RUNTIME": "real"},
    )
    assert code == 0


def test_default_env_falls_back_to_os_environ(tmp_path, monkeypatch) -> None:
    """Every other test passes env= explicitly; none of them would have
    caught a regression in the env=None path main() actually runs under
    when invoked for real (sys.exit(main()), as docker-compose.yml's
    gpu-worker command does) - which is exactly what broke: line 73 read
    the raw env=None parameter instead of the resolved_env built from
    os.environ two lines above it."""
    monkeypatch.delenv("TTS_BACKEND", raising=False)
    code = check_models.main(["--models-dir", str(tmp_path)])
    assert code == 0


def test_models_dir_falls_back_to_the_env_var(tmp_path, monkeypatch) -> None:
    tts_dir = tmp_path / "tts"
    tts_dir.mkdir()
    (tts_dir / "voice.onnx").write_bytes(b"fake")

    code = check_models.main([], env={"MODELS_DIR": str(tmp_path), "TTS_BACKEND": "production"})
    assert code == 0


@pytest.mark.parametrize("category", list(check_models._CATEGORIES))
def test_every_documented_category_maps_to_a_real_download_models_destination(
    category: str,
) -> None:
    """Cross-check against scripts/download-models.ps1's own -Dest paths,
    so the two scripts cannot silently drift apart."""
    ps1 = (Path(__file__).resolve().parent.parent / "scripts" / "download-models.ps1").read_text(
        encoding="utf-8"
    )
    assert f'"{category}\\' in ps1, f"{category!r} has no matching -Dest in download-models.ps1"
