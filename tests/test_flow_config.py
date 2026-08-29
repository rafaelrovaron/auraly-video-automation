from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import stat
from typing import Any, cast

import pytest

from auraly_pipeline.flow import FLOW_URL, FlowBrowserLaunchError
from auraly_pipeline.flow.config import (
    FlowGenerationConfig,
    FlowRuntimeConfig,
    resolve_flow_generation_config,
    resolve_flow_runtime_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _assert_config_error(**options: object) -> None:
    with pytest.raises(FlowBrowserLaunchError) as raised:
        resolve_flow_runtime_config(**cast(Any, options))

    assert raised.value.failed_step == "validate_config"
    assert str(raised.value) == ""


def test_generation_timeout_defaults_and_environment() -> None:
    config = resolve_flow_generation_config(environment={})

    assert config == FlowGenerationConfig(
        generation_timeout_seconds=600,
        download_timeout_seconds=120,
    )

    overridden = resolve_flow_generation_config(
        environment={
            "AURALY_FLOW_GENERATION_TIMEOUT_SECONDS": "900",
            "AURALY_FLOW_DOWNLOAD_TIMEOUT_SECONDS": "180",
        }
    )

    assert overridden.generation_timeout_seconds == 900
    assert overridden.download_timeout_seconds == 180


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("AURALY_FLOW_GENERATION_TIMEOUT_SECONDS", ""),
        ("AURALY_FLOW_GENERATION_TIMEOUT_SECONDS", "0"),
        ("AURALY_FLOW_GENERATION_TIMEOUT_SECONDS", "-1"),
        ("AURALY_FLOW_GENERATION_TIMEOUT_SECONDS", "1.5"),
        ("AURALY_FLOW_GENERATION_TIMEOUT_SECONDS", "3601"),
        ("AURALY_FLOW_DOWNLOAD_TIMEOUT_SECONDS", ""),
        ("AURALY_FLOW_DOWNLOAD_TIMEOUT_SECONDS", "0"),
        ("AURALY_FLOW_DOWNLOAD_TIMEOUT_SECONDS", "-1"),
        ("AURALY_FLOW_DOWNLOAD_TIMEOUT_SECONDS", "1.5"),
        ("AURALY_FLOW_DOWNLOAD_TIMEOUT_SECONDS", "3601"),
    ),
)
def test_generation_config_rejects_invalid_or_excessive_timeout(
    name: str,
    value: str,
) -> None:
    with pytest.raises(FlowBrowserLaunchError) as raised:
        resolve_flow_generation_config(environment={name: value})

    assert raised.value.failed_step == "validate_config"
    assert str(raised.value) == ""


def test_generation_config_exposes_no_target_or_browser_launch_override() -> None:
    import inspect

    assert set(inspect.signature(resolve_flow_generation_config).parameters) == {"environment"}


def test_config_precedence_is_cli_then_env_then_exact_defaults(tmp_path: Path) -> None:
    env = {
        "AURALY_FLOW_PROFILE_DIR": str(tmp_path / "env-profile"),
        "AURALY_FLOW_LOGIN_TIMEOUT_SECONDS": "120",
    }

    config = resolve_flow_runtime_config(
        profile_dir=tmp_path / "cli-profile",
        diagnostics_dir=tmp_path / "cli-diagnostics",
        login_timeout_seconds=45,
        environment=env,
        repository_root=REPOSITORY_ROOT,
        _local_state_root=tmp_path / "state",
    )

    assert config.profile_dir == (tmp_path / "cli-profile").resolve()
    assert config.diagnostics_dir == (tmp_path / "cli-diagnostics").resolve()
    assert config.lock_path == (tmp_path / "state" / "locks" / "google-flow-browser.lock").resolve()
    assert config.staging_root == (tmp_path / "state" / "staging" / "google-flow").resolve()
    assert config.login_timeout_seconds == 45
    assert config.navigation_timeout_seconds == 30
    assert config.flow_url == FLOW_URL


def test_config_uses_environment_then_local_state_defaults(tmp_path: Path) -> None:
    state = tmp_path / "state"

    config = resolve_flow_runtime_config(
        environment={
            "AURALY_FLOW_PROFILE_DIR": str(tmp_path / "env-profile"),
            "AURALY_FLOW_DIAGNOSTICS_DIR": str(tmp_path / "env-diagnostics"),
            "AURALY_FLOW_LOGIN_TIMEOUT_SECONDS": "120",
            "AURALY_FLOW_NAVIGATION_TIMEOUT_SECONDS": "15",
        },
        repository_root=REPOSITORY_ROOT,
        _local_state_root=state,
    )

    assert config.profile_dir == (tmp_path / "env-profile").resolve()
    assert config.diagnostics_dir == (tmp_path / "env-diagnostics").resolve()
    assert config.lock_path == (state / "locks" / "google-flow-browser.lock").resolve()
    assert config.staging_root == (state / "staging" / "google-flow").resolve()
    assert config.login_timeout_seconds == 120
    assert config.navigation_timeout_seconds == 15


