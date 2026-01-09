FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY api_converters.py .
COPY static/ ./static/

# Create data directory for persistent storage
RUN mkdir -p /app/data

# Set environment variables
ENV PORT=8000
ENV PYTHONUNBUFFERED=1
ENV ACCOUNTS_FILE=/app/data/accounts.json
ENV SETTINGS_FILE=/app/data/settings.json
ENV API_KEYS_FILE=/app/data/api_keys.json
ENV USAGE_LOGS_FILE=/app/data/usage_logs.json

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/auth/check')"

# Run application
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120 --access-logfile - --error-logfile -
