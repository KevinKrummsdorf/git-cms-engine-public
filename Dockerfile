# =====================================================================
# Stage 1: Build & Dependency Resolution Stage
# Resolves and compiles python packages using a slim build environment.
# =====================================================================
FROM python:3.12-slim as builder

# Set the active working directory within the container
WORKDIR /app

# Install native compilation dependencies required for building python C-extensions (such as psycopg2 or greenlet)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest file to leverage Docker build cache layers
COPY requirements.txt .

# Install dependencies into user directory context to avoid root package conflicts and optimize footprint
RUN pip install --no-cache-dir --user -r requirements.txt


# =====================================================================
# Stage 2: Clean Production Runtime Environment
# Installs minimal runtime dependencies and launches the FastAPI application.
# =====================================================================
FROM python:3.12-slim as runner

# Set the active working directory within the container
WORKDIR /app

# Install runtime dependencies:
# - libpq5 is required for database connections via PostgreSQL clients
# - git is required by the Git-Sync service to clone and pull content repositories
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-compiled and resolved Python packages from the builder stage
COPY --from=builder /root/.local /root/.local

# Prepend the local user binary path to the system execution environment PATH variable
ENV PATH=/root/.local/bin:$PATH

# Copy backend source code files into the container space
COPY . .

# Expose target listening port 8000 for network API traffic
EXPOSE 8000

# Prevent Python from buffering standard output and error streams (enables real-time logging)
ENV PYTHONUNBUFFERED=1

# Run the standalone FastAPI ASGI web server using high-performance Uvicorn parameters
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
