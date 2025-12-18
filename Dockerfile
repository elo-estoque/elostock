# Usamos a imagem slim que é rápida e leve
FROM python:3.9-slim

WORKDIR /app

# Instala apenas o necessário para o Banco de Dados (Postgres)
# Removemos todas as bibliotecas de vídeo/gráfico que estavam dando erro
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

EXPOSE 5000

# Timeout seguro
CMD ["gunicorn", "-w", "4", "--timeout", "120", "-b", "0.0.0.0:5000", "app:app"]
