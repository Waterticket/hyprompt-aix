FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PDF_FONT_PATH=/usr/share/fonts/truetype/nanum/NanumGothic.ttf

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 kicklog \
    && useradd --uid 10001 --gid kicklog --no-create-home kicklog \
    && mkdir /app/instance \
    && chown kicklog:kicklog /app/instance

COPY requirements.txt requirements-docker.txt ./
RUN pip install -r requirements-docker.txt

COPY app/ ./app/
COPY data/ ./data/

USER kicklog
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/', timeout=3).close()"

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-", "app:create_app()"]
