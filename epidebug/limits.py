"""Single source of truth for input-size caps.

Request-side caps (enforced with HTTP 413 in web/app.py) and prompt-side caps
(applied when evidence is delimited into LLM prompts, epidebug/engine/
evidence_prompt.py) live here so the two layers cannot drift apart. Prompt
caps are always <= the matching request caps: anything accepted at the edge
is truncated, never silently dropped, on the way into a prompt.
"""

from __future__ import annotations

# --- request (HTTP) caps -------------------------------------------------
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_REQUEST = 12
MAX_MESSAGE_CHARS = 4000          # chat turn
MAX_INFO_CHARS = 4000             # add-info, follow-up intervention/outcome
MAX_TITLE_CHARS = 200
MAX_DOMAIN_CHARS = 80
MAX_REASON_CHARS = 500            # hypothesis rejection reason
MAX_CAPTION_CHARS = 500
MAX_BUNDLE_FIELD_CHARS = 20000    # each free-text field of a bundle
MAX_SESSION_MESSAGES = 60
MAX_PDF_PAGES = 200

# --- prompt caps (evidence delimiting) ----------------------------------
PROMPT_TITLE_CHARS = MAX_TITLE_CHARS
PROMPT_DOMAIN_CHARS = MAX_DOMAIN_CHARS
PROMPT_FIELD_CHARS = 2000
PROMPT_LIST_ITEM_CHARS = 1000
PROMPT_LIST_TOTAL_CHARS = 3000
PROMPT_NOTE_CHARS = 1500
PROMPT_FOLLOWUP_CHARS = 1000
PROMPT_FILENAME_CHARS = 120
PROMPT_FILE_CHARS = 1500
PROMPT_CHAT_TURN_CHARS = MAX_MESSAGE_CHARS
PROMPT_EVIDENCE_TOTAL_CHARS = 8000
PROMPT_CHAT_DUMP_CHARS = 6000

assert PROMPT_CHAT_TURN_CHARS <= MAX_MESSAGE_CHARS
assert PROMPT_FIELD_CHARS <= MAX_BUNDLE_FIELD_CHARS
