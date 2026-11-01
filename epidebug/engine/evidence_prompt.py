"""Delimited, length-capped evidence blocks for LLM prompts (security H4).

Every piece of user-controlled text that reaches an LLM prompt -- free-text
fields (objective, outcome, setup, notes, telemetry, context, follow-ups,
chat turns), file names, captions, and file extracts (including CSV headers
and EXIF) -- goes through :func:`field_block` or :func:`file_block`. Each block:

* is wrapped in ``<field id=ID name=... trust="untrusted">`` /
  ``<file id=ID ...>`` delimiters whose closing tags repeat a random
  per-request ``ID`` (``</field id=ID>``),
* goes through :func:`epidebug.untrusted.clean`: NFKC, all Unicode ``Cf``
  chars removed, homoglyph/hyphen folding, role markers stripped, and every
  ``<``/``>`` escaped as ``‹``/``›`` -- so no spelling of a tag survives, and
* is truncated to a per-field cap, with a visible truncation marker.

The heuristic engine still sees the plain blob; only LLM prompts use this.
"""

from __future__ import annotations

import re
import secrets
from typing import Iterable, Sequence

from epidebug import limits
from epidebug.untrusted import clean, normalize, strip_roles

# Per-field caps (characters); single source of truth in epidebug/limits.py.
CAP_TITLE = limits.PROMPT_TITLE_CHARS
CAP_DOMAIN = limits.PROMPT_DOMAIN_CHARS
CAP_FIELD = limits.PROMPT_FIELD_CHARS
CAP_LIST_ITEM = limits.PROMPT_LIST_ITEM_CHARS
CAP_LIST_TOTAL = limits.PROMPT_LIST_TOTAL_CHARS
CAP_NOTE = limits.PROMPT_NOTE_CHARS
CAP_FOLLOWUP = limits.PROMPT_FOLLOWUP_CHARS
CAP_FILENAME = limits.PROMPT_FILENAME_CHARS
CAP_FILE = limits.PROMPT_FILE_CHARS
CAP_CHAT_TURN = limits.PROMPT_CHAT_TURN_CHARS
CAP_EVIDENCE_TOTAL = limits.PROMPT_EVIDENCE_TOTAL_CHARS

TRUNC = " …[truncated]"
_NAME_SAFE = re.compile(r"[^A-Za-z0-9._ +-]")


def new_block_id() -> str:
    """Random per-request id carried by every delimiter tag of one prompt."""
    return secrets.token_hex(6)


def sanitize(text: object, cap: int) -> str:
    """NFKC, strip Cf chars, fold homoglyphs/hyphens, strip role markers,
    escape every < and > (as ‹ ›), then cap length."""
    s = clean(text).strip()
    if len(s) > cap:
        s = s[: max(0, cap - len(TRUNC))].rstrip() + TRUNC
    return s


def safe_attr(value: object, cap: int = CAP_FILENAME) -> str:
    """Attribute-safe label (file names, field names): no quotes, brackets, parens."""
    s = _NAME_SAFE.sub("_", strip_roles(normalize(value)))
    return s[:cap] or "untitled"


def _bid(block_id: str | None) -> str:
    return block_id or new_block_id()


def field_block(name: str, text: object, cap: int = CAP_FIELD, block_id: str | None = None) -> str:
    body = sanitize(text, cap)
    if not body:
        return ""
    bid = _bid(block_id)
    return f'<field id="{bid}" name="{safe_attr(name, 40)}" trust="untrusted">\n{body}\n</field id="{bid}">'


def list_block(name: str, items: Sequence[object], item_cap: int = CAP_LIST_ITEM,
               total_cap: int = CAP_LIST_TOTAL, block_id: str | None = None) -> str:
    cleaned = [sanitize(i, item_cap) for i in items or []]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return ""
    body = "\n".join(f"- {c}" for c in cleaned)
    # Already escaped; a second clean() is idempotent.
    return field_block(name, body, total_cap, block_id)


def file_block(artifact, cap: int = CAP_FILE, block_id: str | None = None) -> str:
    from epidebug.engine.preprocess import prompt_from_artifact

    kind = getattr(getattr(artifact, "kind", None), "value", None) or str(getattr(artifact, "kind", "other"))
    name = safe_attr(getattr(artifact, "filename", ""))
    parts = []
    caption = getattr(artifact, "caption", "") or ""
    if caption:
        parts.append("caption: " + sanitize(caption, 300))
    parts.append(sanitize(prompt_from_artifact(artifact), cap))
    body = "\n".join(p for p in parts if p)
    bid = _bid(block_id)
    return (
        f'<file id="{bid}" name="{name}" kind="{safe_attr(kind, 20)}" trust="untrusted">\n{body}\n</file id="{bid}">'
    )


def user_turn_block(text: object, block_id: str | None = None, cap: int = CAP_CHAT_TURN) -> str:
    bid = _bid(block_id)
    return f'<user_turn id="{bid}" trust="untrusted">\n{sanitize(text, cap)}\n</user_turn id="{bid}">'


def _join_capped(blocks: Iterable[str], total_cap: int, block_id: str) -> str:
    out: list[str] = []
    used = 0
    for block in blocks:
        if not block:
            continue
        if used + len(block) + 1 > total_cap:
            out.append(f'<field id="{block_id}" name="omitted" trust="system">further evidence omitted '
                       f'(prompt budget)</field id="{block_id}">')
            break
        out.append(block)
        used += len(block) + 1
    return "\n".join(out)


def wrap_evidence(inner: str, block_id: str | None = None) -> str:
    bid = _bid(block_id)
    return f'<evidence id="{bid}">\n' + (inner or "(empty)") + f'\n</evidence id="{bid}">'


def block_id_of(evidence: str) -> str | None:
    m = re.match(r'<evidence id="([0-9a-f]+)">', evidence or "")
    return m.group(1) if m else None


def evidence_from_experiment(exp, extra_information: Sequence[str] = (), followups: Sequence = (),
                             total_cap: int = CAP_EVIDENCE_TOTAL, block_id: str | None = None) -> str:
    """Delimited evidence for an ExperimentInput, one block per field/file.
    All tags share one random ``block_id`` for this request."""
    bid = _bid(block_id)
    fb = lambda *a: field_block(*a, block_id=bid)  # noqa: E731
    lb = lambda *a: list_block(*a, block_id=bid)  # noqa: E731
    blocks = [
        fb("objective", exp.objective),
        fb("unexpected_outcome", exp.unexpected_outcome),
        fb("setup", exp.setup_description),
        lb("materials", exp.materials),
        lb("processing", exp.processing),
        lb("protocol", exp.protocol),
        lb("telemetry", exp.telemetry_notes),
        lb("context", exp.contextual_clues),
        lb("notes", list(exp.extra_information) + list(extra_information or []), CAP_NOTE),
    ]
    for i, fu in enumerate(followups or []):
        blocks.append(fb(f"followup_{i + 1}_intervention", getattr(fu, "intervention", ""), CAP_FOLLOWUP))
        blocks.append(fb(f"followup_{i + 1}_outcome", getattr(fu, "outcome", ""), CAP_FOLLOWUP))
    file_blocks = [file_block(a, block_id=bid) for a in exp.artifacts or []]
    return wrap_evidence(_join_capped(blocks + file_blocks, total_cap, bid), bid)


def evidence_from_blob(name: str, blob: str, total_cap: int = CAP_EVIDENCE_TOTAL,
                       block_id: str | None = None) -> str:
    """Fallback for catalog cases: whole blob as one delimited field."""
    bid = _bid(block_id)
    return wrap_evidence(field_block(name, blob, total_cap, bid), bid)
