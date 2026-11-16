FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
RUN python -m pip install --no-cache-dir "pytest>=8,<10"

WORKDIR /workspace
USER 65532:65532
