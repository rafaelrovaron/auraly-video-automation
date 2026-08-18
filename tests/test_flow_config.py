from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from auraly_pipeline.flow import FLOW_URL, FlowBrowserLaunchError
from auraly_pipeline.flow.config import FlowRuntimeConfig, resolve_flow_runtime_config


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _assert_config_error(**options: object) -> None:
    with pytest.raises(FlowBrowserLaunchError) as raised:
        resolve_flow_runtime_config(**cast(Any, options))

    assert raised.value.failed_step == "validate_config"
    assert str(raised.value) == ""


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


@pytest.mark.parametrize("runtime_dir", ("locks", "staging", "staging/google-flow"))
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
    except OSError:
        pytest.skip("symlink creation is unavailable for this test account")

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
