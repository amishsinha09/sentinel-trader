FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sentinel ./sentinel
COPY config ./config
COPY strategies ./strategies
COPY tests ./tests
# state/ (experiment DB, paper account, risk state, kill switch) is mounted as a volume
VOLUME ["/app/state"]
RUN useradd -m bot && mkdir -p /app/state && chown -R bot /app
USER bot
ENTRYPOINT ["python", "-m", "sentinel"]
CMD ["status"]
