# Stage 0 验收报告

日期：2026-08-31  
分支：`stage/0-foundation`  
状态：本地阶段门已通过，等待远程 CI。

## 本地环境

- Python 3.11.16，环境名 `evistream_env`
- Node.js 24.14.0
- pnpm 11.19.0
- FFmpeg 2026-03-22 git build
- faster-whisper 1.2.1，`tiny.en`、CPU、INT8
- WSL2 Ubuntu 22.04.5 LTS
- WSL Python 3.11.16、Node.js 24.20.0、pnpm 11.19.0、FFmpeg 4.4.2
- Docker Desktop 4.88.1、Engine 29.7.2、Compose 5.4.0

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
| Linux 环境 | WSL2 Ubuntu 中执行 `make doctor` | 9 项通过，0 项失败 |
| 百炼模型 | `evistream model-smoke --profile dashscope-test` | `qwen3.8-flash` 返回统一结构 |

真实 ASR 输出保存在
[`verification/stage0-asr-tiny-en.json`](verification/stage0-asr-tiny-en.json)。Stage 0 不以
精确 WER 为门槛，因此合成语音中的轻微识别偏差不阻断验收。

百炼真实调用于 2026-08-31 完成：实际模型 `qwen3.8-flash`，耗时 2405 ms，输入 80
Token、输出 79 Token，总计 159 Token。完整的非敏感响应元数据保存在
[`verification/stage0-model-dashscope.json`](verification/stage0-model-dashscope.json)。

## 前端健康页

![EviStream Stage 0 health page](assets/stage-0-health.png)

页面已经在真实 Vite 与 Uvicorn 进程组合下核对。默认使用同源健康接口，Vite 在开发期代理
`/api`，分离部署时可设置 `VITE_API_BASE_URL`。

## 尚未通过的阶段门

- 分支尚未推送，GitHub Actions 尚未运行。
- 当前需要提交新增部署约束与 Linux doctor，然后推送两个分支，等待 CI 全绿，再合并 PR。

## GitHub 协作项

- [M0 Foundation](https://github.com/linlin-is-me/EviStream/milestone/1)
- [Stage 0: Foundation](https://github.com/linlin-is-me/EviStream/issues/1)

## 已知限制与 Stage 1 前置条件

- CI 只运行 Mock Gateway 和本地兼容服务器，不访问外部模型，也不下载 ASR 模型。
- 兼容 Gateway 接受 URL 或 Data URI 媒体引用；本地文件编码与对象存储上传留到后续阶段。
- Stage 1 开始前需先完成远程 CI 和 PR 合并，再引入 PostgreSQL、Redis 与 RQ。
