# RepoScout 星探

> GitHub 机会雷达 —— 每天 10 分钟，从 Trending 里筛出「1–4 周可复刻、且存在商业空白」的项目。

本仓库已实现 [PRD](../PRD-RepoScout.md) 的 **M1 数据采集层**、**M2 增长趋势计算层**、
**M3 可复刻性评估层（浅分析 + 深分析）**、**M4 商业价值评估层** 与 **M5 Web 核心页面**。
当前版本负责采集原始数据、计算增长与趋势分、评估复刻性与商业价值，并通过本地 Web
应用提供列表、筛选、详情、收藏、反馈与 CSV 导出。

## 已实现范围

| PRD 编号 | 功能 | 状态 |
| --- | --- | --- |
| FR-101 | Trending daily/weekly/monthly HTML 抓取与解析 | ✅ |
| FR-102 | GitHub REST 元数据补全（stars/license/topics/语言/时间等） | ✅ |
| FR-103 | 官方星标历史（周级聚合 + 30 周回填） | ✅ 实测接入 |
| FR-104 | Token 池、配额水位切换、403/429 冷却 | ✅ 基础版 |
| FR-105 | 6 小时增量快照（stars/forks/issues） | ✅ |
| FR-106 | 镜像/归档/清单/教程/模板/纯文档分类与黑名单 | ✅ |
| FR-107 | 解析零结果告警、5xx 重试、失败不覆盖旧数据 | ✅ |
| FR-108 | 手动添加仓库 | ✅ `add` 命令 |
| FR-109 | 采集运行记录与状态查询 | ✅ `ingest_runs` + `status` |
| FR-201 | Δ24h/7d/30d、增速、加速率、Stars/Contributor | ✅ |
| FR-202 | 3σ 异常波动 + 单日爆发检测 | ✅ 快照满 7 天后生效 |
| FR-203 | TrendScore（候选池百分位加权） | ✅ |
| FR-204/205 | 时间衰减 / 排序预设 | ✅ `metrics --sort` |
| FR-206 | 指标来源、置信度、数据不足标记 | ✅ |
| FR-301 | 代码体量（languages API 估算 LoC） | ✅ |
| FR-302 | 根清单依赖数量（8 种清单） | ✅ |
| FR-303 | 外部服务依赖关键词识别 | ✅ |
| FR-304 | 工程结构（Git Trees：src/CI/monorepo/Docker） | ✅ |
| FR-305 | 技术栈匹配与高门槛语言封顶 | ✅ `[repro].tech_stack` |
| FR-306 | License 风险分级（绿/黄/红/灰） | ✅ |
| FR-307 | 复杂度等级 A–F | ✅ 关键字段缺失加 `*` |
| FR-308 | 浅分析全量 / 深分析 | ✅ 浅分析全量 + Top N 深分析（cloc） |
| FR-401 | issues 付费意愿信号 | ✅ REST 最近 100 条 open issues |
| FR-402 | 商业化空白检测 | ✅ README / homepage / FUNDING |
| FR-403 | 竞品数量（Search API） | ✅ 7 天缓存 + 每日预算 |
| FR-404 | 受众支付意愿（topics 权重） | ✅ 附录 B |
| FR-405 | 差异化缺口与变现路径 | ✅ |
| FR-406 | BizScore 与证据链 | ✅ |
| FR-501 | G1–G7 硬闸门 | ✅ 列表默认隐藏 GPL / 巨石 / 低星 |
| FR-502 | TotalScore（α 预设） | ✅ 平衡/稳/赚/小项目 |
| FR-503 | 列表视图（排序/筛选/分页） | ✅ Web 页面（卡片 + 表格双视图） |
| FR-504 | 详情页（增长/复刻/商业/风险/操作） | ✅ Web 页面 |
| M5 控制台 | 采集/指标/分析/商业任务可视化运行 | ✅ 流程面板 + 后台任务队列 |
| FR-505 | 关注与窗口期参考值 | ✅ 收藏/状态/备注 |
| FR-506 | CSV 导出 | ✅ CSV；⏳ Markdown/Notion/飞书 |
| FR-509 | 有用/没用反馈 | ✅ 写入本地 `feedback` |
| FR-510 | 数据新鲜度与空态 | ✅ 顶部状态条 + 分类空态 |

**未实现（后续里程碑）**：日报推送（FR-507）、告警（FR-508）、Notion/飞书导出与反馈统计报表。

## 目录结构

```
repo-scout/
├── pyproject.toml
├── config.example.toml
├── src/repo_scout/
│   ├── cli.py           # init-db / ingest / add / star-history / status / metrics / analyze / deep-analyze / biz-analyze / serve
│   ├── pipeline.py      # 采集流水线编排
│   ├── metrics.py       # M2 增长指标、异常检测与 TrendScore
│   ├── analysis.py      # M3 可复刻性浅分析（LoC/依赖/结构/License）
│   ├── deep.py          # M3 深分析（浅克隆 + cloc）
│   ├── commercial.py    # M4 商业价值信号与 BizScore
│   ├── scoring.py       # M5 硬闸门、总分、筛选与详情数据组装
│   ├── web.py           # M5 本地 Web 服务与 JSON API
│   ├── jobs.py          # M5 后台任务队列（采集/分析/评分）
│   ├── web/             # M5 单页应用（HTML/CSS/JS）
│   ├── trending.py      # FR-101 HTML 解析与抓取
│   ├── github.py        # FR-102/103/104 REST 客户端 + Token 池
│   ├── star_history.py  # FR-103 星标历史解析/分页/累计换算
│   ├── classify.py      # FR-106 分类与黑名单
│   ├── db.py            # SQLite 持久层
│   ├── config.py        # TOML + 环境变量配置
│   ├── http.py          # HTTP 抽象（httpx 生产实现 / Fake 测试注入）
│   ├── models.py        # 领域模型
│   └── util.py          # 时间桶等工具
└── tests/
    ├── fixtures/        # 离线 HTML 样例
    ├── support.py       # FakeTransport / payload 构造器
    └── test_*.py        # 138 个离线用例 + 2 个可选联网用例
```

