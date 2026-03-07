FROM python:3.12-slim

WORKDIR /app

# 3. lxml 등 설치를 위한 시스템 빌드 도구 추가 (매우 중요)
RUN apt-get update && apt-get install -y \
    build-essential \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# pip 업그레이드 후 라이브러리 설치
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

CMD ["streamlit", "run", "menu2.py", "--server.port=8080", "--server.address=0.0.0.0"]
