FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 leadharbor && useradd --uid 10001 --gid 10001 --no-create-home leadharbor && mkdir /app/data && chown 10001:10001 /app/data
COPY --chown=10001:10001 leadharbor /app/leadharbor
COPY --chown=10001:10001 config /app/config
COPY --chown=10001:10001 scripts/backup.py /app/scripts/backup.py
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3)"
CMD ["python", "-m", "leadharbor"]
