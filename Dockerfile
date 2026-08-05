FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PDF_OUTPUT_DIR=/app/output

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fonts-noto-cjk-extra poppler-utils ca-certificates curl \
    && mkdir -p /app/fonts \
    && cp /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc /app/fonts/ \
    && cp /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc /app/fonts/ \
    && rm -rf /var/lib/apt/lists/*

ENV CJK_FONT_DIR=/app/fonts

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY assets ./assets
ENV PDF_ASSET_DIR=/app/assets
RUN mkdir -p /app/output

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
