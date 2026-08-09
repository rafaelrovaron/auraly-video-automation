from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def default_knowledge_root() -> Path:
    return Path(__file__).resolve().parents[3] / "07 Validated Ads Knowledge" / "Top Ads - Auraly"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def knowledge_status(root: Path | None = None) -> dict[str, Any]:
    root = (root or default_knowledge_root()).resolve()
    inventory = _read_json(root / "manifest" / "inventory-summary.json", {})
    review = _read_json(root / "knowledge" / "video-review" / "metrics.json", {})
    transcripts = _read_json(root / "knowledge" / "transcripts" / "summary.json", {})
    review_failures = len(review.get("failures", []))
    transcript_failures = len(transcripts.get("failures", []))
    videos_reviewed = len(review.get("videos", []))
    completed = int(transcripts.get("completed", 0))
    requested = int(transcripts.get("requested", 0))
    status: dict[str, Any] = {
        "root": str(root),
        "files": int(inventory.get("files", 0)),
        "videos": int(inventory.get("kinds", {}).get("video", 0)),
        "documents": int(inventory.get("extractedDocuments", 0)),
        "inventoryFailures": int(inventory.get("failures", 0)),
        "videosReviewed": videos_reviewed,
        "reviewFailures": review_failures,
        "transcriptsCompleted": completed,
        "transcriptFailures": transcript_failures,
    }
    status["ready"] = bool(
        status["files"]
        and status["videosReviewed"] > 0
        and (requested == 0 or completed == requested)
        and not status["inventoryFailures"]
        and not review_failures
        and not transcript_failures
    )
    return status


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9à-ÿ]+", " ", value.casefold()).strip()


def search_knowledge(
    root: Path | None,
    query: str,
    *,
    collection: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    root = (root or default_knowledge_root()).resolve()
    inventory = _read_json(root / "manifest" / "inventory.json", {"assets": []})
    extracts = _read_json(
        root / "knowledge" / "copy-patterns" / "validated-transcript-extracts.json",
        {"items": []},
    )
    documents = _read_json(
        root / "knowledge" / "documents" / "document-index.json",
        {"documents": []},
    )
    extracts_by_id = {item.get("assetId"): item for item in extracts.get("items", [])}
    terms = [term for term in _normalized(query).split() if term]
    matches: list[tuple[int, dict[str, Any]]] = []
    for asset in inventory.get("assets", []):
        if collection and asset.get("collection") != collection:
            continue
        extracted = extracts_by_id.get(asset.get("id"), {})
        searchable_parts = [
            asset.get("relativePath", ""),
            asset.get("originalDrivePath", ""),
            extracted.get("hookFirst12Sec", ""),
            extracted.get("ctaLast22Sec", ""),
            " ".join(extracted.get("angles", [])),
            " ".join(extracted.get("ctaTypes", [])),
            " ".join(extracted.get("claimFlags", [])),
        ]
        searchable = _normalized(" ".join(searchable_parts))
        if terms and not all(term in searchable for term in terms):
            continue
        score = sum(searchable.count(term) for term in terms)
        matches.append(
            (
                score,
                {
                    "recordType": "asset",
                    "assetId": asset.get("id"),
                    "path": asset.get("relativePath"),
                    "originalDrivePath": asset.get("originalDrivePath"),
                    "collection": asset.get("collection"),
                    "kind": asset.get("kind"),
                    "hook": extracted.get("hookFirst12Sec"),
                    "cta": extracted.get("ctaLast22Sec"),
                    "angles": extracted.get("angles", []),
                    "ctaTypes": extracted.get("ctaTypes", []),
                    "claimFlags": extracted.get("claimFlags", []),
                },
            )
        )
    for document in documents.get("documents", []):
        if collection and document.get("collection") != collection:
            continue
        text = document.get("text", "")
        searchable = _normalized(
            " ".join(
                [
                    document.get("title", ""),
                    document.get("sourceDrivePath", ""),
                    text,
                ]
            )
        )
        if terms and not all(term in searchable for term in terms):
            continue
        score = sum(searchable.count(term) for term in terms)
        folded_text = text.casefold()
        position = folded_text.find(terms[0]) if terms else 0
        start = max(0, position - 100)
        end = min(len(text), position + 220)
        snippet = text[start:end].strip()
        matches.append(
            (
                score,
                {
                    "recordType": "document",
                    "documentId": document.get("documentId"),
                    "title": document.get("title"),
                    "path": document.get("localMarkdownPath"),
                    "originalDrivePath": document.get("sourceDrivePath"),
                    "collection": document.get("collection"),
                    "kind": "document",
                    "snippet": snippet,
                },
            )
        )
    matches.sort(key=lambda pair: (-pair[0], str(pair[1].get("path", "")).casefold()))
    return [item for _, item in matches[: max(1, limit)]]
