from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.heygen.db_models import RemoteAssetRow
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig, video_logical_key
from auraly_pipeline.heygen.video_handler import HeyGenVideoHandler
from auraly_pipeline.heygen.video_repository import HeyGenVideoRepository
from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobSubmit, RetrySafety
from auraly_pipeline.jobs.handlers import JobExecutionContext
from auraly_pipeline.jobs.service import JobService
from tests.heygen_video_support import ready_video_campaign
from tests.test_heygen_video_domain import video_item


def test_crash_after_dispatch_never_recreates(tmp_path: Path) -> None:
    provider=FakeHeyGenProvider(scenario='success')
    db=tmp_path/'a.db'
    root=tmp_path/'work'
    ready_video_campaign(db,root,provider)
    engine=create_sqlite_engine(db)
    factory=sessionmaker(engine,expire_on_commit=False)
    repo=HeyGenVideoRepository(factory)
    with factory() as session:
        assets=list(session.scalars(select(RemoteAssetRow)))
    preflight=provider.preflight_video(HeyGenVideoConfig())
    image=next(a for a in assets if a.kind=='image')
    voice=next(a for a in assets if a.kind=='audio')
    item=video_item(account_ref=provider.account_ref,image_sha256=image.sha256,audio_sha256=voice.sha256,image_asset_id=image.remote_asset_id,audio_asset_id=voice.remote_asset_id,schema_fingerprint=preflight.schema_fingerprint,config=HeyGenVideoConfig(poll_timeout_seconds=1,poll_initial_seconds=1))
    handler=HeyGenVideoHandler(factory,provider,root,sleep=lambda seconds:None)
    jobs=JobService.for_database(db,handlers={'heygen.video.generate':handler})
    submission=jobs.submit_linked_job(JobSubmit(job_type='heygen.video.generate',campaign_id='campaign-one',scene_variant_id=item.scene_variant_id,idempotency_key='video-job',input={'key':'one'},retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY),lambda s,j:repo.create_in_session(s,j,item,max_paid_renders=3,approved_by='tester'),lambda j:repo.list_campaign('campaign-one')[0])
    context=JobExecutionContext(job_id=submission.job.job_id,job_type='heygen.video.generate',input={'render_id':submission.linked.render_id},attempt_number=1,campaign_id='campaign-one')
    repo.mark_dispatch(submission.linked.render_id)
    assert handler.execute(context).outcome==JobExecutionOutcome.BLOCKED
    assert 'create_video' not in provider.events
    jobs.close()
    engine.dispose()


def test_scoped_claim_two_runners(tmp_path: Path) -> None:
    service=JobService.for_database(tmp_path/'jobs.db')
    for index in range(4):
        service.submit_job(JobSubmit(job_type='fake.success',idempotency_key=f'scope-{index}',input={}))
    unrelated=service.submit_job(JobSubmit(job_type='fake.retry-once',idempotency_key='unrelated',input={}))
    assert service.claim_next_job('a',job_type='fake.success',max_running=2) is not None
    assert service.claim_next_job('b',job_type='fake.success',max_running=2) is not None
    assert service.claim_next_job('c',job_type='fake.success',max_running=2) is None
    assert service.get_job(unrelated.job_id).status.value=='queued'
    service.close()


def test_poll_backoff_and_deadline(tmp_path: Path) -> None:
    provider=FakeHeyGenProvider()
    db=tmp_path/'poll.db'
    root=tmp_path/'work'
    ready_video_campaign(db,root,provider)
    provider.scenario='timeout'
    engine=create_sqlite_engine(db)
    factory=sessionmaker(engine,expire_on_commit=False)
    repo=HeyGenVideoRepository(factory)
    with factory() as session:
        assets=list(session.scalars(select(RemoteAssetRow)))
    image=next(a for a in assets if a.kind=='image')
    voice=next(a for a in assets if a.kind=='audio')
    config=HeyGenVideoConfig(poll_timeout_seconds=150)
    item=video_item(account_ref=provider.account_ref,image_sha256=image.sha256,audio_sha256=voice.sha256,image_asset_id=image.remote_asset_id,audio_asset_id=voice.remote_asset_id,schema_fingerprint=provider.preflight_video(config).schema_fingerprint,config=config)
    elapsed=[0.0]
    sleeps: list[float]=[]
    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        elapsed[0]+=seconds
    handler=HeyGenVideoHandler(factory,provider,root,sleep=sleep,monotonic=lambda:elapsed[0])
    jobs=JobService.for_database(db,handlers={'heygen.video.generate':handler})
    submitted=jobs.submit_linked_job(JobSubmit(job_type='heygen.video.generate',campaign_id='campaign-one',scene_variant_id=item.scene_variant_id,idempotency_key='poll-job',input={'logical_key':video_logical_key(item)},retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY),lambda s,j:repo.create_in_session(s,j,item,max_paid_renders=3,approved_by='tester'),lambda j:repo.list_campaign('campaign-one')[0])
    first=jobs.worker_once('poll-worker',campaign_id='campaign-one',job_type='heygen.video.generate',max_running=2)
    assert first is not None and first.status.value=='blocked'
    assert sleeps[:4]==[10,20,40,60]
    stored=repo.get(submitted.linked.render_id)
    assert stored.remote_video_id is not None
    jobs.resume_reconciled_job(stored.job_id,reason='existing_dispatch_reconciled')
    elapsed[0]=0
    jobs.worker_once('resume-worker',campaign_id='campaign-one',job_type='heygen.video.generate',max_running=2)
    assert provider.events.count('create_video')==1
    assert repo.get(stored.render_id).remote_video_id==stored.remote_video_id
    jobs.close()
    engine.dispose()