## 如何正确运行（推荐顺序）

> 最短路径：配置 Token → `init-db` → `ingest` → `metrics` → `analyze` → `biz-analyze` → `serve`。
>
> 在仓库根目录执行。Windows 建议先设置 UTF-8，避免中文提示乱码：
> `set PYTHONUTF8=1`（CMD）或 `export PYTHONUTF8=1`（Git Bash）。
>
> **所有命令都显式带 `--config config.toml`**。`--config` 是全局参数，必须放在
> `ingest` / `analyze` / `serve` 等子命令之前；不传时使用代码内默认配置。

### 0. 环境准备

要求 Python ≥ 3.11（本工程在 3.13 验证）。

```bash
cd repo-scout
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
copy config.example.toml config.toml
```

macOS / Linux 使用：

```bash
cd repo-scout
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp config.example.toml config.toml
```

检查安装：

```bash
python -m repo_scout --help
python -m pytest -q          # 期望：138 passed, 2 skipped
```

### 1. 配置 GitHub Token

优先使用环境变量（不会写入文件）：

```bash
# CMD
set REPO_SCOUT_GITHUB_TOKENS=github_pat_xxx
# Git Bash / macOS / Linux
export REPO_SCOUT_GITHUB_TOKENS=github_pat_xxx
```

也可以写入本地 `config.toml`（已被 `.gitignore` 忽略）：

```toml
[github]
tokens = ["github_pat_xxx"]
```

- 细粒度 PAT 至少需要 **Metadata: read**。
- 多个 Token 用逗号分隔，配额低于 `low_watermark` 时自动轮换。
- 未配置 Token 也能运行，但匿名只有 **60 次/小时**，M3/M4 很容易限流。

> 不要把真实 Token 写进 README、`config.example.toml` 或提交历史；泄露后立即在
> GitHub → Settings → Developer settings → Personal access tokens 吊销重签。

### 2. 初始化数据库

```bash
python -m repo_scout --config config.toml init-db
python -m repo_scout --config config.toml status
```

默认数据库是 `data/repo_scout.sqlite3`。`status` 会返回 `repos`、`snapshots`、
`star_weeks`、`metrics`、`analysis`、`commercial` 等计数。

### 3. 最小网络验证（只访问 `api.github.com`）

```bash
# 只发 1 次请求，不写库
python -m repo_scout --config config.toml star-history octocat/Hello-World --weeks 4

# 验证写库链路
python -m repo_scout --config config.toml add octocat/Hello-World --no-star-history
python -m repo_scout --config config.toml status
```

- 退出码 `0` 表示 Token 与 API 正常；`403/429` 先检查 Token 或配额。
- 这一步不依赖 GitHub Trending 网页，失败时应先解决 Token / 网络 / 代理问题。

### 4. 完整采集（需要 `github.com` 网页可达）

先确认 Trending 网页可访问：

```bash
curl -sS -o /dev/null -w "%{http_code}\n" --max-time 20 https://github.com/trending
```

返回 `200` 后执行：

```bash
# 先跑最小流水线：只抓 daily，跳过元数据与星标历史
python -m repo_scout --config config.toml ingest --period daily --no-enrich --no-star-history

# 再跑完整采集：元数据补全 + 星标历史
python -m repo_scout --config config.toml ingest
```

如果 `trending` 超时（本机实测过 `api.github.com` 正常、`github.com` 网页超时）：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
```

或把 `config.toml` 的 `github.trending_base_url` 改成可访问镜像。退出码：
`0` 成功、`2` 部分成功（旧数据保留）、`1` 失败。

### 5. 计算指标与评估

按依赖顺序执行：

```bash
# M2：纯本地计算，不消耗配额；ingest/add 后也会自动执行
python -m repo_scout --config config.toml metrics --top 20

# M3 浅分析：每仓库约 4–8 次 API 请求
python -m repo_scout --config config.toml analyze --limit 100 --top 20

# M3 深分析：浅克隆 + cloc，需先有 analyze 结果
python -m repo_scout --config config.toml deep-analyze --limit 20 --top 10

