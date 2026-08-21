# 后端 API 镜像 — FastAPI + uv
FROM python:3.11-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# 安装 uv（经 PyPI，服务器可达）
RUN pip install --no-cache-dir uv

# 先复制依赖声明，利用层缓存
COPY pyproject.toml uv.lock ./

# 仅安装依赖（uv.lock 锁定版本）
RUN uv sync --frozen --no-install-project --no-dev

# 复制应用代码
COPY . .

EXPOSE 8000

# 启动：先执行数据库迁移（幂等），再启动服务
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000"]
