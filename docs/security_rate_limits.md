# Rate limits and LLM spend controls (H1)

## In-app limits (this PR)

| Control | Default | Override |
|---|---|---|
| LLM-backed requests per client IP per minute | 12 | `EPIDEBUG_RATE_PER_MIN` |
| LLM-backed requests per client IP per hour | 120 | `EPIDEBUG_RATE_PER_HOUR` |
| Chat message / title | 4000 / 200 chars | `web/security.py` |
| add-info, follow-up fields | 4000 chars | `web/security.py` |
| Bundle text fields | 20000 chars each | `web/app.py` |
| Turns per session | 60 | `web/security.py` |
| LLM `max_tokens` per completion | 1200 | `epidebug/llm.py` |

Rate-limited endpoints: `/api/diagnose`, `/api/diagnose-bundle`, `/api/chat`,
`/api/sessions/{id}/chat|reject|add-info|artifacts|followup`, `/api/tools/{name}`.
Exceeding a limit returns HTTP 429.

The limiter is in memory and per warm serverless instance. It is a
cost-damping layer, not a hard global quota. Client IP is read from the first
`X-Forwarded-For` hop (set by Vercel's edge).

## OpenAI budget cap (manual step, owner only)

The app cannot enforce a provider-side spend ceiling. The account owner must
set it in the OpenAI dashboard:

1. platform.openai.com → Settings → Organization → **Limits**.
2. Set a monthly **budget** (hard cap) and an email alert threshold
   (for example alert at 50%, cap at 100%).
3. Prefer a dedicated **project** with its own API key for EpiDebug and set the
   project budget there, so a leaked key cannot spend beyond it.
4. Rotate the key on Vercel (`OPENAI_API_KEY`) after creating the project key.

Do the same for Anthropic (Console → Plans & Billing → spend limits) if
`EPIDEBUG_LLM_PROVIDER=anthropic` is used.
