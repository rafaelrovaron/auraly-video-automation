from __future__ import annotations

import json
from pathlib import Path

from auraly_pipeline.voices.domain import VoiceImportRequest, VoiceMaster


def export_schemas(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, contract in (("voice-import", VoiceImportRequest), ("voice-master", VoiceMaster)):
        schema = contract.model_json_schema(by_alias=True, mode="validation")
        schema["$id"] = f"https://auraly.local/schemas/{name}.schema.v1.json"
        (directory / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    export_schemas(Path("schemas"))
