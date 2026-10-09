# One small, non-root runtime image (the build/deploy contract).
FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml ./
RUN pip install --no-cache-dir . && \
    adduser --disabled-password --gecos "" --uid 10001 appuser

COPY app ./app
COPY openapi.yaml ./openapi.yaml

# Numeric UID, not the name (hadolint DL3066): a name is resolved inside the
# image, but Kubernetes runAsNonRoot and host tooling need the numeric id.
USER 10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
