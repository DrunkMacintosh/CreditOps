from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _migration() -> str:
    return (
        (ROOT / "supabase/migrations/202607170010_upload_intents_completion.sql")
        .read_text(encoding="utf-8")
        .lower()
    )


def test_upload_migration_provisions_private_bounded_buckets_fail_closed() -> None:
    sql = _migration()
    assert "creditops-incoming" in sql
    assert "creditops-originals" in sql
    assert "creditops-derived" in sql
    assert "raise exception" in sql
    assert "existing_public" in sql
    assert "104857600" in sql
    assert "allowed_mime_types" in sql
    assert "declared_size_bytes <= 104857600" in sql


def test_upload_migration_links_consumption_to_completed_idempotency() -> None:
    sql = _migration()
    assert "completion_idempotency_record_id" in sql
    assert "references public.idempotency_records(id)" in sql
    assert "completed_at is not null" in sql
    assert "upload_intents_consumed_status_check" in sql
    assert "elsif new.status = 'consumed'" in sql
    assert "elsif new.status <> 'open'" not in sql


def test_api_writes_are_not_granted_to_browser_roles() -> None:
    sql = _migration()
    assert "revoke all on" in sql
    assert "from public, anon, authenticated" in sql
    assert "to creditops_api" in sql
    assert "processing_tasks_api_insert" in sql
    assert "status = 'pending'" in sql
    assert "and attempt_count = 0" in sql
    assert "document_versions as version" in sql


def test_storage_adapter_never_returns_service_role_or_enables_upsert() -> None:
    source = (
        (ROOT / "services/api/src/creditops/infrastructure/supabase/storage.py")
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "service_role_key" in source
    assert '"x-upsert": "false"' in source
    assert '"upsert": false' in source
    assert "return uploadauthorization" in source
    assert "service_role_key" not in source.split("return uploadauthorization", 1)[1]
