# Story Engine — Client Mode

The engine now supports per-client context. Each client gets their own voice, ICP, credibility vault, and beliefs injected into every generation. VAs log in with a password and the engine auto-loads everything.

---

## How it works

```
clients/
├── basit/
│   ├── config.json         ← niche, default length, trigger words
│   └── engine-context.md   ← ICP + voice + receipts + beliefs (~1700 tokens)
├── asli/
│   ├── config.json
│   └── engine-context.md
└── deborah/
    ├── config.json
    └── engine-context.md
```

On every API call, the matching `engine-context.md` is injected as a cached system message. Prompt caching means we only pay full token cost once every 5 minutes per client — repeat generations are basically free.

---

## Run locally

```bash
cd "Projects/story-engine"
export ANTHROPIC_API_KEY=sk-ant-...
export BASIT_PASSWORD=pick-a-password
export ASLI_PASSWORD=pick-a-password
export DEBORAH_PASSWORD=pick-a-password
python3 server.py
```

Open http://localhost:3000. You'll see the login screen, pick a client, enter the matching password.

---

## Deploy to Render

The existing `render.yaml` already works. You just need to add the 3 client passwords as env vars in the Render dashboard:

1. Go to your Render service → Environment
2. Add:
   - `BASIT_PASSWORD` → whatever you want to give Basit's VA
   - `ASLI_PASSWORD` → for Aslı's team
   - `DEBORAH_PASSWORD` → for Deborah / her VA
3. `ANTHROPIC_API_KEY` is already set
4. Push the branch → Render auto-deploys

Then send each VA: the URL + the password for their client. They pick the client tile, type the password, they're in. Session lives for the tab — closing the tab logs them out.

---

## Adding a 4th client later

1. Create folder: `clients/<id>/` (lowercase, no spaces — e.g. `clients/sarah/`)
2. Add `config.json`:
   ```json
   {
     "id": "sarah",
     "name": "Sarah Smith",
     "niche": "Fitness coach for postpartum mums",
     "default_length": 8,
     "default_language": "English",
     "trigger_words": ["READY"],
     "password_env": "SARAH_PASSWORD"
   }
   ```
3. Add `engine-context.md` — same shape as the existing ones (ICP + voice + receipts + beliefs + anti-patterns)
4. Set `SARAH_PASSWORD` env var on Render
5. Push → live

---

## Two things to know

1. **Engine contexts are committed to the repo.** That's fine — they're not secrets, they're guidelines. But if you ever put credentials in them, that'll be in git history. Don't.
2. **Sonnet 4 hits end-of-life on June 15, 2026.** The model `claude-sonnet-4-20250514` is in `server.py` 5 times. Before mid-June, bulk-replace it with `claude-sonnet-4-6` or `claude-sonnet-4-7`. Newer model = better output, slightly more expensive. Test one generation per client before flipping for real.

---

## What it doesn't do (yet)

- **No admin UI to edit contexts** — you (Diro) edit the markdown files in this repo and re-deploy. Fine for now since you write them anyway.
- **No per-VA login** — one password per client, shared with whoever they hand it to. If you need to rotate, change the Render env var, redeploy, hand out the new password.
- **No usage analytics** — no idea who generated what or how often. Add later if Basit asks.

---

## Quick check it's working

After login, open browser devtools → Network tab → run a generation. Look at the `/api/generate` request:
- Request headers should include `X-Client-Id: basit` and `X-Client-Password: <password>`
- The output should sound like the client (real names from their credibility vault, real numbers, signature phrases)
- If output reads generic, the context isn't loading — check Render logs for `Failed to load client …` errors
