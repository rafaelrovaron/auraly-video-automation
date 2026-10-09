from __future__ import annotations

import json
from pathlib import Path

from auraly_pipeline.editing.domain import EditManifestV2, EditProfile, EditResolveRequest, EditingModel
from auraly_pipeline.editing.batch_domain import EditBatchRequest, CaptionTimingInput, EditBatchPlan
from auraly_pipeline.editing.render_domain import RenderReceipt, RenderBatchResult


def export_editing_schemas(output_dir: Path) -> tuple[Path, ...]:
    paths = (output_dir / "edit-profile.schema.json", output_dir / "edit-resolve.schema.json",
             output_dir / "edit-manifest.v2.schema.json", output_dir / "edit-batch-request.schema.json",
             output_dir / "caption-timing.schema.json", output_dir / "edit-batch-plan.schema.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    models: tuple[type[EditingModel], ...] = (
        EditProfile, EditResolveRequest, EditManifestV2, EditBatchRequest, CaptionTimingInput, EditBatchPlan)
    for model, path in zip(models, paths, strict=True):
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$id"] = f"https://auraly.local/schemas/{path.name}"
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return paths


def export_render_schemas(output_dir: Path) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for model, name in ((RenderReceipt, "render-receipt.schema.json"),
                        (RenderBatchResult, "render-batch-result.schema.json")):
        path = output_dir / name
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$id"] = f"https://auraly.local/schemas/{name}"
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return tuple(paths)


def export_render_job_schemas(output_dir: Path) -> tuple[Path, ...]:
    from auraly_pipeline.api.render_contracts import RenderJobSubmission, RenderJobView
    from auraly_pipeline.editing.render_job_domain import RenderJobRequest

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for model, name, mode in ((RenderJobRequest, "request", "validation"),
                              (RenderJobSubmission, "submission", "serialization"),
                              (RenderJobView, "view", "serialization")):
        path = output_dir / f"render-job-{name}.schema.json"
        schema = model.model_json_schema(by_alias=True, mode=mode)  # type: ignore[arg-type]
        schema["$id"] = f"https://auraly.local/schemas/{path.name}"
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return tuple(paths)


def export_qc_schemas(output_dir: Path) -> tuple[Path, ...]:
    from auraly_pipeline.editing.qc_domain import QcRequest, QcReport, QcBatchResult

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for model, name, mode in ((QcRequest, "request", "validation"),
                              (QcReport, "report", "validation"),
                              (QcBatchResult, "batch-result", "serialization")):
        path = output_dir / f"qc-{name}.schema.json"
        schema = model.model_json_schema(by_alias=True, mode=mode)  # type: ignore[arg-type]
        schema["$id"] = f"https://auraly.local/schemas/{path.name}"
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return tuple(paths)


if __name__ == "__main__":
    export_editing_schemas(Path("schemas"))
    export_render_schemas(Path("schemas"))
    export_render_job_schemas(Path("schemas"))
    export_qc_schemas(Path("schemas"))
