FROM python:3.9-slim

# Evita perguntas durante a instalação
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# 1. Atualiza o sistema
# 2. Instala compiladores básicos (gcc, build-essential) para o Postgres e CFFI
# 3. Instala APENAS as libs gráficas do Linux que o WeasyPrint precisa (Pango, Cairo)
# REMOVIDO: Pacotes 'python3-xxx' que estavam causando o erro 100
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    build-essential \
    python3-dev \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Expõe a porta
EXPOSE 5000

# Roda com Gunicorn (Timeout aumentado para 120s)
CMD ["gunicorn", "-w", "4", "--timeout", "120", "-b", "0.0.0.0:5000", "app:app"]
