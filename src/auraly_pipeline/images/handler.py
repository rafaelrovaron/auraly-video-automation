from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.images.db_models import ImageCandidateRow, ImageGenerationRow
from auraly_pipeline.images.domain import ImageCandidate
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobExecutionResult, RetrySafety
from auraly_pipeline.jobs.handlers import JobExecutionContext


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_WIDTH = 8
_PNG_HEIGHT = 8
_FAKE_FORMAT_VERSION = "fake-png-v1"


@dataclass(frozen=True)
class _PngFacts:
    width: int
    height: int
    size_bytes: int
    sha256: str


class _ImageArtifactError(RuntimeError):
    pass


class _ImageArtifactMissingError(_ImageArtifactError):
    pass


class _ImageArtifactConflictError(_ImageArtifactError):
    pass


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _pixel_bytes(generation_id: str, candidate_index: int) -> bytes:
    seed = hashlib.sha256(
        f"{generation_id}:{candidate_index}:{_FAKE_FORMAT_VERSION}".encode("utf-8")
    ).digest()
    pixels = bytearray()
    counter = 0
    while len(pixels) < _PNG_WIDTH * _PNG_HEIGHT * 3:
        pixels.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
        counter += 1
    pixels = pixels[: _PNG_WIDTH * _PNG_HEIGHT * 3]
    pixels[-1] = candidate_index % 256
    return bytes(pixels)


def deterministic_png_bytes(generation_id: str, candidate_index: int) -> bytes:
    """Return the stable local-fake RGB PNG for one generation candidate."""
    rows = b"".join(
        b"\x00" + _pixel_bytes(generation_id, candidate_index)[
            row * _PNG_WIDTH * 3 : (row + 1) * _PNG_WIDTH * 3
        ]
        for row in range(_PNG_HEIGHT)
    )
    ihdr = struct.pack(">IIBBBBB", _PNG_WIDTH, _PNG_HEIGHT, 8, 2, 0, 0, 0)
    return _PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr) + _png_chunk(
        b"IDAT", zlib.compress(rows)
    ) + _png_chunk(b"IEND", b"")


