# Deploy the EpiDebug website on Vercel

The researcher UI is FastAPI (`web.app:app`) serving `web/static/`. Vercel
runs that ASGI app as one Python function. This path does **not** need
Hugging Face Spaces or Docker.

## Entrypoint

| Item | Value |
| --- | --- |
| Module | `web.app:app` |
| Config | `[tool.vercel] entrypoint = "web.app:app"` in `pyproject.toml` |
| Function key | `web/app.py` in `vercel.json` |
| Install | `[project].dependencies` and root `requirements.txt` (FastAPI, Uvicorn, python-multipart, Pillow, pypdf) |

## Deploy

### CLI

```bash
npm i -g vercel   # or: npx vercel
vercel login
vercel            # preview deployment
vercel --prod     # production
```

Framework preset should be **FastAPI**. Leave the root directory as the
repository root. Vercel installs from `pyproject.toml` / `requirements.txt`.

### Git

1. Import this repository in the Vercel dashboard (GitHub connect).
2. Confirm FastAPI is detected; root directory = repo root.
3. Deploy. Optional environment variables (Project → Settings → Environment Variables):

   | Variable | Purpose |
   | --- | --- |
   | `OPENAI_API_KEY` | LLM diagnosis + chat (heuristic still works without it) |
   | `ANTHROPIC_API_KEY` | Optional Anthropic if OpenAI is unset |
   | `EPIDEBUG_MODEL` | Default `gpt-4o-mini` |
   | `EPIDEBUG_LLM_PROVIDER` | Optional `openai` or `anthropic` |

   Do not put keys in the repo. After setting vars, redeploy so the function sees them.

## Expected URL behavior

Same routes as local `uvicorn web.app:app --host 127.0.0.1 --port 8000`:

| Path | Behavior |
| --- | --- |
| `/` | Researcher UI (`web/static/index.html`) |
| `/assets/*` | CSS/JS from `web/static/` (FastAPI, not a separate frontend) |
| `/api/health` | Engine status (`platform` is `vercel` on Vercel) |
| `/api/diagnose-bundle` | Multipart dump + diagnosis (optional `session_id` keeps the chat thread) |
| `/api/chat`, `/api/sessions/.../chat` | Conversational diagnostic loop |
| `/api/cases`, `/api/sessions/...` | Catalog and HITL |
| `/mock_data/*` | Fixture files |

Static assets stay on the function (`[tool.vercel.fastapi.static] cdn = false`)
so FastAPI owns every route.

## Local uvicorn (unchanged)

```bash
python -m pip install -e .
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
# or
python scripts/serve.py
```

Uploads still land in `./uploads` unless `EPIDEBUG_UPLOAD_DIR` is set.

## Known limits

- **Writable filesystem:** Vercel Functions can write only under `/tmp`.
  When `VERCEL=1`, sessions and uploads go to `/tmp/epidebug-uploads`.
  Override with `EPIDEBUG_UPLOAD_DIR`.
- **Ephemeral storage:** `/tmp` does not survive cold starts or other
  instances. Session JSON, chat transcripts, and uploaded files can
  disappear between requests. Durable storage (and any model training)
  is out of scope for this deploy.
- **Cold start:** the first request after idle imports Python + FastAPI and
  can take several seconds.
- **Hobby idle:** unused Hobby deployments scale to zero; the next visit
  pays a cold start (sometimes described as “sleep”).
- **Upload / body size:** the app accepts 25 MB per file locally. Vercel
  Functions cap the **request body at 4.5 MB**. Larger dumps return
  `413 FUNCTION_PAYLOAD_TOO_LARGE`.
- **Duration:** `vercel.json` sets `maxDuration` to 60 seconds for diagnosis
  uploads. Hobby Fluid compute allows up to 300 seconds if you raise this.
- **Bundle:** `excludeFiles` drops tests, paper, results, docs, and scripts
  from the function. `test_cases/`, `mock_data/`, `epidebug/`, and
  `web/static/` stay in the bundle.
