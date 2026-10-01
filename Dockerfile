FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app ./app
COPY wsgi.py ./

# SQLite 数据目录（由 Compose 挂载为命名卷）
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

# 容器启动即完成建表初始化（init_db 幂等），随后由 waitress 提供服务
CMD ["sh", "-c", "python -c \"from app import create_app; create_app('/app/data/cutting.db')\" && python wsgi.py"]
