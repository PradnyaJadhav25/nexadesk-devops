
FROM python:3.12-slim

# Update Debian packages to include available security fixes
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir "pip==26.2.1" \
    && pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chown=10001:10001 . .

# Create required directories and set permissions
RUN mkdir -p /data /app/instance \
    && chown -R 10001:10001 /app /data

# Run as a non-root user
USER 10001:10001

EXPOSE 5000

# Container health check
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '5000') + '/health', timeout=3)"

# Initialize the database and start Gunicorn
CMD ["sh", "-c", "flask --app 'app:create_app()' init-db && exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers ${WEB_CONCURRENCY:-2} --access-logfile - --error-logfile - 'app:create_app()'"]
