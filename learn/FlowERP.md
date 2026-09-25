# FlowERP 操作手册（macOS）

FlowERP 是**独立客户仓库**，与工作台仓库 CodexFDE 平级存放，不放在 CodexFDE 目录内。本手册按本机实际执行顺序记录：取得代码 → 建独立环境 → 让工作台找到它 → 自检 → 启动 → 排错。

本机实际路径（示例，按自己的目录替换）：

| 项目 | 路径 |
|---|---|
| 工作台仓库 | `/Users/leaf.shi/career/study/2026/codex/sourcecode/CodexFDE` |
| 客户项目仓库 | `/Users/leaf.shi/career/study/2026/codex/sourcecode/FlowERP` |
| 客户项目解释器 | `/Users/leaf.shi/career/study/2026/codex/sourcecode/FlowERP/.venv/bin/python` |

两个仓库各自有 `.venv`，不要互相装包，也不要把 FlowERP 克隆到 CodexFDE 里面。

## 1. 取得独立仓库代码

在**两个仓库的共同父目录**执行，不要进到 CodexFDE 里克隆：

```bash
cd /Users/leaf.shi/career/study/2026/codex/sourcecode
git clone --depth 1 https://github.com/congde/flowERP.git FlowERP
```

成功时输出形如 `Receiving objects: 100% (72/72) ... done.`，随后出现 `FlowERP/` 目录。

- `--depth 1` 只取最新一个提交，下载快、够日常运行与文件校验使用；需要完整提交历史或课程标签时再执行 `git fetch --unshallow`。
- 目标目录 `FlowERP` 必须还不存在，克隆不会覆盖已有文件。
- 也可以不手工克隆：在工作台首页「＋ 添加项目」里填 Git 链接或本地目录登记，效果相同（见第 3 节）。

## 2. 建客户项目自己的虚拟环境

在**克隆出来的 FlowERP 目录**里执行，不是 CodexFDE：

```bash
cd FlowERP
python3 -m venv .venv && .venv/bin/python -m pip install -e .
```

成功标志是最后一行 `Successfully installed flowerp-0.1.0`。中途 `Building editable for flowerp (pyproject.toml) ... done` 表示按 `pyproject.toml` 以可编辑方式装好。

- 这条命令只需跑一次；以后启动不用重装。
- 装的是 FlowERP 自己的包，与工作台的 `.venv` 是两个环境。
- 若 `python3 --version` 低于 3.10，先按 CodexFDE 手册补装 Python 3.11 再回来执行。
- 报错时看最后几行；`.venv` 残破不要直接删数据，按提示重建目录或让老师检查。

## 3. 让工作台知道客户项目在哪

工作台按 `FLOWERP_PROJECT_ROOT` 或工作台上登记的项目找到客户仓库。本机选择写入 shell 配置，长期生效：

```bash
vim ~/.zshrc
source ~/.zshrc
```

在 `~/.zshrc` 末尾加入一行（路径换成自己的克隆目录）：

```bash
export FLOWERP_PROJECT_ROOT="/Users/leaf.shi/career/study/2026/codex/sourcecode/FlowERP"
```

保存后 `source ~/.zshrc` 让当前终端立即生效；新开的终端自动生效。

- 另一种做法是在工作台首页「＋ 添加项目」登记本地目录，适合同时管理多个项目；登记多个含 `flowerp/server.py` 的项目时，仍需用本变量明确选一个。
- 变量必须指向**独立客户仓库**，指向 CodexFDE 会被拒绝。
- 该变量要在**启动工作台的同一个终端环境**里可见；用 `main.py` 启动时会继承当前环境。

## 4. 自检：确认路径和识别条件都成立

```bash
echo $FLOWERP_PROJECT_ROOT
ls "$FLOWERP_PROJECT_ROOT/flowerp/server.py"
```

期望结果：第一条打印 `/Users/leaf.shi/career/study/2026/codex/sourcecode/FlowERP`；第二条打印该文件的完整路径 `.../FlowERP/flowerp/server.py`，不报 `No such file or directory`。

