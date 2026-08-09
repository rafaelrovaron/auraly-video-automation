from __future__ import annotations

import re
from dataclasses import dataclass


_SECTION_NAMES = {
    "headline para tela": "headline",
    "hook": "hook",
    "body": "body",
    "cta": "cta",
}


_DISPLAY_NAMES = {
    "headline": "Headline para tela",
    "hook": "Hook",
    "body": "Body",
    "cta": "CTA",
}


class CopyFormatError(ValueError):
    """Raised when a canonical Auraly copy is incomplete or malformed."""


@dataclass(frozen=True, slots=True)
class CopyDocument:
    headline: str
    hook: str
    body: str
    cta: str

    @property
    def spoken_text(self) -> str:
        return "\n\n".join((self.hook, self.body, self.cta))


def _clean_markdown(lines: list[str]) -> str:
    cleaned: list[str] = []
    previous_blank = False
    for raw_line in lines:
        line = raw_line.strip().replace("**", "").replace("__", "")
        if not line:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        cleaned.append(line)
        previous_blank = False
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned)


def parse_copy(markdown: str) -> CopyDocument:
    sections: dict[str, list[str]] = {name: [] for name in _SECTION_NAMES.values()}
    current: str | None = None

    for line in markdown.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = _SECTION_NAMES.get(heading.group(1).strip().casefold())
            continue
        if current is not None:
            sections[current].append(line)

    values = {name: _clean_markdown(lines) for name, lines in sections.items()}
    missing = [_DISPLAY_NAMES[name] for name, value in values.items() if not value]
    if missing:
        raise CopyFormatError(f"Missing required section(s): {', '.join(missing)}")
    return CopyDocument(**values)
