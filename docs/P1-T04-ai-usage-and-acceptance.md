# P1-T04 — AI usage disclosure and evidence separation

## AI-generated material in this turn

OpenAI ChatGPT assisted with Phase 4 architecture, data contracts, Python policy functions, signed-input readers, decision engine, verifier, CLI, pytest suite, PowerShell local runner and this documentation. The model also executed development smoke tests in its isolated environment. This does **not** mean the candidate independently authored the code or verified it on their Windows/PostgreSQL machine.

Candidate's original challenge requirements and acceptance history were inputs. Candidate is responsible for reviewing, understanding, correcting, executing, retaining prompt history and acknowledging precisely which suggestions were incorporated.

## Evidence ledger (do not overwrite prior evidence)

| Item | Source | Status |
|---|---|---|
| P1-T01 PostgreSQL tests and 0002 migration | Candidate-reported laptop logs | Accepted according to earlier conversation, preserved |
| P1-T02 smoke, 29/29 | Candidate-reported laptop logs | Accepted; full scale deferred |
| P1-T03 64/64 and 6/6 same seed | Candidate-reported laptop logs | Accepted |
| P1-T04 tests | AI local isolated source snapshot | Executed; saved in this patch's `evidence/P1-T04-AI-*` |
| P1-T04 Windows local verification | Candidate laptop | **NOT YET RUN / NOT ACCEPTED** |
| PostgreSQL Forecast/Recommendation integration | Not implemented here | **NOT VERIFIED** |
| Full 2,000 × 100 × 365 | Deferred by candidate | **NOT VERIFIED** |
| Frontend and end-to-end acceptance | Not implemented here | **NOT VERIFIED** |

## Personal acceptance checklist

- Review Phase 4 treatment of historic stockouts, genuinely intermittent zeros, promotion horizon and supplier delays.
- Execute new local PowerShell verification and inspect full pytest output, decision CSV, reason codes and reproducibility report.
- Inspect sample trace numbers manually: compare lead demand, daily curve sum, ROP, safety, MOQ, pack and store capacity calculations.
- Audit confidential information before including evidence and chat prompt history in a public repository.
- Note any modifications (authorship and reasons) separately from AI-generated suggestions.
