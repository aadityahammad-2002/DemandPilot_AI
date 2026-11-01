# YottaFlex AI - Official submission requirements

Source: **YottaFlex AI Candidate Project Submission Guidelines** and **YottaFlex AI Candidate Project FAQ**, both supplied to the candidate. The following is a faithful summary of the provided documents, not additional requirements invented by the project.

| Requirement | Official guidance / how to comply |
|---|---|
| Deadline | Exactly **72 hours after YottaFlex sends the project**, calculated from the email's recorded send timestamp **and its time zone**. The artifact timestamps may be checked. A verbal midnight estimate is not a substitute for the actual email deadline. |
| Public GitHub | **Create a new, dedicated public repository.** Make the development understandable through the code and meaningful commits. Keep it public/editable until the deadline. |
| Send repository link | Send the link **from the same email address used during recruitment** and follow the recruitment submission instructions. Early submission is permitted; the latest version at the deadline is evaluated. |
| Executable code | Provide install/configure/run/verification instructions so reviewers can execute the software. |
| AI prompt history | Include **all prompts submitted to every AI tool used**. AI responses are optional. If full export is unavailable, retain a reasonable record and **disclose the limitation**. |
| AI contribution | Explain where AI accelerated the work, where reliance was heavy, and what the candidate personally reviewed, edited, validated and understands. |
| Engineering reasoning | Explain assumptions, technical choices, constraints, testing, measurable outputs, exceptions and limitations. |
| Individual work | AI/public learning sources permitted; implementation help from other people is not. |
| Infrastructure | Use free or locally runnable services; avoid unusually large/slow dependencies where possible. |
| Hosted demo / automated tests | **Not mandatory**, although working verification and clear setup instructions are mandatory. |
| Partial implementation | Explicitly allowed if works/does not work, assumptions, deviations, limitations and next steps are honestly disclosed. |
| Third-party code | Standard dependencies and small examples permitted; do not reuse a complete core solution. Honor licensing and attribution. |
| Public safety | No secrets, passwords, sensitive personal information or unauthorized proprietary code. |

## This repository's position

- Small development dataset: previously validated on Windows; curated evidence retained.
- Independent medium generator/validation/reproducibility verification: PASS in a separate runtime on 2026-11-01.
- **Full required 2,000 x 100 x 365 Parquet acceptance: NOT VERIFIED.** A guarded Windows runner is included; a runtime precheck without PyArrow is not a pass.
- Crash recovery/concurrency acceptance at full scale: not established.
- The repository does not claim live authentication/RBAC, distributed workers, hosted demonstration or calibrated demand forecasting accuracy.

**Submission readiness is the candidate's responsibility:** verify the original deadline email, merge any missing exact AI prompts from every tool, publish the GitHub repository, and send its link. No GitHub publication or email submission is asserted by this package.
