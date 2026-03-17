# ============================================================
# Earnings Research AI — Docker Image
# ============================================================
# Multi-stage build for a lean production image
# ============================================================

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # PDF processing
    wkhtmltopdf \
    poppler-utils \
    # Video/animation rendering
    ffmpeg \
    xvfb \
    # OpenGL dependencies for Arcade
    libgl1-mesa-glx \
    libgl1-mesa-dri \
    libegl1-mesa \
    libgles2-mesa \
    libglvnd0 \
    libglx0 \
    libopengl0 \
    libxkbcommon0 \
    libxcb-xfixes0 \
    libxcb-shape0 \
    libxcb-render0 \
    libxcb-shm0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-xinerama0 \
    # Networking
    curl \
    wget \
    ca-certificates \
    # Node.js for Claude CLI
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create reports directory
RUN mkdir -p /app/reports

# Expose the dashboard port
EXPOSE 8090

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8090/api/status || exit 1

# Start via startup script (handles Xvfb + env loading)
RUN chmod +x start.sh
CMD ["./start.sh"]