# M4 商业分析：issues + README + Search API，需先有 analyze 结果
python -m repo_scout --config config.toml biz-analyze --limit 100 --top 20
```

- 深分析会自动定位 cloc；本机没有时从 `AlDanial/cloc` 下载 Perl 脚本到
  `data/tools/cloc.pl`（需要 Perl）。仍不可用时退化为内置行数统计。
- `git clone` 失败会自动回退到 codeload tarball；超过 `deep_max_size_mb` 的仓库
  会标记 `too_large`。网络较慢时把 `[repro].deep_max_size_mb` 调到 50、
  `deep_timeout_seconds` 调到 180。
- 商业分析只对 ReproScore ≥ `competition_min_repro`（默认 60）查竞品，7 天缓存、
  每日预算 50。配额紧张时加 `--no-competition`。
- 任意阶段可加 `--force` 忽略缓存重新计算。
- 这些步骤也可以不敲命令：启动 Web 后，在「采集与分析流程」面板中逐项点击运行；
  任务在后台执行，页面会自动刷新进度和结果。

### 6. 启动 Web 应用

```bash
python -m repo_scout --config config.toml serve --port 8765 --open
```

打开 <http://127.0.0.1:8765/>。服务只监听本机、默认无鉴权，请不要直接暴露到公网。
页面包含：

- **流程控制台**：采集、M2、M3 浅分析、M3 深分析、M4 商业分析五个阶段，显示状态、
  数量、更新时间，可单步运行或一键运行全流程；
- **卡片视图**（默认）：每个项目展示简介、stars、语言、Δ7d、增速、竞品、商业化状态、
  复刻/商业/总分、License 与风险，直接回答“它是干什么的”；
- **表格视图**：高密度扫描，可点击列头排序；
- 今日 Top 5、详情页、筛选、收藏、反馈和 CSV 导出。

### 7. 验证最终状态

```bash
python -m repo_scout --config config.toml status
```

正常情况下应看到：

- `repos` 非 0；
- `metrics`、`analysis`、`commercial` 覆盖大部分有效仓库；
- `deep_analysis` 是成功完成深分析的仓库数；
- `last_run.status` 为 `ok` 或 `partial`。

### 常见失败对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `config file not found` | `--config` 路径不对 | 确认在仓库根目录执行，或传绝对路径 |
| 配置修改不生效 | `--config` 放在子命令之后 / 未传 | 使用 `python -m repo_scout --config config.toml <子命令>` |
| `401/403/429` | Token 无效、权限不足或配额耗尽 | 检查 PAT 的 Metadata: read、换 Token、降低 limit |
| `ingest` 报 `timed out` | `github.com/trending` 不可达 | 设置 `HTTPS_PROXY` / 使用镜像；API 类命令不受影响 |
| `analyze` 竞赛品数为 `null` | ReproScore <60、无 topic/描述或预算用尽 | 正常降级；查看 `low_confidence_fields` |
| `deep-analyze` 很慢 | 仓库过大或网络慢 | 调低 `deep_max_size_mb` / `deep_limit` / `deep_timeout_seconds` |
| Web 页面空白 | 还没采集/评分，或端口被占用 | 先跑 ingest/analyze/biz-analyze；换 `--port` |
| 中文乱码 | Windows GBK 控制台 | `set PYTHONUTF8=1` 或 `chcp 65001` |

## 详细配置与单步说明

> 首次运行只需按上面的「如何正确运行」步骤 0–7 操作；本节是逐项展开与最小验证清单。

要求 Python ≥ 3.11（本工程在 3.13 上验证）。

```bash
cd repo-scout
python -m venv .venv
.venv\Scripts\activate          # Windows；macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"

copy config.example.toml config.toml   # Windows
# cp config.example.toml config.toml   # macOS/Linux
```

### 配置 GitHub Token

建议通过环境变量注入（环境变量优先级最高，且不会写进任何文件）：

```bash
# Windows CMD
set REPO_SCOUT_GITHUB_TOKENS=github_pat_xxx
# Git Bash / macOS / Linux
export REPO_SCOUT_GITHUB_TOKENS=github_pat_xxx
```

也可以写入本地 `config.toml`（该文件已被 `.gitignore` 忽略，不会提交）：

```toml
[github]
tokens = ["github_pat_xxx"]
```

- 细粒度 PAT 至少需要 **Metadata: read**（读取公开仓库元数据与星标历史）。
- 多个 Token 用逗号分隔，配额低于 `low_watermark` 时自动轮换。
- 未配置 Token 也能运行，但匿名配额只有 **60 次/小时**，建议配置。

> **安全提示**：不要把真实 Token 写进 README、`config.example.toml` 或提交历史。
> 一旦泄露，立即到 GitHub →
> Settings → Developer settings → Personal access tokens 中吊销并重新签发。

### 初始化并运行

**注意：`config.toml` 不会被自动加载。** 必须通过全局参数 `--config` 显式传入，
且该参数要放在子命令之前：

```bash
python -m repo_scout init-db                                  # 使用代码内默认配置
python -m repo_scout --config config.toml ingest              # 完整采集
python -m repo_scout --config config.toml status              # 查看数据库与最近一次运行
```

不传 `--config` 时使用内置默认值（与 `config.example.toml` 一致，数据库同为
`data/repo_scout.sqlite3`）；环境变量（`REPO_SCOUT_*`）始终优先。

### 最小请求验证

按「API 只读 → 写库 → 最小流水线」三步验证，便于快速定位环境问题：

```bash
# 1) 只发 1 次 GitHub API 请求，不写库
python -m repo_scout --config config.toml star-history octocat/Hello-World --weeks 4

# 2) 写库链路（元数据补全 + 分类 + 快照）
python -m repo_scout --config config.toml add octocat/Hello-World --no-star-history
python -m repo_scout --config config.toml status

# 3) 最小采集流水线：只抓 daily，跳过元数据补全与星标历史
python -m repo_scout --config config.toml ingest --period daily --no-enrich --no-star-history

# 4) 计算 M2 增长指标与 TrendScore
python -m repo_scout --config config.toml metrics --top 10

# 5) M3 浅分析（联网，每个仓库约 4–8 次 API 请求）
python -m repo_scout --config config.toml analyze --limit 5 --top 5

# 6) M3 深分析（浅克隆 + cloc，需先完成浅分析）
python -m repo_scout --config config.toml deep-analyze --limit 3 --top 3

# 7) M4 商业价值分析（issues / README / Search API）
python -m repo_scout --config config.toml biz-analyze --limit 5 --top 5

