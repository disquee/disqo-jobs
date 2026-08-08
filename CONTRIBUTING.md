# Contributing

disqo jobs exists so that someone job-hunting without money, contacts or a
career coach has the same tools as someone who has all three. That's the test
for any change: does it help that person get hired?

## Things that need no paperwork

- **Bug reports.** What you did, what happened, what you expected. If a page
  misbehaved, the terminal window running disqo jobs usually says why.
- **Ideas and questions.** Open an issue.

Both are real contributions and neither needs an agreement.

## Code

Pull requests need a one-time agreement first: [CLA.md](CLA.md), which assigns
copyright in your contribution to the project's owner. It's there so the terms
of the GNU AGPL-3.0 stay enforceable — the reasoning is at the bottom of that file. Sign by
adding yourself to [CONTRIBUTORS.md](CONTRIBUTORS.md) in your first pull request.

### Setting up

```bash
git clone https://github.com/disquee/disqo-jobs.git && cd disqo-jobs
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
disqo-jobs serve            # http://127.0.0.1:8000
```

Two extras, each needed only by the feature next to it:

```bash
playwright install chromium               # assisted apply
brew install pango gdk-pixbuf libffi      # WeasyPrint, for PDF resumes and covers
```

### While you work

Templates are re-read on every request, so an edit shows up on refresh. Python
modules are not — a change under `jobpilot/` needs a restart, and a half-reloaded
app fails as a 500 rather than as a missing feature.

### What we look for

- **Tests pass**, and new behaviour comes with a test. `pytest` is the whole suite.
- **Nothing leaves the machine.** No analytics, no telemetry, no phoning home.
  Someone's resume, job list and work-search log stay on their computer. The only
  outbound calls are to job-board APIs and the user's own AI provider, and the
  Data page tells them so.
- **No scraping and no auto-submitting.** Discovery uses official APIs and public
  ATS boards. Applying opens a real browser and hands over control before submit.
  This keeps users' accounts safe, and it's not negotiable.
- **The work-search log is a legal record.** People file it for unemployment
  benefits. Code that touches it should preserve entries, never silently drop them.
- **Plain language in the interface.** The person using this is stressed and not
  a developer. "Nothing in the queue" beats "No results returned".

### Commits

Write the message for whoever hits this code in a year — what changed and why,
not a restatement of the diff.
