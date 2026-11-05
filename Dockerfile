FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    groupadd --gid 10001 workbench && \
    useradd --uid 10001 --gid workbench --no-create-home workbench && \
    mkdir -p /data /private && chown workbench:workbench /data /private
COPY auth.py application.py store.py server.py setup_app.py word_export.py rich_content.py document_import.py pdf_import.py ingestion_worker.py writing.py templates.json schema.sql fixtures.json ./
COPY static ./static
USER 10001:10001
CMD ["python", "server.py", "--bind", "0.0.0.0", "--database", "/data/workbench.db"]
