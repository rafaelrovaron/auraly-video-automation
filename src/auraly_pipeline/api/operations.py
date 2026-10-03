from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import JsonValue, TypeAdapter

from auraly_pipeline.api.action_contracts import LocalOperationRequest
from auraly_pipeline.api.contracts import ERROR_MESSAGES, QueryError
from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobExecutionResult, RetrySafety
from auraly_pipeline.jobs.handlers import JobExecutionContext

if TYPE_CHECKING:
    from auraly_pipeline.api.commands import ApiCommands

LOCAL_OPERATION_JOB = "api.local.operation"


class ApiOperationHandler:
    retry_safety = RetrySafety.MANUAL_ONLY

    def __init__(self, commands: ApiCommands) -> None:
        self.commands = commands

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        try:
            request: LocalOperationRequest = TypeAdapter(LocalOperationRequest).validate_python(context.input)
            if request.campaign_id != context.campaign_id:
                raise QueryError("invalid_request")
            result = self.commands.execute_operation(request)
            return JobExecutionResult(
                outcome=JobExecutionOutcome.SUCCESS,
                result=cast(dict[str, JsonValue], result.model_dump(mode="json", by_alias=True)),
            )
        except QueryError as error:
            return JobExecutionResult(
                outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                error_code=error.code, error_message=ERROR_MESSAGES[error.code],
            )
        except ValueError:
            return JobExecutionResult(
                outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                error_code="invalid_request", error_message=ERROR_MESSAGES["invalid_request"],
            )
