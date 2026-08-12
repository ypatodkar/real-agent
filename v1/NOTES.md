# v1 — archived

Short-form video pipeline, built Aug 7–12. Archived because the output was not
watchable: crude PIL sprites, gradient backgrounds, synthesised noise instead of
speech. The machinery underneath worked; what it rendered did not.

This file exists so the next attempt can steal what was good and skip what was
not. Everything below was verified by running it, not by reasoning about it.

---

## Worth taking

**`harness/`** — the part that earned its keep. No video-domain logic in it; it
survived having LangGraph ripped out in two files, because every stage is just
`(state, ctx) -> partial dict`.

| File | What it does | Reusable? |
|---|---|---|
| `recorder.py` | Append-only JSONL per run, verbatim model payloads including `groundingMetadata`. Makes re-scoring free. | **Yes, as-is** |
| `budget.py` | Per-stage caps checked *before* execution, three breach behaviours, per-call estimates | **Yes, as-is** |
| `node.py` | The wrapper: budget → execute → record. Plus `assert_scope`, a runtime guard that a repair only touched what it declared | **Yes, as-is** |
| `runner.py` | ~60 lines that walk a declared topology. Gates suspend on a generator, so no checkpointer is needed | **Yes** — simpler than any framework for a fixed pipeline |
| `model.py` | One interface over Vertex / AI Studio / stub. Hard-won: see *Credentials* below | **Yes** — the diagnosis hints alone save hours |

**`qc/rules.py`** — eleven checks, nine of them pure arithmetic. `loudness_spec`
uses ffmpeg's real ITU-R BS.1770 scanner rather than an RMS approximation. The
plan/render split (cheap checks before any asset exists) genuinely worked: it
caught planning errors for one text repair instead of a wasted asset round.

**`qc/graders.py`** — the two model-graded rules, pinned in a namespace the
Improvement Agent cannot address, with a fingerprint check that fails loudly on
runtime tampering. Verified by tampering with it.

**The EDL-as-artifact idea.** Planning produces a document; rendering is a
deterministic function of it. That made cuts verifiable against a beat grid and
made "same plan, same bytes" testable. Worth keeping whatever renders the pixels.

---

## What went wrong

**The visual approach was a cost decision wearing an aesthetic.** Nine flat
drawings per character, animated by arithmetic, chosen to survive a $100
ceiling. The ceiling turned out to be ~5× looser than assumed, so the constraint
that justified the whole look had largely evaporated — but the look stayed.

**Green checkmarks measured the machinery, not the video.** `11/11 rules pass`
was true and meaningless: none of the eleven asks whether anyone would watch it.
There is no check for "is this good", and the absence never surfaced because the
passing ones felt like progress.

**Placeholders became the product.** `spike/render.py`, `make_sprites.py` and
`make_fixtures.py` were written to test plumbing without an API key. They were
never replaced, and every demo ran on them.

---

## Facts established — these survive any rewrite

**Costs are ~5× below the original estimates.** A full run with four model calls
is **$0.005–0.02**, not the `$0.110` the design budgeted. A grounded search call
is `$0.00018`. Cost is not the binding constraint it was assumed to be.

**Credentials — the path that works:**
- AI Studio prepay and Google Cloud credit are **separate meters**. A hackathon
  Cloud credit is invisible to an API key; it only reaches models via Vertex.
- Vertex needs: `aiplatform.googleapis.com` enabled, a service account with
  **Vertex AI User**, a JSON key, and three env vars. No CLI required.
- `API_KEY_SERVICE_BLOCKED` ≠ `SERVICE_DISABLED`. The first is the key's
  restriction list; the second is the API. They need opposite console pages.
- `gemini-2.5-flash` **works on Vertex** and 404s on new AI Studio keys.
- Imagen was **not listed in `us-central1`** on Vertex — check region before
  planning around it.

**Model behaviour worth designing for:**
- Ask for JSON via `response_mime_type` or you get prose. Grounding and JSON
  mode are mutually exclusive, so grounded calls need extraction from text.
- Ask for JSON *without a schema* and the model invents its own shape.
- Given no line-count constraint, it wrote **25 lines for a 15-second video**.
- Given `"Synthesize and measure the VO"` it returned a description of
  **Vanadium(II) Oxide**. Prompts that make sense to a developer do not
  necessarily make sense to a model.

**Six stages were paying a model for arithmetic** before anyone noticed. If the
architecture says a stage is deterministic, wire it deterministically.

---

## Running it, if you need to

```bash
cd v1
python3 tools/make_fixtures.py && python3 tools/make_sprites.py
python3 run.py --topic "..."        # CLI
python3 ui/server.py                # browser UI at :8000
```

Needs `.env` at the repo root with the Vertex variables. Last known state:
green end to end, four model calls, zero fallbacks, ~$0.005 a run.
