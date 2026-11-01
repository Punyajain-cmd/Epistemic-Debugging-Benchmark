"""Canonical handling of untrusted text before it reaches an LLM prompt (H4).

Pipeline (order matters):

1. NFKC normalisation: fullwidth / compatibility forms (``＜``, ``［``,
   ``ｓｙｓｔｅｍ``) collapse to ASCII.
2. Remove every Unicode category ``Cf`` (format) char: zero-width space/joiners
   (U+200B-200F), word joiner (U+2060), BOM (U+FEFF), bidi overrides/isolates,
   soft hyphen, and other control characters (except newline/tab).
3. Fold confusables used to forge role words: Cyrillic/Greek homoglyphs to
   Latin, unicode hyphens/dashes to ``-``.
4. Strip role markers on the folded text (``[system]``, ``[ SYSTEM_TOOL ]``,
   ``(system-tool)``, ``### SYSTEM:``, ``assistant:`` at line start,
   ``<|im_start|>`` ...), repeated until stable.
5. Escape every ``<`` and ``>`` as ``‹`` / ``›`` so untrusted text can never
   open or close a delimiter, whatever its spelling (``< /field>``, unclosed
   ``<field trust="system"``, etc.).

This pipeline is for PROMPTS only. Stored / displayed text goes through
:func:`clean_stored`, which only drops ``Cf`` chars and otherwise keeps the
user's text verbatim.

There is no tag blacklist: step 5 makes delimiter forgery impossible, and the
per-request random block id (see ``evidence_prompt``) makes guessing a real
closing tag useless even if a future change forgot step 5.
"""

from __future__ import annotations

import re
import unicodedata

# Cyrillic / Greek letters that render like Latin ones (lower + upper).
_HOMOGLYPHS = str.maketrans({
    "а": "a", "А": "A", "е": "e", "Е": "E", "о": "o", "О": "O", "р": "p", "Р": "P",
    "с": "c", "С": "C", "у": "y", "У": "Y", "х": "x", "Х": "X", "ѕ": "s", "Ѕ": "S",
    "і": "i", "І": "I", "ј": "j", "Ј": "J", "ԁ": "d", "һ": "h", "Һ": "H", "ӏ": "l",
    "Т": "T", "т": "t", "М": "M", "м": "m", "Н": "H", "н": "h", "В": "B", "в": "b",
    "К": "K", "к": "k", "ԛ": "q", "ԝ": "w", "Ԝ": "W", "ɡ": "g", "ɩ": "i",
    "α": "a", "Α": "A", "ε": "e", "Ε": "E", "ο": "o", "Ο": "O", "ρ": "p", "Ρ": "P",
    "τ": "t", "Τ": "T", "υ": "u", "ι": "i", "Ι": "I", "κ": "k", "Κ": "K", "ν": "v",
    "Ν": "N", "Μ": "M", "Η": "H", "Β": "B", "Χ": "X", "χ": "x", "Ζ": "Z", "Υ": "Y",
})
# Unicode hyphens, dashes, minus signs -> ASCII hyphen.
_HYPHENS = re.compile(r"[\u2010-\u2015\u2043\u2212\u2e3a\u2e3b\ufe58\ufe63\uff0d\u058a\u05be\u1400\u1806\u30a0]")
_KEEP_CC = {"\n", "\t"}

_ROLE_WORD = r"(?:system[\s_\-.]*tool|system|assistant|developer|tool|function|im_start|im_end)"
# Bare "tool:" / "### Tools" are common in lab notes, so line-start forms only
# match the unambiguous role words.
_LINE_ROLE = r"(?:system[\s_\-.]*tool|system|assistant|developer)"
ROLE_RE = re.compile(
    r"[\[\(\{<]\s*/?\s*" + _ROLE_WORD + r"\s*[\]\)\}>]"     # [system] (system-tool) {tool} <system>
    r"|<\|[^|>]{0,40}\|>"                                   # <|im_start|>
    r"|^[ \t>*_-]*#{1,6}[ \t]*" + _LINE_ROLE + r"\b[ \t]*:?"  # ### SYSTEM:
    r"|^[ \t>*_-]*" + _LINE_ROLE + r"[ \t]*:",                 # system: / assistant: at line start
    re.I | re.M,
)


def normalize(text: object) -> str:
    s = unicodedata.normalize("NFKC", "" if text is None else str(text))
    s = "".join(
        ch for ch in s
        if ch in _KEEP_CC or unicodedata.category(ch) not in ("Cf", "Cc", "Cs", "Co")
    )
    s = s.translate(_HOMOGLYPHS)
    return _HYPHENS.sub("-", s)


def strip_roles(text: str) -> str:
    for _ in range(5):
        new = ROLE_RE.sub("", text)
        if new == text:
            break
        text = new
    return text


def escape_angles(text: str) -> str:
    return text.replace("<", "\u2039").replace(">", "\u203a")


def clean(text: object) -> str:
    """normalize -> strip role markers -> escape angle brackets."""
    return escape_angles(strip_roles(normalize(text)))


def clean_stored(text: object) -> str:
    """Request-time pass for text we STORE and SHOW back to the user.

    Stored evidence must stay byte-faithful to what the user typed: NFKC would
    turn ``10⁻³ A`` into ``10-3 A``, ``½`` into ``1⁄2``, ``µ`` (micro sign) into
    ``μ``, and role stripping would eat the ``SYSTEM:`` in ``SYSTEM: pressure
    2 bar``. So here we only drop invisible format characters (Unicode ``Cf``:
    zero-width, bidi overrides, BOM). NFKC, homoglyph folding, role stripping
    and ``<``/``>`` escaping happen in :func:`clean`, when prompts are built.
    """
    s = "" if text is None else str(text)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
