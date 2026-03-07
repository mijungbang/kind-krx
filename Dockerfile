# 1. 파이썬 3.12 슬림 이미지 사용 (용량이 작아 배포가 빠릅니다)
FROM python:3.12-slim

# 2. 컨테이너 내부 작업 디렉토리 설정
WORKDIR /app

# 3. 시스템 의존성 설치 (필요한 경우)
# 혹시 라이브러리 설치 중 에러가 나면 아래 두 줄의 주석을 해제하세요.
# RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

# 4. 현재 폴더의 모든 파일을 컨테이너로 복사
COPY . .

# 5. 파이썬 라이브러리 설치
RUN pip install --no-cache-dir -r requirements.txt

# 6. 스트림릿 포트 설정 (Cloud Run 기본 포트 8080)
EXPOSE 8080

# 7. 실행 명령어 (메인 파일이 menu2.py인 경우)
CMD ["streamlit", "run", "menu2.py", "--server.port=8080", "--server.address=0.0.0.0"]