def test_config_uses_exact_production_defaults_from_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "controlled-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    config = resolve_flow_runtime_config(environment={}, repository_root=REPOSITORY_ROOT)

    state = (home / ".auraly").resolve()
    assert config.profile_dir == state / "browser-profiles" / "google-flow"
    assert config.diagnostics_dir == state / "diagnostics" / "google-flow"
    assert config.lock_path == state / "locks" / "google-flow-browser.lock"
    assert config.staging_root == state / "staging" / "google-flow"
    assert config.login_timeout_seconds == 300
    assert config.navigation_timeout_seconds == 30
    assert config.flow_url == FLOW_URL


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("AURALY_FLOW_LOGIN_TIMEOUT_SECONDS", ""),
        ("AURALY_FLOW_LOGIN_TIMEOUT_SECONDS", "0"),
        ("AURALY_FLOW_LOGIN_TIMEOUT_SECONDS", "-1"),
        ("AURALY_FLOW_LOGIN_TIMEOUT_SECONDS", "not-an-integer"),
        ("AURALY_FLOW_NAVIGATION_TIMEOUT_SECONDS", ""),
        ("AURALY_FLOW_NAVIGATION_TIMEOUT_SECONDS", "0"),
        ("AURALY_FLOW_NAVIGATION_TIMEOUT_SECONDS", "-1"),
        ("AURALY_FLOW_NAVIGATION_TIMEOUT_SECONDS", "not-an-integer"),
    ),
)
def test_config_rejects_invalid_environment_timeout(name: str, value: str, tmp_path: Path) -> None:
    _assert_config_error(
        environment={name: value},
        repository_root=REPOSITORY_ROOT,
        _local_state_root=tmp_path / "state",
    )


@pytest.mark.parametrize("unsafe_name", ["profile", "diagnostics", "lock"])
def test_config_rejects_any_runtime_path_inside_repository(
    unsafe_name: str, tmp_path: Path
) -> None:
    options: dict[str, object] = {
        "profile_dir": tmp_path / "profile",
        "diagnostics_dir": tmp_path / "diagnostics",
        "_local_state_root": tmp_path / "state",
        "repository_root": REPOSITORY_ROOT,
        "environment": {},
    }
    if unsafe_name == "profile":
        options["profile_dir"] = REPOSITORY_ROOT / "unsafe-profile"
    elif unsafe_name == "diagnostics":
        options["diagnostics_dir"] = REPOSITORY_ROOT / "unsafe-diagnostics"
    else:
        options["_local_state_root"] = REPOSITORY_ROOT / "unsafe-state"

    _assert_config_error(**options)


@pytest.mark.parametrize(
    ("profile_dir", "diagnostics_dir"),
    (
        ("shared", "shared"),
        ("shared", "shared/diagnostics"),
        ("shared/profile", "shared"),
    ),
)
def test_config_rejects_profile_and_diagnostics_overlap(
    profile_dir: str, diagnostics_dir: str, tmp_path: Path
) -> None:
    _assert_config_error(
        profile_dir=tmp_path / profile_dir,
        diagnostics_dir=tmp_path / diagnostics_dir,
        repository_root=REPOSITORY_ROOT,
        _local_state_root=tmp_path / "state",
        environment={},
    )


