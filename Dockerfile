FROM python:3.11-slim

# Never write .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies + strip dead weight in one layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && find /usr/local/lib -type d -name "tests"     -exec rm -rf {} + 2>/dev/null; true \
    && find /usr/local/lib -type d -name "test"      -exec rm -rf {} + 2>/dev/null; true \
    && find /usr/local/lib -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true

# Copy application code
COPY *.py ./

# config.json and template JSONs are NOT baked in — mount them at runtime.
# See README for volume mount instructions.

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "ui.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
