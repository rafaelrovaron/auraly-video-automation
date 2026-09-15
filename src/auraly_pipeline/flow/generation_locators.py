"""Exact semantic locators for safe Flow image-generation observation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar, cast
from urllib.parse import urlsplit

from .generation_domain import (
    FlowCandidateObservation,
    FlowGenerationFailedStep,
    FlowGenerationLocatorName,
    FlowGenerationUiContractError,
)
from .domain import FlowFailureCategory, FlowPreflightControl, FlowPrimaryFailure
from .locators import LocatorProtocol, PageProtocol, SemanticRole, blocking_overlay_present


_LocatorT = TypeVar("_LocatorT", bound=LocatorProtocol)
_SAFE_CANDIDATE_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_FLOW_HOST = "labs.google"
_FLOW_PREFIX = "/fx/tools/flow"
_CANONICAL_FLOW_HOST = "flow.google.com"
_CANONICAL_PROJECT_PATH = re.compile(
    r"^/project/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_COMPLETED_ROLE = "completed"
_LIVE_UPLOAD_BUTTON_NAMES = ("Menu para adicionar arquivos",)
_LIVE_UPLOAD_ITEM_NAMES = ("Enviar",)
_GENERATE_NAMES = ("Generate", "Iniciar geração")


class _ScopedLocatorProtocol(LocatorProtocol, Protocol):
    def locator(self, selector: str) -> LocatorProtocol: ...


class _ScopedPageProtocol(PageProtocol[LocatorProtocol], Protocol):
    def locator(self, selector: str) -> _ScopedLocatorProtocol: ...


@dataclass(frozen=True)
class FlowReferenceUploadControl(Generic[_LocatorT]):
    """One exact legacy input or live upload-menu entry point."""

    kind: Literal["input", "menu"]
    locator: _LocatorT


class _CandidateLocatorProtocol(LocatorProtocol, Protocol):
    """The reviewed locator surface needed only after a semantic grid is resolved."""

    def get_by_role(
        self,
        role: SemanticRole,
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
            and not parsed.query
            and not parsed.fragment
            and parsed.scheme == "https"
            and (
                (
                    parsed.netloc == _FLOW_HOST
                    and (parsed.path == _FLOW_PREFIX or parsed.path.startswith(f"{_FLOW_PREFIX}/"))
                )
                or (
                    parsed.netloc == _CANONICAL_FLOW_HOST
                    and _CANONICAL_PROJECT_PATH.fullmatch(parsed.path) is not None
                )
            )
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


def resolve_reference_upload_control(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> FlowReferenceUploadControl[_LocatorT]:
    """Resolve exactly one supported upload contract without preferring either family."""
    _require_safe_generation_route(page, _target)
    _raise_if_blocked(page, failed_step="upload_reference", locator_name="REFERENCE_INPUT")
    direct = _actionable_candidates(page.get_by_label("Reference image", exact=True))
    menu = _actionable_candidates_for_roles(page, "button", _LIVE_UPLOAD_BUTTON_NAMES)
    if len(direct) + len(menu) != 1:
        observed = len(direct) + len(menu)
        raise FlowGenerationUiContractError(
            failed_step="upload_reference",
            failed_locator="REFERENCE_INPUT",
            primary_failure=FlowPrimaryFailure(
                phase="verify_flow_ui",
                control="flow.upload_menu_button",
                category="missing" if observed == 0 else "ambiguous",
                expected_cardinality=1,
                observed_cardinality=_safe_cardinality(observed),
                ambiguity_detected=observed > 1,
                unsafe_fallback_required=True,
            ),
        )
    if direct:
        return FlowReferenceUploadControl(kind="input", locator=cast(_LocatorT, direct[0]))
    return FlowReferenceUploadControl(kind="menu", locator=cast(_LocatorT, menu[0]))


def resolve_upload_menu_item(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve the one exact live upload action after its menu has been opened."""
    _require_safe_generation_route(page, _target)
    menu: LocatorProtocol = _require_unique_candidates(
        page,
        _actionable_candidates(page.get_by_role("menu")),
        locator_name="REFERENCE_INPUT",
        failed_step="upload_reference",
        primary_control="flow.upload_menuitem_send",
    )
    return cast(
        _LocatorT,
        _require_unique_candidates(
            page,
            _actionable_candidates(
                cast(_CandidateLocatorProtocol, menu).get_by_role(
                    "menuitem", name=_LIVE_UPLOAD_ITEM_NAMES[0], exact=True
                )
            ),
            locator_name="REFERENCE_INPUT",
            failed_step="upload_reference",
            primary_control="flow.upload_menuitem_send",
        ),
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
    _raise_if_blocked(page, failed_step="fill_prompt", locator_name="GENERATION_PROMPT")
    legacy = _actionable_candidates(page.get_by_label("Prompt", exact=True))
    hosts = tuple(
        candidate
        for candidate in cast(_ScopedPageProtocol, page).locator("flow-rich-text-editor").all()
        if candidate.is_visible()
    )
    if len(hosts) > 1:
        raise FlowGenerationUiContractError(
            failed_step="fill_prompt",
            failed_locator="GENERATION_PROMPT",
            primary_failure=_preflight_cardinality_failure("flow.prompt_host", len(hosts)),
        )
    live: tuple[LocatorProtocol, ...] = ()
    if hosts:
        editor = cast(_ScopedLocatorProtocol, hosts[0]).locator("[contenteditable='true']")
        live = _actionable_candidates(editor)
        if len(live) != 1:
            raise FlowGenerationUiContractError(
                failed_step="fill_prompt",
                failed_locator="GENERATION_PROMPT",
                primary_failure=_preflight_cardinality_failure("flow.prompt_editor", len(live)),
            )
    return cast(
        _LocatorT,
        _require_unique_candidates(
            page,
            (*legacy, *live),
            locator_name="GENERATION_PROMPT",
            failed_step="fill_prompt",
            primary_control="flow.prompt_host" if not hosts else "flow.prompt_editor",
        ),
    )


def resolve_generate_control(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve the single enabled irreversible Generate control."""
    _require_safe_generation_route(page, _target)
    return cast(
        _LocatorT,
        _require_unique_candidates(
            page,
            _actionable_candidates_for_roles(page, "button", _GENERATE_NAMES),
            locator_name="GENERATE_CONTROL",
            failed_step="dispatch_generate",
        ),
    )


def resolve_preflight_generate_control(
    page: PageProtocol[_LocatorT],
    *,
    _target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
) -> _LocatorT:
    """Resolve one visible exact Generate control while permitting its initial disabled state."""
    _require_safe_generation_route(page, _target)
    exact_controls: list[LocatorProtocol] = []
    for name in _GENERATE_NAMES:
        exact_controls.extend(page.get_by_role("button", name=name, exact=True).all())
    candidates = [candidate for candidate in exact_controls if _is_observable(candidate)]
    if not candidates:
        if len(exact_controls) == 1:
            control = exact_controls[0]
            raise FlowGenerationUiContractError(
                failed_step="dispatch_generate",
                failed_locator="GENERATE_CONTROL",
                primary_failure=_preflight_cardinality_failure(
                    "flow.generate_button",
                    1,
                    category="unexpected_state" if control.is_visible() else "not_visible",
                    visible=control.is_visible(),
                    enabled=control.is_enabled(),
                ),
            )
    return cast(
        _LocatorT,
        _require_unique_candidates(
            page,
            candidates,
            locator_name="GENERATE_CONTROL",
            failed_step="dispatch_generate",
            primary_control="flow.generate_button",
        ),
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
        candidate
        for candidate, observed_fingerprint in candidates
        if observed_fingerprint == fingerprint
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
    for candidate in (
        cast(_CandidateLocatorProtocol, grid).get_by_role("listitem", include_hidden=True).all()
    ):
        candidate_locator = cast(_CandidateLocatorProtocol, candidate)
        fingerprint = _fingerprint_for_candidate(candidate_locator, identity_source)
        if not _is_actionable(candidate_locator) or fingerprint is None:
            raise FlowGenerationUiContractError(
                failed_step=failed_step, failed_locator="CANDIDATE_SLOT"
            )
        candidates.append((candidate_locator, fingerprint))
    if not candidates or len({fingerprint for _candidate, fingerprint in candidates}) != len(
        candidates
    ):
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
    return cast(
        _LocatorT,
        _require_unique_candidates(
            page,
            _actionable_candidates(locator),
            locator_name=locator_name,
            failed_step=failed_step,
        ),
    )


def _require_unique_candidates(
    page: PageProtocol[LocatorProtocol],
    candidates: tuple[LocatorProtocol, ...] | list[LocatorProtocol],
    *,
    locator_name: FlowGenerationLocatorName,
    failed_step: FlowGenerationFailedStep,
    primary_control: FlowPreflightControl | None = None,
) -> LocatorProtocol:
    _raise_if_blocked(page, failed_step=failed_step, locator_name=locator_name)
    if len(candidates) != 1:
        raise FlowGenerationUiContractError(
            failed_step=failed_step,
            failed_locator=locator_name,
            primary_failure=(
                None
                if primary_control is None
                else _preflight_cardinality_failure(primary_control, len(candidates))
            ),
        )
    return candidates[0]


def _raise_if_blocked(
    page: PageProtocol[LocatorProtocol],
    *,
    failed_step: FlowGenerationFailedStep,
    locator_name: FlowGenerationLocatorName,
) -> None:
    if blocking_overlay_present(page):
        raise FlowGenerationUiContractError(failed_step=failed_step, failed_locator=locator_name)


def _actionable_candidates(locator: LocatorProtocol) -> tuple[LocatorProtocol, ...]:
    return tuple(candidate for candidate in locator.all() if _is_actionable(candidate))


def _safe_cardinality(count: int) -> Literal[0, 1, "2+"]:
    return 0 if count == 0 else 1 if count == 1 else "2+"


def _preflight_cardinality_failure(
    control: FlowPreflightControl,
    count: int,
    *,
    category: FlowFailureCategory | None = None,
    visible: bool | None = None,
    enabled: bool | None = None,
) -> FlowPrimaryFailure:
    return FlowPrimaryFailure(
        phase="verify_flow_ui",
        control=control,
        category=category if category is not None else "missing" if count == 0 else "ambiguous",
        expected_cardinality=1,
        observed_cardinality=_safe_cardinality(count),
        visible=visible,
        enabled=enabled,
        ambiguity_detected=count > 1,
        unsafe_fallback_required=True,
    )


def _actionable_candidates_for_roles(
    page: PageProtocol[LocatorProtocol],
    role: SemanticRole,
    names: tuple[str, ...],
) -> tuple[LocatorProtocol, ...]:
    candidates: list[LocatorProtocol] = []
    for name in names:
        candidates.extend(_actionable_candidates(page.get_by_role(role, name=name, exact=True)))
    return tuple(candidates)


def _is_observable(candidate: LocatorProtocol) -> bool:
    aria_disabled = getattr(candidate, "get_attribute", lambda _name: None)("aria-disabled")
    return candidate.is_visible() and aria_disabled in {None, "false", "true"}


def _is_actionable(candidate: LocatorProtocol) -> bool:
    """Permit only absent or canonical-false ARIA disabled state on enabled semantic controls."""
    aria_disabled = getattr(candidate, "get_attribute", lambda _name: None)("aria-disabled")
    return candidate.is_visible() and candidate.is_enabled() and aria_disabled in {None, "false"}


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
