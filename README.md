# Second Unit

Short-form video, generated and verified.

Built for [Agentic Cinema](https://agentic-cinema.devpost.com/), Grafana track.

---

## Status

**Restarting.** The first attempt is archived in [v1/](v1/) — the pipeline,
harness and QC worked, but what it rendered was not watchable.

[v1/NOTES.md](v1/NOTES.md) has the honest post-mortem: what is worth reusing,
what went wrong, and the facts that were established by running it — real
costs, the credential path that works, and how the model behaves when you are
careless with a prompt.

The brainstorm workflow is now implemented: a context-driven interview builds
toward a scene outline, with SQLite draft archiving and optional live voice
typing through Google Cloud Speech-to-Text.

## Run locally

```bash
python3 -m pip install -r requirements.txt
python3 app/server.py
```

Open `http://localhost:8000`. The microphone is enabled when
`GOOGLE_CLOUD_PROJECT` and Application Default Credentials are configured and
the Cloud Speech-to-Text API is enabled. The HTTP server runs on the selected
port and its local speech WebSocket uses the next port (`8001` by default).
Voice recordings are streamed directly to Google and are not stored.

Enable Speech-to-Text for the configured project once:

```bash
gcloud services enable speech.googleapis.com --project "$GOOGLE_CLOUD_PROJECT"
```

The official rules are summarised in [RULES.md](RULES.md) — worth reading first,
because two of them constrain the architecture directly: no non-Google agent
frameworks, and the Grafana Cloud MCP server must be called **at runtime**.
