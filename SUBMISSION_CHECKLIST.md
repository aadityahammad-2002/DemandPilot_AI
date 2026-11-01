# Final candidate submission checklist

Use this checklist **before sending the repository URL**. It is based on YottaFlex's supplied Guidelines + FAQ, and is intentionally honest about incomplete scale gates.

- [ ] Confirm exact **72-hour deadline** from the recruitment email's original timestamp and time zone (do not rely on an assumed midnight deadline if the email says otherwise).
- [ ] Create a **new, dedicated public GitHub repository**, and verify its public visibility from a logged-out browser.
- [ ] Review `git status` and staged diff; no `.env`, credentials, PostgreSQL dumps, local machine caches, full Parquet outputs, restricted personal data or unauthorized material.
- [ ] Run the working smoke API/dashboard or check preserved acceptance evidence; explain the missing full-scale gate rather than claiming PASS.
- [ ] Ensure `README.md` gives enough steps to install, configure, run and validate the system.
- [ ] Include **all original AI prompts from every tool**. `docs/AI_PROMPT_HISTORY.md` is only the recoverable/translated subset and is **not claimed complete**. Export and append exact prompts where possible.
- [ ] Review `docs/AI_ASSISTANCE_DISCLOSURE.md`; disclose heavy AI assistance and candidate-owned review/testing accurately.
- [ ] Make actual Git commits with understandable descriptions (do **not** fabricate a retrospective multi-day commit history).
- [ ] If the full generator gate is run, include its **real** `full_scale_acceptance.json` and `full_reproducibility.json` result; no PASS evidence exists in this archive.
- [ ] Keep the public repository available/editable through the actual deadline, and send the GitHub link **from the same email address used in recruitment** following recruiter submission instructions.

The YottaFlex FAQ allows partial implementations with clear disclosure. A live hosted demo and automated tests are not official mandatory deliverables; runnable code, transparent AI use and reasoning remain important.