# 8) 打开 M5 Web 应用
python -m repo_scout --config config.toml serve --port 8765
```

第 3 步成功后再执行完整采集：

```bash
python -m repo_scout --config config.toml ingest
```

手动添加一个仓库：

```bash
python -m repo_scout --config config.toml add https://github.com/acme/notes-ai
```

直接查询某个仓库的星标历史（联网，GitHub 官方接口）：

```bash
python -m repo_scout --config config.toml star-history octocat/Hello-World --weeks 30
```

`ingest` / `add` 默认会回填星标历史；如只想采集榜单元数据，可加 `--no-star-history`。

退出码：`0` 成功、`2` 部分成功（某个数据源失败，旧数据保留）、`1` 失败。

## M2 增长指标与 TrendScore

每次 `ingest` / `add` 结束后会自动基于本地数据重算 M2 指标（纯本地计算，不消耗 API 配额），
也可随时手动重算：

```bash
python -m repo_scout --config config.toml metrics --top 20
python -m repo_scout --config config.toml metrics --sort delta7 --top 0   # 全部
python -m repo_scout --config config.toml metrics --no-write              # 只预览，不写库
```

排序：`--sort trend`（默认，TrendScore）、`delta7`、`velocity`、`stars`；`--top 0` 输出全部。

### 指标口径与降级

| 指标 | 数据来源与口径 |
| --- | --- |
| ΔStars_24h | 优先当日 Trending `stars today`；缺失时用 24h 快照，范围不足不外推 |
| ΔStars_7d / 30d | 优先官方 `star_weekly` 周级累计曲线线性插值；缺失时用快照 |
| Velocity_7d | `ΔStars_7d / 7` |
| Velocity_prev | `(stars(t−7d) − stars(t−14d)) / 7` |
| Acceleration | `(Velocity_7d − Velocity_prev) / max(Velocity_prev, 5)`；上周新增 < 20 星时置空 |
| 3σ 异常 / 单日爆发 | 用近 30 天（≥7 天）快照日增量；负增量不进入基线；单日占比 ≥70% 标记爆发 |
| Stars/Contributor | `stars / max(contributors, 1)`，>10,000 标记刷星嫌疑 |
| TrendScore | `0.40×P(Δ7d) + 0.25×P(Δ24h) + 0.20×P(Δ30d) + 0.15×P(Acceleration)`，候选池内百分位；缺失按 50 分位；异常/爆发 ×0.6 |

`metrics` 输出中的 `coverage` 展示各指标可用仓库数，`history_confidence` 区分
`high`（7d/30d 均来自官方历史）/ `medium` / `low`。数据不足时指标为 `null`，不会伪造成 0。
快照不足 7 天时 `anomaly_available=false`，异常检测自动禁用。

## M3 可复刻性分析（浅分析）

通过 GitHub REST API 的 `languages` / `git trees` / `contents` 接口分析，不克隆仓库：

```bash
python -m repo_scout --config config.toml analyze --limit 50 --top 20
python -m repo_scout --config config.toml analyze --force --top 0 --sort grade
python -m repo_scout --config config.toml analyze --limit 5          # 只分析 5 个
```

- 每个仓库约 **4–8 次请求**；同一仓库默认 24 小时内跳过（`[repro].refresh_hours`）。
- 结果写入 `repo_analysis` 表，`status` 中可见 `analysis` 数量与最近时间。
- `--sort repro`（默认）/ `stars` / `grade`；`--force` 忽略缓存。

### 子项、权重与降级

| 子项 | 权重 | 数据来源 / 分档 |
| --- | --- | --- |
| `s_loc` | 25% | `Σ languages bytes / 35`；<5k→100，<10k→85，<20k→75，<50k→55，<100k→25，>100k→5；未知 60 |
| `s_dep` | 15% | 根清单运行时依赖数；≤20→100，≤50→75，≤100→50，>100→20；未知 60 |
| `s_ext` | 10% | README/清单关键词命中数；0→100，1–2→80，3–4→55，≥5→25；未知 60 |
| `s_lang` | 20% | 命中技能栈 100；邻近语言 80；Python/JS/TS/Go 未命中 55；Rust/C++ 等高门槛未命中 20；未知 50 |
| `s_license` | 20% | 绿 100 / 黄 55 / 红 0 / 无 License 20 / 其他 50 |
| `s_struct` | 10% | 100 起扣：monorepo −30、CI workflows ≥5 −15、compose 服务 ≥5 −10、顶层目录 >20 −10 |

```text
ReproScore = 0.25·s_loc + 0.15·s_dep + 0.10·s_ext
           + 0.20·s_lang + 0.20·s_license + 0.10·s_struct
```

- 缺失字段按中性 **60** 分，并写入 `low_confidence_fields`；`src_files` 只统计 `src/` 目录，
  扁平布局仓库为 0 并不代表没有代码。
- 高门槛语言（Rust/C++/Scala/Haskell/Zig）未命中技能栈时，ReproScore **上限 40**。
- 复杂度：`100 − (0.40·s_loc + 0.30·s_dep + 0.20·s_ext + 0.10·s_struct)`，映射 A–F；
  LoC 或依赖未知时等级带 `*`。
- 复刻周期按 LoC 基数 + 外部服务（每个 +0.5 周，最多 +2）+ monorepo +0.5、
  高门槛语言 ×1.5 估算，输出区间。
- License 仅做风险提示，不构成法律意见；红标（GPL/AGPL）将在 M5 默认隐藏。

### 配置

```toml
[repro]
tech_stack = ["python", "typescript", "javascript", "go"]
analysis_limit = 100
refresh_hours = 24
fetch_readme = true
```

`tech_stack` 直接用语言名（小写）；`node`/`js`、`ts`、`golang`、`c++`、`c#` 等别名会自动归一化。

