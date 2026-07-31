# youtube-content — Web UI Docker image
# Build:  docker build -t youtube-content .
# Run:    docker run -p 8080:8080 -v yt_output:/app/output youtube-content

FROM python:3.11-slim

# ffmpeg needed for Whisper audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir flask

COPY scripts/ ./scripts/
COPY youtube_content/ ./youtube_content/
COPY pyproject.toml .
RUN mkdir -p /app/output

# Web UI
EXPOSE 8080
CMD ["python", "scripts/webui.py", "--host", "0.0.0.0", "--port", "8080"]
