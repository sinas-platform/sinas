FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (needed for psycopg2 and other packages)
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy only the files necessary for installing dependencies
COPY pyproject.toml /app/

# Install dependencies
RUN pip install --no-cache-dir -e .

# Copy the rest of the application
COPY . /app/

# Copy executor script for user containers
COPY container_executor.py /app/executor.py

# Create directory for logs
RUN mkdir -p /app/logs

# Expose the port the app runs on
EXPOSE 8000

# Run database migrations on startup, then start the application with multiple workers
# UVICORN_WORKERS environment variable can override default of 4
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-4}