# RepoScout 星探

> GitHub 机会雷达 —— 每天 10 分钟，从 Trending 里筛出「1–4 周可复刻、且存在商业空白」的项目。

RepoScout 自动抓取 GitHub Trending，补全仓库元数据与星标历史，计算增长趋势，评估复刻难度和商业价值，并通过本地 Web 应用给出排序后的候选清单与证据链。

## 功能概览

- **数据采集**：Trending daily/weekly/monthly 抓取、GitHub 元数据补全、30 周星标历史、6 小时快照、仓库分类与黑名单。
- **增长指标**：Δ24h/7d/30d、增速、加速率、3σ 异常与单日爆发检测、TrendScore。
- **可复刻性评估**：代码体量、依赖数量、外部服务、工程结构、License 风险、技术栈匹配与复刻周期估算；支持浅分析（API）与深分析（浅克隆 + cloc）。
- **商业价值评估**：issues 付费意愿、商业化空白、竞品数量、受众支付意愿、差异化缺口与 BizScore。
- **决策界面**：G1–G7 硬闸门、TotalScore 排序、筛选/分页、详情页、收藏、反馈、CSV 导出，以及可视化的采集与分析流程面板。

## 目录结构

```text
repo-scout/
├── pyproject.toml
├── config.example.toml
├── src/repo_scout/
│   ├── cli.py           # 命令行入口
│   ├── pipeline.py      # 采集流水线
│   ├── metrics.py       # 增长指标与 TrendScore
│   ├── analysis.py      # 浅分析（可复刻性）
│   ├── deep.py          # 深分析（clone + cloc）
│   ├── commercial.py    # 商业价值分析
│   ├── scoring.py       # 硬闸门、总分与详情组装
│   ├── web.py           # 本地 Web 服务与 JSON API
│   ├── jobs.py          # Web 后台任务队列
│   ├── web/             # 单页应用（HTML/CSS/JS）
│   ├── trending.py      # Trending 页面抓取与解析
│   ├── github.py        # GitHub REST 客户端与 Token 池
│   ├── star_history.py  # 星标历史解析
│   ├── classify.py      # 分类与黑名单
│   ├── db.py            # SQLite 持久层
│   └── config.py        # TOML + 环境变量配置
└── tests/               # 离线测试与 fixtures
```

## 快速开始

要求 Python ≥ 3.11。

### 1. 安装

```bash
cd repo-scout
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

python -m pip install -e ".[dev]"
cp config.example.toml config.toml        # Windows: copy config.example.toml config.toml
```

Windows 控制台建议先设置 UTF-8，避免中文乱码：

```bash
set PYTHONUTF8=1          # CMD
export PYTHONUTF8=1       # Git Bash
```

### 2. 配置 GitHub Token（推荐）

优先使用环境变量，避免写入文件：

```bash
export REPO_SCOUT_GITHUB_TOKENS=github_pat_xxx
```

也可以写入本地 `config.toml`（该文件已被 `.gitignore` 忽略）：

```toml
[github]
tokens = ["github_pat_xxx"]
```

细粒度 PAT 至少需要 **Metadata: read**；多个 Token 用逗号分隔。未配置 Token 也能运行，但匿名仅 60 次/小时，容易限流。

### 3. 初始化并运行

```bash
python -m repo_scout --config config.toml init-db
python -m repo_scout --config config.toml ingest
python -m repo_scout --config config.toml metrics --top 20
python -m repo_scout --config config.toml analyze --limit 100 --top 20
python -m repo_scout --config config.toml biz-analyze --limit 100 --top 20
python -m repo_scout --config config.toml serve --port 8765 --open
```

打开 <http://127.0.0.1:8765/> 即可查看结果；页面顶部的「采集与分析流程」面板也可以直接运行各阶段任务。

> `--config` 是全局参数，必须放在 `ingest` / `analyze` / `serve` 等子命令之前。
> 服务只监听本机、默认无鉴权，请不要直接暴露到公网。

如果 `ingest` 抓取 `github.com/trending` 超时，可先设置代理：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
```

## 常用命令

| 命令 | 说明 |
| --- | --- |
| `init-db` | 初始化 SQLite 数据库 |
| `ingest` | 抓取 Trending、补全元数据、回填星标历史 |
| `add <owner/repo>` | 手动添加仓库 |
| `star-history <owner/repo>` | 查询单个仓库的星标历史 |
| `status` | 查看数据库计数与最近运行状态 |
| `metrics` | 计算增长指标与 TrendScore |
| `analyze` | 浅分析：评估可复刻性 |
| `deep-analyze` | 深分析：浅克隆 + cloc，需先有分析结果 |
| `biz-analyze` | 商业价值分析，需先有分析结果 |
| `serve` | 启动本地 Web 应用 |

各命令支持 `--limit`、`--top`、`--force` 等参数，完整用法见 `python -m repo_scout <command> --help`。

## 配置

所有配置项见 `config.example.toml`。配置文件的修改不会自动生效，运行时需显式传入：

```bash
python -m repo_scout --config config.toml <command>
```

环境变量优先级高于配置文件：

- `REPO_SCOUT_GITHUB_TOKENS`（兼容 `GITHUB_TOKENS`）
- `REPO_SCOUT_DB_PATH`

## 测试

```bash
python -m pytest -q
```

测试默认完全离线（Trending 使用本地 fixtures，GitHub API 由 FakeTransport 模拟）。可选联网测试：

```bash
REPO_SCOUT_ONLINE=1 python -m pytest tests/test_online.py -v
```

## 数据与安全

- `config.toml`、`data/` 已被 `.gitignore` 忽略，不会提交到仓库。
- 不要把真实 Token 写进 README、`config.example.toml` 或提交历史；一旦泄露，立即到 GitHub → Settings → Developer settings → Personal access tokens 吊销重签。
- 数据库只保存元数据与聚合计数，不保存仓库源码。

## 合规提示

- 优先使用 GitHub 官方 API；Trending 页面仅低频抓取，并设置可识别的 User-Agent。
- License 信息仅作为风险提示的原始数据，不构成法律意见。
