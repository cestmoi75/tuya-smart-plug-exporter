FROM python:3.13-slim

WORKDIR /app
COPY app.py /app/app.py
COPY static /app/static

ENV PROMETHEUS_URL=http://192.168.0.124:9090 \
    PORT=8088 \
    CACHE_TTL_SECONDS=5 \
    MAX_HISTORY_HOURS=168

EXPOSE 8088
USER 65532:65532
CMD ["python", "/app/app.py"]
