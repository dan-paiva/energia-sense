FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY energia ./energia
RUN pip install . && useradd --system --uid 1001 energia
USER energia

EXPOSE 8000
CMD ["uvicorn", "energia.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
