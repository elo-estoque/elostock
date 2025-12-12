FROM python:3.9-slim

WORKDIR /app

# Instala bibliotecas do sistema necessárias para o Postgres
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# Copia requirements e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o código (incluindo a pasta templates)
COPY . .

# Expõe a porta 5000
EXPOSE 5000

# Roda com Gunicorn (Servidor de Produção)
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
