# ---------------------------------------------------------------
# MUDANÇA CRÍTICA: Usando a imagem COMPLETA (não a slim)
# Isso resolve os erros de dependência do Linux (exit code 100)
# ---------------------------------------------------------------
FROM python:3.9

# Evita perguntas de configuração (timezone, etc)
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Instalação das bibliotecas para PDF (WeasyPrint) e Banco (Postgres)
# Como a imagem é completa, removemos bibliotecas de compilação redundantes
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    python3-pip \
    python3-cffi \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala as dependências do Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Expõe a porta
EXPOSE 5000

# Roda a aplicação com timeout seguro para gerar PDFs
CMD ["gunicorn", "-w", "4", "--timeout", "120", "-b", "0.0.0.0:5000", "app:app"]
