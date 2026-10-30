# Deploying the demo (Render)

The public demo is the staff review screen serving precomputed documents: pages 84–97, 109, 124 and
130 of OpenStax *Introductory Statistics 2e* (CC BY), 14 figures, 2 of them waiting for review
(Figure 2.9, series told apart by color only; Figure 2.25, no numeric scale). Figure 2.3 quotes its
values from the printed Table 2.9. With
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
   and 2 figures in the queue ("Needs review only" is on). Turn it off: 14 figures.
3. Click a few figures: the crop, the page image with the detection overlay, the trace and the
   issues all show. Open DevTools → Network: no red (4xx/5xx) requests.
4. **Preview** opens the accessible HTML in a new tab; **Export HTML** downloads it; try a reading
   profile there and **Hear the chart** on a bar chart (e.g. Figure 2.3).
5. **Upload PDF**: the dialog says it's a demo and asks for the passcode. Submit a PDF with a wrong
   passcode: refused with a message. (With the right one and ≤ 5 pages it processes; that spends
   credits.)
6. **Student inbox** in the header opens the course portal: "Introductory Statistics" with the
   lecture "Two Ways to Display Data". Open it: the video plays with captions, "Play from" buttons
   seek, and the notes show Figures 2.3 and 2.6 with their descriptions.
7. Render dashboard → **Logs**: no tracebacks; no `ModelCallsBlocked` errors (they would mean some
   page tried a model call and was stopped).
8. Try it on a phone too (the review screen and export work at 375 px).

## Free or paid

**Free** (512 MB RAM, 0.1 CPU, $0) works, but has two problems for judges:

- **It sleeps.** After 15 minutes without traffic the service spins down, and the next visit waits
  about a minute while it starts again. A judge who opens the link cold sees a loading screen for a
  minute, which can look broken. (750 free instance hours a month, enough to stay up all month if
  something keeps it awake.)
- **Its disk is ephemeral.** Every spin-down, restart or redeploy throws away what visitors
  changed and the demo returns to the bundled state. That is mostly good (see "Clean state for
  every judge" below), but on Free it also happens mid-visit: a judge who comes back after 15 idle
  minutes finds their approvals gone.
- **Memory is not the problem.** Measured locally: ~170 MB idle, ~180 MB after serving every page,
  crop and export, 320–370 MB peak while a 5-page passcode upload is processed (4 figure workers;
  two measurements, pages 85–89 and 107–111). That
  fits 512 MB for viewing; several passcode uploads at once could not, but uploads run one at a
  time (`api._lock`). The 0.1 CPU makes page rendering in uploads slow, not the review screen.

**Recommended: Starter from Nov 3 to Nov 16** (submission on Nov 4, then the judging period; legacy
name `starter`, plan `0.5c-512mb`: 0.5 CPU, 512 MB, about **$7/month**, so roughly half that for two
weeks if billed pro rata). It never sleeps, so judges never wait for a cold start. Switch the
instance type to Starter on Nov 3 and back to Free (or suspend the service) on Nov 16. The $7 comes from third-party price lists (Render's docs
give only the specs), so confirm it on https://render.com/pricing before choosing. The next tier,
Standard (1 CPU, 2 GB, about $25/month), is only worth it for many passcode uploads. A persistent
disk (paid plans only) is not needed: the demo data ships in the image.

## Clean state for every judge

The demo's data lives in the image, and the disk is ephemeral on every Render plan, so each restart
or redeploy restores the bundled state: the same 14 figures, 2 waiting for review, no staff edits,
no uploads. Judges' approvals, edits, rejections and passcode uploads are never kept.

- On **Free** this happens on its own after every 15 idle minutes, so in practice each judge starts
  clean.
- On **Starter** the service never sleeps, so visitors share one state until the next restart:
  judge B would see what judge A approved. To reset it, use **Manual Deploy → Restart service** in
  the Render dashboard (about a minute, data back to the bundle). Do it once a day during judging,
  and before anyone you know is about to look.
