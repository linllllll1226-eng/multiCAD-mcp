# multiCAD-mcp v0.4

[English](README.md) | **简体中文**

`multiCAD-mcp` 通过 Windows COM 将兼容 MCP 的 AI 客户端连接到 CAD 软件。本版本为
AutoCAD 2022 增加了受控绘图工作流，可用于图片重建、结果复核、经验记忆、任务追踪、
按任务正式提交与撤回，以及扫描图纸的可选本地 OCR。

> 本仓库基于 [AnCode666/multiCAD-mcp](https://github.com/AnCode666/multiCAD-mcp)
> 的 Apache-2.0 开源项目扩展而来，保留了原项目的 7 个统一工具和多 CAD 适配器。

## 当前状态

- **主要实机验证目标：** Windows 上的 AutoCAD 2022（`COM 24.1`）。
- **MCP 工具数量：** 23 个，包括 7 个上游统一工具和 16 个增强工作流、记忆、任务与视觉工具。
- **自动测试：** v0.4 安全发布门禁共 252 项测试。
- **质量门禁：** 完整 Ruff 检查、格式检查和仓库卫生检查。
- **通信方式：** 本地 STDIO，Codex 不需要监听网络端口。
- **安全入口：** `src/server_memory.py`。
- **兼容入口：** `src/server.py`，用于保留上游兼容性，但不包含完整受控工作流。

上游适配器还面向中望 CAD、浩辰 CAD 和 BricsCAD。本分支的增强校验、原生尺寸标注、
XData 和任务生命周期验收均在 AutoCAD 2022 上完成；其他 CAD 产品可能提供不同的 COM 属性。

## 受控写入工作流

所有增强写入都必须依次执行：

```text
cad_plan_validate
        -> cad_execute_plan
        -> cad_verify_execution
```

系统会在执行前校验单位、几何、尺寸来源、约束、图层、不确定项和破坏性意图。执行后，
它会重新读取 AutoCAD 中的真实图元数据并生成复核结果。不能仅凭工具调用没有报错就认定
绘图成功。

为兼容上游，旧写入工具仍然存在；但随仓库提供的 `autocad-drawing-assistant` Skill 在增强
步骤失败时不会静默绕过安全流程。

## 增强功能

| 功能领域 | 能力 |
|---|---|
| 绘图记忆 | 将用户确认的纠正、绘图配置和执行结果保存在本地 SQLite 中 |
| 绘图计划 | 记录结构化图元、约束、置信度、尺寸来源和不确定项 |
| 执行后复核 | 使用真实 CAD 图元数据输出目标值、实际值、误差和通过状态 |
| 标注防错 | 使用原生直径/半径标注，保持 `TextOverride` 为空并检查文字填充 |
| 任务追踪 | 使用稳定的 `task_id`、持久化图元来源和 AI 对象查询 |
| 安全生命周期 | 通过复核后正式提交预览，并按任务撤回而不是调用全局 `UNDO` |
| 视觉辅助 | 矢量 PDF/图片预处理、本地 OCR、尺寸证据、缓存和基准测试 |
| 易用性 | 绘图配置档、写入前备份、受控模板初始化和一键启动器 |

## 运行要求

- Windows
- Python 3.10 或更高版本
- AutoCAD 2022（增强工作流的已验证版本）
- `uv`
- Codex 或其他兼容 MCP 的客户端

## 安装

```powershell
git clone https://github.com/linllllll1226-eng/multiCAD-mcp.git
cd multiCAD-mcp
git checkout v0.4.0
uv sync --extra dev --extra vision --extra docs --extra ocr
```

如果需要发布后的最新文档和修复，可以使用 `main`，不切换到发布标签。只克隆上游仓库
不会包含这里的 v0.4 扩展。仅需要上游基础功能时，执行 `uv sync` 即可。`vision` 会安装
OpenCV、NumPy 和 PyMuPDF；`ocr` 会安装 PaddleOCR 和本地 Paddle 推理环境，不需要云端
OCR 服务。

## 配置 Codex

在 `%USERPROFILE%\.codex\config.toml` 中添加增强型本地 STDIO 服务：

```toml
[mcp_servers.autocad]
command = "D:\\AI\\multiCAD-mcp\\.venv\\Scripts\\python.exe"
args = ["D:\\AI\\multiCAD-mcp\\src\\server_memory.py"]
cwd = "D:\\AI\\multiCAD-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
enabled = true
```

请根据实际安装目录调整路径。执行写入任务前，先启动 AutoCAD，并打开空白图纸或真实图纸副本。

检查 MCP 注册状态：

```powershell
codex mcp list
```

## 典型用法

```text
使用 $autocad-drawing-assistant。
加载 university_mechanical_drawing 配置档。
先分析这张图，只采用明确尺寸和能够唯一推导的几何约束。
列出不确定轮廓，在我确认前不要绘制。
```

确认计划后：

```text
开始预览。严格执行受控三段式工作流，并从真实 CAD 图元中复核所有主要尺寸。
```

对于扫描图，`cad_analyze_source` 默认使用 OCR；矢量 PDF 会优先保留更准确的嵌入路径和
文字。第一次处理栅格图片时可能需要下载官方 OCR 模型；相同文件之后会使用本地结果缓存。

Skill 支持以下快捷意图：`分析这张图`、`开始预览`、`正式提交`、`检查图纸` 和
`撤回本次`。

## 安全机制

- 默认先写入预览图层。
- `approximate_reference` 近似轮廓不能进入正式轮廓图层。
- 删除、覆盖和其他破坏性操作必须得到明确确认。
- 正式提交与撤回根据经过验证的 `task_id` 识别对象，不依赖当前选择集或全局 `UNDO`。
- 本地经验数据库不会提交到 Git。
- 默认禁止任意导出路径。
- 一键启动器不会自动打开、保存或修改 DWG。
- 首次验收使用空白图纸，真实任务使用已保存的副本。

安全问题请按照 [`SECURITY.md`](SECURITY.md) 报告，不要在公开 Issue 中放入漏洞细节或
私人图纸。可复现的发布检查和 Bandit SQL 审查记录位于
[`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md)。

## 工具分组

上游提供的 7 个统一工具：

```text
manage_session, draw_entities, manage_layers, manage_files,
manage_entities, manage_blocks, export_data
```

增强入口新增：

```text
cad_memory_search, cad_memory_add_correction, cad_memory_list,
cad_memory_delete, cad_save_drawing_profile, cad_load_drawing_profile,
cad_plan_validate, cad_execute_plan, cad_verify_execution,
cad_list_ai_tasks, cad_get_task_entities, cad_get_entity_provenance,
cad_commit_preview_task, cad_revert_ai_task,
cad_analyze_source, cad_vision_capabilities
```

## 测试和质量检查

```powershell
uv run pytest -q -p no:cacheprovider
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mkdocs build --strict
uv run python scripts/check_repository_hygiene.py
```

Windows CI 会在 Python 3.10、3.11 和 3.12 上运行测试。

## 已知限制

- 图片中缺少的尺寸不能成为可信的制造尺寸，相关轮廓必须单独标记为不确定。
- OCR 只能增强扫描图文字证据，不能让模糊或缺失的尺寸自动变得可靠。
- 当前基准测试使用确定性的工程测试样本，不代表对所有图纸都有相同识别率。
- 受控执行器有意不支持部分任意编辑和删除操作。
- 图元级数据复核后，尺寸标注排版仍建议进行人工视觉检查。
- AutoCAD 内置侧边栏和语音面板不在 v0.4 范围内。

## 文档

- [文档索引](docs/README.md)
- [记忆与校验](docs/CAD_MEMORY_VALIDATION.md)
- [任务追踪与安全生命周期](docs/CAD_TASK_TRACKING.md)
- [安全强化](docs/CAD_SAFETY_HARDENING.md)
- [视觉处理流程](docs/CAD_VISION_PIPELINE.md)
- [扫描图 OCR](docs/CAD_OCR.md)
- [视觉基准测试](docs/CAD_VISION_BENCHMARK.md)
- [易用性层](docs/CAD_UX_IMPROVEMENTS.md)
- [更新日志](docs/03-CHANGELOG.md)

## 许可证与致谢

本项目使用 Apache-2.0 许可证。重新分发衍生版本时，请保留上游版权、许可证和来源说明。
