# 用 Playwright 官方 Python 镜像:已含 chromium 及全部系统依赖
FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

WORKDIR /app

# 依赖先装,利用层缓存
COPY pyproject.toml README.md ./
COPY app ./app
# vision 额外带 anthropic:视觉采集器靠它做截图识别。
# 只装 [render] 时视觉路径会在运行时抛 ModuleNotFoundError，
# 而后台只显示「脚本执行抛出异常」，很难看出是镜像少装了依赖。
#
# playwright 必须钉死在与基础镜像一致的版本(见上方 FROM 的 tag)。
# 基础镜像只预装了对应那一版的浏览器(/ms-playwright/chromium-1234)；
# pyproject 写的是 playwright>=1.42，pip 会装到最新版，于是去找
# chromium-1243 而报 "Executable doesn't exist"——12 个走浏览器渲染的
# 采集器会一起失效。升级时 FROM 的 tag 与这里的版本必须同时改。
RUN pip install --no-cache-dir -e ".[render,vision]" "playwright==1.62.*"

COPY data ./data

# 启用 Playwright(SiliconFlow 国内 CNY 抓取器依赖它)
ENV MPH_USE_PLAYWRIGHT=1

EXPOSE 8000

# 默认起 API;抓取用 `docker compose run updater` 或 scheduler 服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
