# Source cleanup and repository hygiene

This clean repository is a **copy of the supplied source**, not a destructive edit of the candidate's working `yottaflex_phase1` directory.

## Preserved

- All application source under `backend/app` and `frontend/src`, PostgreSQL migrations, backend tests, browser tests, dependency manifests, and functional setup/configuration scripts.
- 10-entity data dictionary, architectural/engineering documentation, version 8 acceptance references.
- Small illustrative synthetic data (`examples/smoke_run`) and safe **summary-level** regression evidence.
- Full-scale gated runner modules and separate full-scale instructions. The 73-million-combination dataset is not included.

## Excluded from the public repository

- Local credentials and secrets (`.env`), bytecode, caches, build output, test traces, temporary/backup files and database dumps.
- Multiple copies of intermediate job artifacts and earlier temporary evidence folders.
- Old patch staging directories and superseded setup instructions.

**Exclusion categories observed during packaging**:

- duplicate runtime job artifacts: 145 files
- intermediate docs: 10 files
- other: 14 files

Source files examined: 326; files omitted from clean copy: 169. This audit counts only original source files and omits new submission documentation.

## Never do this

Do not run bulk `Remove-Item` against the original source. Keep your existing working directory, PostgreSQL database `yottaflex_dual_v7`, and original evidence safe. Perform GitHub publication from this separate clean checkout.
