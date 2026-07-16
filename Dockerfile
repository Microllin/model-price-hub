# 用 Playwright 官方 Python 镜像:已含 chromium 及全部系统依赖
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

# 依赖先装,利用层缓存
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir -e ".[render]"

COPY data ./data

# 启用 Playwright(SiliconFlow 国内 CNY 抓取器依赖它)
ENV MPH_USE_PLAYWRIGHT=1

EXPOSE 8000

# 默认起 API;抓取用 `docker compose run updater` 或 scheduler 服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
