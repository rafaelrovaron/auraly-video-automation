from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig
from auraly_pipeline.heygen.video_service import HeyGenVideoService
from tests.heygen_video_support import ready_video_campaign
pytest_plugins = ['tests.test_heygen_video_media']


def test_three_variants_share_voice(tmp_path: Path, mp4: bytes) -> None:
    provider=FakeHeyGenProvider()
    database=tmp_path/'test.db'
    root=tmp_path/'work'
    ready_video_campaign(database,root,provider)
    service=HeyGenVideoService.for_database(database,work_root=root,provider=provider,client_factory=lambda:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=mp4))))
    config=HeyGenVideoConfig(resolution='720p')
    plan=service.plan_videos('campaign-one',config,max_paid_renders=3)
    assert plan.new_count==3
    assert len({i.audio_asset_id for i in plan.items})==1
    assert len({i.image_asset_id for i in plan.items})==2
    with pytest.raises(ValueError):
        service.submit_videos('campaign-one',config,max_paid_renders=2,approved_by='tester')
    assert service.list_videos('campaign-one')==[]
    renders=service.submit_videos('campaign-one',config,max_paid_renders=3,approved_by='tester')
    assert len(renders)==3
    summary=service.run_videos('campaign-one')
    assert summary.ready_count==3
    assert len({r.remote_video_id for r in summary.renders})==3
    assert provider.events.count('create_video')==3
    assert service.plan_videos('campaign-one',config,max_paid_renders=3).new_count==0
    service.submit_videos('campaign-one',config,max_paid_renders=3,approved_by='tester')
    service.run_videos('campaign-one')
    assert provider.events.count('create_video')==3
    service.close()


def test_expired_url_resumes_same_id(tmp_path: Path, mp4: bytes) -> None:
    provider=FakeHeyGenProvider()
    database=tmp_path/'test.db'
    root=tmp_path/'work'
    ready_video_campaign(database,root,provider)
    status=[403]
    def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status[0],content=mp4 if status[0]==200 else b'')
    service=HeyGenVideoService.for_database(database,work_root=root,provider=provider,client_factory=lambda:httpx.Client(transport=httpx.MockTransport(response)))
    service.submit_videos('campaign-one',HeyGenVideoConfig(resolution='720p'),max_paid_renders=3,approved_by='tester')
    summary=service.run_videos('campaign-one')
    assert summary.blocked_count==3
    ids={r.render_id:r.remote_video_id for r in summary.renders}
    status[0]=200
    for render in summary.renders:
        service.reconcile_video(render.render_id)
    assert service.run_videos('campaign-one').ready_count==3
    assert {r.render_id:r.remote_video_id for r in service.list_videos('campaign-one')}==ids
    assert provider.events.count('create_video')==3
    service.close()


def test_changed_account_or_inputs_block(tmp_path: Path) -> None:
    provider=FakeHeyGenProvider()
    db=tmp_path/'test.db'
    root=tmp_path/'work'
    ready_video_campaign(db,root,provider)
    service=HeyGenVideoService.for_database(db,work_root=root,provider=provider)
    service.submit_videos('campaign-one',HeyGenVideoConfig(),max_paid_renders=3,approved_by='tester')
    provider.account_ref='other-account'
    assert service.run_videos('campaign-one').blocked_count==3
    assert 'create_video' not in provider.events
    service.close()


def test_manual_binding_validation(tmp_path: Path) -> None:
    provider=FakeHeyGenProvider()
    db=tmp_path/'test.db'
    root=tmp_path/'work'
    ready_video_campaign(db,root,provider)
    service=HeyGenVideoService.for_database(db,work_root=root,provider=provider)
    renders=service.submit_videos('campaign-one',HeyGenVideoConfig(),max_paid_renders=3,approved_by='tester')
    provider.scenario='ambiguous'
    assert service.run_videos('campaign-one').blocked_count==3
    with pytest.raises(ValueError):
        service.reconcile_video(renders[0].render_id)
    provider.scenario='success'
    correct=provider.create_video(renders[0].item,callback_id=renders[0].logical_key)
    with pytest.raises(ValueError):
        service.reconcile_video(renders[0].render_id,video_id=correct)
    with pytest.raises(ValueError):
        service.reconcile_video(renders[1].render_id,video_id=correct,confirm_manual_binding=True)
    bound=service.reconcile_video(renders[0].render_id,video_id=correct,confirm_manual_binding=True)
    assert bound.manual_binding and bound.remote_video_id==correct
    service.close()
