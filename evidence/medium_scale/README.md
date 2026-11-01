# Independent medium-scale verification (2026-11-01)

This evidence was generated from the candidate-supplied implementation in a separate assistant execution environment. It is **not** candidate-Windows full-scale acceptance.

- Config: `100` SKUs, `10` stores, `90` days, seed `20261030`, CSV, two-store batches.
- Generation and validation: **PASS**; `86,490` sales observations, `87,130` inventory snapshots, `86,490` ground truth rows.
- Independent deterministic replay: **PASS**; `72` manifest files, `65` fact partitions, `333,745` logical rows and no SHA-256 checksum mismatches.
- Approximate generation runtime on this assistant container: 19.19 s; maximum RSS about 100 MB. These results **cannot** be extrapolated to full scale.
- Complete generated CSV facts (approx. 39 MB) were intentionally not included in the public repo. The manifest, validator summary, and independent replay report are retained here.
- **Full-scale Parquet gate:** attempted in this assistant container but stopped during PRECHECK because `pyarrow` is unavailable; not a PASS.
