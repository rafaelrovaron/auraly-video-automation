from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import ValidationError

from auraly_pipeline.image_generation import (
    DEFAULT_DOWNLOADS_DIR,
    DEFAULT_MODEL_NAME,
    DEFAULT_RETRY_COUNT,
    DEFAULT_TIMEOUT_SECONDS,
    ImageGenerationError,
    export_image_generation_schema,
    failure_screenshot_path,
    finalize_generation,
    prepare_generation,
    record_download_baseline,
    wait_for_download,
    write_failure_result,
)
from auraly_pipeline.ingest import IngestError, ingest_reel
from auraly_pipeline.knowledge import default_knowledge_root, knowledge_status, search_knowledge
from auraly_pipeline.models import EditManifest
from auraly_pipeline.schema import export_schema


app = typer.Typer(
    name="auraly",
    help="Deterministic local post-production pipeline for Auraly Reels.",
    no_args_is_help=True,
)


@app.command("ingest")
def ingest_command(
    video: Annotated[Path, typer.Option("--video", help="HeyGen MP4 source")],
    copy: Annotated[Path, typer.Option("--copy", help="Canonical Markdown copy")],
    character: Annotated[
        Literal["susan-smith", "soul-constellation"],
        typer.Option("--character"),
    ],
    work_root: Annotated[Path, typer.Option("--work-root")] = Path("work"),
    reel_id: Annotated[str | None, typer.Option("--reel-id")] = None,
) -> None:
    """Copy sources into a new workspace and create a draft edit manifest."""
    try:
        reel_dir = ingest_reel(
            video=video,
            copy=copy,
            character=character,
            work_root=work_root,
            reel_id=reel_id,
        )
    except IngestError as exc:
        typer.echo(f"Ingest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Created Reel workspace: {reel_dir}")


@app.command("validate")
def validate_command(manifest: Path) -> None:
    """Validate an edit.json against the current Pydantic contract."""
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        EditManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        typer.echo(f"Manifest invalid: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Manifest valid: {manifest}")


@app.command("export-schema")
def export_schema_command(
    output: Annotated[Path, typer.Option("--output")] = Path("schemas/edit.schema.json"),
) -> None:
    """Regenerate the versioned JSON Schema from the Pydantic contract."""
    path = export_schema(output)
    typer.echo(f"Schema exported: {path}")


@app.command("knowledge-status")
def knowledge_status_command(
    root: Annotated[Path, typer.Option("--root", help="Validated ads knowledge root")] = default_knowledge_root(),
) -> None:
    """Report whether the validated-ads reference library is ready."""
    status = knowledge_status(root)
    typer.echo(f"Knowledge root: {status['root']}")
    typer.echo(f"Ready: {'yes' if status['ready'] else 'no'}")
    typer.echo(f"Files: {status['files']}")
    typer.echo(f"Videos: {status['videos']}")
    typer.echo(f"Documents: {status['documents']}")
    typer.echo(f"Videos reviewed: {status['videosReviewed']}")
    typer.echo(f"Transcripts completed: {status['transcriptsCompleted']}")
    failures = status['inventoryFailures'] + status['reviewFailures'] + status['transcriptFailures']
    typer.echo(f"Failures: {failures}")
    if not status["ready"]:
        raise typer.Exit(code=1)


