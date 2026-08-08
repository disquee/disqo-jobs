# disqo jobs

AI job-application assistant. It discovers jobs from ToS-compliant sources, scores
how well each fits your profile, drafts a tailored resume + cover letter + screening
answers (and, if you turn it on, a full-length CV), lets you review/edit, helps you
apply (you submit), and logs every application to a CSV.

> **Scope & safety.** disqo jobs does **not** scrape LinkedIn or auto-submit
> applications. Discovery uses official APIs (Adzuna, Jooble) and public ATS boards
> (Greenhouse, Lever). "Apply" opens a real browser, pre-fills what it can, and hands
> control to you for the final submit. This keeps your accounts safe and your
> applications high quality.

## Install

Double-click the launcher in the disqo jobs folder:

- **macOS / Linux** — `start-disqo-jobs.command`
- **Windows** — `start-disqo-jobs.bat`

That's the whole install. The first run builds an environment and installs the
app, which takes a couple of minutes; after that it starts straight up. Either
way it opens <http://127.0.0.1:8000> in your browser. Leave the window open
while you're using disqo jobs — closing it quits.

The one thing you need beforehand is **Python 3.10 or newer**. If it's missing,
the launcher says so and points you at [python.org](https://www.python.org/downloads/).

### First run

disqo jobs opens on **Setup** and stays there until you're done. It walks you
through AI access, your resume, a few details about you, and what to look for.
There are no files to copy and no keys to paste into a terminal. Everything you
set there can be changed later under **Settings**.

Your resume, your jobs and your log live in a folder outside the app, so
updating or deleting disqo jobs never touches them:

| | |
|---|---|
| macOS | `~/Library/Application Support/jobpilot` |
| Windows | `%APPDATA%\jobpilot` |
| Linux | `~/.local/share/jobpilot` |

Set `JOBPILOT_DATA_DIR` to put that somewhere else — a synced folder, say. The
**Data** page shows the current location, what's in it, and the backups.

## Install for development

```bash
git clone <this repo> && cd jobpilot
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
disqo-jobs serve                          # http://127.0.0.1:8000
```

Two extras, each needed only by the feature next to it:

```bash
playwright install chromium               # assisted apply
brew install pango gdk-pixbuf libffi      # WeasyPrint, for PDF resumes and covers
```

Run the tests with `pip install -e '.[dev]' && pytest`.

Templates are re-read on every request, so an edit shows up on refresh. Python
modules are not — a change to `server.py` or `store.py` needs a restart, and a
half-reloaded app fails as a 500 rather than as a missing feature.

### Secrets outside the app

Setup writes keys to `.env` for you, and that's the path that works on every OS.
If you'd rather keep them elsewhere, anything already in the environment wins,
and on macOS the Keychain is checked after that:

```bash
security add-generic-password -a "$USER" -s ANTHROPIC_API_KEY -w 'sk-ant-...'
```

### Files Setup writes for you

Editable by hand afterwards if you prefer; the real ones are gitignored so your
details stay local.

- `profile/resume_master.md` — your master resume, the source everything else is cut from
- `profile/profile.yaml` — skills, experience, screening defaults
- `config.yaml` — searches, target ATS companies, fit threshold

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

Everything below lands in the data folder listed under [First run](#first-run),
not in the app folder.

`output/applications.csv` columns: `date_applied, job_title, company, location,
posting_text, apply_url, fit_score, resume_path, cover_letter_path, status`.

Tailored PDFs land in `output/resumes/` and `output/cover_letters/` — and
`output/cvs/` when the CV is turned on. The CV is off by default; the setup
walkthrough asks once, Settings holds the default, and every job's page has its
own switch that overrides it.

## Get API keys (free tiers)

Job-board keys go under **Settings → AI and job-board keys**; you don't need a
terminal for them.

- Adzuna: https://developer.adzuna.com/  → `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`
- Jooble: https://jooble.org/api/about  → `JOOBLE_API_KEY`
- Greenhouse/Lever boards need no key; pick companies during Setup, or later
  under **Settings → What to search for**.

## Licence

Copyright © 2026 Eric Disque. Released under the
[GNU Affero General Public License v3.0](LICENSE).

Use it, change it, pass it to a friend — free, for as long as you like. The one
thing the licence asks is that if you run a modified copy as a service other
people use over a network, you publish your changes under the same terms. That's
deliberate: this was built so that someone job-hunting without money or contacts
has the tools that normally cost both, and it should stay that way rather than
becoming somebody's subscription.

Contributions are welcome and go through a
[contributor agreement](CLA.md) that assigns copyright to the project owner, so
those terms remain enforceable. See [CONTRIBUTING.md](CONTRIBUTING.md) — bug
reports and ideas need no agreement at all.