### M3 深分析（浅克隆 + cloc）

在浅分析之后运行（需要已有 `repo_analysis` 记录）：

```bash
python -m repo_scout --config config.toml deep-analyze --limit 20 --top 10
python -m repo_scout --config config.toml deep-analyze --force --top 0
python -m repo_scout --config config.toml deep-analyze --limit 1 --keep   # 调试：保留克隆目录
```

流程：

- 按 ReproScore 取 Top N（`deep_limit`，默认 20），同一仓库 7 天内不重复分析（`deep_refresh_hours`）。
- 源码获取：`deep_source="auto"` 时先 `git clone --depth 1`，失败后自动回退到
  `codeload.github.com` 的 tarball；也可强制 `clone` 或 `tarball`。
- GitHub 元数据 `size_kb` 超过 `deep_max_size_mb`（默认 200MB）直接跳过并标记 `too_large`。
- cloc 查找顺序：`cloc_path` → PATH → `data/tools/cloc.pl` 缓存 → 从
  `AlDanial/cloc` 自动下载（需要 Perl）。都不可用时退化为内置行数统计，
  `loc_method=line_count`、置信度 `medium`。
- cloc 结果覆盖估算值：`loc_method=cloc`、`loc_confidence=high`，并重算
  `s_loc` / ReproScore / 复杂度 / 复刻周期。
- **只统计代码语言**：Markdown / JSON / YAML / TOML / INI / CSV / XML / Text 等
  文档与配置不计入 LoC；原始 cloc 总和保存在 `score_breakdown.inputs.cloc_total`。
- 克隆目录无论成功失败都会立即删除；失败只记录 `deep_error`，不覆盖已有浅分析结果。

## M4 商业价值分析

在 M3 浅/深分析之后运行（需要已有 `repo_analysis`）：

```bash
python -m repo_scout --config config.toml biz-analyze --limit 50 --top 20
python -m repo_scout --config config.toml biz-analyze --force --top 0 --sort market
python -m repo_scout --config config.toml biz-analyze --no-competition   # 跳过 Search API
```

每个仓库约 3–4 次请求（README、FUNDING、issues、可选 Search），结果写入
`repo_commercial`，`status` 中可见 `commercial` 数量与时间。

### 信号与评分

| 子项 | 权重 | 计算 |
| --- | --- | --- |
| `market_alignment` 市场验证 | 30% | `0.5 × P(ΔStars_7d) + 0.5 × paid_intent_score` |
| `whitespace` 商业化空白 | 25% | `none` 90 / `possible` 60 / `likely` 20 |
| `competition` 竞品稀缺度 | 20% | 0→100，1–4→85，5–10→65，11–20→45，>20→20，未知 50 |
| `audience` 受众支付意愿 | 15% | topics 权重表最高分（附录 B），无命中 50 |
| `differentiation` 差异化空间 | 10% | `20 + 25 × 缺口数`，上限 100 |

```text
BizScore = 0.30·market_alignment + 0.25·whitespace
         + 0.20·competition + 0.15·audience + 0.10·differentiation
```

- **付费意愿（FR-401）**：最近 100 条 open issues 匹配中英文关键词（would pay /
  愿意付费 / 付费版 / pricing / take my money 等）；命中 0→40，1–2→70，3–5→85，
  ≥6→100；保留前 3 条 issue 链接与命中词。
- **商业化空白（FR-402）**：README、homepage、`.github/FUNDING.yml` 中的
  pricing / pro plan / book a demo / waitlist / sponsor / hosted / SaaS 等信号；
  2 个以上强信号判 `likely`；1 个强信号、赞助/资助、或 2 个弱信号判 `possible`；
  仅有一个普通 homepage 不算商业化。赞助按钮只算 possible。
- **竞品（FR-403）**：`topic:<核心topic> language:<语言> stars:>100 fork:false`
  并排除 awesome/awesome-list；结果数减 1 得到竞品数。查询缓存 7 天，每日最多
  `competition_daily_budget` 个仓库，只对 ReproScore ≥ `competition_min_repro` 的仓库执行，
  查询间隔默认 2 秒（Search API 30 次/分钟）。
- **受众（FR-404）**：topics 命中 `b2b/enterprise/saas` 100、`seo/marketing` 95、
  `productivity/automation` 90、`chrome-extension/plugin` 85……无命中 50。
- **差异化（FR-405）**：检测无 i18n、无移动端、仅 CLI、可自托管但无商业化、
  疑似商业化但无自托管、无 API、仅 Web 等缺口，生成变现路径。
- 所有子分、输入、证据、置信度写入 `score_breakdown` / `low_confidence_fields`；
  数据缺失不会伪造为 0。

## M5 Web 应用

启动本地应用（读写本地 SQLite；采集/分析任务在服务进程内后台执行）：

```bash
python -m repo_scout --config config.toml serve --port 8765
# 打开 http://127.0.0.1:8765/
```

- **流程控制台**：五个阶段（采集 / M2 指标 / M3 浅分析 / M3 深分析 / M4 商业分析）
  展示状态、数量与更新时间；支持单步运行、一键全流程、强制刷新、浅/深/商业上限、
  是否查询竞品。任务串行执行，进度与日志实时显示，完成后自动刷新列表。
- **双视图**：默认卡片视图，每张卡片展示项目简介、stars、语言、Δ7d、增速、竞品、
  商业化状态、复刻/商业/总分、License、风险与一句话结论；可切换表格视图做高密度扫描。
