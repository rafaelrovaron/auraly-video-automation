from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Annotated

import typer

from auraly_pipeline.campaigns.persistence import default_database_path
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig
from auraly_pipeline.heygen.video_service import HeyGenVideoService


@contextmanager
def _service(database: Path, root: Path | None) -> Iterator[HeyGenVideoService]:
    service = None
    try:
        service = HeyGenVideoService.for_database(database, work_root=root)
        yield service
    except (typer.Abort, typer.Exit):
        raise
    except Exception:
        typer.echo(
            json.dumps(
                {
                    "success": False,
                    "error": {
                        "code": "heygen_video_operation_failed",
                        "message": "Check approved inputs, ready assets, config and render budget; ambiguous videos require reconciliation.",
                    },
                }
            )
        )
        raise typer.Exit(1) from None
    finally:
        if service is not None:
            service.close()


def register_video_commands(app: typer.Typer) -> None:
    @app.command("plan-videos")
    def plan(
        campaign_id: str,
        config: Annotated[Path, typer.Option("--config")],
        max_paid_renders: Annotated[int, typer.Option("--max-paid-renders", min=1)],
        database: Path = default_database_path(),
        work_root: Path | None = None,
    ) -> None:
        with _service(database, work_root) as service:
            settings = HeyGenVideoConfig.model_validate_json(config.read_text(encoding="utf-8"))
            result = service.plan_videos(campaign_id, settings, max_paid_renders=max_paid_renders)
            typer.echo(json.dumps({"success": True, "plan": result.model_dump(mode="json")}))

    @app.command("generate-videos")
    def generate(
        campaign_id: str,
        config: Annotated[Path, typer.Option("--config")],
        max_paid_renders: Annotated[int, typer.Option("--max-paid-renders", min=1)],
        approved_by: Annotated[str, typer.Option("--approved-by")],
        yes: Annotated[bool, typer.Option("--yes")] = False,
        database: Path = default_database_path(),
        work_root: Path | None = None,
    ) -> None:
        with _service(database, work_root) as service:
            settings = HeyGenVideoConfig.model_validate_json(config.read_text(encoding="utf-8"))
            preview = service.plan_videos(campaign_id, settings, max_paid_renders=max_paid_renders)
            typer.echo(preview.model_dump_json(), err=True)
            if not yes:
                typer.confirm("Reserve this approved video batch?", err=True, abort=True)
            renders = service.submit_videos(
                campaign_id, settings, max_paid_renders=max_paid_renders, approved_by=approved_by
            )
            typer.echo(
                json.dumps(
                    {
                        "success": True,
                        "renders": [
                            r.model_dump(mode="json", exclude_computed_fields=True) for r in renders
                        ],
                    }
                )
            )

    @app.command("run-videos")
    def run(
        campaign_id: str, database: Path = default_database_path(), work_root: Path | None = None
    ) -> None:
        with _service(database, work_root) as service:
            typer.echo(
                "Running reserved videos; paid dispatches use the recorded approval.", err=True
            )
            result = service.run_videos(campaign_id)
            typer.echo(
                json.dumps(
                    {
                        "success": True,
                        "summary": result.model_dump(mode="json", exclude_computed_fields=True),
                    }
                )
            )

    @app.command("videos")
    def videos(
        campaign_id: str, database: Path = default_database_path(), work_root: Path | None = None
    ) -> None:
        with _service(database, work_root) as service:
            typer.echo(
                json.dumps(
                    {
                        "success": True,
                        "renders": [
                            r.model_dump(mode="json", exclude_computed_fields=True)
                            for r in service.list_videos(campaign_id)
                        ],
                    }
                )
            )

    @app.command("reconcile-video")
    def reconcile(
        render_id: str,
        video_id: str | None = None,
        confirm_manual_binding: bool = False,
        database: Path = default_database_path(),
        work_root: Path | None = None,
    ) -> None:
        with _service(database, work_root) as service:
            result = service.reconcile_video(
                render_id, video_id=video_id, confirm_manual_binding=confirm_manual_binding
            )
            typer.echo(
                json.dumps(
                    {
                        "success": True,
                        "render": result.model_dump(mode="json", exclude_computed_fields=True),
                    }
                )
            )
