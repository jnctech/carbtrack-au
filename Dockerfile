FROM python:3.12-slim

WORKDIR /app

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# libheif1: runtime dep for pillow-heif so iPhone HEIC photos can be processed
RUN apt-get update && apt-get install -y --no-install-recommends libheif1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN mkdir -p /app/data && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8765

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
