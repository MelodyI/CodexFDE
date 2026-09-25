# 操作步骤

以下为 macOS 操作记录。除 FlowERP 一节外，命令都在本仓库根目录执行；Python 用 `python3`，虚拟环境解释器是 `.venv/bin/python`（不要写成 Windows 的 `.venv\Scripts\python.exe`）。

## python3

先确认版本。仓库要求 Python 3.10+，课堂统一 3.11.x；低于 3.10 时后面的建环境和所有 `-m workbench.cli` 命令都会失败。

```bash
python3 --version
HOMEBREW_NO_AUTO_UPDATE=1 brew install python@3.11
```

已满足版本要求则不必安装；装完新版本要重开终端再核一次 `python3 --version`。

## CODEX

安装 Codex CLI 并登录。登录会在浏览器完成授权，未登录时工作台的调研与执行都会失败。

```
HOMEBREW_NO_AUTO_UPDATE=1 brew install --cask codex
codex login
```

建本仓库独立环境、验证安装、再启动两个界面。顺序不要颠倒：`pip install -e .` 之前跑 `workbench.cli` 会报找不到模块。

```
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .

python3 -X utf8 -m workbench.cli environment-check
python3 -X utf8 -m eval.harness --suite blocking

python3 main.py 

```

逐条说明：

- `python3 -m venv .venv` / `source .venv/bin/activate`：只建一次。激活后提示符出现 `(.venv)`；后续命令可继续用 `python3`，也可用 `.venv/bin/python3` 直接调用。
- `python3 -m pip install -e .`：按 `pyproject.toml` 安装本仓库，只装工作台与课程依赖，不会安装 FlowERP。
- `environment-check`：只读检查解释器、包来源和页面资源是否齐全，输出 JSON，`ok: true` 只表示这些安装项通过，不表示业务可用。已登记 FlowERP 后另跑 `python3 -X utf8 -m workbench.cli environment-check --product` 检查客户环境。
- `eval.harness --suite blocking`：工作台阻断级 Eval，退出码非 0 即未通过，不要跳过或改判。这是工作台自身检查，不代表 FlowERP 业务通过。
- `python3 main.py`：一条命令启动工作台（8001）与 FlowERP（8000），服务在后台运行，命令返回后仍可访问。用系统 `python3` 调用时会自动转入本仓库 `.venv`，但不要在其他项目的已激活虚拟环境里执行。启动结果里 `started` 是新启动、`restarted` 是已重启、`reused` 是复用；`flowerp` 显示 `unavailable` 时工作台仍可用，先查 FlowERP 是否登记或设置 `FLOWERP_PROJECT_ROOT`。
- `python3 main.py --reuse`：保留已有工作台进程不重启，避免换到空目录导致看不到历史数据。需要自动开浏览器时加 `--open-browser`。
- `python3 main.py --stop`：停止本机服务（可选 `workbench` / `flowerp` 只停一个），macOS/Linux 也可用；身份核对不通过就拒绝停止，任务与数据保留。自定义端口时同样带 `--erp-port`。
- 打开后：工作台唯一入口 <http://127.0.0.1:8001/>（日常研发、课程任务、事项与决策都从这里进），FlowERP 客户项目 <http://127.0.0.1:8000/>。只开工作台可执行 `python3 -X utf8 -m workbench.cli serve-workbench`；需要网页授权 Codex 改代码时再加 `--enable-code-execution`。
- 课程跟跑另跑 `python3 -X utf8 -m workbench.cli course-status`；`course_ready: true` 只说明课程合同可跟跑。

## FlowERP

FlowERP 已迁到独立仓库 https://github.com/congde/flowERP.git，本仓库不再包含 `flowerp/` 与 `web/`。先取得独立仓库代码，再在**该仓库目录**建它自己的环境：

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -e .
```

要点：

- 这条命令在独立 FlowERP 仓库根目录执行，与本仓库的 `.venv` 是两个环境，不要混用或把包互相安装。
- 尚未取得代码时先 `git clone https://github.com/congde/flowERP.git <目标目录>`，目标目录必须还不存在；也可以在工作台首页「＋ 添加项目」里用 Git 链接克隆或用本地目录登记。
- 环境准备好后回到本仓库用 `python3 main.py` 启动，工作台通过项目登记或 `FLOWERP_PROJECT_ROOT` 找到该仓库；两者只登记一次即可，日常启动不用重复初始化业务数据。
- 需要单独前台启动排错时，在独立仓库里执行 `.venv/bin/python -X utf8 -m flowerp serve --port 8002 --runtime-dir .runtime`，端口换 8002 以区别于仍在运行的旧 8000 服务，完整参数以独立仓库 README 为准。
- 客户业务 Eval 在独立仓库运行：工作台绿灯不等于 ERP 业务通过。
