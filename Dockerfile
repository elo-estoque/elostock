# Usando a imagem oficial completa (Bullseye/Debian)
FROM python:3.9

# Configuração para não travar em perguntas do Linux
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# --- CORREÇÃO DO ERRO ---
# Instala APENAS as bibliotecas de sistema (C/C++) necessárias para o WeasyPrint.
# REMOVIDO: python3-dev, python3-pip, python3-cffi (Isso causava o conflito/erro 100)
# O Python oficial já tem as ferramentas de dev necessárias embutidas.
RUN apt-get update && apt-get install -y \
    build-essential \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements e instala as dependências Python via PIP
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY . .

# Expõe a porta
EXPOSE 5000

# Roda com Gunicorn (Timeout mantido em 120s para segurança no envio de emails)
CMD ["gunicorn", "-w", "4", "--timeout", "120", "-b", "0.0.0.0:5000", "app:app"]
