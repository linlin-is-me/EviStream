# Stage 0 验收报告

日期：2026-08-31  
分支：`stage/0-foundation`  
状态：等待百炼真实调用与远程 CI，尚未达到完成门槛。

## 本地环境

- Python 3.11.16，环境名 `evistream_env`
- Node.js 24.14.0
- pnpm 11.19.0
- FFmpeg 2026-03-22 git build
- faster-whisper 1.2.1，`tiny.en`、CPU、INT8

项目配置和脚本没有写入上述工具的本机绝对路径。

## 已通过的检查

| 检查 | 命令或方式 | 结果 |
| --- | --- | --- |
| Python 风格 | `ruff check .` | 通过 |
| Python 类型 | `mypy evistream apps` | 通过，23 个源码文件 |
| Python 测试 | `pytest` | 28 项通过，核心覆盖率 89.73% |
| 前端风格 | `pnpm --dir apps/web lint` | 通过 |
| 前端测试 | `pnpm --dir apps/web test --run` | 2 项通过 |
| 前端构建 | `pnpm --dir apps/web build` | 通过 |
| API 进程 | 启动 Uvicorn 后请求 `/api/v1/health` | HTTP 200，Schema 与版本正确 |
| InlineExecutor | `evistream run-demo-job` | `SUCCEEDED`，共享 Handler 返回确定性结果 |
| Mock Gateway | `evistream model-smoke --profile mock` | 返回统一 `ModelResponse` |
| 兼容接口 | 本地 OpenAI-compatible HTTP 服务器 | Schema、追踪 ID、媒体格式、重试和错误映射通过 |
| FFprobe | `evistream probe-video tests/fixtures/media/stage0_sample.mp4` | 30 秒、640×360、H.264、AAC |
| Mock ASR | `evistream asr-smoke ... --backend mock` | 返回统一 `ASRResponse` |
| 真实 ASR | `evistream asr-smoke ... --backend faster-whisper` | 成功解码，返回 0–7720 ms 非空片段 |

真实 ASR 输出保存在
[`verification/stage0-asr-tiny-en.json`](verification/stage0-asr-tiny-en.json)。Stage 0 不以
精确 WER 为门槛，因此合成语音中的轻微识别偏差不阻断验收。

## 前端健康页

![EviStream Stage 0 health page](assets/stage-0-health.png)

页面已经在真实 Vite 与 Uvicorn 进程组合下核对。默认使用同源健康接口，Vite 在开发期代理
`/api`，分离部署时可设置 `VITE_API_BASE_URL`。

## 尚未通过的阶段门

- 本机尚未配置 `EVISTREAM_MODEL_API_KEY`，未执行百炼 `qwen3.8-flash` 真实调用。
- 分支尚未推送，GitHub Actions 尚未运行。
- 由于两项严格门槛未完成，当前不推送 `main` 或 `stage/0-foundation`，也不创建 PR。

百炼验收成功后需在本节补录实际模型 ID、调用日期、耗时、Token 用量和供应商请求 ID。随后推送
两个分支，等待 CI 全绿，再合并 PR。

## GitHub 协作项

- [M0 Foundation](https://github.com/linlin-is-me/EviStream/milestone/1)
- [Stage 0: Foundation](https://github.com/linlin-is-me/EviStream/issues/1)

## 已知限制与 Stage 1 前置条件

- CI 只运行 Mock Gateway 和本地兼容服务器，不访问外部模型，也不下载 ASR 模型。
- 兼容 Gateway 接受 URL 或 Data URI 媒体引用；本地文件编码与对象存储上传留到后续阶段。
- Stage 1 开始前需先完成百炼真实调用、远程 CI 和 PR 合并，再引入 PostgreSQL、Redis 与 RQ。
