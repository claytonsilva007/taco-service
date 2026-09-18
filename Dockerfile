# Build context is the repository root. Only api/ and data/processed/ are
# copied in: api/main.py loads its DataFrames from data/processed/{taco,pof}
# at import time (see DATA_DIR/POF_DIR there) -- data/raw/ and scripts/ are
# the offline processing pipeline, never read by the running API.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 1000 taco && \
    useradd --uid 1000 --gid taco --shell /bin/bash --create-home taco

# Install dependencies first so this layer is cached across data/code changes.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY data/processed ./data/processed

RUN chown -R taco:taco /app
USER taco

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
