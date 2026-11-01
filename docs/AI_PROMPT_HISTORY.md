# AI Prompt History — User Inputs Only

**Project:** YottaFlex AI, 72-hour engineering challenge  
**AI assistance:** ChatGPT (OpenAI); additional tools, if used, must be disclosed by the candidate.  
**Scope:** This file intentionally contains **user instructions/prompts only**. It does **not** reproduce model responses, entire chat transcripts, local shell output, source-code diffs or passwords.

> **Completeness disclosure — important:** The available materials contain only selected original user messages and phase summaries. This is **NOT CERTIFIED AS THE COMPLETE HISTORY OF EVERY PROMPT** used during the 72-hour assessment. The recruitment FAQ asks for all prompts from every AI tool. Before submission, the candidate must append exact missing prompts from the ChatGPT export and any other coding AI tool. If exporting is not possible, disclose the gap rather than falsely asserting this document is complete. Any English translation or reconstruction below is explicitly labeled; it is not a verbatim original prompt.

## Original English prompts / instructions recoverable from the candidate's messages

### P-01 — Official challenge requirements forwarded to the assistant

**Language:** English  
**Source:** Candidate's message forwarding the official YottaFlex 72-hour problem statement, 2026-11-01.  
**User-provided challenge specification (transcribed with normalized Markdown formatting; check against original prompt export for byte-exact reproduction):**

> 72 Hours Code Challenge
>
> Associate AI Forward Deployed Engineers · AI Forward Deployed Engineer
>
> Business Context / Problem Forecast SKU-location demand and recommend replenishment based on lead time, safety stock and ordering constraints.
>
> Objective and Scope Build a working solution for this end-to-end workflow: Forecast SKU-location demand and recommend replenishment based on lead time, safety stock and ordering constraints. The solution should expose key decisions and exceptions, preserve traceability, and produce measurable outputs. Medium complexity means multiple related entities, conditional rules, at least one exception path and meaningful evaluation metrics.
>
> Primary Actors / Systems Customer, store/warehouse operator, merchandising/e-commerce analyst and operations manager
>
> Business Workflow and Rules 1. Generate/reference master data first: SKU, Store, SalesHistory, InventorySnapshot, Supplier. 2. Generate dependent transaction/event records using valid parent IDs. 3. Apply business logic: Create SKU/store matrix; generate daily sales with promotions and seasonality; record stockouts separately from zero demand; define supplier pack sizes, minimum order quantities and lead-time distributions; compute forecast; apply reorder point and capacity constraints. 4. Persist state changes as explicit records/events where relevant; do not overwrite history. 5. Generate outcomes and ground-truth labels from documented rules, not independent random assignment.
>
> Entities and Relationships Core entities: SKU, Store, SalesHistory, InventorySnapshot, Supplier, SupplierSKU, LeadTimeObservation, Forecast, ReorderPolicy, PurchaseRecommendation. Define a primary key for every entity and foreign keys for every relationship. Document cardinality (1:1, 1:N, N:M), optional relationships, effective dates and lifecycle states. Use bridge tables for many-to-many relationships; retain event history for state changes; validate all joins. For deliberate anomaly cases, keep an issue_type/ground_truth label rather than silently corrupting unrelated rows.
>
> ID / Nomenclature Conventions SKU-000001; STORE-000001; SALE-<store-id>-<sku>-<date>; FC-<store-id>-<sku>-2026-01; PO-2026-000001.
>
> Synthetic Data Generation Instructions (Granular) Step 1 — Write a data dictionary for: SKU, Store, SalesHistory, InventorySnapshot, Supplier, SupplierSKU, LeadTimeObservation, Forecast, ReorderPolicy, PurchaseRecommendation. For each field specify type, required/optional, allowed values, units, uniqueness and validation rule. Step 2 — Create parent/master tables first, then child and event tables. Apply this domain-specific generation logic: Create SKU/store matrix; generate daily sales with promotions and seasonality; record stockouts separately from zero demand; define supplier pack sizes, minimum order quantities and lead-time distributions; compute forecast; apply reorder point and capacity constraints. Step 3 — Apply deterministic IDs and naming: SKU-000001; STORE-000001; SALE-<store-id>-<sku>-<date>; FC-<store-id>-<sku>-2026-01; PO-2026-000001. Keep counters zero-padded, IDs immutable, and referenced IDs exactly consistent across files. Step 4 — Generate the baseline volume: Generate 2,000 SKUs, 100 stores and 365 days of sales/inventory. Include intermittent demand, seasonal peaks, stockouts, supplier delays and product launches. Use a fixed seed (e.g. 20261022) and parameterize scale so starter and full datasets use the same generator. Step 5 — Generate valid baseline records first, then inject labelled edge cases: Stockout mistaken for zero demand; reorder quantity ignores pack size; discontinued SKU recommended; lead time negative; forecast uses future sales. Step 6 — Calculate derived values (totals, balances, scores, statuses and dates) from source records rather than generating them independently. Step 7 — Produce a separate ground_truth.csv/json for known anomalies, expected decisions or labels; never leak it into input features. Step 8 — Run validation: primary-key uniqueness, foreign-key integrity, allowed enums, date ordering, arithmetic reconciliation, lifecycle transitions and duplicate checks. Step 9 — Export CSV/JSON/SQL seed data plus README, schema/data dictionary, generator command, fixed seed, source manifest and validation summary. Step 10 — Regenerate twice with the same seed and compare row counts/checksums to demonstrate reproducibility.
>
> Suggested Volume and Coverage Generate 2,000 SKUs, 100 stores and 365 days of sales/inventory. Include intermittent demand, seasonal peaks, stockouts, supplier delays and product launches.
>
> Required Edge Cases / Negative Scenarios Stockout mistaken for zero demand; reorder quantity ignores pack size; discontinued SKU recommended; lead time negative; forecast uses future sales.
>
> Validation Rules / Acceptance Criteria Forecast evaluation uses temporal holdout; recommendations respect MOQ/pack size/capacity; stockout censoring is identified.
>
> Evaluation Focus for Generated Data Review: (a) realistic distributions and meaningful variation; (b) consistent IDs and valid joins; (c) dependency/lifecycle consistency across entities; (d) correct derived values and reconciliation; (e) normal, boundary, failure and exception coverage; (f) separate ground truth; (g) fixed-seed reproducibility, documentation and lawful provenance.
>
> Public Data Guidance / Lawful Use External data is optional. If used, record source URL, dataset/version, retrieval date, license/terms, attribution, transformations and fields discarded. Use only data you are legally permitted to access and redistribute; do not bypass access controls or collect confidential/personal information. If licensing or suitability is unclear, use synthetic data and cite public reference data only for schema inspiration.

