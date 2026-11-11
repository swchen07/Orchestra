# Orchestra — built on Open WebUI

本地运行、通过网页使用的多框架 Agent 工作区。基于 [Open WebUI](https://github.com/open-webui/open-webui) 扩展，保留 Open WebUI 的网页界面与品牌标识。项目不是 Open WebUI 官方发行版。

Orchestra 把模型直连、Claude Code、Codex、DSH 和 Hermes 封装成可组合模块，在同一个聊天中组织多 Agent 工作。

- **Projects**：工作目录、参与者和默认绑定。
- **Connections**：框架 → Provider → Model，以及原生 Profile / Agent 预设。
- **Workflows**：统一 Node + Action 图，支持预定义图、AI 规划、依赖驱动的并行执行与审核返工。
- **Run**：图结构、工具 Activity、中间报告、Token 和时间统计。
- **Code workspaces**：各模块自己的 Git 工作区、版本化共享产物、真实检查、冲突解决与独立审核。

## 本地快速启动

需要 **Git、Node.js 22 LTS、Python 3.12–3.14**。原生框架还需在本机单独安装并配置；只用模型 API 时不需要全部安装。首次安装依赖需要联网。

```sh
git clone https://github.com/swchen07/Orchestra.git
cd Orchestra
python3 scripts/orchestra.py setup
./start.sh
```

打开 **http://localhost:8080**。前后端由同一个本地服务提供，不需要分别打开两个端口；按 `Ctrl+C` 停止。

首次访问按网页引导创建管理员，再到 **Configuration → Connections** 配置自己的 Provider。仓库不包含示例账号、API Key、现有聊天或他人的原生登录信息。

常用命令：

```sh
python3 scripts/orchestra.py doctor        # 检查依赖和可发现的本机框架
python3 scripts/orchestra.py build         # 重建网页
./start.sh --port 9090                    # 指定未占用的端口
```

启动默认监听 `127.0.0.1`。现有端口被占用时会报错，不会结束其他服务。`start` 在网页源码变化时重建前端，不会清除数据或重复安装依赖。

如果系统里装有多套运行时，可指定可执行文件：

```sh
ORCHESTRA_PYTHON=/path/to/python3.14 python3 scripts/orchestra.py setup
ORCHESTRA_NODE=/path/to/node22 python3 scripts/orchestra.py build
```

macOS Homebrew 的 Node 22 会自动发现，不需要修改源码里的用户名或安装路径。Windows 使用 `python scripts/orchestra.py start`，原生框架能力以各 CLI 对该平台的支持为准。

## 开发模式

先完成上面的 `setup`，然后在两个终端中启动：

```sh
# 终端一：后端 http://localhost:8080
bash dev-backend.sh

# 终端二：网页热更新 http://localhost:5173
bash dev-frontend.sh
```

也可直接使用 `python3 scripts/orchestra.py backend` / `frontend`。如果后端端口改变，为前端设置 `WEBUI_BACKEND_URL=http://127.0.0.1:9090`。

## 数据、原生框架与执行边界

- 默认数据存放在 `backend/data/`，不提交到 Git。更新源码保留这个目录及认证密钥。
- 用 `ORCHESTRA_DATA_DIR=/absolute/data/path ./start.sh` 指定独立数据目录；新目录会生成并持久化自己的认证密钥。不要把密钥提交或上传。
- 原有安装的 `backend/.webui_secret_key` 会继续使用，以保留既有登录和加密配置。
- Claude Code / Codex / DSH / Hermes 是可选的本机依赖。应用启动不会替你安装框架、切换它们的全局 Provider 或登录账号。
- Hermes 的独立编码工作区需要显式选择相匹配的本机原生 Profile；HTTP 网关连接和该原生工作区路径分别保留。
- CC Switch 集成使用固定版本的数据契约，读取本机已有配置；不随仓库分发用户的 CC Switch 数据库。见[集成说明](backend/open_webui/integrations/cc_switch/README.md)。
- 原生代码模块使用独立 Git 工作区，属于可信本地执行。可选择 Docker 作为权威评估环境；这不等于原生 CLI 也被 Docker 隔离。未安装 Docker 时，必要的 Docker 检查会在模型执行前拒绝，不会偷偷降级。
- 初始代码工作区/产物上限为 16 MiB；不支持托管符号链接、硬链接。Token 来源不完整时显示下界或未知，不把缓存和子模块重复相加。

快速安装使用 `requirements-slim.txt`，适合外部模型、原生框架与 SQLite。上游完整的本地嵌入/语音等重依赖不在此快速安装中。浏览器 Python 运行时按 slim 方式生成，额外 Python 包可能需要联网下载。

## 源码结构

```text
src/                                Svelte 网页前端
src/lib/components/execution/        Projects、Workflows、Run、Activity
backend/open_webui/execution/        模块协议、图调度、框架适配、工作区和统计
backend/open_webui/integrations/     CC Switch 数据契约与适配
backend/tests/execution/             调度、隔离、用量与文件操作回归测试
scripts/orchestra.py                 本地安装、构建、启动和环境检查
```

[模块与 Action 契约](docs/module-action-composition-contract.md) · [Node + Action](docs/node-actions-and-examples.md) · [工作区 API](docs/workspace-artifact-api-20261110.md)

本仓库的自动检查位于 `.github/workflows/orchestra.yml`。上游发布流程保存在 `.github/upstream-workflows/` 作为来源记录，不会触发上游镜像或包发布。

## 来源与许可证

上游是 **Open WebUI**，原作者为 Timothy Jaeryang Baek / Open WebUI Inc.，基于上游 commit `33c1978` 开始扩展。本仓库的主要新增部分是多框架适配、可组合 Workflow、执行轨迹、独立编码工作区和统一统计。

代码继续受仓库中的 [LICENSE](LICENSE) 以及适用的 [LICENSE_HISTORY](LICENSE_HISTORY)、[LICENSE_NOTICE](LICENSE_NOTICE) 约束；没有重新声明成 MIT 等其他许可证。保留上游版权、许可证和现有网页品牌，分发时仍须遵守其中的品牌等条件。

[来源说明](FORK_NOTICE.md) · [上游 README](README.upstream.md) · [上游文档](https://docs.openwebui.com/)
