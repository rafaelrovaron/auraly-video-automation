from __future__ import annotations

import json
from pathlib import Path

from auraly_pipeline.editing.domain import EditManifestV2, EditProfile, EditResolveRequest, EditingModel


def export_editing_schemas(output_dir: Path) -> tuple[Path, Path, Path]:
    paths = (output_dir / "edit-profile.schema.json", output_dir / "edit-resolve.schema.json",
             output_dir / "edit-manifest.v2.schema.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    models: tuple[type[EditingModel], ...] = (EditProfile, EditResolveRequest, EditManifestV2)
    for model, path in zip(models, paths, strict=True):
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$id"] = f"https://auraly.local/schemas/{path.name}"
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return paths


if __name__ == "__main__":
    export_editing_schemas(Path("schemas"))