### P-02 — Critical backend corrections (verbatim recoverable excerpt)

**Language:** English  
**Source:** Candidate's 2026-10-31 V6 backend correction request. **Excerpt only; do not mistake this for the full original prompt.**

> YottaFlex AI — V6 Final Critical Backend Business-Logic Corrections
>
> Use the latest verified V5 source as the baseline.
>
> The independent V1–V5 audit reproduced two HIGH-severity business-logic defects that remain despite 153 passing backend tests.
>
> Implement both corrections, run automated regression tests, and deliver a complete backend-only merge-ready V6 package. Do not stop at a report.
>
> Fix Historical SKU Lifecycle Eligibility.
>
> Current issue: app/replenishment/engine.py uses the SKU's present/end-state status alongside its lifecycle dates. The synthetic generator may store DISCONTINUED as the eventual status. Historical decisions dated before discontinuation are therefore incorrectly blocked.
>
> Required improvements: Implement consistent date-effective lifecycle eligibility. Distinguish lifecycle state as of the decision date from the current master-record status. Preserve blocking.

### P-03 — Full-scale development constraint (candidate original wording, recoverable)

> Do not run/request full-scale 2,000 × 100 × 365 during intermediate development. Use smaller validated datasets for development. Preserve Phase 1 migrations and Phase 2 code/evidence. Full-scale generation is reserved for final end-to-end acceptance.

**Provenance:** This English record captures a candidate instruction preserved in project history. The exact original message may include additional text; it must be supplemented from the exported AI prompt history if available.

## English translations / task-only reconstructions (NOT verbatim prompts)

The following records capture additional *candidate requests* that are materially relevant to the build and submission. They are English-language translations or compact reconstructions from conversational instructions. They are included for transparency, not as a substitute for the original prompts required by the FAQ.

### T-01 — Work phase by phase

> Complete the end-to-end forecasting and replenishment system, including all ten entities, five negative scenarios, deterministic synthetic generation, APIs, traceability, error handling, downloadable evidence and an interactive dashboard. Validate each phase with reproducible tests. Preserve already accepted migrations and evidence.

### T-02 — Backend hardening and source-level corrections

> Use the latest verified source archive as the baseline; find and fix backend correctness bugs, preserve earlier implemented phases, add regression tests and provide a complete merge-ready code package. Do not claim Windows/PostgreSQL tests that you did not actually run.

### T-03 — Consolidated testing

> Combine the remaining tests into one PowerShell script so that the project can be verified in a single run instead of many separate manual steps.

### T-04 — Cleanup, full-scale acceptance and GitHub submission

> Remove unnecessary generated and temporary files from the project without disturbing working source code or evidence. Create an English prompt-only AI-use history, not a full chat transcript. Test the mandatory 2,000-SKU, 100-store, 365-day data generation and reproducibility gate, then assemble a clean public-repository-ready source package. Include the official submission rules.

## Candidate completion action — required for compliance

- [ ] Export any missing **exact** user prompts from ChatGPT and other AI coding tools, including architecture, implementation, bug fixing and acceptance prompts.
- [ ] Add them **as original prompts** in English when originally written in English. For other languages, explicitly mark translations and retain the originals privately / attach original prompt text as permitted.
- [ ] Ensure the historical request dates, tool names, and any quoted prompts are accurate; do not add fabricated prompts.
- [ ] Revisit the AI assistance disclosure and honestly state what was personally reviewed/validated.

**The assessment FAQ explicitly permits a reasonable record when a tool cannot export prompts, but the export limitation must be disclosed.** This curated file is a partial recoverable record and does not falsely certify completeness.

## Optional exact-prompt recovery from an export

If a ChatGPT data export provides a `conversations.json` file, use the offline helper below to extract **only user messages**, filtered to YottaFlex-titled conversations, into a new file **outside the public repo**:

```powershell
python .\scripts\EXTRACT_USER_PROMPTS_FROM_EXPORT.py --source "C:\PATH\TO\conversations.json" --title-contains "YottaFlex" --output "$env:USERPROFILE\Downloads\YottaFlex_Exact_Prompts_Review.md"
```

Review the output for passwords, private content, irrelevant messages and missing conversations **before** copying approved text into this file. Exports and conversation titles vary; this helper may omit messages or conversations, so it is not an automatic completeness guarantee. Do not publish raw `conversations.json`.
