from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal

from auraly_pipeline.editing.batch_domain import EditBatchPlan, EditPlannedOutput
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_domain import (
    RenderBatchResult, RenderError, RenderOutputResult, RenderReceipt, RenderRuntime,
    render_key, validate_supported,
)
from auraly_pipeline.editing.render_media import audio_filter, check_master, encode_master
from auraly_pipeline.editing.render_runtime import detect_runtime
from auraly_pipeline.editing.render_text import write_ass
from auraly_pipeline.editing.service import publish_editing_json, validate_editing_path
from auraly_pipeline.probe import ProbeError, probe_media


InputName = Literal["source", "headlineFont", "captionsFont", "music", "timing"]


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class RenderService:
    def __init__(self, *, project_root: Path, work_root: Path) -> None:
        # Full content hashes make render paths exceed legacy Windows MAX_PATH.
        if os.name == "nt" and not str(work_root).startswith("\\\\?\\"):
            native = str(validate_editing_path(work_root, work_root))
            work_root = Path("\\\\?\\" + ("UNC\\" + native[2:] if native.startswith("\\\\") else native))
        self.batch = EditBatchService(project_root=project_root, work_root=work_root)
        self.editing = self.batch.editing
        self.work_root = self.editing.work_root

    def _assets(self, plan: EditBatchPlan, output: EditPlannedOutput) -> tuple[dict[str, Path], dict[InputName, str]]:
        manifest = output.manifest
        validate_supported(manifest, plan.caption_input)
        assets: dict[str, Path] = {"source": self.editing.validate_asset(manifest.source, "source")}
        hashes: dict[InputName, str] = {"source": manifest.source.sha256}
        for field, style, name in (("headline", manifest.headline, "headlineFont"),
                                   ("captions", manifest.captions, "captionsFont")):
            if style.enabled:
                if style.font is None:
                    raise EditingError(field + ".font", "local font required")
                assets[field] = self.editing.validate_font(style.font, field + ".font")
                hashes[name] = style.font.sha256  # type: ignore[index]
        if manifest.music.enabled:
            if manifest.music.asset is None or not manifest.music_accepted:
                raise EditingError("music.asset", "accepted local music required")
            assets["music"] = self.editing.validate_asset(manifest.music.asset, "music.asset")
            audio_filter(manifest, music_duration_sec=self.editing.audio_duration(manifest.music.asset))
            hashes["music"] = manifest.music.asset.sha256
        if manifest.captions.enabled:
            timing = plan.caption_input.timing_ref
            if timing is None:
                raise EditingError("captionInput", "timing asset required")
            assets["timing"] = self.editing.validate_asset(timing, "timingRef")
            hashes["timing"] = timing.sha256
        return assets, hashes

    def _path(self, plan: EditBatchPlan, output: EditPlannedOutput, key: str) -> Path:
        return validate_editing_path(self.work_root, self.work_root / "campaigns" / plan.campaign_id
            / "editing" / "renders" / output.output_variant_id / key / output.filename)

    def _reuse(self, final: Path, plan: EditBatchPlan, output: EditPlannedOutput,
               runtime: RenderRuntime, hashes: dict[InputName, str]) -> bool:
        receipt_path = validate_editing_path(self.work_root, final.with_name("render.json"))
        if not final.exists() and not receipt_path.exists():
            return False
        if not final.is_file() or not receipt_path.is_file():
            raise EditingError("output", "orphan render; manual artifact repair required")
        receipt = RenderReceipt.model_validate_json(receipt_path.read_bytes())
        if (receipt.campaign_id != plan.campaign_id or receipt.video_id != plan.video_id
                or receipt.output_variant_id != output.output_variant_id
                or receipt.manifest_hash != output.manifest_hash or receipt.output_hash != output.output_hash
                or receipt.render_key != render_key(output.output_hash, runtime) or receipt.runtime != runtime
                or receipt.inputs != hashes or receipt.path != final.relative_to(self.work_root).as_posix()
                or receipt.size_bytes != final.stat().st_size or receipt.sha256 != _sha(final)):
            raise EditingError("output", "render receipt conflict; manual artifact repair required")
        actual = check_master(final, duration_sec=plan.source.duration_sec)
        if actual != receipt.probe:
            raise EditingError("output", "stored master metadata conflict")
        return True

    def render(self, campaign_id: str, video_id: str, plan_hash: str, *, dry_run: bool = False) -> RenderBatchResult:
        plan = self.batch.get_plan(campaign_id, video_id, plan_hash)
        source = self.editing.validate_asset(plan.source, "source")
        try:
            facts = probe_media(source, timeout_seconds=30)
            if (source.suffix.lower() != ".mp4" or "mp4" not in facts.format_name.split(",")
                    or facts.video.codec != "h264" or facts.audio is None or facts.audio.codec != "aac"
                    or facts.video.rotation % 360 != 0 or not math.isfinite(facts.duration_sec)
                    or abs(facts.duration_sec - plan.source.duration_sec) > .01):
                raise ValueError()
        except (OSError, ValueError, ProbeError, subprocess.SubprocessError):
            raise EditingError("source", "valid synchronized H.264/AAC source required") from None
        runtime = detect_runtime()
        outputs = []
        for output in plan.outputs:
            key = render_key(output.output_hash, runtime)
            result = RenderOutputResult(key=output.key, output_variant_id=output.output_variant_id,
                                        status="planned", render_key=key)
            try:
                assets, hashes = self._assets(plan, output)
                final = self._path(plan, output, key)
                relative = final.relative_to(self.work_root).as_posix()
                if self._reuse(final, plan, output, runtime, hashes):
                    result.status, result.path, result.fit_measured = "reused", relative, True
                elif dry_run:
                    result.path = relative
                else:
                    self._publish(plan, output, runtime, final, assets, hashes)
                    result.status, result.path, result.fit_measured = "rendered", relative, True
            except EditingError as exc:
                result.status = "failed"
                result.error = RenderError(field=exc.field, message=str(exc))
            except (OSError, ValueError, KeyError, TypeError, ProbeError, subprocess.SubprocessError):
                result.status = "failed"
                result.error = RenderError(field="output", message="local render failed safely; inspect artifact integrity")
            outputs.append(result)
        return RenderBatchResult(plan_hash=plan.plan_hash, dry_run=dry_run, outputs=outputs)

    def _publish(self, plan: EditBatchPlan, output: EditPlannedOutput, runtime: RenderRuntime,
                 final: Path, assets: dict[str, Path], hashes: dict[InputName, str]) -> None:
        validate_editing_path(self.work_root, final)
        final.parent.mkdir(parents=True, exist_ok=True)
        validate_editing_path(self.work_root, final.parent)
        staging = Path(tempfile.mkdtemp(prefix=".render-", dir=self.work_root))
        try:
            validate_editing_path(self.work_root, staging)
            fonts = {name: path for name, path in assets.items() if name in {"headline", "captions"}}
            ass = write_ass(output.manifest, plan.caption_input, font_paths=fonts, staging=staging,
                            runtime=runtime) if fonts else None
            master = staging / "master.mp4"
            probe = encode_master(output.manifest, source_path=assets["source"], music_path=assets.get("music"),
                                  ass_path=ass, output_path=master, runtime=runtime)
            # Rehash original inputs after encoding; staging copies are not source authority.
            _, after = self._assets(plan, output)
            if hashes != after:
                raise EditingError("assets", "render inputs changed during encode")
            validate_editing_path(self.work_root, final)
            validate_editing_path(self.work_root, master)
            receipt = RenderReceipt(campaign_id=plan.campaign_id, video_id=plan.video_id,
                output_variant_id=output.output_variant_id, plan_hash=plan.plan_hash,
                manifest_hash=output.manifest_hash, output_hash=output.output_hash,
                render_key=render_key(output.output_hash, runtime), runtime=runtime, inputs=hashes,
                mix_policy="fixed_duck_mix" if output.manifest.music.enabled else "source_copy",
                path=final.relative_to(self.work_root).as_posix(), size_bytes=master.stat().st_size,
                sha256=_sha(master), probe=probe)
            os.link(master, final)
            publish_editing_json(self.work_root, final.with_name("render.json"),
                                 receipt.model_dump(mode="json", by_alias=True, exclude_computed_fields=True))
        finally:
            validate_editing_path(self.work_root, staging)
            shutil.rmtree(staging)
