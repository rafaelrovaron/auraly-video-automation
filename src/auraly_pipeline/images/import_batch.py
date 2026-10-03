"""Explicit, validated intake of manually produced campaign images."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
from typing import Literal, Self, cast
from uuid import uuid4

from pydantic import Field, PrivateAttr, field_validator, model_validator
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.campaigns.service import CampaignNotFoundError, CampaignService
from auraly_pipeline.flow.artifacts import (
    FlowArtifactConflictError,
    FlowArtifactInvalidError,
    inspect_image_artifact,
    publish_image_artifact_exclusive,
    resolve_trusted_image_path,
)
from auraly_pipeline.images.domain import ImageCandidate, ImageCandidateReviewStatus
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.models import ContractModel


class ImageImportError(RuntimeError):
    code = "image_import_failed"
    public_message = "The image import failed safely."


class ImageImportValidationError(ImageImportError):
    code = "image_import_manifest_invalid"

    def __init__(self, issues: list[ImageImportIssue]) -> None:
        super().__init__(self.public_message)
        self.issues = tuple(sorted(issues, key=lambda item: (item.variant_id or "", item.code)))


class ImageImportSourceChangedError(ImageImportError):
    code = "image_import_source_changed"
    public_message = "An image source changed after validation."


class ImageImportArtifactConflictError(ImageImportError):
    code = "image_import_artifact_conflict"
    public_message = "An image artifact conflicts with existing evidence."


class ImageImportPersistenceError(ImageImportError):
    code = "image_import_persistence_failed"
    public_message = "The image import could not be persisted."


class ImageImportApprovedCandidateConflictError(ImageImportError):
    code = "image_import_approved_candidate_conflict"
    public_message = "A variant already has a different approved image."


class ImageImportItem(ContractModel):
    variant_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    path: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
            raise ValueError("path must be relative and contained")
        return posix.as_posix()


class ImageImportBatch(ContractModel):
    schema_version: Literal["1.0"]
    campaign_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    approve_imported: bool = False
    approved_by: str | None = Field(default=None, min_length=1, max_length=120)
    items: list[ImageImportItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_approval(self) -> Self:
        if self.approve_imported != (self.approved_by is not None):
            raise ValueError("approvedBy must be supplied exactly when approveImported is true")
        return self


class ImageImportIssue(ContractModel):
    code: str
    variant_id: str | None = None
    message: str


class ImageImportPlanItem(ContractModel):
    variant_id: str
    scene_variant_id: str
    source_relative_path: str
    format: str
    width: int
    height: int
    size_bytes: int
    sha256: str
    destination_path: str
    action: Literal["create", "reuse"]
    review_status: Literal["pending_review", "approved", "rejected", "superseded"]


class ImageImportPlan(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["valid"] = "valid"
    batch: ImageImportBatch
    manifest_sha256: str
    items: list[ImageImportPlanItem]
    _source_root: Path = PrivateAttr()
    _manifest_path: Path = PrivateAttr()
    _source_paths: dict[str, Path] = PrivateAttr(default_factory=dict)


class ImageImportPrepared(ContractModel):
    campaign_id: str
    manifest_path: Path
    images_path: Path
    variant_count: int


class ImageImportResultItem(ContractModel):
    image_candidate_id: str
    variant_id: str
    scene_variant_id: str
    source_path: str
    sha256: str
    action: Literal["create", "reuse"]
    review_status: ImageCandidateReviewStatus


class ImageImportResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["completed"] = "completed"
    campaign_id: str
    manifest_sha256: str
    total: int
    created: int
    reused: int
    approved: int
    items: list[ImageImportResultItem]


class ImageImportService:
    def __init__(self, database_path: Path, *, work_root: Path) -> None:
        migrate_database(database_path)
        self._initialize(create_sqlite_engine(database_path), work_root=work_root, owns_engine=True)

    def _initialize(self, engine: Engine, *, work_root: Path, owns_engine: bool) -> None:
        self._engine = engine
        self._owns_engine = owns_engine
        self._sessions = sessionmaker(self._engine, expire_on_commit=False, class_=Session)
        self._campaigns = CampaignService(self._engine)
        self._repository = ImageRepository(self._sessions)
        self._work_root = work_root

    @classmethod
    def from_engine(cls, engine: Engine, *, work_root: Path) -> ImageImportService:
        service = cls.__new__(cls)
        service._initialize(engine, work_root=work_root, owns_engine=False)
        return service

    @classmethod
    def for_database(
        cls, database_path: Path, *, work_root: Path | None = None
    ) -> ImageImportService:
        return cls(database_path, work_root=work_root or Path("work"))

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    def prepare_directory(self, campaign_id: str, output: Path) -> ImageImportPrepared:
        try:
            campaign = self._campaigns.get_campaign(campaign_id)
        except CampaignNotFoundError as exc:
            raise ImageImportError("Campaign not found.") from exc
        if output.exists():
            raise ImageImportError("The import directory already exists.")
        output_parent = output.parent.resolve(strict=False)
        output_parent.mkdir(parents=True, exist_ok=True)
        temporary = output_parent / f".{output.name}.{uuid4().hex}.tmp"
        try:
            (temporary / "images").mkdir(parents=True)
            payload = {
                "schemaVersion": "1.0",
                "campaignId": campaign_id,
                "approveImported": False,
                "approvedBy": None,
                "items": [
                    {"variantId": variant.variant_id, "path": ""}
                    for variant in sorted(campaign.scene_variants, key=lambda item: item.variant_id)
                ],
            }
            manifest = temporary / "image-import.json"
            manifest.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.rename(temporary, output)
        except OSError as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise ImageImportError("The import directory could not be prepared.") from exc
        return ImageImportPrepared(
            campaign_id=campaign_id,
            manifest_path=output / "image-import.json",
            images_path=output / "images",
            variant_count=len(campaign.scene_variants),
        )

    def plan(self, manifest_path: Path) -> ImageImportPlan:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            batch = ImageImportBatch.model_validate(payload)
        except (OSError, ValueError) as exc:
            raise ImageImportValidationError(
                [self._issue("image_import_manifest_invalid", None, "The import manifest is invalid.")]
            ) from exc
        try:
            campaign = self._campaigns.get_campaign(batch.campaign_id)
        except CampaignNotFoundError as exc:
            raise ImageImportValidationError(
                [self._issue("image_import_campaign_not_found", None, "Campaign not found.")]
            ) from exc

        variants = {item.variant_id: item for item in campaign.scene_variants}
        issues: list[ImageImportIssue] = []
        counts: dict[str, int] = {}
        for item in batch.items:
            counts[item.variant_id] = counts.get(item.variant_id, 0) + 1
        for variant_id, count in counts.items():
            if count > 1:
                issues.append(
                    self._issue(
                        "image_import_variant_coverage_invalid",
                        variant_id,
                        "Variant appears more than once.",
                    )
                )
            if variant_id not in variants:
                issues.append(
                    self._issue(
                        "image_import_variant_coverage_invalid",
                        variant_id,
                        "Variant does not belong to the campaign.",
                    )
                )
        for variant_id in variants.keys() - counts.keys():
            issues.append(
                self._issue(
                    "image_import_variant_coverage_invalid",
                    variant_id,
                    "Variant is missing from the batch.",
                )
            )

        source_root = manifest_path.parent.resolve(strict=False)
        physical_sources: dict[Path, str] = {}
        source_paths: dict[str, Path] = {}
        planned: list[ImageImportPlanItem] = []
        for item in sorted(batch.items, key=lambda value: (value.variant_id, value.path)):
            variant = variants.get(item.variant_id)
            try:
                source = resolve_trusted_image_path(
                    source_root / item.path,
                    trusted_root=source_root,
                )
            except FlowArtifactInvalidError:
                issues.append(
                    self._issue(
                        "image_import_source_path_invalid",
                        item.variant_id,
                        "Image source is missing or unsafe.",
                    )
                )
                continue
            try:
                facts = inspect_image_artifact(source)
            except FlowArtifactInvalidError:
                issues.append(
                    self._issue(
                        "image_import_media_invalid",
                        item.variant_id,
                        "Image media is invalid.",
                    )
                )
                continue
            previous = physical_sources.setdefault(source, item.variant_id)
            if previous != item.variant_id or counts[item.variant_id] > 1:
                issues.append(
                    self._issue(
                        "image_import_source_path_invalid",
                        item.variant_id,
                        "Image source is used more than once.",
                    )
                )
            if facts.height <= facts.width:
                issues.append(
                    self._issue(
                        "image_import_orientation_invalid",
                        item.variant_id,
                        "Image must use portrait orientation.",
                    )
                )
            if variant is None:
                continue
            existing = self._repository.list_candidates_for_scene(variant.scene_variant_id)
            approved = next((row for row in existing if row.review_status == "approved"), None)
            matching = next((row for row in existing if row.sha256 == facts.sha256), None)
            if approved is not None and approved.sha256 != facts.sha256:
                issues.append(
                    self._issue(
                        "image_import_approved_candidate_conflict",
                        item.variant_id,
                        "Variant already has a different approved image.",
                    )
                )
            extension = ".jpeg" if facts.format == "jpeg" else f".{facts.format}"
            destination = PurePosixPath(
                "campaigns",
                batch.campaign_id,
                "variants",
                variant.scene_variant_id,
                "images",
                "imported",
                f"{facts.sha256}{extension}",
            ).as_posix()
            planned.append(
                ImageImportPlanItem(
                    variant_id=item.variant_id,
                    scene_variant_id=variant.scene_variant_id,
                    source_relative_path=item.path,
                    format=facts.format,
                    width=facts.width,
                    height=facts.height,
                    size_bytes=facts.size_bytes,
                    sha256=facts.sha256,
                    destination_path=destination,
                    action="reuse" if matching is not None else "create",
                    review_status=(
                        cast(ImageCandidateReviewStatus, matching.review_status)
                        if matching is not None
                        else "approved" if batch.approve_imported else "pending_review"
                    ),
                )
            )
            source_paths[item.variant_id] = source

        if issues:
            raise ImageImportValidationError(issues)
        canonical = json.dumps(
            batch.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        plan = ImageImportPlan(
            batch=batch,
            manifest_sha256=hashlib.sha256(canonical).hexdigest(),
            items=sorted(planned, key=lambda item: item.variant_id),
        )
        plan._source_root = source_root
        plan._manifest_path = manifest_path.resolve(strict=False)
        plan._source_paths = source_paths
        return plan

    def execute(self, plan: ImageImportPlan) -> ImageImportResult:
        created_finals: list[tuple[Path, ImageImportPlanItem]] = []
        try:
            for item in plan.items:
                source = plan._source_paths[item.variant_id]
                try:
                    current = inspect_image_artifact(source)
                except FlowArtifactInvalidError as exc:
                    raise ImageImportSourceChangedError from exc
                if (
                    current.sha256 != item.sha256
                    or current.size_bytes != item.size_bytes
                    or current.width != item.width
                    or current.height != item.height
                    or current.format != item.format
                ):
                    raise ImageImportSourceChangedError
                final = self._work_root / item.destination_path
                existed = self._path_exists(final)
                staging = final.parent / ".staging" / f"{uuid4().hex}.part"
                staging.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, staging)
                published = publish_image_artifact_exclusive(
                    staging,
                    final,
                    trusted_root=self._work_root,
                )
                if published.sha256 != item.sha256:
                    raise ImageImportSourceChangedError
                if not existed:
                    created_finals.append((final, item))
        except ImageImportError:
            self._cleanup_created_finals(created_finals)
            raise
        except (OSError, FlowArtifactInvalidError, FlowArtifactConflictError) as exc:
            self._cleanup_created_finals(created_finals)
            raise ImageImportArtifactConflictError from exc

        now = datetime.now(UTC)

        def persist(session: Session) -> list[ImageImportResultItem]:
            results: list[ImageImportResultItem] = []
            for item in plan.items:
                candidates = self._repository.candidates_for_scene_in_session(
                    session, item.scene_variant_id
                )
                approved = next(
                    (candidate for candidate in candidates if candidate.review_status == "approved"),
                    None,
                )
                matching = next(
                    (candidate for candidate in candidates if candidate.sha256 == item.sha256),
                    None,
                )
                if approved is not None and approved.sha256 != item.sha256:
                    raise ImageImportApprovedCandidateConflictError
                action: Literal["create", "reuse"] = "reuse"
                if matching is None:
                    action = "create"
                    candidate = ImageCandidate(
                        image_candidate_id=str(uuid4()),
                        scene_variant_id=item.scene_variant_id,
                        source_kind="manual_import",
                        import_manifest_sha256=plan.manifest_sha256,
                        import_source_path=item.source_relative_path,
                        candidate_index=0,
                        source_path=item.destination_path,
                        sha256=item.sha256,
                        width=item.width,
                        height=item.height,
                        size_bytes=item.size_bytes,
                        format=item.format,
                        review_status="approved" if plan.batch.approve_imported else "pending_review",
                        approved_at=now if plan.batch.approve_imported else None,
                        approved_by=plan.batch.approved_by,
                        created_at=now,
                        updated_at=now,
                    )
                    matching = self._repository.create_candidate_in_session(session, candidate)
                results.append(
                    ImageImportResultItem(
                        image_candidate_id=matching.id,
                        variant_id=item.variant_id,
                        scene_variant_id=item.scene_variant_id,
                        source_path=matching.source_path,
                        sha256=matching.sha256,
                        action=action,
                        review_status=cast(ImageCandidateReviewStatus, matching.review_status),
                    )
                )
            session.flush()
            return results

        try:
            items = self._repository.immediate_transaction(persist)
        except (ImageImportError, IntegrityError, OSError, ValueError) as exc:
            self._cleanup_created_finals(created_finals)
            if isinstance(exc, ImageImportError):
                raise
            raise ImageImportPersistenceError from exc
        created = sum(item.action == "create" for item in items)
        approved = sum(item.review_status == "approved" for item in items)
        return ImageImportResult(
            campaign_id=plan.batch.campaign_id,
            manifest_sha256=plan.manifest_sha256,
            total=len(items),
            created=created,
            reused=len(items) - created,
            approved=approved,
            items=sorted(items, key=lambda item: item.variant_id),
        )

    def import_batch(
        self, manifest_path: Path, *, dry_run: bool = False
    ) -> ImageImportPlan | ImageImportResult:
        plan = self.plan(manifest_path)
        return plan if dry_run else self.execute(plan)

    def _cleanup_created_finals(
        self, created_finals: list[tuple[Path, ImageImportPlanItem]]
    ) -> None:
        for final, item in reversed(created_finals):
            try:
                if (
                    self._repository.count_candidates_for_source_path(item.destination_path) == 0
                    and inspect_image_artifact(final).sha256 == item.sha256
                ):
                    os.unlink(self._native_path(final))
            except (OSError, FlowArtifactInvalidError):
                pass

    @staticmethod
    def _native_path(path: Path) -> str | Path:
        if os.name != "nt":
            return path
        absolute = os.path.abspath(path)
        if absolute.startswith("\\\\"):
            return "\\\\?\\UNC\\" + absolute[2:]
        return "\\\\?\\" + absolute

    @classmethod
    def _path_exists(cls, path: Path) -> bool:
        try:
            os.stat(cls._native_path(path), follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _issue(code: str, variant_id: str | None, message: str) -> ImageImportIssue:
        return ImageImportIssue(code=code, variant_id=variant_id, message=message)


def export_image_import_schema(output: Path) -> Path:
    schema = ImageImportBatch.model_json_schema(by_alias=True)
    schema["$id"] = "https://auraly.local/schemas/image-import.schema.v1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
