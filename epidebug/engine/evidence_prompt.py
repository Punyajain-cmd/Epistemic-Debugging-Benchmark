"""Delimited, length-capped evidence blocks for LLM prompts (security H4).

Every piece of user-controlled text that reaches an LLM prompt -- free-text
fields (objective, outcome, setup, notes, telemetry, context, follow-ups,
chat turns), file names, captions, and file extracts (including CSV headers
and EXIF) -- goes through :func:`field_block` or :func:`file_block`. Each block:

* is wrapped in an explicit ``<field name=... trust="untrusted">`` /
  ``<file name=... trust="untrusted">`` delimiter,
* has forged role markers and any of our own delimiter tags removed, so the
  content cannot close its block or open a new one,
* has control characters removed, and
* is truncated to a per-field cap, with a visible truncation marker.

The heuristic engine still sees the plain blob; only LLM prompts use this.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from epidebug.llm import _strip_roles

# Per-field caps (characters).
CAP_TITLE = 200
CAP_DOMAIN = 80
CAP_FIELD = 2000
CAP_LIST_ITEM = 1000
CAP_LIST_TOTAL = 3000
CAP_NOTE = 1500
CAP_FOLLOWUP = 1000
CAP_FILENAME = 120
CAP_FILE = 1500
CAP_CHAT_TURN = 4000
CAP_EVIDENCE_TOTAL = 8000

TRUNC = " …[truncated]"

# Our own delimiters (and look-alikes) that untrusted text must never contain.
_OWN_TAGS = re.compile(
    r"</?\s*(evidence|field|file|user_turn|engine_note|system|untrusted|instructions?)\b[^>]{0,200}>",
    re.I,
)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
_NAME_SAFE = re.compile(r"[^A-Za-z0-9._ ()+-]")


def sanitize(text: object, cap: int) -> str:
    """Strip role markers, our delimiters, and control chars; cap length."""
    s = "" if text is None else str(text)
    s = _CTRL.sub("", s)
    # Repeat until stable so nested forgeries like "<evi<evidence>dence>" die.
    for _ in range(3):
        new = _OWN_TAGS.sub("", _strip_roles(s))
        if new == s:
            break
        s = new
    s = s.strip()
    if len(s) > cap:
        s = s[: max(0, cap - len(TRUNC))].rstrip() + TRUNC
    return s


def safe_attr(value: object, cap: int = CAP_FILENAME) -> str:
    """Attribute-safe label (file names, field names): no quotes or brackets."""
    s = _NAME_SAFE.sub("_", _CTRL.sub("", "" if value is None else str(value)))
    return s[:cap] or "untitled"


def field_block(name: str, text: object, cap: int = CAP_FIELD) -> str:
    body = sanitize(text, cap)
    if not body:
        return ""
    return f'<field name="{safe_attr(name, 40)}" trust="untrusted">\n{body}\n</field>'


def list_block(name: str, items: Sequence[object], item_cap: int = CAP_LIST_ITEM,
               total_cap: int = CAP_LIST_TOTAL) -> str:
    cleaned = [sanitize(i, item_cap) for i in items or []]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return ""
    body = "\n".join(f"- {c}" for c in cleaned)
    return field_block(name, body, total_cap)


def file_block(artifact, cap: int = CAP_FILE) -> str:
    from epidebug.engine.preprocess import prompt_from_artifact

    kind = getattr(getattr(artifact, "kind", None), "value", None) or str(getattr(artifact, "kind", "other"))
    name = safe_attr(getattr(artifact, "filename", ""))
    parts = []
    caption = getattr(artifact, "caption", "") or ""
    if caption:
        parts.append("caption: " + sanitize(caption, 300))
    parts.append(sanitize(prompt_from_artifact(artifact), cap))
    body = "\n".join(p for p in parts if p)
    return (
        f'<file name="{name}" kind="{safe_attr(kind, 20)}" trust="untrusted">\n{body}\n</file>'
    )


def _join_capped(blocks: Iterable[str], total_cap: int) -> str:
    out: list[str] = []
    used = 0
    for block in blocks:
        if not block:
            continue
        if used + len(block) + 1 > total_cap:
            out.append('<field name="omitted" trust="system">further evidence omitted (prompt budget)</field>')
            break
        out.append(block)
        used += len(block) + 1
    return "\n".join(out)


def wrap_evidence(inner: str) -> str:
    return "<evidence>\n" + (inner or "(empty)") + "\n</evidence>"


def evidence_from_experiment(exp, extra_information: Sequence[str] = (), followups: Sequence = (),
                             total_cap: int = CAP_EVIDENCE_TOTAL) -> str:
    """Delimited evidence for an ExperimentInput, one block per field/file."""
    blocks = [
        field_block("objective", exp.objective),
        field_block("unexpected_outcome", exp.unexpected_outcome),
        field_block("setup", exp.setup_description),
        list_block("materials", exp.materials),
        list_block("processing", exp.processing),
        list_block("protocol", exp.protocol),
        list_block("telemetry", exp.telemetry_notes),
        list_block("context", exp.contextual_clues),
        list_block("notes", list(exp.extra_information) + list(extra_information or []), CAP_NOTE),
    ]
    for i, fu in enumerate(followups or []):
        blocks.append(field_block(f"followup_{i + 1}_intervention", getattr(fu, "intervention", ""), CAP_FOLLOWUP))
        blocks.append(field_block(f"followup_{i + 1}_outcome", getattr(fu, "outcome", ""), CAP_FOLLOWUP))
    file_blocks = [file_block(a) for a in exp.artifacts or []]
    return wrap_evidence(_join_capped(blocks + file_blocks, total_cap))


def evidence_from_blob(name: str, blob: str, total_cap: int = CAP_EVIDENCE_TOTAL) -> str:
    """Fallback for catalog cases: whole blob as one delimited field."""
    return wrap_evidence(field_block(name, blob, total_cap))
