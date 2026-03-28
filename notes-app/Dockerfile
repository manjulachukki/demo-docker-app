# ── Base image ────────────────────────────────────────────────────────────────
# python:3.13-alpine — Alpine Linux keeps the image tiny (~60 MB vs ~1 GB
# for the standard Python image).  Alpine has a minimal attack surface which
# is better for security.
FROM python:3.13-alpine

# ── Working directory ─────────────────────────────────────────────────────────
# All subsequent COPY, RUN, CMD instructions run relative to /app inside the
# container.  Creates the directory automatically if it does not exist.
WORKDIR /app

# ── Install dependencies BEFORE copying app code ──────────────────────────────
# Docker builds images in layers.  By copying requirements.txt first and
# running pip install before copying app.py, we create a cache layer for
# the pip install step.  If only app.py changes (no new packages), Docker
# reuses the cached pip layer — rebuilds are fast.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# --no-cache-dir: do not write pip's download cache to the image — keeps the
#                 layer smaller.

# ── Copy application code ─────────────────────────────────────────────────────
COPY app.py .

# ── Document the port ─────────────────────────────────────────────────────────
# EXPOSE does NOT open the port — it is documentation for developers and tools.
# The actual port mapping is set in docker-compose.yml with "ports: 5001:5001".
EXPOSE 5001

# ── Start command ─────────────────────────────────────────────────────────────
# Use exec form ["python", "app.py"] (not shell form "python app.py").
# Exec form means Python is PID 1 and receives Docker signals (SIGTERM, SIGINT)
# directly — enabling graceful shutdown.
CMD ["python", "app.py"]
