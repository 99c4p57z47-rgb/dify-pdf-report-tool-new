FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PDF_OUTPUT_DIR=/app/output

# ReportLab TTFont rejects Noto CJK's CFF/PostScript outlines. WenQuanYi
# Micro Hei uses TrueType outlines and can be embedded deterministically.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-microhei poppler-utils ca-certificates curl \
    && mkdir -p /app/fonts \
    && cp /usr/share/fonts/truetype/wqy/wqy-microhei.ttc /app/fonts/NotoSansCJK-Regular.ttc \
    && cp /usr/share/fonts/truetype/wqy/wqy-microhei.ttc /app/fonts/NotoSansCJK-Bold.ttc \
    && rm -rf /var/lib/apt/lists/*

ENV CJK_FONT_DIR=/app/fonts

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY assets ./assets
COPY knowledge ./knowledge
ENV PDF_ASSET_DIR=/app/assets
ENV PDF_KNOWLEDGE_DIR=/app/knowledge
RUN mkdir -p /app/output

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
