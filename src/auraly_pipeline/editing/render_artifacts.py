from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import BinaryIO

from pydantic import TypeAdapter

from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.domain import EditingError, Sha, safe_id
from auraly_pipeline.editing.render_domain import RenderReceipt
from auraly_pipeline.editing.service import validate_editing_path


def render_work_root(root: Path) -> Path:
    root = validate_editing_path(root, root)
    if os.name == "nt" and not str(root).startswith("\\\\?\\"):
        native = str(root)
        root = Path("\\\\?\\" + ("UNC\\" + native[2:] if native.startswith("\\\\") else native))
    return root


@dataclass
class VerifiedRender:
    path: Path
    receipt_path: Path
    receipt: RenderReceipt
    receipt_sha256: str
    stream: BinaryIO


def open_verified_render(*, work_root: Path, plan: EditBatchPlan, output_variant_id: str,
                         render_key: str, expected_path: str | None = None) -> VerifiedRender:
    """Caller owns the verified handle; no runtime detection or media processing."""
    stream = None
    try:
        for identifier in (plan.campaign_id, plan.video_id, output_variant_id):
            safe_id(identifier)
        TypeAdapter(Sha).validate_python(render_key)
        output = next(o for o in plan.outputs if o.output_variant_id == output_variant_id)
        manifest = output.manifest
        root = render_work_root(work_root)
        path = validate_editing_path(root, root / "campaigns" / plan.campaign_id / "editing" / "renders"
                                    / output_variant_id / render_key / output.filename)
        relative = path.relative_to(root).as_posix()
        if expected_path is not None and relative != expected_path:
            raise ValueError("unexpected render path")
        receipt_path = validate_editing_path(root, path.with_name("render.json"))
        raw = receipt_path.read_bytes()
        receipt = RenderReceipt.model_validate_json(raw)
        inputs = {"source": plan.source.sha256}
        for field, style in (("headlineFont", manifest.headline), ("captionsFont", manifest.captions)):
            if style.enabled:
                if style.font is None:
                    raise ValueError("missing font")
                inputs[field] = style.font.sha256
        if manifest.music.enabled:
            if manifest.music.asset is None:
                raise ValueError("missing music")
            inputs["music"] = manifest.music.asset.sha256
        if manifest.captions.enabled:
            if plan.caption_input.timing_ref is None:
                raise ValueError("missing timing")
            inputs["timing"] = plan.caption_input.timing_ref.sha256
        if ((receipt.campaign_id, receipt.video_id, receipt.output_variant_id, receipt.manifest_hash,
             receipt.output_hash, receipt.render_key, receipt.path) !=
            (plan.campaign_id, plan.video_id, output_variant_id, output.manifest_hash,
             output.output_hash, render_key, relative)
                or receipt.inputs != inputs
                or receipt.mix_policy != ("fixed_duck_mix" if manifest.music.enabled else "source_copy")):
            raise ValueError("render receipt mismatch")
        stream = path.open("rb")
        if (os.fstat(stream.fileno()).st_size != receipt.size_bytes
                or hashlib.file_digest(stream, "sha256").hexdigest() != receipt.sha256):
            raise ValueError("invalid master integrity")
        stream.seek(0)
        return VerifiedRender(path, receipt_path, receipt, hashlib.sha256(raw).hexdigest(), stream)
    except (ValueError, OSError, StopIteration):
        if stream is not None:
            stream.close()
        raise EditingError("artifact", "cannot verify existing master and receipt") from None
