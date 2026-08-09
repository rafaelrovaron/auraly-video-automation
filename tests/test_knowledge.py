from __future__ import annotations

import json
from pathlib import Path

from auraly_pipeline.knowledge import knowledge_status, search_knowledge


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_knowledge_status_reports_inventory_and_review_counts(tmp_path: Path) -> None:
    write_json(
        tmp_path / "manifest" / "inventory-summary.json",
        {"files": 142, "kinds": {"video": 128}, "failures": 0, "extractedDocuments": 9},
    )
    write_json(
        tmp_path / "knowledge" / "video-review" / "metrics.json",
        {"videos": [{"id": "asset-0001"}], "failures": []},
    )
    write_json(
        tmp_path / "knowledge" / "transcripts" / "summary.json",
        {"requested": 52, "completed": 52, "failures": []},
    )

    status = knowledge_status(tmp_path)

    assert status == {
        "root": str(tmp_path),
        "files": 142,
        "videos": 128,
        "documents": 9,
        "inventoryFailures": 0,
        "videosReviewed": 1,
        "reviewFailures": 0,
        "transcriptsCompleted": 52,
        "transcriptFailures": 0,
        "ready": True,
    }


def test_search_knowledge_matches_path_hook_and_angle(tmp_path: Path) -> None:
    write_json(
        tmp_path / "manifest" / "inventory.json",
        {
            "assets": [
                {
                    "id": "asset-0001",
                    "relativePath": "Cópia de AD 05 V9 - SM-ING-FB.mp4",
                    "originalDrivePath": "Cópia de AD 05 V9 | SM-ING-FB.mp4",
                    "collection": "validated-ad",
                    "kind": "video",
                },
                {
                    "id": "asset-0002",
                    "relativePath": "competitor.mp4",
                    "originalDrivePath": "ADS CONCORRENTES/competitor.mp4",
                    "collection": "competitor-reference",
                    "kind": "video",
                },
            ]
        },
    )
    write_json(
        tmp_path / "knowledge" / "copy-patterns" / "validated-transcript-extracts.json",
        {
            "items": [
                {
                    "assetId": "asset-0001",
                    "hookFirst12Sec": "My cards will reveal his face.",
                    "ctaLast22Sec": "Take the free quiz.",
                    "angles": ["soulmate-face-reveal"],
                    "ctaTypes": ["take-quiz"],
                    "claimFlags": [],
                }
            ]
        },
    )

    by_angle = search_knowledge(tmp_path, "face reveal", collection="validated-ad")
    by_path = search_knowledge(tmp_path, "competitor")

    assert [item["assetId"] for item in by_angle] == ["asset-0001"]
    assert by_angle[0]["hook"] == "My cards will reveal his face."
    assert [item["assetId"] for item in by_path] == ["asset-0002"]


def test_search_knowledge_matches_full_text_document_content(tmp_path: Path) -> None:
    write_json(tmp_path / "manifest" / "inventory.json", {"assets": []})
    write_json(
        tmp_path / "knowledge" / "documents" / "document-index.json",
        {
            "documents": [
                {
                    "documentId": "doc-001",
                    "title": "Swipe de formatos",
                    "sourceDrivePath": "ADS CONCORRENTES/SWIPE.docx",
                    "localMarkdownPath": "knowledge/documents/raw/swipe.md",
                    "collection": "competitor-reference",
                    "text": "Usar um horóscopo visual na mesa da especialista.",
                }
            ]
        },
    )

    results = search_knowledge(tmp_path, "horóscopo visual")

    assert [item["documentId"] for item in results] == ["doc-001"]
    assert results[0]["recordType"] == "document"
    assert "horóscopo visual" in results[0]["snippet"].casefold()


def test_search_knowledge_supports_production_transcript_field_names(tmp_path: Path) -> None:
    write_json(
        tmp_path / "manifest" / "inventory.json",
        {
            "assets": [
                {
                    "id": "asset-0097",
                    "relativePath": "validated.mp4",
                    "originalDrivePath": "validated.mp4",
                    "collection": "validated-ad",
                    "kind": "video",
                }
            ]
        },
    )
    write_json(
        tmp_path / "knowledge" / "copy-patterns" / "validated-transcript-extracts.json",
        {
            "items": [
                {
                    "assetId": "asset-0097",
                    "hookFirst12Sec": "There's a man planning to call you right now.",
                    "ctaLast22Sec": "Click learn more to discover his name.",
                    "angles": ["hidden-feelings-return"],
                    "ctaTypes": ["learn-more"],
                    "claimFlags": [],
                }
            ]
        },
    )

    matches = search_knowledge(tmp_path, "planning call")

    assert [item["assetId"] for item in matches] == ["asset-0097"]
    assert matches[0]["hook"] == "There's a man planning to call you right now."
