# Access Agent

An agent that turns course materials (scanned or digital PDFs, lecture video) into accessible formats for disability services offices. It **verifies its own image descriptions** and sends only low-confidence items to a human reviewer.

Built for the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026). All code in this repository was written during the hackathon.

## Problem

Students with print disabilities receive course materials in alternative formats through campus disability services. Converting plain text is easy; remediating visual and structural content (figures, charts, equations, scanned pages) is manual, slow, and needs subject knowledge. Existing AI alt-text tools generate descriptions, but staff must still review every output because they can't tell which ones are wrong.

## Approach

```
Upload → Ingest → Structure + Segment (SAM 3.1) → Classify → Describe (Muse Spark)
       → Verify + Retry (agent) → Confidence triage → Human review queue → Accessible HTML
```

| Meta model | Role |
|---|---|
| Muse Spark | Orchestrator, layout/OCR, descriptions, verification |
| SAM 3.1 | Figure / chart / equation segmentation |
| Muse Voice Transcribe | Lecture captions (stretch goal) |
| Muse Spark Open (Baseten) | Description step on open weights (stretch goal) |

## Related work

- **ALADDIN** (Georgia Tech, ACM ASSETS 2025): context-aware alt text for Word/PowerPoint. Review is all-or-nothing.
- **Imperial College AI Alt Text Generator**: single-image proof of concept.
- This project adds: scanned PDFs, document structure, an automated verifier, confidence-based triage, and a published accuracy benchmark.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your MODEL_API_KEY
python scripts/test_api.py
```

## Status

- [ ] Day 1: API connectivity
- [ ] Ingest + segmentation
- [ ] Description pipelines
- [ ] Verifier + triage
- [ ] Review UI + deploy
- [ ] Benchmark
- [ ] Demo video

## Limitations

Not yet evaluated with blind or low-vision users.