- **列表与详情**：今日 Top 5、8 个排序预设、完整筛选抽屉、明暗主题、CSV 导出；
  详情包含 30 周星标曲线、日增量柱状图、复刻报告、商业证据、风险清单、关注/反馈/复制 clone。
- **任务模型**：单进程单 worker 串行执行；任务状态保存在内存（服务重启后历史任务消失），
  业务结果仍写入 SQLite。运行期间请保持 `serve` 进程不关闭。
- **数据接口**：
  - 读：`/api/repos`、`/api/repos/{id}`、`/api/daily`、`/api/pipeline`、`/api/jobs`、`/api/jobs/{id}`；
  - 写：`POST /api/jobs`、`POST /api/watch`、`POST /api/feedback`；
  - 导出：`/api/export.csv`。
- 仅监听本机，默认无鉴权。
- 视觉系统见根目录 `PRODUCT.md` 与 `DESIGN.md`；页面资源位于 `src/repo_scout/web/`。

**G1–G7 闸门与总分**：

| 闸门 | 行为 |
| --- | --- |
| G1/G2 镜像/归档/清单/教程 | 排除，不进入候选池 |
| G3 GPL/AGPL | 默认隐藏，可开关显示（红标） |
| G4 无 License | 显示，⚪ 标注 |
| G5 巨石（LoC ≥ 50k） | 默认隐藏，可开关显示 |
| G6 星数 < 500 | 默认隐藏，可在筛选里调整 |
| G7 刷星嫌疑 | 显示风险标，不自动排除 |

```text
TotalScore = α × ReproScore + (1 − α) × BizScore
平衡 α=0.5 · 稳 α=0.7 · 赚 α=0.3 · 小项目优先 α=0.6（≤20k 星且 ≤20k 行）
```

## 配置说明

见 `config.example.toml`。关键项：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `http.min_trending_items` | 8 | 单个 Trending 窗口解析条目低于此值视为页面结构异常（实测每窗约 12–15 条） |
| `ingest.enrich_limit` | 200 | 单次运行最多补全多少个仓库的元数据（控制配额） |
| `ingest.snapshot_limit` | 500 | 单次最多写多少条快照 |
| `ingest.snapshot_bucket_hours` | 6 | 快照桶大小，必须整除 24 |
| `ingest.fetch_star_history` | true | 是否回填星标历史（FR-103） |
| `ingest.star_history_limit` | 200 | 每次运行最多回填多少个仓库的星标历史 |
| `ingest.star_history_weeks` | 30 | 每个仓库回溯多少周（上限 3000） |
| `github.low_watermark` | 100 | 剩余配额低于该值就切换 Token |
| `github.fetch_contributors` | false | 是否额外请求贡献者数（每仓库多 1 次请求） |
| `repro.tech_stack` | python/typescript/javascript | 熟悉技术栈，用于语言匹配（FR-305） |
| `repro.analysis_limit` | 100 | 单次 `analyze` 最多分析多少个仓库 |
| `repro.refresh_hours` | 24 | 浅分析缓存时间，未过期则跳过 |
| `repro.fetch_readme` | true | 是否抓取 README（每仓库多 1 次请求） |
| `repro.deep_limit` | 20 | 单次深分析最多克隆多少个仓库 |
| `repro.deep_refresh_hours` | 168 | 深分析缓存时间（周级） |
| `repro.deep_max_size_mb` | 200 | 超过该体积直接跳过克隆 |
| `repro.deep_timeout_seconds` | 600 | git clone / cloc 超时 |
| `repro.deep_source` | auto | 源码获取：auto（clone → tarball）/ clone / tarball |
| `repro.cloc_path` | 空 | cloc 可执行文件或 `.pl` 脚本路径 |
| `repro.cloc_auto_download` | true | 本机无 cloc 时自动下载 Perl 脚本 |
| `biz.analysis_limit` | 100 | 单次商业分析最多处理多少个仓库 |
| `biz.refresh_hours` | 168 | 商业信号缓存时间（周级） |
| `biz.issue_limit` | 100 | 付费意愿扫描的 open issues 数量（≤100） |
| `biz.competition` | true | 是否调用 Search API 统计竞品 |
| `biz.competition_daily_budget` | 50 | 每日竞品查询仓库数上限 |
| `biz.competition_min_repro` | 60 | 低于该复刻分的仓库不查竞品 |
| `biz.search_delay_seconds` | 2.0 | Search API 查询间隔（30 次/分钟） |

环境变量优先级高于配置文件：`REPO_SCOUT_DB_PATH`、`REPO_SCOUT_GITHUB_TOKENS`（兼容 `GITHUB_TOKENS`）。

配置文件不会被自动发现，运行时需显式指定：`python -m repo_scout --config config.toml ...`
（`--config` 是全局参数，必须放在 `init-db` / `ingest` / `add` / `status` 等子命令之前）。

## 测试

```bash
python -m pytest -v
```

测试完全离线：Trending 页面使用 `tests/fixtures/`，GitHub API 由注入的 `FakeTransport` 模拟，
不消耗真实配额、不依赖网络。另有 2 个可选联网测试（真实 Trending 页面 + 官方星标历史），
默认跳过：

```bash
REPO_SCOUT_ONLINE=1 python -m pytest tests/test_online.py -v
```

覆盖：

