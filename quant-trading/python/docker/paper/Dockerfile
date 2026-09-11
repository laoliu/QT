# 模拟盘容器镜像
#
# 构建上下文是 python/（见 docker-compose.paper.yml 的 build.context: ..），
# 所以下面的相对路径都相对于 python/。
#
# 代码本身**不烤进镜像**：aipha/ 与 scripts/ 在 compose 里以 bind mount
# 挂进来，改一行策略不用重建镜像。镜像里只放依赖 + cron + 入口脚本。
FROM python:3.13-slim

# cron：容器的唯一常驻进程；tzdata：容器内要用北京时间判交易日；
# procps/curl：出问题时进容器排查用；build-essential：部分依赖需要编译。
RUN apt-get update && apt-get install -y --no-install-recommends \
        cron \
        tzdata \
        procps \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai \
    PYTHONPATH=/app/src:/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AIPHA_PROJECT_ROOT=/app \
    PIP_NO_CACHE_DIR=1

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# 先单独拷 requirements.txt：依赖没变时这层走缓存，重建只要几秒
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# 入口与 crontab 需要在镜像里（entrypoint 在挂载生效前就要跑）
COPY docker/paper/entrypoint.sh /app/docker/paper/entrypoint.sh
COPY docker/paper/crontab /app/docker/paper/crontab
RUN chmod +x /app/docker/paper/entrypoint.sh

# 挂载点先建好，避免 bind mount 目标缺失时 Docker 用 root 建出权限不对的目录
RUN mkdir -p /app/logs/cron /app/logs/audit /app/logs/ops \
             /app/data/paper_state /app/configs /app/output

ENTRYPOINT ["/app/docker/paper/entrypoint.sh"]
