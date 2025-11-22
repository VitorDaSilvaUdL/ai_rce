FROM python:3.12-slim

WORKDIR /app

# Copia solo los archivos necesarios
COPY requirements.txt .

# Copia únicamente la carpeta api_data dentro del contenedor
COPY . ./api

# Instala las dependencias
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

# Ejecuta el servidor desde el archivo correcto
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

