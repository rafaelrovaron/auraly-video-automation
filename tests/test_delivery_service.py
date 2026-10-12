from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from pathlib import Path
from typing import Any

import pytest

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.delivery_domain import DeliveryRequest
from auraly_pipeline.editing.delivery_service import DeliveryService
from tests.review_helpers import review_case as review_case, qc_path, review_path


def delivery_case(case: Any, *, approved: bool = True) -> tuple[DeliveryService, DeliveryRequest]:
    reviews, request, _ = case
    if not approved:
        request = request.model_copy(update={"decision": "rejected", "comment": "Revision"})
    record = reviews.record(request)
    target = DeliveryRequest(**{key: getattr(record, key) for key in DeliveryRequest.model_fields})
    return DeliveryService(project_root=reviews.project_root, work_root=reviews.work_root), target


def test_approved_copy_replay(review_case: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    service, request = delivery_case(review_case)
    reviews, _, report = review_case
    source = reviews.work_root / report.master_path
    before = source.read_bytes()
    assert service.get(request) is None and not service.delivery_root.exists()
    record = service.deliver(request)
    destination = service.delivery_root / record.path
    assert destination.read_bytes() == before and source.read_bytes() == before
    assert record.master_sha256 == hashlib.sha256(before).hexdigest()
    assert record.path == f"{request.campaign_id}/{request.output_variant_id}/{request.review_key}/master.mp4"
    original = destination.with_name("delivery.json").read_bytes()
    assert record.delivered_at.utcoffset() == timedelta(0)
    assert service.deliver(request) == record == service.get(request)
    assert destination.with_name("delivery.json").read_bytes() == original


@pytest.mark.parametrize("approved", [False, True])
def test_unapproved_no_writes(review_case: Any, approved: bool) -> None:
    service, request = delivery_case(review_case, approved=approved)
    if approved:
        request = request.model_copy(update={"review_key": "a" * 64})
    with pytest.raises(EditingError):
        service.deliver(request)
    assert not service.delivery_root.exists()


@pytest.mark.parametrize("component", ["master", "receipt"])
def test_corrupt_destination_no_overwrite(review_case: Any, component: str) -> None:
    service, request = delivery_case(review_case)
    record = service.deliver(request)
    path = service.delivery_root / record.path
    if component == "receipt":
        path = path.with_name("delivery.json")
    path.write_bytes(b"private-corrupt-marker")
    for operation in (service.get, service.deliver):
        with pytest.raises(EditingError) as error:
            operation(request)
        assert "private-" not in str(error.value)
        assert path.read_bytes() == b"private-corrupt-marker"


def test_receipt_without_master_is_not_absence(review_case: Any) -> None:
    service, request = delivery_case(review_case)
    record = service.deliver(request)
    master = service.delivery_root / record.path
    receipt = master.with_name("delivery.json").read_bytes()
    master.unlink()
    for operation in (service.get, service.deliver):
        with pytest.raises(EditingError):
            operation(request)
    assert not master.exists() and master.with_name("delivery.json").read_bytes() == receipt


@pytest.mark.parametrize("equal", [True, False])
def test_equal_or_conflicting_orphan_recovery(review_case: Any, equal: bool) -> None:
    service, request = delivery_case(review_case)
    record = service.deliver(request)
    path = service.delivery_root / record.path
    path.with_name("delivery.json").unlink()
    if not equal:
        path.write_bytes(b"conflict")
    with pytest.raises(EditingError):
        service.get(request)
    if equal:
        recovered = service.deliver(request)
        assert service.get(request) == recovered
    else:
        with pytest.raises(EditingError):
            service.deliver(request)
        assert path.read_bytes() == b"conflict" and not path.with_name("delivery.json").exists()


@pytest.mark.parametrize("component", ["master", "review", "qc"])
def test_stale_source_review_qc(review_case: Any, component: str) -> None:
    service, request = delivery_case(review_case)
    record = service.deliver(request)
    reviews, body, report = review_case
    path = {"master": reviews.work_root / report.master_path,
            "review": review_path(reviews, body, request.review_key),
            "qc": qc_path(reviews, body)}[component]
    path.write_bytes(b"changed")
    for operation in (service.get, service.deliver):
        with pytest.raises(EditingError):
            operation(request)
    assert (service.delivery_root / record.path).exists()


def test_concurrent_original_winner(review_case: Any) -> None:
    service, request = delivery_case(review_case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(service.deliver, [request, request]))
    assert records[0] == records[1] == service.get(request)
    assert not list(service.delivery_root.rglob("*.part"))


def test_failed_publication_cleanup(review_case: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import auraly_pipeline.editing.delivery_service as module
    service, request = delivery_case(review_case)
    publish = module.publish_editing_json
    def fail(*args: object, **kwargs: object) -> None:
        raise EditingError("artifact", "test safe publication failure")
    monkeypatch.setattr(module, "publish_editing_json", fail)
    with pytest.raises(EditingError):
        service.deliver(request)
    assert not list(service.delivery_root.rglob("*.part"))
    with pytest.raises(EditingError):
        service.get(request)
    monkeypatch.setattr(module, "publish_editing_json", publish)
    assert len(list(service.delivery_root.rglob("*.mp4"))) == 1
    assert service.deliver(request) == service.get(request)


@pytest.mark.parametrize("component", ["master", "review", "qc"])
def test_evidence_changed_during_copy(review_case: Any, monkeypatch: pytest.MonkeyPatch, component: str) -> None:
    import auraly_pipeline.editing.delivery_service as module
    service, request = delivery_case(review_case)
    reviews, body, report = review_case
    copy = module.shutil.copyfileobj
    def change(source: Any, destination: Any, *args: Any) -> None:
        copy(source, destination, *args)
        path = {"master": reviews.work_root / report.master_path,
                "review": review_path(reviews, body, request.review_key),
                "qc": qc_path(reviews, body)}[component]
        path.write_bytes(b"changed")
    monkeypatch.setattr(module.shutil, "copyfileobj", change)
    with pytest.raises(EditingError):
        service.deliver(request)
    assert not list(service.delivery_root.rglob("delivery.json"))
    assert not list(service.delivery_root.rglob("*.part"))


def test_root_boundaries(review_case: Any) -> None:
    reviews, _, _ = review_case
    for root in (reviews.work_root, reviews.project_root, reviews.work_root / "campaigns"):
        with pytest.raises(EditingError):
            DeliveryService(project_root=reviews.project_root, work_root=reviews.work_root, delivery_root=root)


@pytest.mark.parametrize("broken", [False, True])
def test_symlink_no_follow(review_case: Any, tmp_path: Path, broken: bool) -> None:
    service, request = delivery_case(review_case)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "missing" if broken else outside
    service.delivery_root.mkdir()
    link = service.delivery_root / request.campaign_id
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(EditingError):
        service.deliver(request)
    assert not list(outside.iterdir())
