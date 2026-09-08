# syntax=docker/dockerfile:1
# enterprise-ocr-service 镜像：销售单据识别 → all_sales.csv 汇总
# 构建: docker build -t enterprise-ocr-service .
# 运行: docker compose up -d （推荐，见 docker-compose.yml）

FROM python:3.11-slim

# 时区/编码（中文输出不乱码）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/ocr

# 先装依赖（利用层缓存；hatchling 打包需要 app 源码，故 COPY 放 install 之前一次装完）
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# 探测脚本（可选，仅本地调试参考）
COPY scripts ./scripts

# 非 root 运行更安全（output/ 由 volume 挂载，需可写）
RUN useradd -m -u 10001 ocr && mkdir -p /srv/ocr/output && chown -R ocr:ocr /srv/ocr
USER ocr

# 数据目录（卷挂载点，all_sales.csv 持久化在这）
VOLUME ["/srv/ocr/output"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
