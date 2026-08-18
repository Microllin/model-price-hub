# Release 流程

## 版本规则

项目使用 Semantic Versioning：

- `MAJOR`：不兼容变更
- `MINOR`：向后兼容的新功能
- `PATCH`：向后兼容的问题修复

## 发布前检查

```bash
pytest
python -m compileall app
git diff --check
docker compose config -q
docker compose build
```

## 发布步骤

1. 更新 `pyproject.toml` 和 `app/__init__.py` 中的版本号。
2. 更新 `CHANGELOG.md`。
3. 提交：

```bash
git commit -m "chore(release): vX.Y.Z"
```

4. 打标签：

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main --tags
```

5. GitHub Release 工作流会校验标签并创建 Release。

## 热修复

- 从最新 tag 创建 hotfix 分支。
- 修复后发布新的 PATCH 版本。
- 不直接修改已发布 tag。
