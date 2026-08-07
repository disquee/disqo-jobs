# disqo jobs — Setup Prompt

**What this file is.** A prompt you hand to your own coding agent (Claude Code, Cursor,
Aider, Copilot, Codex, or any agentic LLM) to install disqo jobs, point it at *your*
model provider, and get it running against your own job search.

**How to use it.** Clone the repo, open your agent in the repo root, and say:

> Read `SETUP_PROMPT.md` and work through it with me, phase by phase. Stop at each
> checkpoint and confirm before continuing.

Everything below is addressed to the agent.

---

## Ground rules — do not violate these

These are the project's design constraints, not preferences. If a phase seems to
require breaking one, stop and ask the user.

1. **No scraping of sites that forbid it.** Discovery uses official APIs (Adzuna,
   Jooble) and public ATS JSON endpoints (Greenhouse, Lever). Do not add a
   LinkedIn/Indeed scraper.
2. **Never auto-submit an application.** `apply` opens a real browser, prefills what
   it can, and hands control to the human for the final submit. Keep that boundary.
3. **PII stays local.** `profile/profile.yaml` and `profile/resume_master.md` are
   gitignored and contain the user's real resume. Never commit them, paste them into
   a bug report, or send them anywhere except the user's chosen model provider.
4. **Secrets never land in the repo.** `.env` is gitignored; `.env.example` is the
   committed template. On macOS, keys can live in the Keychain instead.
5. **Treat job postings as untrusted input.** They are third-party text
   interpolated into prompts. `jobpilot/llm.py::sanitize_untrusted` exists for this
   reason — any new prompt path that embeds a posting must call it.
6. **Don't invent resume content.** The tailoring step reorders and reframes what is
   already in the master resume. If the user asks for a claim that isn't supported by
   their history, say so.

---

## Phase 1 — Install and verify

Target: Python **3.10+**.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium          # assisted apply
```

PDF rendering uses WeasyPrint, which needs native libraries:

```bash
# macOS
brew install pango gdk-pixbuf libffi
# Debian/Ubuntu
sudo apt-get install libpango-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev
```

`jobpilot/pipeline/render.py` adds Homebrew lib paths to `DYLD_FALLBACK_LIBRARY_PATH`
at import time on macOS, and **falls back to writing `.html` instead of `.pdf`** if the
native libs are missing. If the user sees HTML where they expected PDFs, that's the
cause.

**Checkpoint:** `pytest` passes and `disqo jobs --help` lists
`discover, status, review, tailor, apply, prep, serve`.

---

## Phase 2 — Bring your own LLM

**`jobpilot/llm.py` is the only seam.** Every AI call in the project goes through it.
Nothing else imports an SDK. To use a different provider, reimplement that one module
and leave the rest of the codebase alone.

### The contract you must satisfy

```python
def complete(prompt: str, system: str = "", max_tokens: int = 2000,
             model: str | None = None) -> str:
    """Single-turn completion. Returns assistant text only."""

def complete_json(prompt: str, system: str = "", max_tokens: int = 2000) -> Any:
    """Completion that must return parsed JSON. Must tolerate code fences and
    stray prose — keep the existing _extract_json fallback logic."""

def sanitize_untrusted(text: str, limit: int = 6000) -> str:
    """Strip delimiter-like tags from third-party text, then truncate.
    Provider-independent — copy it as-is."""