工作台识别客户仓库的两个硬条件（`workbench/external_project.py`）：

1. 目录下存在 `flowerp/server.py`；
2. 目录下存在 `.venv/bin/python`（Windows 为 `.venv/Scripts/python.exe`）。

条件 2 不满足时会提示「请先在 FlowERP 仓库创建 .venv 并执行 pip install -e .」，回到第 2 节。

环境变量为空说明第 3 节没生效：确认写入的是 `~/.zshrc`（bash 用户为 `~/.bash_profile`），并已 `source`。

## 5. 检查端口占用

```bash
lsof -i:8001
lsof -i:8000
```

没有输出表示该端口空闲。有输出时注意 `PID` 列：8001 应是工作台，8000 应是 FlowERP；属于其他程序就换端口或先停止，服务不会自动抢占端口。停止用 `kill <PID>`，确认没有正在执行的事项再操作。

端口职责：8001 个人研发工作台（唯一入口 `/`）、8000 FlowERP 客户项目、8010 可选完整 Harness（跟跑不必需）。

## 6. 回到工作台仓库启动两个界面

```bash
cd ../CodexFDE
python3 main.py
```

输出 JSON 中 `workbench.state` 为 `started` / `restarted` / `reused` 即工作台可用；`flowerp.state` 为 `unavailable` 时工作台仍可访问，按提示检查第 3 节的变量与第 2 节的 `.venv`。

- 打开工作台 <http://127.0.0.1:8001/>，FlowERP <http://127.0.0.1:8000/>；需要自动开浏览器加 `--open-browser`。
- 已有服务想保留进程：`python3 main.py --reuse`（不加则核对身份后自动重启，macOS 同样支持）。
- 停止服务：`python3 main.py --stop`（可加 `workbench` / `flowerp` 只停一个），避免手工找 PID。
- 只开工作台：`python3 -X utf8 -m workbench.cli serve-workbench`。
- 需要单独前台启动客户项目排错（放在 FlowERP 目录执行）：`.venv/bin/python -X utf8 -m flowerp serve --port 8002 --runtime-dir .runtime`，8002 用来区别于仍在跑的 8000 服务，完整参数以独立仓库 README 为准。

## 7. 验收边界

- 工作台的 `environment-check`、`eval.harness --suite blocking` 是**工作台自身**检查，绿灯不等于 ERP 业务通过。
- 客户业务 Eval 在 FlowERP 仓库里用自己的解释器运行；工作台「质量检查配置」可填（路径换成自己的）：

```json
["/Users/leaf.shi/career/study/2026/codex/sourcecode/FlowERP/.venv/bin/python", "-X", "utf8", "-m", "eval.harness", "--suite", "blocking", "--report-path", "{report_path}"]
```

- 已有业务数据时不要重新初始化、删除数据库或改用空目录启动。
- 运行数据库、备份、日志只留本机，不提交 Git。

## 常见错误

| 现象 | 原因与处理 |
|---|---|
| `请先在 FlowERP 仓库创建 .venv 并执行 pip install -e .` | 客户仓库 `.venv/bin/python` 不存在，回到第 2 节 |
| `请在工作台添加独立 FlowERP 仓库，或设置 FLOWERP_PROJECT_ROOT` | 变量未生效，或工作台登记了 0 个 / 多个候选项目 |
| `FlowERP 必须指向独立客户仓库，不能指向 CodexFDE 工作台` | 变量指向了 CodexFDE，改成 FlowERP 目录 |
| 启动后 `flowerp: unavailable` | 查变量与 `.venv`；工作台本身仍可在 8001 使用 |
| 页面显示“创建您的工作空间”或历史为空 | 运行目录切错了，核对 `--runtime-dir` 与数据库，不要立即新建账号 |
| `lsof` 显示端口被占用 | 确认 PID 归属，停掉不再需要的进程或换端口；不要强制抢占 |