def _png_facts(data: bytes) -> _PngFacts:
    if not data.startswith(_PNG_SIGNATURE):
        raise _ImageArtifactError
    offset = len(_PNG_SIGNATURE)
    ihdr: bytes | None = None
    idat = bytearray()
    found_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise _ImageArtifactError
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise _ImageArtifactError
        chunk_data = data[offset + 8 : offset + 8 + length]
        chunk_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != chunk_crc:
            raise _ImageArtifactError
        offset = end
        if chunk_type == b"IHDR" and ihdr is None:
            ihdr = chunk_data
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            found_iend = offset == len(data)
            break
    if ihdr is None or len(ihdr) != 13 or not found_iend:
        raise _ImageArtifactError
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if (
        width <= 0
        or height <= 0
        or (bit_depth, color_type, compression, filtering, interlace) != (8, 2, 0, 0, 0)
    ):
        raise _ImageArtifactError
    try:
        decoded = zlib.decompress(idat)
    except zlib.error as exc:
        raise _ImageArtifactError from exc
    stride = 1 + width * 3
    if len(decoded) != height * stride or any(decoded[row * stride] != 0 for row in range(height)):
        raise _ImageArtifactError
    return _PngFacts(
        width=width,
        height=height,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


class ImageGenerateHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        work_root: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._work_root = work_root.resolve()
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        with self._sessions() as session:
            generation = session.scalar(
                select(ImageGenerationRow).where(ImageGenerationRow.job_id == context.job_id)
            )
            if (
                generation is None
                or context.job_type != "image.generate"
                or generation.campaign_id != context.campaign_id
                or generation.executor != "local_fake"
                or generation.provider_state
                not in {"queued", "generating", "completed", "blocked"}
            ):
                return self._terminal(
                    "image_job_integrity_failed",
                    "The Image Generation job relationship is invalid.",
                )
            generation_id = generation.id
            campaign_id = generation.campaign_id
            scene_variant_id = generation.scene_variant_id
            generation_number = generation.generation_number
            provider_state = generation.provider_state
            existing_candidates = ImageRepository.candidates_by_index_in_session(
                session, generation.id
            )
            if generation.provider_state == "queued":
                generation.provider_state = "generating"
                generation.dispatched_at = self._clock()
            if provider_state in {"queued", "generating"}:
                generation.updated_at = self._clock()
                session.commit()

        try:
            candidates_to_create: list[ImageCandidate] = []
            if provider_state in {"completed", "blocked"}:
                if provider_state == "completed" and set(existing_candidates) != {0, 1}:
                    return self._terminal(
                        "image_generation_state_invalid",
                        "The Image Generation state is invalid.",
                    )
                for index in range(2):
                    self._inspect_candidate(
                        generation_id=generation_id,
                        campaign_id=campaign_id,
                        scene_variant_id=scene_variant_id,
                        generation_number=generation_number,
                        candidate_index=index,
                        recorded_candidate=existing_candidates.get(index),
                    )
            else:
                for index in range(2):
                    candidate = self._reconcile_candidate(
                        generation_id=generation_id,
                        campaign_id=campaign_id,
                        scene_variant_id=scene_variant_id,
                        generation_number=generation_number,
                        candidate_index=index,
                        recorded_candidate=existing_candidates.get(index),
                    )
                    if candidate is not None:
                        candidates_to_create.append(candidate)
        except _ImageArtifactMissingError:
            if provider_state not in {"completed", "blocked"}:
                self._mark_blocked(generation_id)
            return self._blocked(
                "image_artifact_missing",
                "The persisted image artifact is missing.",
            )
        except _ImageArtifactConflictError:
            if provider_state not in {"completed", "blocked"}:
                self._mark_blocked(generation_id)
            return self._blocked(
                "image_artifact_conflict",
                "The image artifact conflicts with persisted evidence.",
            )
        except _ImageArtifactError:
            if provider_state == "blocked":
                return self._blocked(
                    "image_generation_blocked",
                    "The Image Generation remains blocked.",
                )
            if provider_state != "completed":
                self._mark_failed(generation_id)
            return self._terminal(
                "image_artifact_conflict",
                "The image artifact conflicts with deterministic evidence.",
            )

        if provider_state == "completed":
            return JobExecutionResult(
                outcome=JobExecutionOutcome.SUCCESS,
                result={"imageGenerationId": generation_id, "candidateCount": 2},
            )
        if provider_state == "blocked":
            return self._blocked(
                "image_generation_blocked",
                "The Image Generation remains blocked.",
            )

        timestamp = self._clock()
        with self._sessions() as session:
            generation = session.get(ImageGenerationRow, generation_id)
            if generation is None or generation.provider_state != "generating":
                return self._terminal(
                    "image_generation_state_invalid",
                    "The Image Generation state is invalid.",
                )
            for candidate in candidates_to_create:
                ImageRepository.create_candidate_in_session(session, candidate)
            generation.provider_state = "completed"
            generation.completed_at = timestamp
            generation.updated_at = timestamp
            session.commit()
        return JobExecutionResult(
            outcome=JobExecutionOutcome.SUCCESS,
            result={"imageGenerationId": generation_id, "candidateCount": 2},
        )

    def _inspect_candidate(
        self,
        *,
        generation_id: str,
        campaign_id: str,
        scene_variant_id: str,
        generation_number: int,
        candidate_index: int,
        recorded_candidate: ImageCandidateRow | None,
    ) -> None:
        relative_path, target = self._candidate_paths(
            campaign_id=campaign_id,
            scene_variant_id=scene_variant_id,
            generation_number=generation_number,
            candidate_index=candidate_index,
        )
        if not target.exists():
            if recorded_candidate is not None:
                raise _ImageArtifactMissingError
            return
        try:
            actual = target.read_bytes()
        except OSError as exc:
            raise _ImageArtifactError from exc
        if actual != deterministic_png_bytes(generation_id, candidate_index):
            raise _ImageArtifactConflictError
        facts = _png_facts(actual)
        if recorded_candidate is not None and not self._matches_expected_candidate(
            recorded_candidate,
            relative_path=relative_path,
            facts=facts,
        ):
            raise _ImageArtifactConflictError

    def _reconcile_candidate(
        self,
        *,
        generation_id: str,
        campaign_id: str,
        scene_variant_id: str,
        generation_number: int,
        candidate_index: int,
        recorded_candidate: ImageCandidateRow | None,
    ) -> ImageCandidate | None:
        relative_path, target = self._candidate_paths(
            campaign_id=campaign_id,
            scene_variant_id=scene_variant_id,
            generation_number=generation_number,
            candidate_index=candidate_index,
        )
        expected = deterministic_png_bytes(generation_id, candidate_index)
        expected_facts = _png_facts(expected)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = target.resolve()
        if not target.is_relative_to(self._work_root):
            raise _ImageArtifactError

        if recorded_candidate is not None and not target.exists():
            raise _ImageArtifactMissingError

        if target.exists():
            try:
                actual = target.read_bytes()
            except OSError as exc:
                raise _ImageArtifactError from exc
            if actual != expected:
                raise _ImageArtifactConflictError
            facts = _png_facts(actual)
            if recorded_candidate is not None:
                if not self._matches_expected_candidate(
                    recorded_candidate,
                    relative_path=relative_path,
                    facts=facts,
                ):
                    raise _ImageArtifactConflictError
                return None
        else:
            try:
                with target.open("xb") as artifact:
                    artifact.write(expected)
            except FileExistsError:
                try:
                    actual = target.read_bytes()
                except OSError as exc:
                    raise _ImageArtifactError from exc
                if actual != expected:
                    raise _ImageArtifactConflictError
            except OSError as exc:
                raise _ImageArtifactError from exc
            try:
                facts = _png_facts(target.read_bytes())
            except OSError as exc:
                raise _ImageArtifactError from exc
            if facts != expected_facts:
                raise _ImageArtifactConflictError

        timestamp = self._clock()
        return ImageCandidate(
            image_candidate_id=str(uuid4()),
            image_generation_id=generation_id,
            candidate_index=candidate_index,
            source_path=relative_path.as_posix(),
            sha256=facts.sha256,
            width=facts.width,
            height=facts.height,
            size_bytes=facts.size_bytes,
            format="png",
            review_status="pending_review",
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _candidate_paths(
        self,
        *,
        campaign_id: str,
        scene_variant_id: str,
        generation_number: int,
        candidate_index: int,
    ) -> tuple[Path, Path]:
        relative_path = Path(
            "campaigns",
            campaign_id,
            "images",
            scene_variant_id,
            f"generation-{generation_number:04d}",
            f"candidate-{candidate_index:04d}.png",
        )
        target = (self._work_root / relative_path).resolve()
        if not target.is_relative_to(self._work_root):
            raise _ImageArtifactError
        return relative_path, target

    @staticmethod
    def _matches_expected_candidate(
        recorded_candidate: ImageCandidateRow,
        *,
        relative_path: Path,
        facts: _PngFacts,
    ) -> bool:
        return (
            recorded_candidate.source_path == relative_path.as_posix()
            and recorded_candidate.sha256 == facts.sha256
            and recorded_candidate.width == facts.width
            and recorded_candidate.height == facts.height
            and recorded_candidate.size_bytes == facts.size_bytes
            and recorded_candidate.format == "png"
        )

    def _mark_failed(self, generation_id: str) -> None:
        with self._sessions() as session:
            generation = session.get(ImageGenerationRow, generation_id)
            if generation is not None:
                generation.provider_state = "failed"
                generation.updated_at = self._clock()
                session.commit()

    def _mark_blocked(self, generation_id: str) -> None:
        with self._sessions() as session:
            generation = session.get(ImageGenerationRow, generation_id)
            if generation is not None:
                generation.provider_state = "blocked"
                generation.updated_at = self._clock()
                session.commit()

    @staticmethod
    def _terminal(code: str, message: str) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.TERMINAL_FAILURE,
            error_code=code,
            error_message=message,
        )

    @staticmethod
    def _blocked(code: str, message: str) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.BLOCKED,
            error_code=code,
            error_message=message,
        )
