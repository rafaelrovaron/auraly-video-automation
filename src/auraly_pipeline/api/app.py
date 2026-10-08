from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import asyncio
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from pydantic import AfterValidator, ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, Response

from auraly_pipeline.api.contracts import (
    ERROR_MESSAGES, ApiSettings, CampaignDetail, CampaignStatus, CampaignSummary,
    ErrorBody, ErrorCode, ErrorDetail, Health, Items, JobSummary, PlanSummary,
    ProfileView, QueryError, RenderSummary, SceneImages, VoiceSummary,
)
from auraly_pipeline.api.queries import ApiQueries
from auraly_pipeline.campaigns.domain import CampaignBudgetView
from auraly_pipeline.campaigns.persistence import create_readonly_sqlite_engine, create_existing_sqlite_engine, validate_api_database
from auraly_pipeline.api.commands import ApiCommands
from auraly_pipeline.api.worker import LocalApiWorker
from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.domain import Sha, safe_id
from auraly_pipeline.heygen.video_domain import UUID_PATTERN

CampaignId = Annotated[str, Path(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
EditId = Annotated[str, Path(), AfterValidator(safe_id)]
JobId = Annotated[str, Path(pattern=UUID_PATTERN)]
Version = Annotated[int, Path(gt=0)]
PlanHash = Annotated[Sha, Path()]
STATUS: dict[ErrorCode, int] = {
    "invalid_request": 422, "not_found": 404, "artifact_invalid": 409,
    "storage_unavailable": 503, "internal_error": 500, "method_not_allowed": 405,
    "operation_conflict": 409, "operation_not_allowed": 409,
}


def error_response(code: ErrorCode, *, status: int | None = None, field: str | None = None) -> JSONResponse:
    error = QueryError(code, field)
    body = ErrorBody(error=ErrorDetail(code=code, message=ERROR_MESSAGES[code], field=error.field))
    return JSONResponse(body.model_dump(mode="json", by_alias=True), status_code=status or STATUS[code])


def query_dependency(request: Request) -> ApiQueries:
    try:
        validate_api_database(cast(Engine, request.app.state.engine))
    except (SQLAlchemyError, ValueError, OSError):
        raise QueryError("storage_unavailable") from None
    return cast(ApiQueries, request.app.state.queries)


Queries = Annotated[ApiQueries, Depends(query_dependency)]


def create_app(settings: ApiSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine: Engine | None = None
        write_engine: Engine | None = None
        worker: LocalApiWorker | None = None
        try:
            try:
                engine = create_readonly_sqlite_engine(settings.database)
                validate_api_database(engine)
                app.state.engine = engine
                app.state.queries = ApiQueries(settings, engine)
                write_engine = create_existing_sqlite_engine(settings.database)
                app.state.write_engine = write_engine
                app.state.commands = ApiCommands(settings, write_engine)
                worker = LocalApiWorker(app.state.commands)
                app.state.worker = worker
            except (SQLAlchemyError, ValueError, OSError):
                raise QueryError("storage_unavailable") from None
            yield
        finally:
            try:
                if worker is not None:
                    await asyncio.to_thread(worker.shutdown)
            finally:
                if write_engine is not None:
                    write_engine.dispose()
                if engine is not None:
                    engine.dispose()

    errors: dict[int | str, dict[str, Any]] = {code: {"model": ErrorBody} for code in (400, 404, 405, 409, 422, 500, 503)}
    app = FastAPI(title="Auraly Local Operational API", version="1", lifespan=lifespan,
                  responses=errors, redoc_url=None, debug=False)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"], www_redirect=False)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response("invalid_request")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code: ErrorCode = "method_not_allowed" if error.status_code == 405 else "not_found"
        return error_response(code, status=error.status_code)

    @app.exception_handler(QueryError)
    async def query_error(request: Request, error: QueryError) -> JSONResponse:
        return error_response(error.code, field=error.field)

    @app.middleware("http")
    async def query_boundary(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.query_params:
            return error_response("invalid_request")
        if request.method == "POST":
            origins = request.headers.getlist("origin")
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            if (request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                    or len(origins) > 1 or (origins and origins[0] != expected_origin)):
                return error_response("invalid_request")
        try:
            response = await call_next(request)
            if response.status_code == 400:
                return error_response("invalid_request", status=400)
            return response
        except (SQLAlchemyError, OSError):
            return error_response("storage_unavailable")
        except ValidationError:
            return error_response("artifact_invalid")
        except Exception:
            # Keep unexpected failures inside the boundary: ASGI server traceback logs
            # would otherwise disclose private storage paths or request values.
            return error_response("internal_error")

    @app.get("/health")
    def health() -> Health:
        return Health()

    @app.get("/api/v1/campaigns")
    def campaigns(queries: Queries) -> Items[CampaignSummary]:
        return Items(items=queries.list_campaigns())

    @app.get("/api/v1/campaigns/{campaignId}")
    def campaign(campaignId: CampaignId, queries: Queries) -> CampaignDetail:
        return queries.get_campaign(campaignId)

    @app.get("/api/v1/campaigns/{campaignId}/status")
    def status(campaignId: CampaignId, queries: Queries) -> CampaignStatus:
        return queries.get_status(campaignId)

    @app.get("/api/v1/campaigns/{campaignId}/budget")
    def budget(campaignId: CampaignId, queries: Queries) -> CampaignBudgetView:
        return queries.get_budget(campaignId)

    @app.get("/api/v1/campaigns/{campaignId}/images")
    def images(campaignId: CampaignId, queries: Queries) -> Items[SceneImages]:
        return Items(items=queries.list_images(campaignId))

    @app.get("/api/v1/campaigns/{campaignId}/voices")
    def voices(campaignId: CampaignId, queries: Queries) -> Items[VoiceSummary]:
        return Items(items=queries.list_voices(campaignId))

    @app.get("/api/v1/campaigns/{campaignId}/heygen/renders")
    def renders(campaignId: CampaignId, queries: Queries) -> Items[RenderSummary]:
        return Items(items=queries.list_renders(campaignId))

    @app.get('/api/v1/campaigns/{campaignId}/heygen/renders/{renderId}/poster/{sourceSha256}',
             response_class=Response, responses={200: {'content': {'image/png': {'schema': {'type': 'string', 'format': 'binary'}}}}})
    def poster(campaignId: CampaignId, renderId: EditId, sourceSha256: PlanHash, queries: Queries) -> Response:
        return Response(queries.get_render_poster(campaignId, renderId, sourceSha256),
                        media_type='image/png', headers={'Cache-Control': 'no-store'})

    @app.get("/api/v1/campaigns/{campaignId}/jobs")
    def jobs(campaignId: CampaignId, queries: Queries) -> Items[JobSummary]:
        return Items(items=queries.list_jobs(campaignId))

    @app.get("/api/v1/campaigns/{campaignId}/jobs/{jobId}")
    def job(campaignId: CampaignId, jobId: JobId, queries: Queries) -> JobSummary:
        return queries.get_job(campaignId, jobId)

    @app.get("/api/v1/editing/profiles")
    def profiles(queries: Queries) -> Items[ProfileView]:
        return Items(items=queries.list_profiles())

    @app.get("/api/v1/editing/profiles/{profileId}/{version}")
    def profile(profileId: EditId, version: Version, queries: Queries) -> ProfileView:
        return queries.get_profile(profileId, version)

    @app.get("/api/v1/campaigns/{campaignId}/editing/plans")
    def plans(campaignId: CampaignId, queries: Queries) -> Items[PlanSummary]:
        return Items(items=queries.list_plans(campaignId))

    @app.get("/api/v1/campaigns/{campaignId}/editing/plans/{videoId}/{planHash}")
    def plan(campaignId: CampaignId, videoId: EditId, planHash: PlanHash, queries: Queries) -> EditBatchPlan:
        return queries.get_plan(campaignId, videoId, planHash)

    from auraly_pipeline.api.action_routes import register_action_routes
    register_action_routes(app)
    return app
