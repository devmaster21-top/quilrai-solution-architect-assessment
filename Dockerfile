FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home gateway && mkdir /app/data && chown gateway:gateway /app/data
USER gateway
EXPOSE 8000
CMD ["uvicorn", "quilr_gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
