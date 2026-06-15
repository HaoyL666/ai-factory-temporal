from __future__ import annotations

import unittest

from ai_factory_temporal.usage import estimate_usage_cost, normalize_token_usage


class UsageTest(unittest.TestCase):
    def test_normalizes_responses_style_cached_tokens(self) -> None:
        usage = normalize_token_usage(
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_tokens_details": {"cached_tokens": 30},
            }
        )

        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["billable_input_tokens"], 70)
        self.assertEqual(usage["cache_read_input_tokens"], 30)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["total_tokens"], 120)

    def test_estimates_cost_with_cache_prices(self) -> None:
        usage = {
            "billable_input_tokens": 70,
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 5,
        }

        cost = estimate_usage_cost(
            usage,
            input_price_per_1m=10.0,
            output_price_per_1m=20.0,
            cache_read_price_per_1m=1.0,
            cache_creation_price_per_1m=12.0,
        )

        self.assertAlmostEqual(cost or 0, 0.00119)

    def test_normalizes_codex_sdk_total_usage_shape(self) -> None:
        usage = normalize_token_usage(
            {
                "last": {
                    "cached_input_tokens": 15232,
                    "input_tokens": 16245,
                    "output_tokens": 527,
                    "reasoning_output_tokens": 414,
                    "total_tokens": 16772,
                },
                "total": {
                    "cached_input_tokens": 50176,
                    "input_tokens": 61164,
                    "output_tokens": 1808,
                    "reasoning_output_tokens": 1462,
                    "total_tokens": 62972,
                },
            }
        )

        self.assertEqual(usage["input_tokens"], 61164)
        self.assertEqual(usage["cache_read_input_tokens"], 50176)
        self.assertEqual(usage["billable_input_tokens"], 10988)
        self.assertEqual(usage["output_tokens"], 1808)
        self.assertEqual(usage["total_tokens"], 62972)

    def test_cost_is_none_without_prices(self) -> None:
        self.assertIsNone(
            estimate_usage_cost(
                {"input_tokens": 100, "output_tokens": 20},
                input_price_per_1m=None,
                output_price_per_1m=None,
                cache_read_price_per_1m=None,
                cache_creation_price_per_1m=None,
            )
        )


if __name__ == "__main__":
    unittest.main()
