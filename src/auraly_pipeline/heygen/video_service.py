from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.db_models import CampaignRow, SceneVariantRow
from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.config_paths import configured_work_root
from auraly_pipeline.heygen.db_models import RemoteAssetRow
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.provider import HeyGenMcpAdapter
from auraly_pipeline.heygen.video_domain import (
    HeyGenRender, HeyGenRenderStatus, HeyGenVideoConfig, VideoPlan, VideoPlanItem,
    VideoRunSummary, video_logical_key,
)
from auraly_pipeline.heygen.video_handler import HeyGenVideoHandler, validate_video_item
from auraly_pipeline.heygen.video_repository import HeyGenVideoRepository
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.domain import Job, JobSubmit, RetrySafety
from auraly_pipeline.jobs.service import JobService
from auraly_pipeline.jobs.state_machine import JobStatus
from auraly_pipeline.metadata_security import validate_safe_identifier
from auraly_pipeline.voices.repository import VoiceMasterRepository


class HeyGenVideoService:
    def __init__(self, factory: sessionmaker[Session], provider: HeyGenMcpAdapter | FakeHeyGenProvider, jobs: JobService, root: Path) -> None:
        self._factory=factory
        self._provider=provider
        self._jobs=jobs
        self._root=root
        self._repo=HeyGenVideoRepository(factory)

    @classmethod
    def for_database(cls, database_path: Path, *, work_root: Path | None = None, provider: HeyGenMcpAdapter | FakeHeyGenProvider | None = None, client_factory: Callable[[],httpx.Client]=httpx.Client) -> HeyGenVideoService:
        migrate_database(database_path)
        factory=sessionmaker(create_sqlite_engine(database_path),expire_on_commit=False,class_=Session)
        resolved=provider or HeyGenMcpAdapter()
        root=configured_work_root(work_root)
        handler=HeyGenVideoHandler(factory,resolved,root,client_factory=client_factory)
        jobs=JobService.for_database(database_path,handlers={'heygen.video.generate':handler},work_root=root)
        return cls(factory,resolved,jobs,root)

    def close(self) -> None:
        self._jobs.close()
        engine=self._factory.kw.get('bind')
        if engine is not None:
            engine.dispose()

    def plan_videos(self, campaign_id: str, config: HeyGenVideoConfig, *, max_paid_renders: int) -> VideoPlan:
        if type(max_paid_renders) is not int or max_paid_renders<1:
            raise ValueError('explicit positive render budget required')
        preflight=self._provider.preflight_video(config)
        items: list[VideoPlanItem]=[]
        with self._factory() as session:
            if session.get(CampaignRow,campaign_id) is None:
                raise ValueError('campaign not found')
            voice=VoiceMasterRepository(session).approved_for_campaign(campaign_id)
            if voice is None or not voice.processed_sha256 or not voice.duration_seconds:
                raise ValueError('approved Voice Master required')
            scenes=list(session.scalars(select(SceneVariantRow).where(SceneVariantRow.campaign_id==campaign_id).order_by(SceneVariantRow.variant_id)))
            if not scenes:
                raise ValueError('campaign scenes required')
            assets=list(session.scalars(select(RemoteAssetRow).where(RemoteAssetRow.provider_account_ref==preflight.account_ref,RemoteAssetRow.status=='ready')))
            by_material={(a.kind,a.sha256):a.remote_asset_id for a in assets}
            for scene in scenes:
                image=ImageRepository.approved_candidate_for_scene_in_session(session,scene.id)
                if image is None:
                    raise ValueError('approved image required for each scene')
                image_id=by_material.get(('image',image.sha256))
                audio_id=by_material.get(('audio',voice.processed_sha256))
                if image_id is None or audio_id is None:
                    raise ValueError('prepare HeyGen assets before planning videos')
                item=VideoPlanItem(campaign_id=campaign_id,scene_variant_id=scene.id,image_candidate_id=image.id,
                    voice_master_id=voice.id,account_ref=preflight.account_ref,image_sha256=image.sha256,
                    audio_sha256=voice.processed_sha256,image_asset_id=image_id,audio_asset_id=audio_id,
                    duration_seconds=voice.duration_seconds,config=config,schema_fingerprint=preflight.schema_fingerprint)
                validate_video_item(self._factory,self._root,item)
                items.append(item)
        reused=sum(self._repo.find(video_logical_key(i)) is not None for i in items)
        return VideoPlan(items=items,new_count=len(items)-reused,reused_count=reused,
            reserved_count=len(self._repo.list_campaign(campaign_id)),max_paid_renders=max_paid_renders,
            total_audio_seconds=sum(i.duration_seconds for i in items))

    def submit_videos(self, campaign_id: str, config: HeyGenVideoConfig, *, max_paid_renders: int, approved_by: str) -> list[HeyGenRender]:
        validate_safe_identifier(approved_by,'approved_by',max_length=200)
        plan=self.plan_videos(campaign_id,config,max_paid_renders=max_paid_renders)
        by_key={video_logical_key(i):i for i in plan.items}
        requests=[JobSubmit(job_type='heygen.video.generate',campaign_id=campaign_id,scene_variant_id=i.scene_variant_id,
            idempotency_key='heygen.video.generate:'+key,input={'logical_key':key},retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY) for key,i in by_key.items()]
        def create(session: Session, job: JobRow) -> HeyGenRender:
            item=by_key[str(job.input_json['logical_key'])]
            validate_video_item(self._factory,self._root,item,session=session)
            return self._repo.create_in_session(session,job,item,max_paid_renders=max_paid_renders,approved_by=approved_by)
        def existing(job: Job) -> HeyGenRender:
            render=self._repo.find(str(job.input['logical_key']))
            if render is None or render.job_id!=job.job_id:
                raise ValueError('video job checkpoint missing')
            return render
        def check(session: Session) -> None:
            for item in plan.items:
                validate_video_item(self._factory,self._root,item,session=session)
            self._repo.check_budget_in_session(session,campaign_id,max_paid_renders)
        return [s.linked for s in self._jobs.submit_linked_batch(requests,create,existing,before_commit=check)]

    def list_videos(self, campaign_id: str) -> list[HeyGenRender]:
        return self._repo.list_campaign(campaign_id)

    def run_videos(self, campaign_id: str) -> VideoRunSummary:
        renders=self.list_videos(campaign_id)
        if not renders:
            raise ValueError('no reserved videos in campaign')
        concurrency=min(r.item.config.concurrency for r in renders if r.status not in {HeyGenRenderStatus.READY,HeyGenRenderStatus.FAILED}) if any(r.status not in {HeyGenRenderStatus.READY,HeyGenRenderStatus.FAILED} for r in renders) else 1
        def worker(index: int) -> None:
            worker_id=f'heygen-video-{uuid4()}-{index}'
            while self._jobs.worker_once(worker_id,campaign_id=campaign_id,job_type='heygen.video.generate',max_running=concurrency) is not None:
                pass
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            list(executor.map(worker,range(concurrency)))
        renders=self.list_videos(campaign_id)
        return VideoRunSummary(renders=renders,ready_count=sum(r.status==HeyGenRenderStatus.READY for r in renders),
            failed_count=sum(r.status==HeyGenRenderStatus.FAILED for r in renders),
            blocked_count=sum(r.status==HeyGenRenderStatus.RECONCILIATION_REQUIRED for r in renders))

    def reconcile_video(self, render_id: str, *, video_id: str | None = None, confirm_manual_binding: bool = False) -> HeyGenRender:
        render=self._repo.get(render_id)
        job=self._jobs.get_job(render.job_id)
        if job.status!=JobStatus.BLOCKED:
            raise ValueError('only blocked video jobs can be reconciled')
        if self._provider.preflight_video(render.item.config).account_ref!=render.item.account_ref:
            raise ValueError('video account mismatch')
        validate_video_item(self._factory,self._root,render.item)
        known_id=render.remote_video_id
        if known_id is not None and video_id is not None and known_id!=video_id:
            raise ValueError('cannot replace known video ID')
        resolved=known_id or video_id
        if resolved is None:
            if render.dispatch_started_at is not None:
                raise ValueError('ambiguous dispatch requires exact video ID and manual binding')
            render=self._repo.reset_no_dispatch(render_id)
            self._jobs.resume_reconciled_job(render.job_id,reason='no_dispatch_proven')
            return render
        state=self._provider.get_video(resolved)
        if state.video_id!=resolved:
            raise ValueError('remote video ID mismatch')
        expected={'callback_id':render.logical_key,'image_asset_id':render.item.image_asset_id,'audio_asset_id':render.item.audio_asset_id}
        for field,value in expected.items():
            fact=getattr(state,field)
            if fact is not None and fact!=value:
                raise ValueError('remote video material mismatch')
        if known_id is None and not confirm_manual_binding:
            raise ValueError('manual binding requires explicit confirmation')
        render=self._repo.record_video(render_id,resolved,manual_binding=known_id is None)
        self._jobs.resume_reconciled_job(render.job_id,reason='existing_dispatch_reconciled')
        return render
