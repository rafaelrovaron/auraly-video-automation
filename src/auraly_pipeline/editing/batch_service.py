from __future__ import annotations

from pathlib import Path
import sqlite3

from pydantic import TypeAdapter, ValidationError

from auraly_pipeline.campaigns.domain import CampaignCreate, CopyMaster
from auraly_pipeline.editing.batch_domain import BatchInputs, CaptionTimingInput, EditBatchPlan, EditBatchRequest
from auraly_pipeline.editing.batch_planner import build_batch_plan, variant_requests, verify_batch_plan
from auraly_pipeline.editing.domain import AssetRef, EditingError, IdentityRef, Sha, SourceVideoRef, relative_path, safe_id
from auraly_pipeline.editing.service import EditingService, _publish, _read, _safe_path
from auraly_pipeline.heygen.video_domain import VideoPlanItem, VideoSource


class EditBatchService:
    def __init__(self, *, project_root: Path, work_root: Path,
                 database_path: Path | None = None) -> None:
        self.editing = EditingService(project_root=project_root, work_root=work_root)
        self.project_root = self.editing.project_root
        self.work_root = self.editing.work_root
        self.database_path = None if database_path is None else _safe_path(
            database_path.absolute().parent, database_path.absolute(),
        )

    def _inputs(self, request: EditBatchRequest, database_path: Path) -> BatchInputs:
        database = _safe_path(
            self.project_root if self.database_path is None else self.database_path.parent,
            database_path,
        )
        if self.database_path is not None and database != self.database_path:
            raise EditingError("database", "configured database required")
        if not database.is_file():
            raise EditingError("database", "existing local database required")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            render = connection.execute("SELECT * FROM heygen_renders WHERE id=?", (request.render_id,)).fetchone()
            if render is None or render["status"] != "ready" or render["source_json"] is None:
                raise EditingError("renderId", "ready render with source required")
            item = VideoPlanItem.model_validate_json(render["item_json"])
            source = VideoSource.model_validate_json(render["source_json"])
            if (render["campaign_id"] != request.campaign_id or item.campaign_id != request.campaign_id
                    or render["voice_master_id"] != item.voice_master_id
                    or render["image_candidate_id"] != item.image_candidate_id
                    or render["scene_variant_id"] != item.scene_variant_id):
                raise EditingError("renderId", "render identity mismatch")
            voice = connection.execute("SELECT * FROM voice_masters WHERE id=?", (item.voice_master_id,)).fetchone()
            image = connection.execute(
                "SELECT i.*,s.campaign_id AS scene_campaign FROM image_candidates i "
                "JOIN scene_variants s ON s.id=i.scene_variant_id WHERE i.id=?",
                (item.image_candidate_id,),
            ).fetchone()
            if (voice is None or voice["status"] != "approved"
                    or voice["campaign_id"] != request.campaign_id
                    or voice["processed_sha256"] != item.audio_sha256
                    or voice["approved_by"] is None or voice["approved_at"] is None):
                raise EditingError("voiceRef", "approved render voice required")
            if (image is None or image["sha256"] != item.image_sha256
                    or image["scene_variant_id"] != item.scene_variant_id
                    or image["scene_campaign"] != request.campaign_id):
                raise EditingError("imageRef", "render image identity/content mismatch")
            row = connection.execute("SELECT * FROM copy_masters WHERE id=?", (voice["copy_master_id"],)).fetchone()
            if (row is None or row["approval_state"] != "approved"
                    or row["approved_by"] is None or row["approved_at"] is None
                    or row["campaign_id"] != request.campaign_id
                    or row["version"] != voice["copy_master_version"]):
                raise EditingError("copyRef", "approved voice copy required")
            copy = CopyMaster.model_validate({
                "copyMasterId": row["id"], **{key: row[key] for key in (
                    "campaign_id", "version", "source_text", "headline", "hook", "body", "cta",
                    "approval_state", "approved_by", "approved_at", "created_at", "updated_at",
                )},
            })
            if copy.sha256 != row["sha256"] or copy.spoken_text != row["spoken_text"]:
                raise EditingError("copyRef", "stored copy content mismatch")
            audio_relative = relative_path(voice["processed_audio_path"])
            audio_path = _safe_path(self.work_root, self.work_root / audio_relative)
            self.editing._asset(AssetRef(path=audio_path.relative_to(self.project_root).as_posix(),
                                        sha256=item.audio_sha256), "voiceRef")
            video_path = _safe_path(self.work_root, self.work_root / relative_path(source.path))
            if not video_path.is_file() or video_path.stat().st_size != source.size_bytes:
                raise EditingError("source", "source size mismatch")
            return BatchInputs(
                source=SourceVideoRef(id=request.render_id,
                    path=video_path.relative_to(self.project_root).as_posix(),
                    sha256=source.sha256, duration_sec=source.probe.duration_sec),
                copy=copy, voice_ref=IdentityRef(id=item.voice_master_id, hash=item.audio_sha256),
                image_ref=IdentityRef(id=item.image_candidate_id, hash=item.image_sha256),
            )
        except EditingError:
            raise
        except (sqlite3.Error, ValidationError, ValueError, TypeError, KeyError, IndexError, OSError):
            raise EditingError("database", "cannot verify local campaign inputs") from None
        finally:
            if connection is not None:
                connection.close()

    def _path(self, campaign_id: str, video_id: str, plan_hash: str) -> Path:
        try:
            safe_id(campaign_id)
            safe_id(video_id)
            TypeAdapter(Sha).validate_python(plan_hash)
        except ValueError:
            raise EditingError("plan", "invalid plan identity") from None
        return _safe_path(self.work_root, self.work_root / "campaigns" / campaign_id /
                          "editing" / "plans" / video_id / plan_hash / "plan.json")

    def plan(self, request: EditBatchRequest, *, database_path: Path,
             persist: bool = True) -> EditBatchPlan:
        request = EditBatchRequest.model_validate(request.model_dump(mode="json", by_alias=True))
        inputs = self._inputs(request, database_path)
        timing = None
        if request.timing_ref is not None:
            path = self.editing._asset(request.timing_ref, "timingRef")
            try:
                timing = CaptionTimingInput.model_validate(_read(self.project_root, path))
            except ValidationError:
                raise EditingError("timingRef", "invalid timing contract") from None
        manifests = [self.editing.resolve(r, persist=False) for _, r in variant_requests(request, inputs)]
        plan = build_batch_plan(request, inputs, manifests, timing=timing)
        if persist:
            path = self._path(plan.campaign_id, plan.video_id, plan.plan_hash)
            if path.exists():
                self.get_plan(plan.campaign_id, plan.video_id, plan.plan_hash)
            _publish(self.work_root, path, plan.model_dump(mode="json", by_alias=True))
        return plan

    def get_plan(self, campaign_id: str, video_id: str, plan_hash: str) -> EditBatchPlan:
        try:
            plan = EditBatchPlan.model_validate(_read(self.work_root, self._path(campaign_id, video_id, plan_hash)))
            verify_batch_plan(plan)
            if (plan.campaign_id, plan.video_id, plan.plan_hash) != (campaign_id, video_id, plan_hash):
                raise EditingError("plan", "stored plan identity mismatch")
            return plan
        except ValidationError:
            raise EditingError("plan", "invalid stored plan contract") from None

    def list_plans(self, campaign_id: str) -> list[EditBatchPlan]:
        try:
            TypeAdapter(CampaignCreate.model_fields["campaign_id"].rebuild_annotation()).validate_python(campaign_id)
        except ValueError:
            raise EditingError("campaignId", "invalid campaign identity") from None
        root = _safe_path(self.work_root, self.work_root / "campaigns" / campaign_id /
                          "editing" / "plans")
        plans = [self.get_plan(campaign_id, path.parent.parent.name, path.parent.name)
                 for path in root.glob("*/*/plan.json")]
        return sorted(plans, key=lambda plan: (plan.video_id, plan.plan_hash))
