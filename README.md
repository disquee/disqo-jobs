# jobpilot

AI job-application assistant. It discovers jobs from ToS-compliant sources, scores
how well each fits your profile, drafts a tailored resume + cover letter + screening
answers, lets you review/edit, helps you apply (you submit), and logs every
application to a CSV.

> **Scope & safety.** jobpilot does **not** scrape LinkedIn or auto-submit
> applications. Discovery uses official APIs (Adzuna, Jooble) and public ATS boards
> (Greenhouse, Lever). "Apply" opens a real browser, pre-fills what it can, and hands
> control to you for the final submit. This keeps your accounts safe and your
> applications high quality.

## Setup

```bash
cd jobpilot
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium            # for assisted apply
# WeasyPrint needs native libs for PDF output (macOS):
#   brew install pango gdk-pixbuf libffi
cp .env.example .env                   # fill in ANTHROPIC_API_KEY + job API keys
```

Then set up your profile (the real files are gitignored so your PII stays local):

```bash
cp profile/profile.example.yaml profile/profile.yaml
cp profile/resume_master.example.md profile/resume_master.md
```

- `profile/resume_master.md` — your real master resume (source of truth)
- `profile/profile.yaml` — skills, experience, screening defaults
- `config.yaml` — searches, target ATS companies, fit threshold

Secrets (`ANTHROPIC_API_KEY`, job API keys) can live in `.env` or, preferably, in
the macOS Keychain — they're resolved from the environment first, then Keychain:

```bash
security add-generic-password -a "$USER" -s ANTHROPIC_API_KEY -w 'sk-ant-...'
```

## Use

```bash
jobpilot discover --tailor     # pull jobs, score them, tailor those above threshold
jobpilot status                # counts by stage
jobpilot serve                 # open the review dashboard at http://127.0.0.1:8000
jobpilot apply <job_id>        # assisted apply for one job (or use the dashboard)
```

### Pipeline

```
discover → score (Claude) → tailor (resume/cover/answers) → review (you)
   → assisted apply (browser autofill, you submit) → log to applications.csv
```

## Output

`output/applications.csv` columns: `date_applied, job_title, company, location,
posting_text, apply_url, fit_score, resume_path, cover_letter_path, status`.

Tailored PDFs land in `output/resumes/` and `output/cover_letters/`.

## Get API keys (free tiers)

- Adzuna: https://developer.adzuna.com/  → `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`
- Jooble: https://jooble.org/api/about  → `JOOBLE_API_KEY`
- Greenhouse/Lever boards need no key; list company slugs in `config.yaml`.

## Sharing it

```bash
python scripts/package.py            # build + PII scan -> dist/jobpilot-<version>.tar.gz
python scripts/package.py --check    # scan only
```

The packager is **allowlist-based**: a file ships only if it's named in `ALLOW`.
Anything new — resumes, interview notes, contracts, `.env`, `jobpilot.db` — is excluded
by default rather than needing a rule. It then scans the staged tree for API keys,
private keys, phone numbers and email addresses, and **refuses to write the archive** if
anything matches. `config.yaml` and `.gitignore` are replaced with neutral starters.

Owner-specific terms (your name, private project names) live in `.pii-local`, which is
gitignored and never shipped — hard-coding them in the script would disclose the very
thing they protect.

Recipients follow `SETUP_PROMPT.md`, which walks an AI agent through install, pointing
it at their own LLM provider, and building their profile.

### Two-repo workflow

This checkout is the **private dev repo** — your real profile, `output/`, and personal
`config.yaml` live here and are gitignored. A separate **public repo** holds only the
allowlisted, scanned tree.

```bash
python scripts/package.py --publish          # sync + commit + show the diff (dry run)
python scripts/package.py --publish --push   # ...and push
```

The public repo is a squashed mirror, not a shared history — which is deliberate.
Content can only reach it by passing the allowlist and the PII scan, so no ordinary
`git push` can leak a resume into it. Commits are authored with your GitHub
`@users.noreply.github.com` address so no real email lands in public metadata.
