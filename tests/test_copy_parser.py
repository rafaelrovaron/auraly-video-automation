import pytest

from auraly_pipeline.copy_parser import CopyFormatError, parse_copy


def test_copy_parser_keeps_headline_visual_and_builds_spoken_text() -> None:
    document = parse_copy(
        """# Copy

## Headline para tela
**WHAT IF LOVE ISN’T LATE… IT’S ALIGNING?**

## Hook
If love feels delayed, this may be the sign you were waiting for.

## Body
You are not behind.

Your birth energy may already carry clues.

## CTA
Take the quick reading and see what your soulmate energy reveals.
"""
    )

    assert document.headline == "WHAT IF LOVE ISN’T LATE… IT’S ALIGNING?"
    assert document.hook.startswith("If love feels delayed")
    assert document.cta.startswith("Take the quick reading")
    assert document.spoken_text == (
        "If love feels delayed, this may be the sign you were waiting for.\n\n"
        "You are not behind.\n\n"
        "Your birth energy may already carry clues.\n\n"
        "Take the quick reading and see what your soulmate energy reveals."
    )
    assert document.headline not in document.spoken_text


def test_copy_parser_rejects_missing_required_section() -> None:
    with pytest.raises(CopyFormatError, match="CTA"):
        parse_copy(
            """## Headline para tela
Headline
## Hook
Hook
## Body
Body
"""
        )