- HTML 解析（三个时间窗、实体解码、缺失字段、窗口内去重、页面结构异常）
- 失败重试（5xx 指数退避、404 不重试）
- Token 池（403 轮换、低水位轮换、冷却后恢复）
- 数据库（大小写不敏感去重、快照桶幂等、元数据写入、运行记录）
- 分类规则（awesome/tutorial/template/docs/镜像/归档/手动覆盖）
- 星标历史（解析、分页、去重、累计星数换算、404、分页参数校验）
- M2 指标（窗口插值、24h 榜单据优先与快照兜底、缺失降级、速度/加速率边界、3σ 与单日爆发、百分位评分、异常惩罚、持久化与排序）
- M3 浅分析（8 种依赖清单解析、Git Tree 结构统计、外部服务关键词、语言/许可/结构评分、复杂度与周期估算、缓存与限流中止）
- M3 深分析（cloc JSON 解析、代码语言过滤、体积上限跳过、克隆失败降级、临时目录清理、缓存）
- M4 商业信号（付费意愿、商业化置信度、受众权重、差异化缺口、竞品查询/预算、BizScore、缓存）
- M5 综合层（G1–G7 闸门、TotalScore 预设、筛选/排序/分页、窗口期、CSV、Web API、收藏与反馈）
- 端到端流水线（成功、重复运行幂等、部分失败保留旧数据、全失败、手动添加、星标回填）

## 常见问题

### `ingest` 报 `transport error: timed out` / `failed to fetch daily trending`

`ingest` 抓取的是 GitHub **网页** `https://github.com/trending`，而 `add` /
`star-history` 只访问 **API** `https://api.github.com`。两者网络可达性可能不同
（例如 API 可直连、网页被阻断）。先用 `curl` 区分：

```bash
curl -sS -o /dev/null -w "api:%{http_code} %{time_total}s\n" --max-time 15 https://api.github.com/rate_limit
curl -sS -o /dev/null -w "web:%{http_code} %{time_total}s\n" --max-time 20 https://github.com/trending
```

若网页不可达，任选其一：

- 启动代理后设置 `HTTPS_PROXY` / `HTTP_PROXY`（httpx 默认读取环境代理），再运行 `ingest`；
- 把 `config.toml` 中的 `github.trending_base_url` 指向可访问的镜像（需支持 `/trending` 路径）；
- 暂时只用 `add` / `star-history` 验证 API 功能。

### 返回 403 / 429 或提示限流

检查 Token 是否生效：带 Token 时核心配额为 5000 次/小时，匿名仅 60 次/小时。
未配置或 Token 失效时，`star-history` 会输出 `hint` 字段。可调低
`ingest.enrich_limit` / `ingest.star_history_limit` 控制单次消耗。

### Windows 控制台中文提示乱码

`.venv` 的控制台编码为 GBK。设置 `PYTHONUTF8=1`（或执行 `chcp 65001`）后再运行。

### `deep-analyze` 很慢或 `git clone` 超时

深分析需要下载仓库快照，受网络与仓库体积影响：

- 调低 `[repro].deep_max_size_mb`（例如 50）或 `deep_limit`，先分析小仓库；
- `deep_source="auto"` 会在 `git clone` 失败时自动改走 codeload tarball；
- `deep_timeout_seconds` 控制单仓库克隆/cloc 超时；超时会杀掉整个子进程树并继续下一个；
- 克隆失败只记录 `deep_error`，不会覆盖该仓库上一次成功的 cloc 结果；
- `--keep` 仅用于调试，正常运行无论成功失败都会立即删除克隆目录。

### `biz-analyze` 的竞品数为什么是 `null`

- 只有 ReproScore ≥ `competition_min_repro`（默认 60）的仓库才查询竞品；
- 仓库没有 topics 且描述为空时无法构造搜索语句；
- 当日预算（`competition_daily_budget`，默认 50）已用完；
- Search API 有独立的 30 次/分钟限制，查询间隔由 `search_delay_seconds` 控制。
  预算耗尽或查询失败时 `competition=50`，并写入 `low_confidence_fields`；
  可用 `--no-competition` 跳过竞品查询。

### `serve` 启动失败或端口被占用

`serve` 默认只监听 `127.0.0.1:8765`，不做鉴权，请勿直接暴露到公网。端口被占用时换一个：

```bash
python -m repo_scout --config config.toml serve --port 8877
```

页面数据来自本地 SQLite；如果列表为空，先运行 `ingest`、`analyze`、`biz-analyze`，
或直接使用页面顶部的「采集与分析流程」面板。

### Web 流程任务长时间运行 / 刷新页面后任务消失

流程任务在 `serve` 进程内串行执行，状态保存在内存：

- 运行期间不要关闭 `serve` 进程；刷新浏览器不会中断任务，但服务重启会丢失任务历史；
- 同一时间只执行一个任务，重复点击会排队或提示已有任务运行；
- 采集/AI 分析受网络和 GitHub 配额影响，较慢时请查看任务日志与 `status`；
- 需要中断时只能停止 `serve`（任务会被中断），已写入 SQLite 的结果不会回滚。

## FR-103 星标历史调研结论（2026-11-11 实测）

**结论：无需自研，GitHub 官方已提供免费、公开可读的星标历史接口。**

| 项 | 实测结果 |
| --- | --- |
| 端点 | `GET https://api.github.com/repos/{owner}/{repo}/stargazers/history` |
| 鉴权 | 公开仓库无需 Token；细粒度 PAT 需 `Metadata: read` |
| 配额 | 未鉴权 60 次/小时；带 Token 5000 次/小时（占用核心配额） |
| 分页 | `per_page` 默认/最大 30；`page` 最大 100，超出返回 422 |
| 响应 | `[{ "week": 1794096000, "total": 323, "days": [9,79,235,0,0,0,0] }]`，新→旧排列 |
| 语义 | `week` 为周起始 Unix 时间戳；`days` 从周日开始；无星周返回 0 |
| 边界 | 当前周是部分数据；周/日边界不保证严格 UTC |
| 改名仓库 | 301 重定向到规范仓库，httpx 自动跟随（实测 `facebook/react` 可用） |

