"""Safe local runtime configuration for the fixed Google Flow destination."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Literal, Mapping

from .domain import FLOW_URL, FlowBrowserLaunchError


_DEFAULT_LOGIN_TIMEOUT_SECONDS = 300
_DEFAULT_NAVIGATION_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class FlowRuntimeConfig:
    """Validated local paths and timeouts for one Flow preflight runtime."""

    profile_dir: Path
    diagnostics_dir: Path
    lock_path: Path
    staging_root: Path
    login_timeout_seconds: int
    navigation_timeout_seconds: int
    flow_url: Literal["https://labs.google/fx/tools/flow"] = field(default=FLOW_URL, init=False)


def resolve_flow_runtime_config(
    *,
    profile_dir: Path | None = None,
    diagnostics_dir: Path | None = None,
    login_timeout_seconds: int | None = None,
    navigation_timeout_seconds: int | None = None,
    environment: Mapping[str, str] | None = None,
    repository_root: Path | None = None,
    _local_state_root: Path | None = None,
) -> FlowRuntimeConfig:
    """Resolve approved options and validate every runtime path before creating directories."""
    try:
        values = os.environ if environment is None else environment
        local_state_root = _canonical_path(
            _local_state_root if _local_state_root is not None else Path.home() / ".auraly"
        )
        canonical_repository_root = _canonical_path(
            repository_root if repository_root is not None else Path(__file__).parents[3]
        )
        resolved_profile_dir = _canonical_path(
            _resolve_path_option(
                cli_value=profile_dir,
                environment=values,
                environment_name="AURALY_FLOW_PROFILE_DIR",
                default=local_state_root / "browser-profiles" / "google-flow",
            )
        )
        resolved_diagnostics_dir = _canonical_path(
            _resolve_path_option(
                cli_value=diagnostics_dir,
                environment=values,
                environment_name="AURALY_FLOW_DIAGNOSTICS_DIR",
                default=local_state_root / "diagnostics" / "google-flow",
            )
        )
        lock_path = _canonical_path(local_state_root / "locks" / "google-flow-browser.lock")
        staging_root = _canonical_path(local_state_root / "staging" / "google-flow")
        resolved_login_timeout_seconds = _resolve_timeout_option(
            cli_value=login_timeout_seconds,
            environment=values,
            environment_name="AURALY_FLOW_LOGIN_TIMEOUT_SECONDS",
            default=_DEFAULT_LOGIN_TIMEOUT_SECONDS,
        )
        resolved_navigation_timeout_seconds = _resolve_timeout_option(
            cli_value=navigation_timeout_seconds,
            environment=values,
            environment_name="AURALY_FLOW_NAVIGATION_TIMEOUT_SECONDS",
            default=_DEFAULT_NAVIGATION_TIMEOUT_SECONDS,
        )

        runtime_paths = (
            resolved_profile_dir,
            resolved_diagnostics_dir,
            lock_path.parent,
            staging_root,
        )
        _validate_runtime_paths(runtime_paths, canonical_repository_root)
        _create_runtime_directories(
            resolved_profile_dir,
            resolved_diagnostics_dir,
            lock_path,
            staging_root,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        raise FlowBrowserLaunchError(failed_step="validate_config") from None

    return FlowRuntimeConfig(
        profile_dir=resolved_profile_dir,
        diagnostics_dir=resolved_diagnostics_dir,
        lock_path=lock_path,
        staging_root=staging_root,
        login_timeout_seconds=resolved_login_timeout_seconds,
        navigation_timeout_seconds=resolved_navigation_timeout_seconds,
    )


def _canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _resolve_path_option(
    *,
    cli_value: Path | None,
    environment: Mapping[str, str],
    environment_name: str,
    default: Path,
) -> Path:
    if cli_value is not None:
        return cli_value
    if environment_name in environment:
        value = environment[environment_name]
        if not value.strip():
            raise ValueError("empty runtime path")
        return Path(value)
    return default


def _resolve_timeout_option(
    *,
    cli_value: int | None,
    environment: Mapping[str, str],
    environment_name: str,
    default: int,
) -> int:
    if cli_value is not None:
        value = cli_value
    elif environment_name in environment:
        value = int(environment[environment_name])
    else:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("timeout must be a positive integer")
    return value


def _validate_runtime_paths(runtime_paths: tuple[Path, ...], repository_root: Path) -> None:
    for candidate in runtime_paths:
        if _contains_or_equals(repository_root, candidate):
            raise ValueError("runtime path is inside repository")
    for position, candidate in enumerate(runtime_paths):
        for other in runtime_paths[position + 1 :]:
            if _contains_or_equals(candidate, other) or _contains_or_equals(other, candidate):
                raise ValueError("runtime paths overlap")


def _contains_or_equals(parent: Path, child: Path) -> bool:
    return child.is_relative_to(parent)


def _create_runtime_directories(
    profile_dir: Path,
    diagnostics_dir: Path,
    lock_path: Path,
    staging_root: Path,
) -> None:
    directories = (profile_dir, diagnostics_dir, lock_path.parent, staging_root)
    for directory in directories:
        _validate_directory_ancestors(directory)
    if lock_path.exists() and lock_path.is_dir():
        raise ValueError("runtime lock path is unusable")

    for directory in directories:
        _create_private_directory_tree(directory)


def _validate_directory_ancestors(directory: Path) -> None:
    for candidate in (directory, *directory.parents):
        if candidate.exists():
            if not candidate.is_dir():
                raise ValueError("runtime directory is unusable")
        elif candidate.parent == candidate:
            raise ValueError("runtime directory anchor is unavailable")


def _create_private_directory_tree(directory: Path) -> None:
    missing_directories: list[Path] = []
    candidate = directory
    while not candidate.exists():
        missing_directories.append(candidate)
        parent = candidate.parent
        if parent == candidate:
            raise ValueError("runtime directory anchor is unavailable")
        candidate = parent
    for missing_directory in reversed(missing_directories):
        missing_directory.mkdir(mode=0o700, exist_ok=True)
