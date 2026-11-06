import pytest

from quilr_gateway.redaction import StreamRedactor

CASES = [
    ("Contact alex@example.com now.", "Contact [REDACTED] now."),
    ("SSN: 123-45-6789!", "SSN: [REDACTED]!"),
    ("Card: 4111 1111 1111 1111!", "Card: [REDACTED]!"),
    ("Card: 4111-1111-1111-1111!", "Card: [REDACTED]!"),
    ("x+y@example.co.uk", "[REDACTED]"),
    ("alex@example.com 123-45-6789 4111111111111111", "[REDACTED] [REDACTED] [REDACTED]"),
    ("Hello world! Nothing sensitive.", "Hello world! Nothing sensitive."),
    ("Number 123 and order 456.", "Number 123 and order 456."),
    ("Mail 123@example.com!", "Mail [REDACTED]!"),
]


@pytest.mark.parametrize("source,expected", CASES)
def test_every_possible_two_chunk_boundary(source, expected):
    for split in range(len(source) + 1):
        redactor = StreamRedactor()
        output = redactor.feed(source[:split]) + redactor.feed(source[split:]) + redactor.finish()
        assert output == expected, (source, split, output)


@pytest.mark.parametrize("source,expected", CASES)
def test_one_character_deltas(source, expected):
    redactor = StreamRedactor()
    pieces = [redactor.feed(c) for c in source]
    pieces.append(redactor.finish())
    assert "".join(pieces) == expected


def test_memory_bounded_for_malicious_unbroken_token():
    redactor = StreamRedactor()
    output = "".join(redactor.feed("a" * 1000) for _ in range(1000))
    output += redactor.feed("@example.com ") + redactor.finish()
    assert output == "[REDACTED] "
    assert redactor.peak_pending <= redactor.MAX_PENDING


def test_prose_is_emitted_before_end_of_stream():
    redactor = StreamRedactor()
    assert redactor.feed("Hello ") == "Hello "
    assert redactor.feed("alex@") == ""
    assert redactor.feed("example.com ") == "[REDACTED] "