**其他候选与放弃原因**：

- 旧 `/repos/{owner}/{repo}/stargazers` 列表接口：未鉴权返回 401（已限制为管理员/协作者），不再可用。
- `api.star-history.com/svg`：返回 SVG 图表而非 JSON，且依赖上游数据，不能作为结构化数据源。
- OSS Insight `api.ossinsight.io/v1/repos/{owner}/{repo}/stargazers/history/`：实测 500，且为月粒度，仅列为备选。
- 自研（stargazers 分页 / GH Archive）：配额与计算成本高，官方接口已满足 30 周回填需求，不采用。

**工程落地**：

- `star_history.py` 负责解析、分页与「周新增 → 周末累计星数」换算；
- `star_weekly` 表保存 `week_start / delta / stars_end / source / fetched_at`；
- 流水线按 `star_history_limit` 回填，`star_history_fetched_at` 保证 24 小时内不重复拉取；
- 官方接口失败（限流/结构变化）只标记 `partial`，不影响 Trending/元数据/快照主流程。

## 设计说明与对 PRD 的偏差

1. **主键**：Trending 阶段还没有 GitHub `repo_id`，因此 `repos` 表使用自增 `id` 作为内部主键，
   `repo_id` 在元数据补全后写入并保持唯一；快照/趋势命中以 `repo_pk` 引用内部主键。
2. **配置格式**：PRD 示例使用 YAML；为减少依赖，当前使用标准库 `tomllib` 读取 TOML。
3. **快照排序**：已接入 M2 TrendScore；`list_repos_for_snapshot()` 优先按趋势分排序，
   尚未评分的仓库退回 `stars_total` 排序。
4. **分类时点**：Trending 入库后先用名称/描述/语言做一次分类；元数据补全后再用 topics、
   fork/archived 标记复核一次（手动白名单/黑名单优先级最高）。
5. **失败语义**：单个时间窗解析失败不会删除既有数据；运行状态区分 `ok` / `partial` / `failed`，
   并写入 `ingest_runs`，`status` 命令可查看。M2 指标计算失败会标记 `partial`，不影响采集结果。
6. **星标历史**：使用官方 `/stargazers/history` 周级聚合；`stars_end` 由当前总星数按周增量倒推，
   当前周为部分数据，因此曲线用于趋势判断与冷启动回填，不当作精确的逐日历史。
   未鉴权时配额仅 60 次/小时，正式运行建议配置 Token 或调低 `star_history_limit`。
7. **M2 窗口口径**：Δ7d/Δ30d 在周级累计曲线上做线性插值，不做外推；当前周是部分数据，
   因此短窗口会有数小时级误差。快照不足 24h 时 Δ24h 不计算（除 Trending `stars today`）。
8. **异常检测**：需要至少 7 个快照日增量；M1 刚上线时 `anomaly_available=false`，
   指标显示缺失而非 0。单日爆发与 3σ 异常同样触发 TrendScore ×0.6 的风险惩罚。
9. **TrendScore 缺失值**：Δ24h/7d/30d 缺失按候选池 50 分位处理，加速率缺失按 0 参与分布，
   与 FR-203 一致，避免冷启动项目被过度惩罚。
10. **M3 分浅/深两级**：全量浅分析用 languages API 估算（`api_estimate`）；Top N 深分析
    用 `git clone --depth 1` + cloc 覆盖（`cloc`/high）。深分析只统计代码语言，
    Markdown/JSON/YAML 等文档与配置不计入 LoC，原始总和保留在 breakdown 中。
    依赖只解析根清单（8 种），monorepo 子包不展开；外部服务来自 README 与清单关键词，
    属于“提及”而非强依赖，置信度按 medium 处理。
11. **分析时不保存源码**：只按需读取 README 与清单文本，不落库源码内容；分析结果保存
    子分、输入、证据与 `score_breakdown`，可用 `scoring_version` 复算。
12. **M4 信号口径**：付费意愿使用 REST issues（最近 100 条 open issues，排除 PR），
    不使用 GraphQL；商业化检测基于关键词与 homepage/FUNDING，赞助按钮只判 `possible`；
    竞品数用 Search API 的 `total_count − 1` 近似，并排除 awesome/awesome-list，
    结果受 topic 选择与 GitHub 索引影响，详情页应展示查询语句供人工复核。
13. **M4 配额保护**：竞品查询只针对 ReproScore ≥ 60，缓存 7 天，每日预算默认 50，
    查询间隔 2 秒；预算耗尽时 `competition=50` 并写入 `low_confidence_fields`。

## 合规提示

- 优先使用 GitHub 官方 API；Trending 页面仅低频抓取（4 次/日、3 个页面），并设置可识别 User-Agent。
- 不注册小号、不使用加号邮箱等可能违反 GitHub 条款的方式获取配额。
- 数据库只保存元数据与聚合计数；不保存仓库源码。
- License 信息仅作为后续风险提示的原始数据，不构成法律意见。

## 下一步

1. 日报推送：每日 08:00 生成 Top 10（Telegram / 飞书 / 邮件）。
2. 告警：关注项加速率、竞品激增、商业化状态变化。
3. Markdown / Notion / 飞书导出与反馈统计报表。
4. 定时任务编排：ingest（每 6h）→ metrics → analyze（每日）→ deep-analyze / biz-analyze（每周）。
