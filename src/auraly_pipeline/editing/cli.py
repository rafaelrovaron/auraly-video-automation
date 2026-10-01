from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from typing import Annotated, Iterator

import typer
from pydantic import ValidationError

from auraly_pipeline.config_paths import configured_project_root, configured_work_root
from auraly_pipeline.editing.domain import EditManifestV2, EditProfile, EditResolveRequest, EditingError
from auraly_pipeline.editing.resolver import verify_manifest_hash
from auraly_pipeline.editing.service import EditingService


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
        field = ".".join(str(part) for part in exc.errors()[0]["loc"])
        typer.echo(f"{field}: invalid field", err=True)
        raise typer.Exit(1) from None
    except (OSError, ValueError):
        typer.echo("input: cannot read valid local JSON", err=True)
        raise typer.Exit(1) from None


def _service(project_root: Path | None, work_root: Path | None) -> EditingService:
    # Preserve supplied lexical roots until ancestor validation in the service.
    project = project_root.absolute() if project_root else configured_project_root()
    work = work_root.absolute() if work_root else configured_work_root(project_root=project)
    return EditingService(project_root=project, work_root=work)


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def register_editing_commands(app: typer.Typer) -> None:
    edit = typer.Typer(help="Resolve local editing profiles and manifests (no render).", no_args_is_help=True)
    app.add_typer(edit, name="edit")

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
