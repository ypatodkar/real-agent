from __future__ import annotations

import unittest

from second_unit.model import GeminiModel, MAX_OUTPUT_TOKENS


class FakeResponse:
    text = '{"response": {}}'

    def model_dump(self, **_: object) -> dict[str, object]:
        return {"usage_metadata": {"prompt_token_count": 3, "candidates_token_count": 2}}


class FakeModels:
    def __init__(self) -> None:
        self.config = None

    def generate_content(self, **kwargs: object) -> FakeResponse:
        self.config = kwargs["config"]
        return FakeResponse()


class FakeClient:
    def __init__(self) -> None:
        self.models = FakeModels()


class GeminiModelBoundaryTests(unittest.TestCase):
    def test_each_provider_call_has_a_deadline_and_output_cap(self) -> None:
        client = FakeClient()
        model = GeminiModel(
            backend="vertex",
            client=client,
            model="fake-model",
            timeout_seconds=12.5,
        )

        result = model.decide(system="system", prompt="prompt", schema={"type": "object"})

        self.assertEqual(result.backend, "vertex")
        self.assertEqual(client.models.config.http_options.timeout, 12_500)
        self.assertEqual(client.models.config.max_output_tokens, MAX_OUTPUT_TOKENS)

    def test_provider_deadline_is_clamped_to_a_bounded_range(self) -> None:
        low = GeminiModel(backend="vertex", client=FakeClient(), timeout_seconds=0)
        high = GeminiModel(backend="vertex", client=FakeClient(), timeout_seconds=500)

        self.assertEqual(low.timeout_seconds, 5.0)
        self.assertEqual(high.timeout_seconds, 30.0)

    def test_outline_call_can_request_a_larger_bounded_budget(self) -> None:
        client = FakeClient()
        model = GeminiModel(backend="vertex", client=client, model="fake-model")

        model.decide(
            system="system", prompt="prompt", schema={"type": "object"},
            timeout_seconds=30, max_output_tokens=5_000,
        )

        self.assertEqual(client.models.config.http_options.timeout, 30_000)
        self.assertEqual(client.models.config.max_output_tokens, 5_000)


if __name__ == "__main__":
    unittest.main()
