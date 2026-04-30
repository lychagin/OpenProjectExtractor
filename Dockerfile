FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ ./src/
COPY db/ ./db/

# Run as non-root.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

ENTRYPOINT ["python", "-m", "src.main"]
