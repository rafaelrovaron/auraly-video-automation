from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from auraly_pipeline.api.app import create_app
from auraly_pipeline.api.contracts import ApiSettings


def serve_command(
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8000,
    project_root: Annotated[Path | None, typer.Option("--project-root")] = None,
    work_root: Annotated[Path | None, typer.Option("--work-root")] = None,
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Serve existing local metadata without starting workers or migrating storage."""
    try:
        settings = ApiSettings.from_options(project_root=project_root, work_root=work_root, database=database)
        # Lifespan ERROR logs contain absolute paths; the CLI owns the static failure message.
        uvicorn.run(create_app(settings), host="127.0.0.1", port=port, workers=1,
                    access_log=False, log_level="critical")
    except (Exception, SystemExit):
        typer.echo("Local API could not start. Check local storage and configuration.", err=True)
        raise typer.Exit(1) from None


def register_api_commands(app: typer.Typer) -> None:
    api = typer.Typer(help="Read-only local campaign API.", no_args_is_help=True)
    api.command("serve")(serve_command)
    app.add_typer(api, name="api")
