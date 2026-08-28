"""Observation-only semantic UI contract for Google Flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Self, TypeVar, runtime_checkable

from .domain import FlowLocatorName, FlowUiContractError


LocatorStrategyKind = Literal["role", "label", "placeholder", "text", "attribute"]
SemanticRole = Literal["main", "button", "dialog", "alertdialog", "status", "list", "listitem"]


class LocatorProtocol(Protocol):
    """Minimal semantic element observations used by this module."""

    def all(self) -> list[Self]: ...

    def count(self) -> int: ...

    def is_visible(self) -> bool: ...

    def is_enabled(self) -> bool: ...


_LocatorT_co = TypeVar("_LocatorT_co", bound=LocatorProtocol, covariant=True)
_LocatorT = TypeVar("_LocatorT", bound=LocatorProtocol)


class PageProtocol(Protocol[_LocatorT_co]):
    """Minimal page surface needed for semantic observation."""

    def get_by_role(
        self,
        role: SemanticRole,
        *,
        name: str | None = None,
        exact: bool | None = None,
    ) -> _LocatorT_co: ...

    def get_by_label(
        self,
        text: str,
        *,
        exact: bool | None = None,
    ) -> _LocatorT_co: ...

    def get_by_placeholder(
        self,
        text: str,
        *,
        exact: bool | None = None,
    ) -> _LocatorT_co: ...

    def get_by_text(
        self,
        text: str,
        *,
        exact: bool | None = None,
    ) -> _LocatorT_co: ...


@runtime_checkable
class AttributePageProtocol(Protocol[_LocatorT_co]):
    """Narrow reviewed stable-attribute lookup seam for tests or adapters."""

    def get_by_attribute(
        self,
        name: str,
        value: str,
        *,
        exact: bool | None = None,
    ) -> _LocatorT_co: ...


@dataclass(frozen=True)
class LocatorStrategy:
    kind: LocatorStrategyKind
    value: str
    role: SemanticRole | None = None
    attribute_name: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "role":
            if self.role is None or self.attribute_name is not None:
                raise ValueError("role locators require only a semantic role")
            return
        if self.kind == "attribute":
            if self.attribute_name is None or self.role is not None:
                raise ValueError("attribute locators require only a stable attribute name")
            return
        if self.role is not None or self.attribute_name is not None:
            raise ValueError("non-role locators cannot declare role or attribute metadata")


@dataclass(frozen=True)
class RequiredLocator:
    name: FlowLocatorName
    strategies: tuple[LocatorStrategy, ...]


REQUIRED_FLOW_LOCATORS: dict[FlowLocatorName, RequiredLocator] = {
    "FLOW_WORKSPACE": RequiredLocator(
        name="FLOW_WORKSPACE",
        strategies=(LocatorStrategy("role", role="main", value="Flow workspace"),),
    ),
    "CREATE_ENTRY_POINT": RequiredLocator(
        name="CREATE_ENTRY_POINT",
        strategies=(LocatorStrategy("role", role="button", value="Create"),),
    ),
    "PROMPT_INPUT": RequiredLocator(
        name="PROMPT_INPUT",
        strategies=(
            LocatorStrategy("label", value="Prompt"),
            LocatorStrategy("placeholder", value="Describe your image"),
        ),
    ),
}


def resolve_required_locator(
    page: PageProtocol[_LocatorT],
    name: FlowLocatorName,
) -> _LocatorT:
    """Resolve one required semantic element or fail closed on absence or ambiguity."""
    required = REQUIRED_FLOW_LOCATORS.get(name)
    if required is None:
        raise FlowUiContractError(failed_locator=name)

    for strategy in required.strategies:
        candidates = _resolve_strategy(page, strategy)
        applicable = tuple(
            candidate
            for candidate in candidates.all()
            if candidate.is_visible() and candidate.is_enabled()
        )
        if len(applicable) > 1:
            raise FlowUiContractError(failed_locator=name)
        if applicable:
            (resolved,) = applicable
            return resolved

    raise FlowUiContractError(failed_locator=name)


def resolve_account_identity_masks(page: PageProtocol[_LocatorT]) -> tuple[_LocatorT, ...]:
    """Return every visible semantic account identity element for screenshot masking."""
    candidates = page.get_by_role("button", name="Google Account", exact=True)
    return tuple(candidate for candidate in candidates.all() if candidate.is_visible())


def blocking_overlay_present(page: PageProtocol[LocatorProtocol]) -> bool:
    """Report whether a visible accessible dialog or alert dialog blocks the page."""
    for role in ("dialog", "alertdialog"):
        candidates = page.get_by_role(role)
        if any(candidate.is_visible() for candidate in candidates.all()):
            return True
    return False


def _resolve_strategy(
    page: PageProtocol[_LocatorT],
    strategy: LocatorStrategy,
) -> _LocatorT:
    if strategy.kind == "role" and strategy.role is not None:
        return page.get_by_role(strategy.role, name=strategy.value, exact=True)
    if strategy.kind == "label":
        return page.get_by_label(strategy.value, exact=True)
    if strategy.kind == "placeholder":
        return page.get_by_placeholder(strategy.value, exact=True)
    if strategy.kind == "text":
        return page.get_by_text(strategy.value, exact=True)
    if strategy.kind == "attribute" and strategy.attribute_name is not None:
        if not isinstance(page, AttributePageProtocol):
            raise FlowUiContractError()
        return page.get_by_attribute(strategy.attribute_name, strategy.value, exact=True)
    raise FlowUiContractError()
