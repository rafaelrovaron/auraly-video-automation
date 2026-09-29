from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import text

from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database


NOW = "2026-09-29T12:00:00+00:00"


def create_ready_campaign(
    database: Path, work_root: Path, *, duplicate_first_two_images: bool = False, voice_content: bytes = b'voice'
) -> None:
    migrate_database(database)
    assets = work_root / "campaigns" / "campaign-one"
    assets.mkdir(parents=True, exist_ok=True)
    voice = assets / "voice.wav"
    voice.write_bytes(voice_content)
    image_facts: list[tuple[str, str, int]] = []
    for index in range(3):
        path = assets / f"image-{index}.png"
        content = b"image-0" if duplicate_first_two_images and index == 1 else f"image-{index}".encode()
        path.write_bytes(content)
        image_facts.append(
            (path.relative_to(work_root).as_posix(), hashlib.sha256(content).hexdigest(), len(content))
        )

    engine = create_sqlite_engine(database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO campaigns (id,character,proof_object,voice_preset,edit_preset,"
                "budget_json,config_json,status,created_at,updated_at) VALUES "
                "('campaign-one','character','proof','voice','edit','{}','{}','draft',:now,:now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO copy_masters (id,campaign_id,version,source_text,headline,hook,body,cta,"
                "spoken_text,sha256,approval_state,approved_by,approved_at,created_at,updated_at) "
                "VALUES ('10000000-0000-4000-8000-000000000000','campaign-one',1,'source','head',"
                "'hook','body','cta','spoken',:sha,'approved','tester',:now,:now,:now)"
            ),
            {"sha": "a" * 64, "now": NOW},
        )
        for index, (relative_path, sha, size) in enumerate(image_facts):
            scene_id = f"20000000-0000-4000-8000-00000000000{index}"
            candidate_id = f"30000000-0000-4000-8000-00000000000{index}"
            connection.execute(
                text(
                    "INSERT INTO scene_variants (id,campaign_id,variant_id,location,time_atmosphere,"
                    "action,prompt,proof_object,status,created_at,updated_at) VALUES "
                    "(:id,'campaign-one',:variant,'studio',NULL,'pose','prompt',NULL,'not_started',:now,:now)"
                ),
                {"id": scene_id, "variant": f"scene-{index}", "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO image_candidates (id,scene_variant_id,source_kind,image_generation_id,"
                    "import_manifest_sha256,import_source_path,candidate_index,source_path,sha256,width,"
                    "height,size_bytes,format,review_status,approved_at,approved_by,created_at,updated_at) "
                    "VALUES (:id,:scene,'manual_import',NULL,:manifest,:path,0,:path,:sha,1080,1920,:size,"
                    "'png','approved',:now,'tester',:now,:now)"
                ),
                {
                    "id": candidate_id,
                    "scene": scene_id,
                    "manifest": str(index + 1) * 64,
                    "path": relative_path,
                    "sha": sha,
                    "size": size,
                    "now": NOW,
                },
            )
        voice_path = voice.relative_to(work_root).as_posix()
        voice_sha = hashlib.sha256(voice.read_bytes()).hexdigest()
        logical_key = "b" * 64
        connection.execute(
            text(
                "INSERT INTO jobs (id,job_type,campaign_id,scene_variant_id,status,priority,"
                "idempotency_key,request_fingerprint,input_json,attempt_count,max_attempts,"
                "retry_safety,created_at,updated_at,queued_at) VALUES "
                "('50000000-0000-4000-8000-000000000000','voice.generate','campaign-one',NULL,"
                "'queued',0,:key,:fingerprint,:input,0,3,'manual_only',:now,:now,:now)"
            ),
            {
                "key": f"voice.generate:{logical_key}",
                "fingerprint": "d" * 64,
                "input": '{"voiceMasterId":"40000000-0000-4000-8000-000000000000"}',
                "now": NOW,
            },
        )
        connection.execute(
            text(
                "INSERT INTO voice_masters (id,campaign_id,copy_master_id,copy_master_version,generation,"
                "logical_key,status,provider,voice_preset,voice_id,model_id,output_format,settings_json,"
                "settings_fingerprint,raw_audio_path,processed_audio_path,transcript_path,manifest_path,"
                "raw_sha256,processed_sha256,transcript_sha256,manifest_sha256,raw_size_bytes,raw_format,"
                "duration_seconds,word_count,wpm,sample_rate,channels,loudness_lufs,true_peak_dbfs,"
                "leading_silence_seconds,trailing_silence_seconds,long_internal_pauses_json,"
                "transcript_source,transcript_match_status,transcript_match_score,headline_spoken,"
                "qc_findings_json,provider_state,job_id,approved_at,approved_by,created_at,updated_at) VALUES "
                "('40000000-0000-4000-8000-000000000000','campaign-one',"
                "'10000000-0000-4000-8000-000000000000',1,1,:logical,'pending','elevenlabs','voice',"
                "'voice-id','model-id','mp3_44100_128','{}',:settings,:path,:path,'transcript.json',"
                "'manifest.json',:sha,:sha,:sha,:sha,5,'wav',1,10,120,44100,1,-16,-1,0,0,'[]',"
                "'faster_whisper','matched',1,0,'[]','response_received',"
                "'50000000-0000-4000-8000-000000000000',:now,'tester',:now,:now)"
            ),
            {
                "logical": logical_key,
                "settings": "c" * 64,
                "path": voice_path,
                "sha": voice_sha,
                "now": NOW,
            },
        )
        connection.execute(
            text(
                "UPDATE voice_masters SET status='review_required' "
                "WHERE id='40000000-0000-4000-8000-000000000000'"
            )
        )
        connection.execute(
            text(
                "UPDATE voice_masters SET status='approved' "
                "WHERE id='40000000-0000-4000-8000-000000000000'"
            )
        )
    engine.dispose()
