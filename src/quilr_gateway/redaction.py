"""Incremental lexical redaction with bounded state and no chunk-boundary leaks.

Words are held until a delimiter; numeric runs may span spaces/hyphens. Oversize
tokens are replaced once and suppressed until their boundary (fail closed).
"""

import re

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
SSN = re.compile(r"(?<![0-9])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9])")
CARD = re.compile(r"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])")
TOKEN_CHARS = frozenset("._%+-@")
REPLACEMENT = "[REDACTED]"


def redact(text: str) -> str:
    for pattern in (EMAIL, SSN, CARD):
        text = pattern.sub(REPLACEMENT, text)
    return text


class StreamRedactor:
    MAX_PENDING = 320

    def __init__(self) -> None:
        self.pending = ""
        self.suppress = False
        self.numeric = False
        self.peak_pending = 0

    def feed(self, chunk: str) -> str:
        output = []
        for char in chunk:
            token_char = char.isalnum() or char in TOKEN_CHARS
            numeric_space = char == " " and self.numeric
            # A letter following a numeric run separated by spaces is a new token.
            if self.numeric and self.pending.endswith(" ") and char not in "0123456789 -":
                output.append(self._flush())
                numeric_space = False
            if token_char or numeric_space:
                if self.suppress:
                    continue
                self.pending += char
                self.numeric = bool(self.pending.strip()) and all(
                    c in "0123456789 -" for c in self.pending
                )
                self.peak_pending = max(self.peak_pending, len(self.pending))
                if len(self.pending) >= self.MAX_PENDING:
                    output.append(REPLACEMENT)
                    self.pending = ""
                    self.suppress = True
            else:
                output.append(self._flush())
                output.append(char)
        return "".join(output)

    def _flush(self) -> str:
        result = "" if self.suppress else redact(self.pending)
        self.pending = ""
        self.suppress = False
        self.numeric = False
        return result

    def finish(self) -> str:
        return self._flush()
