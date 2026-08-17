FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependências primeiro, código depois: assim o cache de layers do Docker só
# invalida a instalação quando o pyproject muda, não a cada edição de código.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir ".[pgvector]"

# Rodar como root em container é desnecessário e amplia o estrago de um RCE.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
