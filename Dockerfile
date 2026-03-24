FROM python:3.11-slim

WORKDIR /app

# Force cache invalidation on requirements change
ARG CACHEBUST=2
RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "bot.main"]
