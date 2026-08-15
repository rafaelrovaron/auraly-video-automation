"""Durable local job orchestration foundation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from auraly_pipeline.jobs.domain import Job


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class LinkedJobSubmission(Generic[T]):
    job: Job
    linked: T
    reused: bool


__all__ = ["LinkedJobSubmission"]
