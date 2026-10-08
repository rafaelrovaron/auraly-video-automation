from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Annotated, Iterator

import typer
from pydantic import ValidationError

from auraly_pipeline.config_paths import DEFAULT_PROJECT_ROOT, WORK_ROOT_RELATIVE
from auraly_pipeline.editing.batch_domain import (
    CaptionInput, CaptionTimingCue, CaptionTimingInput, CopyRef, EditBatchPlan,
    EditBatchRequest, EditPlannedOutput, EditVariant, ResolvedCaptionCue,
)
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditManifestV2, EditProfile, EditResolveRequest, EditingError, validation_field
from auraly_pipeline.editing.resolver import verify_manifest_hash
from auraly_pipeline.editing.service import EditingService
from auraly_pipeline.editing.render_domain import RenderBatchResult, RenderError, RenderOutputResult, RenderReceipt, RenderRuntime
from auraly_pipeline.editing.render_service import RenderService


ProjectRoot = Annotated[Path | None, typer.Option("--project-root")]
WorkRoot = Annotated[Path | None, typer.Option("--work-root")]
RequestFile = Annotated[Path, typer.Option("--request")]
ProfileId = Annotated[str, typer.Option("--profile-id")]
Version = Annotated[int, typer.Option("--version")]


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except EditingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    except ValidationError as exc:
        field = validation_field(exc, extra_models=(CaptionInput, CaptionTimingCue,
            CaptionTimingInput, CopyRef, EditBatchPlan, EditBatchRequest,
            EditPlannedOutput, EditVariant, ResolvedCaptionCue, RenderBatchResult,
            RenderError, RenderOutputResult, RenderReceipt, RenderRuntime))
        typer.echo(f"{field}: invalid field", err=True)
        raise typer.Exit(1) from None
    except (OSError, ValueError):
        typer.echo("input: cannot read valid local JSON", err=True)
        raise typer.Exit(1) from None


def _service(project_root: Path | None, work_root: Path | None) -> EditingService:
    # Preserve supplied lexical roots until ancestor validation in the service.
    configured = os.environ.get("AURALY_PROJECT_ROOT", "").strip()
    project = (project_root or (Path(configured) if configured else DEFAULT_PROJECT_ROOT)).expanduser().absolute()
    work = work_root.expanduser().absolute() if work_root else project / WORK_ROOT_RELATIVE
    return EditingService(project_root=project, work_root=work)


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def register_editing_commands(app: typer.Typer) -> None:
    edit = typer.Typer(help="Resolve editing plans and render local vertical masters.", no_args_is_help=True)
    app.add_typer(edit, name="edit")

    @edit.command("render")
    def render(campaign_id: Annotated[str, typer.Option("--campaign-id")],
               video_id: Annotated[str, typer.Option("--video-id")],
               plan_hash: Annotated[str, typer.Option("--plan-hash")],
               project_root: ProjectRoot = None, work_root: WorkRoot = None,
               dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
        """Render a saved plan locally; dry-run checks inputs without measuring fit."""
        with _errors():
            roots = _service(project_root, work_root)
            result = RenderService(project_root=roots.project_root, work_root=roots.work_root).render(
                campaign_id, video_id, plan_hash, dry_run=dry_run)
            typer.echo(result.model_dump_json(by_alias=True))
            if result.has_failures:
                raise typer.Exit(1)

    @edit.command("plan")
    def plan(request: RequestFile, database: Annotated[Path, typer.Option("--database")],
             project_root: ProjectRoot = None, work_root: WorkRoot = None,
             dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
        with _errors():
            payload = EditBatchRequest.model_validate(_load(request))
            roots = _service(project_root, work_root)
            service = EditBatchService(project_root=roots.project_root, work_root=roots.work_root)
            result = service.plan(payload, database_path=database, persist=not dry_run)
            typer.echo(result.model_dump_json(by_alias=True))

    @edit.command("plan-get")
    def plan_get(campaign_id: Annotated[str, typer.Option("--campaign-id")],
                 video_id: Annotated[str, typer.Option("--video-id")],
                 plan_hash: Annotated[str, typer.Option("--plan-hash")],
                 project_root: ProjectRoot = None, work_root: WorkRoot = None) -> None:
        with _errors():
            roots = _service(project_root, work_root)
            result = EditBatchService(project_root=roots.project_root, work_root=roots.work_root).get_plan(
                campaign_id, video_id, plan_hash)
            typer.echo(result.model_dump_json(by_alias=True))

    @edit.command("profile-create")
    def profile_create(request: RequestFile, project_root: ProjectRoot = None, work_root: WorkRoot = None) -> None:
        with _errors():
            service = _service(project_root, work_root)
            profile = EditProfile.model_validate(_load(request))
            service.create_profile(profile)
            typer.echo(service.get_profile(profile.profile_id, profile.version).model_dump_json(by_alias=True))

    @edit.command("profile-get")
    def profile_get(profile_id: ProfileId, version: Version, project_root: ProjectRoot = None, work_root: WorkRoot = None) -> None:
        with _errors():
            typer.echo(_service(project_root, work_root).get_profile(profile_id, version).model_dump_json(by_alias=True))

    @edit.command("profile-list")
    def profile_list(project_root: ProjectRoot = None, work_root: WorkRoot = None) -> None:
        with _errors():
            profiles = _service(project_root, work_root).list_profiles()
            typer.echo(json.dumps([p.model_dump(mode="json", by_alias=True) for p in profiles], ensure_ascii=False))

    @edit.command("profile-new-version")
    def profile_new_version(profile_id: ProfileId, base_version: Annotated[int, typer.Option("--base-version")], request: RequestFile, project_root: ProjectRoot = None, work_root: WorkRoot = None) -> None:
        with _errors():
            profile = EditProfile.model_validate(_load(request))
            service = _service(project_root, work_root)
            service.create_profile_version(profile_id, base_version, profile)
            typer.echo(service.get_profile(profile_id, profile.version).model_dump_json(by_alias=True))

    @edit.command("resolve")
    def resolve(request: RequestFile, project_root: ProjectRoot = None, work_root: WorkRoot = None, dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
        with _errors():
            result = _service(project_root, work_root).resolve(EditResolveRequest.model_validate(_load(request)), persist=not dry_run)
            typer.echo(result.model_dump_json(by_alias=True))

    @edit.command("manifest-get")
    def manifest_get(campaign_id: Annotated[str, typer.Option("--campaign-id")], video_id: Annotated[str, typer.Option("--video-id")], output_variant_id: Annotated[str, typer.Option("--output-variant-id")], manifest_hash: Annotated[str, typer.Option("--manifest-hash")], project_root: ProjectRoot = None, work_root: WorkRoot = None) -> None:
        with _errors():
            result = _service(project_root, work_root).get_manifest(campaign_id, video_id, output_variant_id, manifest_hash)
            typer.echo(result.model_dump_json(by_alias=True))

    @edit.command("validate")
    def validate(manifest: Annotated[Path, typer.Option("--manifest")]) -> None:
        with _errors():
            payload = _load(manifest)
            if not isinstance(payload, dict) or payload.get("schemaVersion") != "2.0":
                raise EditingError("schemaVersion", "editing requires v2.0; use legacy validate for v1")
            result = EditManifestV2.model_validate(payload)
            verify_manifest_hash(result)
            typer.echo(result.model_dump_json(by_alias=True))
