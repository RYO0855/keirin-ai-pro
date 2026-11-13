FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt && useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
COPY --chown=app:app backend /app/backend
USER app
ENV KEIRIN_DB=/data/keirin.sqlite
EXPOSE 8000
CMD ["uvicorn","backend.app:create_app","--factory","--host","0.0.0.0","--port","8000","--no-proxy-headers","--no-server-header"]
