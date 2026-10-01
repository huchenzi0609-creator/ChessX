#!/usr/bin/env python3
"""Safe connectivity check; --probe makes one small paid reasoning/JSON request."""

import argparse
import asyncio
import json
import time

import httpx
from chessx.config import Settings
from chessx.llm import ModelError, ProviderAdapter


async def check(settings: Settings, *, probe=False, transport=None) -> dict:
    report = {
        "configured": settings.configured,
        "generation_checked": False,
        "generation_calls": 0,
        "passed": False,
    }
    if not settings.configured:
        return {**report, "error_code": "MODEL_NOT_CONFIGURED"}
    started = time.monotonic()
    try:
        adapter = ProviderAdapter(
            settings.base_url,
            settings.api_key,
            settings.model,
            settings.tokenizer_name,
            settings.timeout_seconds,
            response_format=settings.response_format,
            tokenizer_agreement=settings.tokenizer_agreement,
            provider_profile=settings.provider_profile,
            transport=transport,
        )
        async with asyncio.timeout(10):
            async with httpx.AsyncClient(
                timeout=10, transport=transport, follow_redirects=False
            ) as client:
                response = await client.get(
                    adapter.base_url + "/models",
                    headers={"Authorization": "Bearer " + settings.api_key.get_secret_value()},
                )
        report["models_http_status"] = response.status_code
        if response.status_code != 200:
            return {**report, "error_code": "MODEL_LIST_FAILED"}
        envelope = response.json()
        listed = any(item.get("id") == settings.model for item in envelope["data"])
        report["configured_model_listed"] = listed
        if not listed:
            return {**report, "error_code": "MODEL_NOT_LISTED"}
        if probe:
            report.update(generation_checked=True, generation_calls=1)
            await adapter.probe()
            result = adapter.last_probe_result
            report.update(
                exposed_reasoning=True,
                strict_json=True,
                generation_calls=1,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                reasoning_tokens=result.reasoning_tokens,
                generation_ms=result.latency_ms,
            )
        return {**report, "passed": True, "elapsed_seconds": round(time.monotonic() - started, 2)}
    except ModelError as error:
        return {**report, "error_code": error.code}
    except (TimeoutError, httpx.TimeoutException):
        return {**report, "error_code": "MODEL_TIMEOUT"}
    except httpx.HTTPError:
        return {**report, "error_code": "MODEL_TRANSPORT_ERROR"}
    except Exception:
        return {**report, "error_code": "MODEL_CHECK_FAILED"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe", action="store_true", help="Make one bounded paid generation request."
    )
    args = parser.parse_args()
    result = asyncio.run(check(Settings.load(), probe=args.probe))
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)