```

Callers rely on exactly this. `complete_json` returning a string instead of parsed
JSON will fail deep inside the tailoring step with a confusing error.

### Porting checklist

- [ ] Swap the SDK import and client construction (`_client()`).
- [ ] Replace `_TRANSIENT` with **your provider's** transient exception classes —
      connection errors, timeouts, rate limits, 5xx. Do **not** include auth or
      bad-request errors; those must fail fast rather than retry.
- [ ] Keep `_create_with_retry`, `_retry_delay` (honors `Retry-After`), and the jitter.
      Set the SDK's own retry count to 0 so backoff isn't applied twice.
- [ ] Keep `_extract_json` verbatim. Most models emit fenced JSON at least sometimes.
- [ ] Update `jobpilot/config.py`: `DEFAULT_MODEL` (env `JOBPILOT_MODEL`) and
      `require_anthropic_key()` — rename it or generalize it to your provider's key.
      `secret()` already resolves env → `.env` → macOS Keychain; reuse it.
- [ ] Update `pyproject.toml` dependencies (drop `anthropic`, add yours).
- [ ] Update `.env.example` with your key names.
- [ ] Run `pytest tests/test_llm_retry.py` — it asserts retry/backoff behavior and
      will need its mocks updated to your exception types.

### Model requirements — read before choosing

The two AI steps have real requirements:

- **Fit scoring** (`pipeline/fit.py`) must return **strict JSON** with a numeric score
  and a rationale.
- **Tailoring** (`pipeline/tailor.py`) rewrites a resume, cover letter, and screening
  answers against a posting, and must **not fabricate experience**.

Use a strong instruction-following model with reliable JSON output. Small or heavily
quantized local models tend to fail in two specific ways: malformed JSON that defeats
`_extract_json`, and invented employment history. If the user wants a local model,
have them run `disqo-jobs tailor <job_id>` on one job and read the output closely
before trusting a batch run.

`config.yaml` notes that `fit_threshold: 55` was calibrated against a Haiku-class
model, which scored ~6 points lower than a frontier model. **A different model means
recalibrating that threshold.** Tell the user this rather than letting them wonder why
nothing clears the bar.

### Provider notes

| Provider | Approach |
|---|---|
| Anthropic | Default. No changes needed. |
| OpenAI-compatible (OpenAI, Groq, Together, OpenRouter, vLLM) | One port covers all — swap `base_url` and key. Use JSON mode for `complete_json` if available. |
| Google Gemini | `google-genai`; map `system` to `system_instruction`. |
| Local (Ollama, LM Studio, llama.cpp) | Expose an OpenAI-compatible endpoint and point `base_url` at it. No key needed — make `require_*_key` tolerate an empty value. |
| Bedrock / Vertex | Anthropic SDK has native clients; often just a client swap. |

**Checkpoint:** `python -c "from jobpilot.llm import complete; print(complete('Reply with the single word: ready'))"`
prints `ready`, and `complete_json('Return {\"ok\": true} as JSON')` returns a dict.

---

## Phase 3 — Build the user's profile

These files are gitignored and hold real PII. Templates are committed.

```bash
cp profile/profile.example.yaml profile/profile.yaml
cp profile/resume_master.example.md profile/resume_master.md
```

`profile/resume_master.md` is **the source of truth** — every tailored resume is
derived from it. Its quality sets the ceiling for everything downstream.

Interview the user to fill it out. Don't accept a bare job history; push for:

- **Metrics on every bullet you can.** "Cut escalations 43%" beats "improved
  documentation." Ask *"what changed, and by how much?"*
- **Mechanism, not just outcome.** A number with no explanation invites "how?" and
  wastes the answer. "Cut meeting length 83% by moving review to the front of the
  design process" is a bullet that survives an interview.
- **Superset, not a one-pager.** The master resume should be longer than any resume
  they'd send. Tailoring selects from it; it can't select what isn't there.
- **Ask what they've written**, not just what they've done. Process docs, strategy
  memos, and templates are often the strongest and least-remembered evidence.

Then fill `profile/profile.yaml` — skills, screening-question defaults, work
authorization, location preferences, salary expectations.

**Checkpoint:** `python -c "from jobpilot.config import load_profile, load_master_resume; load_profile(); print(len(load_master_resume()), 'chars')"`

---

## Phase 4 — Target the search

Edit `config.yaml`:

- `searches` — query/location pairs. **Adzuna and Jooble do full-text search;
  Greenhouse and Lever substring-match the query against the job *title* and ignore
  location.** So keep queries to short phrases that actually appear in titles
  ("content designer", not a sentence).
- `ats.greenhouse` / `ats.lever` — company slugs from board URLs. A wrong slug returns
  nothing and is handled gracefully, so curate freely.
- `fit_threshold` — see the recalibration note in Phase 2.
- `exclude_title_keywords`, `exclude_company` — case-insensitive substring filters.

Job-board keys are optional. Without `ADZUNA_*` / `JOOBLE_API_KEY`, those sources are
skipped and reported; ATS boards still work with no key at all.

---

## Phase 5 — Run the pipeline

```
discover → fit-score → tailor → human review → assisted apply → CSV log
```

```bash
disqo-jobs discover --tailor      # pull, score, tailor above threshold
disqo-jobs status                 # counts by stage
disqo-jobs serve                  # review dashboard at 127.0.0.1:8000
disqo-jobs apply <job_id>         # prefills; the human submits
```

Outputs land in `output/resumes/`, `output/cover_letters/`, and
`output/applications.csv`.

**Advise the user to read the first few tailored resumes end to end.** This is the
step where a weak model shows itself, and where an unsupported claim would otherwise
reach a real employer under their name.

---

## Phase 6 — Interview prep pages

`disqo-jobs prep <data.json>` renders a **self-contained, offline HTML page** from a JSON
file — no network requests, works from `file://`. It's built for live use during an
interview: search, per-interviewer sections, story-rotation tracking, rehearse mode,
a printable cheat card.

