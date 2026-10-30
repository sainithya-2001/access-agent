# Deploying the demo (Render)

The public demo is the staff review screen serving precomputed documents: pages 84–97, 109, 124 and
130 of OpenStax *Introductory Statistics 2e* (CC BY), 15 figures, 2 of them waiting for review. With
`DEMO_MODE=1` the app makes **no model calls**: any call that misses the cache raises (`app/llm.py`
`BLOCKED`). Uploading a PDF or recropping a figure runs the agent, so both need the passcode in
`DEMO_PASSCODE`, and passcode uploads are capped at 5 pages (25 MB). Approve / edit / reject,
Preview, Export, Blindfold and Hear the chart work for everyone.

What the image contains (`Dockerfile`, `.dockerignore`): `app/`, `web/`, and `demo/` copied as
the working directory (`output/review.db`, page images, crops, overlays, and the source PDF trimmed to
those pages). No `.env`, `cache/` or `output/` from your machine.

## 0. Before deploying

```bash
python scripts/test_demo_mode.py      # serves a copy of demo/ like the image does; must end "All checks passed"
```

To change the demo documents: build a review database with no staff edits
(`python scripts/reset_review_db.py RESULTS --pages ... --db output/demo.db`), then
`python scripts/make_demo_bundle.py --db output/demo.db`, re-run the test, commit `demo/`.

Optional, if Docker is installed: `docker build -t access-agent . && docker run -p 8000:8000 -e DEMO_PASSCODE=test access-agent`, then open http://localhost:8000.

## 1. Create the service

1. Push `main` to GitHub; the repo must be public (hackathon rule), which also lets Render read it.
2. In the Render dashboard: **New → Web Service** → connect the GitHub repo → branch `main`.
3. **Language / Runtime: Docker** (Render finds the `Dockerfile` in the repo root). Leave the build
   and start commands empty: the Dockerfile's `CMD` starts uvicorn on Render's `$PORT`.
4. **Instance type**: see "Free or paid" below.
5. **Health Check Path**: `/api/config`.
6. Add the environment variables (next section), then **Create Web Service**. The first build takes
   a few minutes; later pushes to `main` redeploy automatically.

## 2. Environment variables

| Variable | Value | Needed |
|---|---|---|
| `DEMO_MODE` | `1` | Already set in the Dockerfile; set it in Render too so it's visible. **Never deploy with it off**: anyone could spend the credits. |
| `DEMO_PASSCODE` | a long random string, e.g. from `python -c "import secrets; print(secrets.token_urlsafe(16))"` | Only to allow uploads and recrops. Leave it unset and uploads are off entirely (the Upload button is hidden). Give it to judges only if you want them to try a new PDF. |
| `MODEL_API_KEY` | Meta Model API key | Only with `DEMO_PASSCODE` (Muse Spark, and SAM 3.1 for the page check). |
| `LLM_BACKEND` | `meta` or `baseten` | Optional, default `meta`. |
| `BASETEN_API_KEY` | Baseten key | Only with `LLM_BACKEND=baseten`. |
| `SPARK_MODEL`, `BASETEN_MODEL`, `SAM_MODEL`, `MODEL_API_BASE`, `BASETEN_API_BASE` | | Optional; the code defaults match `.env`. |

Set secrets in Render's **Environment** tab, never in the repo. With no API keys at all, the demo
works fully except uploads.

## 3. Check the live URL in a logged-out browser

Use a private / incognito window (no Render or GitHub session, no cached files), at the
`https://<service>.onrender.com` URL:

1. `https://<service>.onrender.com/api/config` shows `{"demo_mode":true,...}`. If it says
   `false`, stop the service and fix `DEMO_MODE`.
2. The review screen loads with the document "Pages 84-97,109,124,130 · introductory-statistics-2e…"
   and 2 figures in the queue ("Needs review only" is on). Turn it off: 15 figures.
3. Click a few figures: the crop, the page image with the detection overlay, the trace and the
   issues all show. Open DevTools → Network: no red (4xx/5xx) requests.
4. **Preview** opens the accessible HTML in a new tab; **Export HTML** downloads it; try a reading
   profile there and **Hear the chart** on a bar chart (e.g. Figure 2.3).
5. **Upload PDF**: the dialog says it's a demo and asks for the passcode. Submit a PDF with a wrong
   passcode: refused with a message. (With the right one and ≤ 5 pages it processes; that spends
   credits.)
6. Render dashboard → **Logs**: no tracebacks; no `ModelCallsBlocked` errors (they would mean some
   page tried a model call and was stopped).
7. Try it on a phone too (the review screen and export work at 375 px).

## Free or paid

**Free** (512 MB RAM, 0.1 CPU, $0) works, but has two problems for judges:

- **It sleeps.** After 15 minutes without traffic the service spins down, and the next visit waits
  about a minute while it starts again. A judge who opens the link cold sees a loading screen for a
  minute, which can look broken. (750 free instance hours a month, enough to stay up all month if
  something keeps it awake.)
- **Its disk is ephemeral.** Staff edits made on the demo are lost on every spin-down, restart or
  redeploy, and the demo returns to the bundled state. For a demo that is mostly fine (every judge
  starts clean), but one judge's approvals vanish if they come back an hour later.
- **Memory is not the problem.** Measured locally: ~170 MB idle, ~180 MB after serving every page,
  crop and export, 320–370 MB peak while a 5-page passcode upload is processed (4 figure workers;
  two measurements, pages 85–89 and 107–111). That
  fits 512 MB for viewing; several passcode uploads at once could not, but uploads run one at a
  time (`api._lock`). The 0.1 CPU makes page rendering in uploads slow, not the review screen.

**Recommended for the judging window: Starter** (legacy name `starter`, plan `0.5c-512mb`: 0.5
CPU, 512 MB, about **$7/month**). It never sleeps; the disk is still ephemeral. Switch back to Free
(or suspend the service) after judging. The $7 comes from third-party price lists (Render's docs
give only the specs), so confirm it on https://render.com/pricing before choosing. The next tier,
Standard (1 CPU, 2 GB, about $25/month), is only worth it for many passcode uploads. A persistent
disk (paid plans only) is not needed: the demo data ships in the image.
