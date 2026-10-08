from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, BinaryIO

from fastapi import FastAPI, Query
from pydantic import AfterValidator
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from auraly_pipeline.api.action_routes import Commands, matching
from auraly_pipeline.api.app import CampaignId, EditId, JobId
from auraly_pipeline.api.contracts import Items
from auraly_pipeline.api.render_contracts import RenderJobSubmission, RenderJobView
from auraly_pipeline.editing.domain import Sha, safe_id
from auraly_pipeline.editing.render_job_domain import RenderJobRequest

QueryId = Annotated[str, Query(), AfterValidator(safe_id)]


def _chunks(stream: BinaryIO) -> Generator[bytes, None, None]:
    try:
        while chunk := stream.read(64 * 1024):
            yield chunk
    finally:
        stream.close()


def register_render_routes(app: FastAPI) -> None:
    prefix = "/api/v1/campaigns/{campaignId}/editing/renders"

    @app.post(prefix, status_code=202)
    def submit(campaignId: CampaignId, body: RenderJobRequest, commands: Commands) -> RenderJobSubmission:
        matching(campaignId, body.campaign_id)
        return commands.editorial_renders.submit(body)

    @app.get(prefix)
    def listing(campaignId: CampaignId, commands: Commands,
                videoId: QueryId | None = None, planHash: Sha | None = None) -> Items[RenderJobView]:
        return Items(items=commands.editorial_renders.list(campaignId, video_id=videoId, plan_hash=planHash))

    @app.get(prefix + "/{jobId}")
    def get(campaignId: CampaignId, jobId: JobId, commands: Commands) -> RenderJobView:
        return commands.editorial_renders.get(campaignId, jobId)

    @app.get(prefix + "/{jobId}/outputs/{outputVariantId}/media", response_class=StreamingResponse,
             responses={200: {"content": {"video/mp4": {"schema": {"type": "string", "format": "binary"}}}}})
    def media(campaignId: CampaignId, jobId: JobId, outputVariantId: EditId,
              commands: Commands, download: bool = False) -> StreamingResponse:
        stream, filename = commands.editorial_renders.open_media(campaignId, jobId, outputVariantId)
        return StreamingResponse(_chunks(stream), media_type="video/mp4", headers={"Cache-Control": "no-store",
            "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{filename}"'},
            background=BackgroundTask(stream.close))
