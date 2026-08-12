"""Run this the moment you have credentials. Before anything else.

It answers one question: does a grounded Gemini call return citation metadata
that survives all the way into what the recorder stores?

If it does not, replay is not free — every re-score becomes a re-run, the
improvement loop loses half its sweeps, and you find out in week four instead
of today.

    export GOOGLE_CLOUD_PROJECT=your-project     # Vertex, uses GCP credits
    # or
    export GOOGLE_API_KEY=...                    # AI Studio

    python3 tools/check_grounding.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from harness.model import ProviderError, StubClient, get_client  # noqa: E402

QUESTION = "What year was the Voynich manuscript carbon-dated to, and by whom?"


def main() -> int:
    client = get_client(verbose=False)

    if isinstance(client, StubClient):
        print("No credentials found. Set one of:")
        print("  export GOOGLE_CLOUD_PROJECT=<project>   # Vertex — GCP credits")
        print("  export GOOGLE_API_KEY=<key>             # AI Studio")
        return 1

    print(f"backend      {client.backend}")
    print(f"question     {QUESTION}\n")

    try:
        resp = client.generate(QUESTION, grounded=True)
    except ProviderError as exc:
        print(f"BLOCKED  {exc}")
        if exc.hint:
            print(f"\n         {exc.hint}")
        return 2

    print(f"text         {resp.text[:160]}...")
    print(f"tokens       in {resp.usage['prompt_tokens']}, out {resp.usage['output_tokens']}")
    print(f"cost         ${resp.cost:.6f}\n")

    ok = True

    # 1. grounding metadata present at all
    if resp.grounding:
        gm = resp.grounding[0]
        chunks = gm.get("grounding_chunks") or []
        queries = gm.get("web_search_queries") or []
        print(f"PASS  grounding_metadata present")
        print(f"      {len(chunks)} chunks, {len(queries)} search queries")
        for c in chunks[:3]:
            web = c.get("web") or {}
            print(f"        - {web.get('title', '?')[:64]}")
    else:
        print("FAIL  no grounding_metadata on the response")
        print("      Either grounding did not trigger, or the field is being dropped.")
        ok = False

    # 2. it survives the round trip the recorder actually performs
    restored = json.loads(json.dumps(resp.raw, default=str))
    survived = any(c.get("grounding_metadata") for c in restored.get("candidates", []))
    print(f"{'PASS' if survived else 'FAIL'}  survives JSON round trip "
          f"(what the recorder writes and replay reads)")
    ok &= survived

    # 3. the evidence text is stored, not just the URLs
    blob = json.dumps(resp.raw)
    has_text = "grounding_supports" in blob or "retrieved_context" in blob
    print(f"{'PASS' if has_text else 'WARN'}  supporting spans stored"
          f"{'' if has_text else ' — only links? re-scoring may need the source text'}")

    out = pathlib.Path(__file__).resolve().parent.parent / "out" / "grounding_probe.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(resp.raw, indent=2, default=str) + "\n")
    print(f"\nfull payload {out}")

    if not ok:
        print("\nIf this fails, bypass any wrapper for Research and call the SDK "
              "directly. Research is one agent; it can be the exception.")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
