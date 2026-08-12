"""What can this key actually reach?

Answers the question directly instead of guessing from documentation: lists the
models the key can see, grouped by what this project needs them for, and names
anything missing.

    python3 tools/check_models.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from harness.model import DEFAULT_TEXT_MODEL, StubClient, get_client  # noqa: E402

# What the pipeline needs, and the substring that identifies a candidate.
NEEDS = [
    ("Planning + grading", "gemini", "Brief, Outline, Research, Script, graders"),
    ("Images",             "imagen", "character roster and background plates"),
    ("Speech",             "tts",    "the Voice stage"),
]


def main() -> int:
    client = get_client(verbose=False)
    if isinstance(client, StubClient):
        print("No credentials. Put GOOGLE_API_KEY in .env, or set GOOGLE_CLOUD_PROJECT.")
        return 1

    print(f"backend   {client.backend}\n")

    try:
        models = list(client._client.models.list())
    except Exception as exc:
        text = str(exc)
        if "API_KEY_SERVICE_BLOCKED" in text:
            print("BLOCKED — the API is on, but this key may not call it.")
            print("  Console > APIs & Services > Credentials > your key > API restrictions")
            print("  Add 'Generative Language API', or choose \"Don't restrict key\".")
        elif "SERVICE_DISABLED" in text:
            print("BLOCKED — the API is not enabled on this project.")
            print("  console.cloud.google.com/apis/library/generativelanguage.googleapis.com")
        else:
            print(f"FAILED — {text[:300]}")
        return 2

    names = sorted(m.name.replace("models/", "") for m in models)
    print(f"{len(names)} models visible to this key\n")

    for label, token, why in NEEDS:
        found = [n for n in names if token in n.lower()]
        mark = "OK  " if found else "MISS"
        print(f"{mark} {label:20} {why}")
        for n in found[:4]:
            print(f"       · {n}")
        if len(found) > 4:
            print(f"       · … and {len(found) - 4} more")
        if not found:
            print("       · nothing matching — may need a different backend or region")
        print()

    # Listed is not the same as callable: retired models still appear in the
    # listing and 404 on use, and a depleted account lists everything happily.
    configured = DEFAULT_TEXT_MODEL
    print(f"configured text model: {configured}")
    try:
        client.generate("Reply with the single word: ok", model=configured,
                        stub={"ok": True})
        print("  CALLABLE — end to end works")
        return 0
    except Exception as exc:
        from harness.model import ProviderError, _diagnose
        hint = exc.hint if isinstance(exc, ProviderError) else _diagnose(exc)
        print(f"  NOT CALLABLE — {str(exc)[:160]}")
        if hint:
            print(f"\n  {hint}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
