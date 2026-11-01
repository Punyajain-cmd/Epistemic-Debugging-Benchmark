# Rate limits and LLM spend controls (H1)

## In-app limits

| Control | Default | Override |
|---|---|---|
| LLM-backed requests per client IP per minute / hour | 12 / 120 | `EPIDEBUG_RATE_PER_MIN`, `EPIDEBUG_RATE_PER_HOUR` |
| New sessions per client IP per minute / hour | 3 / 20 | `EPIDEBUG_SESSION_CREATE_PER_MIN`, `EPIDEBUG_SESSION_CREATE_PER_HOUR` |
| Tool calls per client IP per minute / hour | 60 / 600 | `EPIDEBUG_TOOL_RATE_PER_MIN`, `EPIDEBUG_TOOL_RATE_PER_HOUR` |
| Global LLM-backed requests per UTC day (circuit breaker, HTTP 503) | 2000 | `EPIDEBUG_DAILY_LLM_CAP` (0 = off) |
| Tracked client keys in memory (LRU, oldest evicted) | 50000 | `EPIDEBUG_RATE_MAX_KEYS` |
| Field caps (chat 4000, info 4000, title 200, bundle field 20000, 60 turns, ...) | | `epidebug/limits.py` |
| LLM `max_tokens` per completion | 1200 | `epidebug/llm.py` |

All request caps and the prompt-delimiting caps live in `epidebug/limits.py`
(one module, so the HTTP layer and the prompt layer cannot drift).

LLM-backed endpoints (`/api/diagnose`, `/api/diagnose-bundle`, `/api/chat`,
`/api/sessions/{id}/chat|reject|add-info|artifacts|followup`) share the LLM
bucket and count against the daily budget. Session-creating calls
(`/api/diagnose`, `/api/diagnose-bundle` without `session_id`, `/api/chat`
without `session_id`) additionally hit the strict session-creation bucket.

**Tools do not share the LLM bucket.** `/api/tools/{name}` runs deterministic
calculators with no model call, so it has its own looser bucket and does not
consume the daily LLM budget. If a tool ever calls a model, move it to
`rate_limit()`.

### Client IP

On Vercel (`VERCEL` set) the key is `x-vercel-forwarded-for`, then
`x-real-ip`, both set by Vercel's edge. `X-Forwarded-For` is never trusted
(clients can prepend arbitrary hops). Off Vercel the socket peer is used; if
you run behind another proxy, terminate there and add a trusted-header rule.

### Shared backend (optional)

Set `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` (or Vercel KV's
`KV_REST_API_URL` + `KV_REST_API_TOKEN`) to use fixed-window counters in
Redis over REST, making the per-IP limits and the daily budget global across
serverless instances. Any KV error falls back to the in-memory limiter for
that request. `EPIDEBUG_RATE_BACKEND=memory` forces in-memory. Without KV the
limits are per warm instance (cost damping, not a hard global quota).

### Turnstile (optional, off by default)

Set `EPIDEBUG_TURNSTILE_SECRET` to require a Cloudflare Turnstile token in the
`cf-turnstile-response` header on session-creating calls. The frontend widget
is not wired yet; enable only after adding it.

### Cross-site requests

Every state-changing `/api/*` request must carry `X-EpiDebug-Client: web`
(the bundled frontend sends it; a cross-site form or simple fetch cannot
without a CORS preflight), and a browser `Origin`, when present, must match the
`Host` or `EPIDEBUG_ALLOWED_ORIGINS`. Otherwise HTTP 403.

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
