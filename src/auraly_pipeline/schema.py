from __future__ import annotations

import json
from pathlib import Path

from auraly_pipeline.models import EditManifest


def export_schema(output: Path) -> Path:
    schema = EditManifest.model_json_schema(by_alias=True, mode="validation")
    schema["$id"] = "https://auraly.local/schemas/edit.schema.v1.json"
    schema["title"] = "Auraly Edit Manifest v1"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    export_schema(Path("schemas/edit.schema.json"))
