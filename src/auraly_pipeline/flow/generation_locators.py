"""Exact semantic locators for safe Flow image-generation observation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast
from urllib.parse import urlsplit

from .generation_domain import (
    FlowCandidateObservation,
    FlowGenerationFailedStep,
    FlowGenerationLocatorName,
    FlowGenerationUiContractError,
)
from .locators import LocatorProtocol, PageProtocol, blocking_overlay_present


_LocatorT = TypeVar("_LocatorT", bound=LocatorProtocol)
_SAFE_CANDIDATE_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_FLOW_HOST = "labs.google"
_FLOW_PREFIX = "/fx/tools/flow"
_COMPLETED_ROLE = "completed"


class _CandidateLocatorProtocol(LocatorProtocol, Protocol):
    """The reviewed locator surface needed only after a semantic grid is resolved."""

    def get_by_role(
        self,
        role: str,
        *,
        name: str | None = None,
        exact: bool | None = None,
        include_hidden: bool | None = None,
    ) -> LocatorProtocol: ...

    def get_attribute(self, name: str) -> str | None: ...


@dataclass(frozen=True)
class _CandidateIdentitySource:
    """Explicit safe attributes from which a candidate fingerprint may be derived."""

    candidate_id_attribute: str
    completion_role_attribute: str
    completed_role: str = _COMPLETED_ROLE


@dataclass(frozen=True)
class _GenerationLocatorTarget:
    """Private route and identity policy; local pages require an exact injected allowlist."""

    identity_source: _CandidateIdentitySource
    local_urls: frozenset[str] = frozenset()
    permits_production_route: bool = False

    def allows_url(self, url: str) -> bool:
        if url in self.local_urls:
            return True
        parsed = urlsplit(url)
        return (
            self.permits_production_route
            and parsed.scheme == "https"
            and parsed.netloc == _FLOW_HOST
            and (parsed.path == _FLOW_PREFIX or parsed.path.startswith(f"{_FLOW_PREFIX}/"))
            and not parsed.query
            and not parsed.fragment
        )


_PRODUCTION_IDENTITY_SOURCE = _CandidateIdentitySource(
    candidate_id_attribute="data-candidate-id",
    completion_role_attribute="data-completion-role",
)
_LOCAL_FIXTURE_IDENTITY_SOURCE = _CandidateIdentitySource(
    candidate_id_attribute="data-flow-candidate-id",
    completion_role_attribute="data-flow-completion-role",
)
_PRODUCTION_GENERATION_TARGET = _GenerationLocatorTarget(
    identity_source=_PRODUCTION_IDENTITY_SOURCE,
    permits_production_route=True,
)


def _local_test_target(
    *local_urls: str,
    identity_source: _CandidateIdentitySource = _LOCAL_FIXTURE_IDENTITY_SOURCE,
) -> _GenerationLocatorTarget:
    """Build the private deterministic-page seam without accepting arbitrary local files."""
    if not local_urls or any(
        urlsplit(url).scheme != "file" or urlsplit(url).query or urlsplit(url).fragment
        for url in local_urls
    ):
        raise ValueError("local generation targets require exact file URLs without suffixes")
    return _GenerationLocatorTarget(
        identity_source=identity_source,
        local_urls=frozenset(local_urls),
    )


def resolve_reference_input(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve the one enabled reference image input or fail closed."""
    _require_safe_generation_route(page, _target)
    return _require_unique(
        page,
        page.get_by_label("Reference image", exact=True),
        locator_name="REFERENCE_INPUT",
        failed_step="upload_reference",
    )


