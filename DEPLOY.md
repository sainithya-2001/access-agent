# Deploying the demo (Render)

**Live:** https://access-agent-y65u.onrender.com (Render, auto-deploys from `main`). The passcode is
set in the Render dashboard only, never in the repo.

The public demo is the staff review screen serving precomputed documents: pages 84–97, 109, 124 and
130 of OpenStax *Introductory Statistics 2e* (CC BY), 14 figures, 2 of them waiting for review
(Figure 2.9, series told apart by color only; Figure 2.25, no numeric scale). Figure 2.3 quotes its
values from the printed Table 2.9. With
`DEMO_MODE=1` the app makes **no model calls**: any call that misses the cache raises (`app/llm.py`
`BLOCKED`). Uploading a PDF or recropping a figure runs the agent, so both need the passcode in
`DEMO_PASSCODE`, and passcode uploads are capped at 5 pages (25 MB). Approve / edit / reject,
Preview, Export, Blindfold and Hear the chart work for everyone, and so does the student inbox
(Student inbox in the header): one precomputed course with the demo lecture's notes page.

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
4. **Instance type**: Free (see "Instance type: Free, kept awake" below).
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
`https://access-agent-y65u.onrender.com` URL:

1. `https://access-agent-y65u.onrender.com/api/config` shows `{"demo_mode":true,...}`. If it says
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
   lecture "Bar Graphs and Histograms" (2.5 min, synthetic narration). Open it: the video plays with captions, "Play from" buttons
   seek, and the notes show Figures 2.3 and 2.6 with their descriptions.
7. Render dashboard → **Logs**: no tracebacks; no `ModelCallsBlocked` errors (they would mean some
   page tried a model call and was stopped).
8. Try it on a phone too (the review screen and export work at 375 px).

## Instance type: Free, kept awake

The demo stays on **Free** (512 MB RAM, 0.1 CPU, $0). On its own, Free spins down after 15 minutes
without traffic and the next visit waits about a minute for a cold start, which can look broken to a
judge. To prevent that, an **UptimeRobot** monitor requests
`https://access-agent-y65u.onrender.com/api/config` **every 5 minutes**, so the service never goes
15 minutes without traffic and never sleeps.

- **Instance hours.** Free gives 750 instance hours a month per workspace; one service awake all
  month uses at most 744 (31 days), so it fits only if no other Free service in the workspace is
  running. Check the usage in the Render dashboard.
- **Memory is not the problem.** Measured locally: ~170 MB idle, ~180 MB after serving every page,
  crop and export, 320–370 MB peak while a 5-page passcode upload is processed (4 figure workers;
  two measurements, pages 85–89 and 107–111). That fits 512 MB for viewing; several passcode
  uploads at once could not, but uploads run one at a time (`api._lock`). The 0.1 CPU makes page
  rendering in uploads slow, not the review screen.
- A persistent disk is not needed: the demo data ships in the image.

## Clean state for every judge

The demo's data lives in the image and the disk is ephemeral, so each restart or redeploy restores
the bundled state: the same 14 figures, 2 waiting for review, no staff edits, no uploads, and the
one precomputed lecture course. Judges' approvals, edits, rejections and passcode uploads are never
kept.

Because the monitor keeps the service awake, it no longer resets itself: **all visitors share one
state until the next restart**, so judge B would see what judge A approved. Reset it with
**Manual Deploy → Restart service** in the Render dashboard (about a minute, data back to the bundle):

- **before judging starts**, and
- **once a day during judging** (and before anyone you know is about to look).

A push to `main` also redeploys and so resets the state.
