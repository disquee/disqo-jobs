# disqo jobs

AI job-application assistant. It discovers jobs from ToS-compliant sources, scores
how well each fits your profile, drafts a tailored resume + cover letter + screening
answers, lets you review/edit, helps you apply (you submit), and logs every
application to a CSV.

> **Scope & safety.** disqo jobs does **not** scrape LinkedIn or auto-submit
> applications. Discovery uses official APIs (Adzuna, Jooble) and public ATS boards
> (Greenhouse, Lever). "Apply" opens a real browser, pre-fills what it can, and hands
> control to you for the final submit. This keeps your accounts safe and your
> applications high quality.

## Setup

```bash
cd disqo jobs
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
disqo-jobs discover --tailor     # pull jobs, score them, tailor those above threshold
disqo-jobs status                # counts by stage
disqo-jobs serve                 # open the review dashboard at http://127.0.0.1:8000
disqo-jobs apply <job_id>        # assisted apply for one job (or use the dashboard)
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
