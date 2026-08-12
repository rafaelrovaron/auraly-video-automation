from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from auraly_pipeline.campaigns.persistence import migrate_database, sqlite_url


def test_database_rejects_direct_approved_insert_with_recursive_triggers_disabled(
    tmp_path: Path,
) -> None:
    database = tmp_path / "direct-approved.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    now = "2026-08-12T00:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(text("PRAGMA recursive_triggers=OFF"))
        connection.execute(
            text(
                "INSERT INTO campaigns (id,character,proof_object,voice_preset,edit_preset,"
                "budget_json,config_json,status,created_at,updated_at) VALUES "
                "('campaign-a','character','proof','preset','edit','{}','{}','draft',:now,:now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO copy_masters (id,campaign_id,version,source_text,headline,hook,body,cta,"
                "spoken_text,sha256,approval_state,approved_by,approved_at,created_at,updated_at) "
                "VALUES ('copy-a','campaign-a',1,'source','Headline','Hook','Body','CTA',"
                "'Hook Body CTA',:sha,'approved','operator',:now,:now,:now)"
            ),
            {"sha": "a" * 64, "now": now},
        )
        try:
            connection.execute(
                text(
                    "INSERT INTO voice_masters (id,campaign_id,copy_master_id,copy_master_version,"
                    "generation,logical_key,status,provider,voice_preset,voice_id,model_id,output_format,"
                    "settings_json,settings_fingerprint,raw_audio_path,processed_audio_path,transcript_path,"
                    "manifest_path,raw_sha256,processed_sha256,transcript_sha256,manifest_sha256,"
                    "raw_size_bytes,raw_format,duration_seconds,word_count,wpm,sample_rate,channels,"
                    "loudness_lufs,true_peak_dbfs,leading_silence_seconds,trailing_silence_seconds,"
                    "long_internal_pauses_json,transcript_source,transcript_match_status,"
                    "transcript_match_score,headline_spoken,qc_findings_json,provider_state,approved_at,"
                    "approved_by,created_at,updated_at) VALUES ('voice-a','campaign-a','copy-a',1,1,:key,"
                    "'approved','elevenlabs','preset','voice','model','mp3_44100_128','{}',:sha,"
                    "'raw.mp3','processed.wav','transcript.json','manifest.json',:sha,:sha,:sha,:sha,1,"
                    "'mp3',1,1,60,48000,1,-16,-1,0,0,'[]','elevenlabs_alignment','matched',1,0,'[]',"
                    "'response_received',:now,'operator',:now,:now)"
                ),
                {"key": "b" * 64, "sha": "c" * 64, "now": now},
            )
        except IntegrityError as exc:
            assert "must be inserted pending" in str(exc)
        else:
            raise AssertionError("direct approved VoiceMaster insert was accepted")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM voice_masters")) == 0
    engine.dispose()