@pytest.mark.parametrize(
    "runtime_dir", ("locks", "locks/profile", "staging", "staging/google-flow")
)
def test_config_rejects_profile_or_diagnostics_overlap_with_runtime_state(
    runtime_dir: str, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    _assert_config_error(
        profile_dir=state / runtime_dir,
        diagnostics_dir=tmp_path / "diagnostics",
        repository_root=REPOSITORY_ROOT,
        _local_state_root=state,
        environment={},
    )
    _assert_config_error(
        profile_dir=tmp_path / "profile",
        diagnostics_dir=state / runtime_dir,
        repository_root=REPOSITORY_ROOT,
        _local_state_root=state,
        environment={},
    )


def test_config_rejects_canonical_symlink_escape_into_repository(tmp_path: Path) -> None:
    link = tmp_path / "linked-repository"
    try:
        link.symlink_to(REPOSITORY_ROOT, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows account lacks the directory symlink privilege")
        raise

    _assert_config_error(
        profile_dir=link / "profile",
        diagnostics_dir=tmp_path / "diagnostics",
        repository_root=REPOSITORY_ROOT,
        _local_state_root=tmp_path / "state",
        environment={},
    )


def test_config_rejects_existing_file_as_runtime_directory(tmp_path: Path) -> None:
    profile_file = tmp_path / "profile-file"
    profile_file.write_text("not a directory", encoding="utf-8")

    _assert_config_error(
        profile_dir=profile_file,
        diagnostics_dir=tmp_path / "diagnostics",
        repository_root=REPOSITORY_ROOT,
        _local_state_root=tmp_path / "state",
        environment={},
    )


def test_config_keeps_fixed_flow_destination_despite_environment_value(tmp_path: Path) -> None:
    config = resolve_flow_runtime_config(
        environment={"AURALY_FLOW_URL": "https://attacker.invalid/flow"},
        repository_root=REPOSITORY_ROOT,
        _local_state_root=tmp_path / "state",
    )

    assert config.flow_url == FLOW_URL


def test_config_is_immutable(tmp_path: Path) -> None:
    config = resolve_flow_runtime_config(
        repository_root=REPOSITORY_ROOT,
        _local_state_root=tmp_path / "state",
    )

    assert isinstance(config, FlowRuntimeConfig)
    with pytest.raises(FrozenInstanceError):
        config.login_timeout_seconds = 1  # type: ignore[misc]


def test_config_constructor_cannot_override_fixed_flow_destination(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        FlowRuntimeConfig(
            profile_dir=tmp_path / "profile",
            diagnostics_dir=tmp_path / "diagnostics",
            lock_path=tmp_path / "lock",
            staging_root=tmp_path / "staging",
            login_timeout_seconds=1,
            navigation_timeout_seconds=1,
            flow_url="https://attacker.invalid/flow",  # type: ignore[call-arg]
        )


def test_config_validates_all_directory_ancestors_before_creating_any_runtime_directory(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    lock_ancestor = state / "locks"
    lock_ancestor.parent.mkdir()
    lock_ancestor.write_text("not a directory", encoding="utf-8")
    profile_dir = tmp_path / "profile"
    diagnostics_dir = tmp_path / "diagnostics"

    _assert_config_error(
        profile_dir=profile_dir,
        diagnostics_dir=diagnostics_dir,
        repository_root=REPOSITORY_ROOT,
        _local_state_root=state,
        environment={},
    )

    assert not profile_dir.exists()
    assert not diagnostics_dir.exists()
    assert not (state / "staging").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows roots are required for this regression")
def test_config_rejects_unavailable_windows_anchor_without_creating_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable_root = Path("Z:/").resolve(strict=False)
    original_exists = Path.exists
    anchor_checks = 0

    class AnchorLoopDetected(BaseException):
        pass

    def missing_anchor_exists(path: Path) -> bool:
        nonlocal anchor_checks
        if path == unavailable_root:
            anchor_checks += 1
            if anchor_checks > 2:
                raise AnchorLoopDetected
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", missing_anchor_exists)
    diagnostics_dir = tmp_path / "diagnostics"

    _assert_config_error(
        profile_dir=unavailable_root,
        diagnostics_dir=diagnostics_dir,
        repository_root=REPOSITORY_ROOT,
        _local_state_root=tmp_path / "state",
        environment={},
    )

    assert not diagnostics_dir.exists()
    assert not (tmp_path / "state").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows roots are required for this regression")
@pytest.mark.parametrize("unavailable_option", ("diagnostics", "local_state"))
def test_config_rejects_later_unavailable_windows_anchor_before_any_creation(
    unavailable_option: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable_root = Path("Z:/").resolve(strict=False)
    original_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: False if path == unavailable_root else original_exists(path),
    )
    profile_dir = tmp_path / "profile"
    diagnostics_dir = (
        unavailable_root if unavailable_option == "diagnostics" else tmp_path / "diagnostics"
    )
    local_state_root = unavailable_root if unavailable_option == "local_state" else tmp_path / "state"

    _assert_config_error(
        profile_dir=profile_dir,
        diagnostics_dir=diagnostics_dir,
        repository_root=REPOSITORY_ROOT,
        _local_state_root=local_state_root,
        environment={},
    )

    assert not profile_dir.exists()
    if diagnostics_dir != unavailable_root:
        assert not diagnostics_dir.exists()
    if local_state_root != unavailable_root:
        assert not local_state_root.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes are not honored on Windows")
def test_config_creates_each_new_runtime_component_with_private_mode(tmp_path: Path) -> None:
    state = tmp_path / "state"
    prior_umask = os.umask(0o022)
    try:
        resolve_flow_runtime_config(
            profile_dir=tmp_path / "profile",
            diagnostics_dir=tmp_path / "diagnostics",
            repository_root=REPOSITORY_ROOT,
            _local_state_root=state,
            environment={},
        )
    finally:
        os.umask(prior_umask)

    for directory in (
        tmp_path / "profile",
        tmp_path / "diagnostics",
        state,
        state / "locks",
        state / "staging",
        state / "staging" / "google-flow",
    ):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