@app.command("knowledge-search")
def knowledge_search_command(
    query: Annotated[str, typer.Argument(help="Terms such as angle, hook, CTA, or filename")],
    root: Annotated[Path, typer.Option("--root", help="Validated ads knowledge root")] = default_knowledge_root(),
    collection: Annotated[str | None, typer.Option("--collection")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 10,
) -> None:
    """Search validated and competitor references without treating them as licensed assets."""
    matches = search_knowledge(root, query, collection=collection, limit=limit)
    if not matches:
        typer.echo("No matching references.")
        return
    for item in matches:
        if item.get("recordType") == "document":
            typer.echo(f"{item['documentId']} [{item['collection']}] {item['title']}")
            typer.echo(f"  Path: {item['path']}")
            typer.echo(f"  Snippet: {item['snippet']}")
            continue
        typer.echo(f"{item['assetId']} [{item['collection']}] {item['path']}")
        if item.get("angles"):
            typer.echo(f"  Angles: {', '.join(item['angles'])}")
        if item.get("hook"):
            typer.echo(f"  Hook: {item['hook']}")
        if item.get("cta"):
            typer.echo(f"  CTA: {item['cta']}")
        if item.get("claimFlags"):
            typer.echo(f"  Claim flags: {', '.join(item['claimFlags'])}")


@app.command("export-image-generation-schema")
def export_image_generation_schema_command(
    output: Annotated[Path, typer.Option("--output")] = Path(
        "schemas/image-generation.schema.json"
    ),
) -> None:
    """Export the Google AI Studio image-generation manifest schema."""
    path = export_image_generation_schema(output)
    typer.echo(f"Schema exported: {path}")


def _configure_image_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _json_echo(payload: dict) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@app.command("image-prepare")
def image_prepare_command(
    job_name: Annotated[str, typer.Option("--job-name")],
    reference_image: Annotated[str, typer.Option("--reference-image")],
    prompt_file: Annotated[Path, typer.Option("--prompt-file")],
    output_filename: Annotated[str | None, typer.Option("--output-filename")] = None,
    model_name: Annotated[str, typer.Option("--model-name")] = DEFAULT_MODEL_NAME,
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds", min=1)] = DEFAULT_TIMEOUT_SECONDS,
    retry_count: Annotated[int, typer.Option("--retry-count", min=0, max=5)] = DEFAULT_RETRY_COUNT,
    downloads_dir: Annotated[Path, typer.Option("--downloads-dir")] = DEFAULT_DOWNLOADS_DIR,
) -> None:
    """Validate a request and write a browser-run context without generating an image."""
    _configure_image_logging()
    try:
        prompt = prompt_file.read_text(encoding="utf-8")
        context, context_path = prepare_generation(
            job_name=job_name,
            reference_image_path=reference_image,
            prompt=prompt,
            output_filename=output_filename,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
            downloads_dir=downloads_dir,
        )
    except (ImageGenerationError, OSError, UnicodeError) as exc:
        failed_step = exc.step if isinstance(exc, ImageGenerationError) else "read_prompt"
        _json_echo(
            {
                "success": False,
                "jobName": job_name,
                "failedStep": failed_step,
                "savedFilePath": None,
                "timestamp": datetime.now(UTC).isoformat(),
                "error": str(exc),
            }
        )
        raise typer.Exit(code=1) from exc
    payload = context.model_dump(by_alias=True, mode="json")
    payload.update({"success": True, "contextPath": str(context_path)})
    _json_echo(payload)


@app.command("image-download-baseline")
def image_download_baseline_command(
    context: Annotated[Path, typer.Option("--context")],
) -> None:
    """Record the exact pre-download directory state immediately before clicking Download."""
    _configure_image_logging()
    try:
        prepared = record_download_baseline(context)
    except ImageGenerationError as exc:
        _json_echo({"success": False, "failedStep": exc.step, "error": str(exc)})
        raise typer.Exit(code=1) from exc
    _json_echo(
        {
            "success": True,
            "contextPath": str(context.resolve()),
            "downloadStartedAtNs": prepared.download_started_at_ns,
            "baselineFileCount": len(prepared.download_baseline),
        }
    )


@app.command("image-wait-download")
def image_wait_download_command(
    context: Annotated[Path, typer.Option("--context")],
    timeout_seconds: Annotated[int | None, typer.Option("--timeout-seconds", min=1)] = None,
) -> None:
    """Wait for exactly one new stable image file relative to the recorded baseline."""
    _configure_image_logging()
    try:
        downloaded = wait_for_download(context, timeout_seconds)
    except ImageGenerationError as exc:
        _json_echo({"success": False, "failedStep": exc.step, "error": str(exc)})
        raise typer.Exit(code=1) from exc
    _json_echo({"success": True, "downloadedFile": str(downloaded)})


@app.command("image-finalize")
def image_finalize_command(
    context: Annotated[Path, typer.Option("--context")],
    downloaded_file: Annotated[Path | None, typer.Option("--downloaded-file")] = None,
) -> None:
    """Move the correlated download into the job and write its success manifest."""
    _configure_image_logging()
    try:
        result = finalize_generation(context, downloaded_file)
    except ImageGenerationError as exc:
        _json_echo({"success": False, "failedStep": exc.step, "error": str(exc)})
        raise typer.Exit(code=1) from exc
    _json_echo(result.model_dump(by_alias=True, mode="json"))


@app.command("image-screenshot-path")
def image_screenshot_path_command(
    context: Annotated[Path, typer.Option("--context")],
    failed_step: Annotated[str, typer.Option("--failed-step")],
) -> None:
    """Reserve the canonical path for a diagnostic screenshot."""
    try:
        path = failure_screenshot_path(context, failed_step)
    except ImageGenerationError as exc:
        _json_echo({"success": False, "failedStep": exc.step, "error": str(exc)})
        raise typer.Exit(code=1) from exc
    _json_echo({"success": True, "debugScreenshot": str(path)})


@app.command("image-failure")
def image_failure_command(
    context: Annotated[Path, typer.Option("--context")],
    failed_step: Annotated[str, typer.Option("--failed-step")],
    error: Annotated[str, typer.Option("--error")],
    debug_screenshot: Annotated[Path | None, typer.Option("--debug-screenshot")] = None,
) -> None:
    """Write a structured, non-sensitive browser failure diagnostic."""
    _configure_image_logging()
    try:
        result = write_failure_result(
            context,
            failed_step=failed_step,
            error=error,
            debug_screenshot=debug_screenshot,
        )
    except ImageGenerationError as exc:
        _json_echo({"success": False, "failedStep": exc.step, "error": str(exc)})
        raise typer.Exit(code=1) from exc
    _json_echo(result.model_dump(by_alias=True, mode="json"))


if __name__ == "__main__":
    app()
