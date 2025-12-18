FROM python:3.9-slim

WORKDIR /app

# Instalação de dependências do Sistema
# 1. gcc e libpq-dev: Necessários para o PostgreSQL (psycopg2)
# 2. Bibliotecas gráficas (Pango/Cairo): Necessárias para o WeasyPrint gerar PDF
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    build-essential \
    python3-dev \
    python3-cffi \
    python3-brotli \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    libcairo2 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements e instala as dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o código da aplicação
COPY . .

# Expõe a porta 5000
EXPOSE 5000

# Roda com Gunicorn (Servidor de Produção)
CMD ["gunicorn", "-w", "4", "--timeout", "120", "-b", "0.0.0.0:5000", "app:app"]
