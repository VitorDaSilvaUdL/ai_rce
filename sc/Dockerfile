FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY utils ./utils
COPY api_data ./api
COPY main.py ./main.py

EXPOSE 8001

CMD ["python", "-u", "main.py"]