FROM python:3.9-slim

# Evita que o Linux faça perguntas durante a instalação (ex: Timezone)
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Instala dependências do Sistema
# Adicionamos --no-install-recommends para manter a imagem leve
# build-essential e python3-dev são vitais para compilar as libs do WeasyPrint
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    build-essential \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-wheel \
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

# Copia requirements e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Expõe a porta
EXPOSE 5000

# Roda com Gunicorn (Timeout aumentado para 120s para garantir envio de email/PDF)
CMD ["gunicorn", "-w", "4", "--timeout", "120", "-b", "0.0.0.0:5000", "app:app"]
