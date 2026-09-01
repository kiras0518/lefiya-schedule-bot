FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /nonexistent app

COPY --chown=app:app src ./src

USER app

EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn --workers 2 --bind \"0.0.0.0:${PORT:-8080}\" 'lefiya_schedule_bot.webhook:create_app()'"]
