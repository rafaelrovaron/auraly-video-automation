# Linux Database Path Test Design

## Problem

The Linux full-verification job executes a test that expects Windows-only MSYS drive-path
normalization. Production code intentionally performs that conversion only when `os.name ==
"nt"`, so the unconditional assertion fails on Linux while Windows behaves correctly.

## Design

Keep production path handling unchanged. Mark the MSYS drive-form assertion as Windows-only and
add a POSIX-only assertion that an absolute native path supplied through `AURALY_DATABASE_PATH`
is preserved. This makes each platform test its actual contract without simulating another
platform or weakening the Linux full suite.

## Verification

Run the two database-path tests, the complete migration test module, the fast harness with that
module, and the full deterministic harness. GitHub Actions remains the independent proof that the
Linux runner is repaired.
