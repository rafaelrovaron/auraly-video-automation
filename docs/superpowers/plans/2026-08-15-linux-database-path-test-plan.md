# Linux Database Path Test Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make database-path tests express the native Windows and POSIX contracts so Linux full verification can run the suite correctly.

**Architecture:** Change only `tests/test_migrations.py`. Preserve production path normalization, gate the MSYS assertion to Windows, and add a POSIX-native environment override test.

**Tech Stack:** Python 3.11, pytest, pathlib

## Global Constraints

- Do not change production path behavior.
- Do not invoke providers or paid actions.
- Preserve the full deterministic verification baseline.

---

### Task 1: Correct platform-specific database-path coverage

**Files:**
- Modify: `tests/test_migrations.py:1-90`

**Interfaces:**
- Consumes: `default_database_path() -> Path`
- Produces: native-platform regression coverage for `AURALY_DATABASE_PATH`

- [ ] **Step 1: Add the POSIX regression test before changing the existing test**

```python
@pytest.mark.skipif(os.name == "nt", reason="POSIX path semantics only")
def test_database_path_accepts_native_posix_form(monkeypatch) -> None:
    monkeypatch.setenv("AURALY_DATABASE_PATH", "/tmp/auraly/auraly.db")
    assert default_database_path() == Path("/tmp/auraly/auraly.db")
```

- [ ] **Step 2: Verify the current platform-specific suite exposes the unconditional MSYS test**

Run the existing failing test against a Linux environment or use the captured GitHub Actions
failure as the red evidence: `tests/test_migrations.py::test_database_path_accepts_msys_drive_form`
fails because Linux preserves the supplied path.

- [ ] **Step 3: Gate the MSYS assertion to Windows**

```python
@pytest.mark.skipif(os.name != "nt", reason="Windows MSYS path semantics only")
def test_database_path_accepts_msys_drive_form(monkeypatch) -> None:
    ...
```

- [ ] **Step 4: Run targeted and full local verification**

```bash
uv run pytest tests/test_migrations.py -q
uv run python scripts/verify.py fast --pytest tests/test_migrations.py
uv run python scripts/verify.py full
```

- [ ] **Step 5: Review and commit the focused fix**

```bash
git diff --check
git add tests/test_migrations.py docs/superpowers/specs/2026-08-15-linux-database-path-test-design.md docs/superpowers/plans/2026-08-15-linux-database-path-test-plan.md
git commit -m "test: make database path checks platform aware"
```
