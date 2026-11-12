"""Regex-based redaction of obvious secrets and personal data.

Flow text is passed through redact() before it is embedded and again before it
is sent to the chat model. It catches common patterns: bearer tokens, JWTs, API
keys, SAS signatures, connection-string secrets, private keys, password-style
fields, and email addresses.

This is a safety net, not a guarantee. It cannot recognize business data, so
review what you index. GUIDs are deliberately left alone, because flow and run
IDs are needed for analysis.
"""
import re

_RULES = [
    # PEM private keys
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED_PRIVATE_KEY]"),
    # JSON Web Tokens
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
     "[REDACTED_JWT]"),
    # "Bearer <token>" in headers or logs
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{16,}=*"),
     "Bearer [REDACTED]"),
    # Connection-string secrets, e.g. AccountKey=..., Password=..., SharedAccessSignature=...
    (re.compile(r"(?i)\b(AccountKey|Password|Pwd|SharedAccessSignature|SharedAccessKey)(\s*=\s*)[^;\"'\s]+"),
     r"\1\2[REDACTED]"),
    # SAS signature in URLs
    (re.compile(r"(?i)([?&]sig=)[^&\s\"']+"),
     r"\1[REDACTED]"),
    # Password-style key/value pairs, in JSON or plain text. The key is kept, the value is replaced.
    #  - ":" only separates a key from its value when whitespace or a quote follows it. Otherwise
    #    "token:clientSecret" (a connection parameter name) would be read as key "token", value "clientSecret".
    #  - Values that start with "{", "[" or "@" are skipped: they are structure or expressions such as
    #    @parameters('x'), not literal secrets. This also skips values that are already "[REDACTED...]".
    (re.compile(
        r"(?i)([\"']?\b(?:password|passwd|client[_-]?secret|secret|api[_-]?key|apikey|"
        r"access[_-]?key|access[_-]?token|refresh[_-]?token|token)\b[\"']?"
        r"(?:\s*=\s*|:(?=[\s\"'])\s*)[\"']?)"
        r"(?![\[{@])([^\"'\s,;&}\]]+)"),
     r"\1[REDACTED]"),
    # OpenAI-style API keys
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
     "[REDACTED_API_KEY]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
     "[REDACTED_EMAIL]"),
]


def redact(text):
    """Return `text` with secret-like and personal-data patterns replaced."""
    if not text:
        return text
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
