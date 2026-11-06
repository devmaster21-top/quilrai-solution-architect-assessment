"""Bounded-state evaluation; generates input lazily and discards output."""

import json
import platform
import time
import tracemalloc
from pathlib import Path

from quilr_gateway.redaction import StreamRedactor


def measure(name, chunk, count):
    redactor = StreamRedactor()
    tracemalloc.start()
    started = time.perf_counter()
    emitted = 0
    for _ in range(count):
        emitted += len(redactor.feed(chunk))
    emitted += len(redactor.finish())
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert redactor.peak_pending <= 320
    return {
        "case": name,
        "input_characters": len(chunk) * count,
        "output_characters": emitted,
        "elapsed_seconds": round(elapsed, 4),
        "peak_traced_bytes": peak,
        "max_pending_characters": redactor.peak_pending,
    }


def main():
    result = {
        "python": platform.python_version(),
        "platform": platform.system(),
        "cases": [
            measure("normal_prose", "Hello alex@example.com, card 4111 1111 1111 1111! ", 20000),
            measure("adversarial_unbroken_token", "a" * 1000, 10000),
        ],
    }
    destination = Path("docs/results/redaction-benchmark.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
