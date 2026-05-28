# FRTB IMA Risk Monitor - application image.
# Runs the daily pipeline once to populate the database, then serves the
# Plotly Dash dashboard. Intended to be launched via docker-compose (which also
# provisions PostgreSQL); see docker-compose.yml.

FROM python:3.13-slim

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code (.dockerignore keeps .env and other cruft out).
COPY . .

# Serve on all interfaces inside the container.
ENV DASH_HOST=0.0.0.0 \
    DASH_PORT=8050

EXPOSE 8050

# Populate the database, then launch the dashboard. run_pipeline self-bootstraps
# the schema and exits cleanly even if a non-critical step is skipped.
CMD ["sh", "-c", "python pipeline/run_pipeline.py; python dashboard/app.py"]