def resolve_upload_complete(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve the positive upload-completion signal, never generic page readiness."""
    _require_safe_generation_route(page, _target)
    return _require_unique(
        page,
        page.get_by_role("status", name="Reference upload complete", exact=True),
        locator_name="UPLOAD_COMPLETE",
        failed_step="verify_reference",
    )


def resolve_generation_prompt(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve the exact prompt control whose value is later verified in memory."""
    _require_safe_generation_route(page, _target)
    return _require_unique(
        page,
        page.get_by_label("Prompt", exact=True),
        locator_name="GENERATION_PROMPT",
        failed_step="fill_prompt",
    )


def resolve_generate_control(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve the single enabled irreversible Generate control."""
    _require_safe_generation_route(page, _target)
    return _require_unique(
        page,
        page.get_by_role("button", name="Generate", exact=True),
        locator_name="GENERATE_CONTROL",
        failed_step="dispatch_generate",
    )


def resolve_generating_indicator(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve recognized positive evidence that one Generate dispatch started."""
    _require_safe_generation_route(page, _target)
    return _require_unique(
        page,
        page.get_by_role("status", name="Generating", exact=True),
        locator_name="GENERATING_INDICATOR",
        failed_step="confirm_dispatch",
    )


def observe_completed_candidate_slots(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> tuple[FlowCandidateObservation, ...]:
    """Enumerate unique completed slots in provider semantic order without retaining UI text."""
    _require_safe_generation_route(page, _target)
    candidates = _validated_candidate_slots(
        page,
        identity_source=_target.identity_source,
        failed_step="observe_candidates",
    )
    return tuple(
        FlowCandidateObservation(fingerprint=fingerprint, semantic_order=index, completed=True)
        for index, (_candidate, fingerprint) in enumerate(candidates)
    )


def resolve_candidate_2k_action(
    page: PageProtocol[_LocatorT],
    fingerprint: str,
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve the unique enabled 2K action for one previously observed candidate fingerprint."""
    _require_safe_generation_route(page, _target)
    candidates = _validated_candidate_slots(
        page,
        identity_source=_target.identity_source,
        failed_step="request_2k",
    )
    matching_candidates = [
        candidate for candidate, observed_fingerprint in candidates if observed_fingerprint == fingerprint
    ]
    if len(matching_candidates) != 1:
        raise FlowGenerationUiContractError(
            failed_step="request_2k", failed_locator="CANDIDATE_SLOT"
        )
    action = matching_candidates[0].get_by_role("button", name="Request 2K", exact=True)
    return cast(
        _LocatorT,
        _require_unique(
            page,
            action,
            locator_name="CANDIDATE_2K_ACTION",
            failed_step="request_2k",
        ),
    )


def _require_safe_generation_route(
    page: PageProtocol[LocatorProtocol],
    target: _GenerationLocatorTarget,
) -> None:
    """Reject redirects before DOM inspection through the injected private route policy."""
    url = getattr(page, "url", "")
    if target.allows_url(url):
        return
    raise FlowGenerationUiContractError(failed_step="open_workspace")


def _validated_candidate_slots(
    page: PageProtocol[_LocatorT],
    *,
    identity_source: _CandidateIdentitySource,
    failed_step: FlowGenerationFailedStep,
) -> tuple[tuple[_CandidateLocatorProtocol, str], ...]:
    """Validate every visible grid slot before any one candidate action can be returned."""
    grid = _require_unique(
        page,
        page.get_by_role("list", name="Generated candidates", exact=True),
        locator_name="CANDIDATE_GRID",
        failed_step=failed_step,
    )
    candidates: list[tuple[_CandidateLocatorProtocol, str]] = []
    for candidate in cast(_CandidateLocatorProtocol, grid).get_by_role(
        "listitem", include_hidden=True
    ).all():
        candidate_locator = cast(_CandidateLocatorProtocol, candidate)
        fingerprint = _fingerprint_for_candidate(candidate_locator, identity_source)
        if not _is_actionable(candidate_locator) or fingerprint is None:
            raise FlowGenerationUiContractError(
                failed_step=failed_step, failed_locator="CANDIDATE_SLOT"
            )
        candidates.append((candidate_locator, fingerprint))
    if not candidates or len({fingerprint for _candidate, fingerprint in candidates}) != len(candidates):
        raise FlowGenerationUiContractError(
            failed_step=failed_step, failed_locator="CANDIDATE_SLOT"
        )
    return tuple(candidates)


def _require_unique(
    page: PageProtocol[LocatorProtocol],
    locator: _LocatorT,
    *,
    locator_name: FlowGenerationLocatorName,
    failed_step: FlowGenerationFailedStep,
) -> _LocatorT:
    if blocking_overlay_present(page):
        raise FlowGenerationUiContractError(
            failed_step=failed_step, failed_locator=locator_name
        )
    applicable = tuple(candidate for candidate in locator.all() if _is_actionable(candidate))
    if len(applicable) != 1:
        raise FlowGenerationUiContractError(
            failed_step=failed_step, failed_locator=locator_name
        )
    return cast(_LocatorT, applicable[0])


def _is_actionable(candidate: LocatorProtocol) -> bool:
    """Reject native-disabled and explicit ARIA-disabled semantic controls alike."""
    aria_disabled = getattr(candidate, "get_attribute", lambda _name: None)("aria-disabled")
    return candidate.is_visible() and candidate.is_enabled() and aria_disabled != "true"


def _safe_candidate_key(value: str | None) -> bool:
    return value is not None and _SAFE_CANDIDATE_KEY.fullmatch(value) is not None


def _fingerprint_for_candidate(
    candidate: _CandidateLocatorProtocol,
    identity_source: _CandidateIdentitySource,
) -> str | None:
    slot_key = candidate.get_attribute(identity_source.candidate_id_attribute)
    completion_role = candidate.get_attribute(identity_source.completion_role_attribute)
    if (
        slot_key is None
        or not _safe_candidate_key(slot_key)
        or completion_role != identity_source.completed_role
    ):
        return None
    return _candidate_fingerprint(slot_key, completion_role)


def _candidate_fingerprint(slot_key: str, completion_role: str) -> str:
    payload = json.dumps(
        {"completion_role": completion_role, "slot_key": slot_key},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
