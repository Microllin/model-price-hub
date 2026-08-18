# 贡献指南

## 开发环境

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,vision]"
python -m playwright install chromium
```

## 提交流程

1. 从 `main` 创建功能分支。
2. 修改代码并补充测试。
3. 运行：

```bash
pytest
python -m compileall app
git diff --check
```

4. 提交信息使用 Conventional Commits：

```text
feat: ...
fix: ...
docs: ...
chore: ...
refactor: ...
test: ...
```

5. 提交 PR 前确认没有包含运行期数据库、凭据、Agent 证据或 Webhook 配置。

## 代码边界

- 模型上线/下线只能由 `app/catalog` 产生。
- 价格变化只能由 `app/pipeline` 和官方价格抓取器产生。
- Discovery 只能生成候选和监控证据。
- Agent 只能处理解析、OCR、截图和上下文复核，不能直接绕过数据校验。
- 第三方来源不能标记为官方来源。

## 新增厂商

- 官方模型目录：加入 `/v1/sources` 配置。
- 官方价格：新增 `BaseScraper` 子类并注册到 `app/scrapers/registry.py`。
- 动态页面：优先使用 Playwright；复杂页面使用 Agent OCR。
- 必须补充离线 fixture 测试。
