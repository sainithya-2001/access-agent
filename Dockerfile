# The public demo (DEPLOY.md): the review screen and exports, serving the precomputed documents in
# demo/ (scripts/make_demo_bundle.py). DEMO_MODE=1: no model calls; uploads need DEMO_PASSCODE.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEMO_MODE=1
WORKDIR /app

# Playwright is only for scripts/check_a11y.py; PyMuPDF installs as a wheel, no system packages.
COPY requirements.txt .
RUN grep -v '^playwright' requirements.txt > /tmp/req.txt \
    && pip install --no-cache-dir -r /tmp/req.txt

COPY app/ app/
COPY web/ web/
# demo/ is laid out as the working directory: output/review.db, output/<pdf_id>/..., data/samples/.
COPY demo/ ./

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# Render sets PORT; 8000 elsewhere.
CMD ["sh", "-c", "exec uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