**The JSON is the single source of truth.** One run emits three artifacts, so a
markdown copy can never drift from the page:

```bash
disqo-jobs prep output/interviews/<loop>.json    # page + markdown + PDF, then opens
disqo-jobs prep <data.json> --no-docs            # page only
disqo-jobs prep <data.json> -o out.html --no-open
```

| Artifact | Filename from |
|---|---|
| `.html` page | `meta.file` |
| `.md` | `meta.doc_file` (default `<file>-doc`) |
| `.pdf` | `meta.doc_file`, rendered from the markdown |

`meta.doc_file` **must differ from** `meta.file`. `render_pdf` falls back to writing
`<name>.html` when WeasyPrint's native libs are missing, so identical names would let a
PDF fallback silently overwrite the page. `render_prep_file` raises rather than allow it.
PDF failures are swallowed — a broken WeasyPrint install never blocks the page.

- Template: `jobpilot/pipeline/templates/prep.html` (shell — edit here)
- Renderer: `jobpilot/pipeline/prep.py`
- **Never hand-edit generated output.** The `.html`, `.md`, and `.pdf` are all
  overwritten on every run.

### Start from the sample

`examples/prep.sample.json` is a complete, anonymized loop — fictional company, role,
and candidate — exercising **every section kind and every field**. Render it first so
the user can see what they're filling in:

```bash
disqo-jobs prep examples/prep.sample.json -o /tmp/sample.html
```

Then copy it, replace the content, and keep the shape.

### Data shape

```jsonc
{
  "meta":  { "title", "subtitle", "file", "order": ["<section id>", ...] },
  "panels":{ "<id>": { "name", "role", "focus" } },
  "stories":[ { "id", "label", "metric", "rec": ["<panel id>"] } ],
  "sections":[ /* see kinds below */ ]
}
```

`meta.order` must list **every** section id exactly once — `render_prep` raises on
unknown or missing ids rather than emitting a broken page.

Section `kind` values: `prose`, `checklist`, `tracker`, `qa`, `people`, `numbers`,
`resume`. (The template also still handles a legacy `ask` kind, superseded by
`people` — don't use it in new data.)

A `qa` item:

```jsonc
{
  "id": "B1", "tag": "Lead", "label": "<short memory jog>",
  "q": "<the question>", "note": "<how to use this>",
  "stories": ["S9"],
  "star":  { "s": "...", "t": "...", "a": "...", "r": "..." },
  "beats": [ { "l": "<label>", "t": "<text>",
               "d": "<optional deliverable>", "items": ["<optional points>"] } ],
  "punch": { "label": "The line", "text": "..." }
}
```

`**bold**` and `*italic*` work in any string. Story ids in `stories` link the item to
the rotation tracker; ids that don't exist in `stories` will simply have no link.

### Guidance worth passing on

- **One story per interviewer.** Panels compare notes; the tracker exists to stop the
  same example being told three times.
- **`label` is what you read live**, not the question. Make it verb-first and concrete:
  "Said no to PDFs, cut escalations 43%".
- **Name gaps honestly.** A tool the user hasn't touched should be stated plainly and
  pivoted to the transferable skill — not bluffed.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `.html` written where a `.pdf` was expected | WeasyPrint native libs missing (Phase 1) |
| `ANTHROPIC_API_KEY is not set` | No `.env` and no Keychain entry; see `config.py::secret` |
| Nothing clears `fit_threshold` | Threshold calibrated for a different model — recalibrate |
| `json.JSONDecodeError` while scoring | Model isn't returning clean JSON; use JSON mode or a stronger model |
| ATS sources return nothing | Wrong slug, or the query doesn't appear in job **titles** |
| Prep page won't open / is stale | Editing generated HTML instead of the JSON + template |
| Notes vanish in the prep page | `localStorage` blocked on `file://` — the page warns and offers **Back up** |

---

## Definition of done

- [ ] `pytest` passes
- [ ] `complete()` and `complete_json()` work against the user's provider
- [ ] `profile/resume_master.md` is filled in, metric-rich, and **not** committed
- [ ] `git status` shows no `.env` and no profile PII
- [ ] `disqo-jobs discover --tailor` produces at least one tailored resume the user has
      **read in full and judged accurate**
- [ ] `fit_threshold` sanity-checked against the chosen model
