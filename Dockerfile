FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libcairo2 \
 && rm -rf /var/lib/apt/lists/*
COPY skills/sketch-to-cad/requirements-lock.txt /tmp/requirements-lock.txt
COPY tools/requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-lock.txt -r /tmp/requirements-dev.txt
# Test-only: reportlab renderPM backend, used by tests to rasterize drawing
# sheets. Not part of the skill's runtime lock (pycairo has no Linux wheels).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config libcairo2-dev \
 && pip install --no-cache-dir pycairo==1.29.2 rlPyCairo==0.4.0 freetype-py==2.5.1 \
 && apt-get purge -y build-essential pkg-config libcairo2-dev \
 && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 dev
WORKDIR /work
USER 1000
ENV PYTHONDONTWRITEBYTECODE=1
# The image's own Python already has the pinned dependencies: pointing the
# venv at its prefix makes run.py use it instead of looking for a venv.
ENV SKETCHCAD_VENV=/usr/local
CMD ["pytest"]
