/* RepoScout M5 — list + detail SPA. No external dependencies. */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  preset: "balanced",
  sort: "total",
  order: "desc",
  page: 1,
  pageSize: 50,
  filters: {
    q: "",
    language: "",
    license: "",
    paidVersion: "",
    complexity: "",
    minStars: 500,
    minDelta7: "",
    minRepro: "",
    minBiz: "",
    maxCompetitors: "",
    risk: "",
    includeGpl: false,
    includeGiant: false,
    watchedOnly: false,
  },
  data: null,
  meta: null,
  daily: null,
  detail: null,
  detailId: null,
  abort: null,
  searchTimer: null,
  toastTimer: null,
  view: localStorage.getItem("repo-scout-view") === "table" ? "table" : "cards",
  pipeline: null,
  activeJobId: null,
  jobTimer: null,
};

const DEFAULT_FILTERS = { ...state.filters };
const ICONS = {
  arrow: '<path d="M4 10h11m0 0-4-4m4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  back: '<path d="M16 10H5m0 0 4-4m-4 4 4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  star: '<path d="m10 2.8 2.2 4.5 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 8l5-.7Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
  copy: '<rect x="7" y="7" width="9" height="9" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M13 7V4.8c0-.9-.7-1.6-1.6-1.6H4.8c-.9 0-1.6.7-1.6 1.6v6.6c0 .9.7 1.6 1.6 1.6H7" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  external: '<path d="M9 4H4.8C3.8 4 3 4.8 3 5.8v9.4c0 1 .8 1.8 1.8 1.8h9.4c1 0 1.8-.8 1.8-1.8V11M12 3h5v5M17 3l-7 7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  filter: '<path d="M3 5h14M6 10h8M8 15h4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
};

// -- tiny DOM helpers -------------------------------------------------------
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function append(parent, ...children) {
  for (const child of children) {
    if (child !== null && child !== undefined) parent.appendChild(child);
  }
  return parent;
}
function icon(name, className = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");
  if (className) svg.setAttribute("class", className);
  svg.innerHTML = ICONS[name] || "";
  return svg;
}
function svg(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) {
    node.setAttribute(key, String(value));
  }
  return node;
}

// -- formatting -------------------------------------------------------------
const numberFormat = new Intl.NumberFormat("zh-CN");
function fmtInt(value) {
  return value === null || value === undefined ? "—" : numberFormat.format(value);
}
function fmtSigned(value) {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${numberFormat.format(value)}`;
}
function fmtOne(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(1);
}
function fmtScore(value) {
  return value === null || value === undefined ? "—" : Math.round(value);
}
function fmtPercent(value) {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}
function deltaClass(value) {
  if (value === null || value === undefined) return "delta-zero";
  if (value > 0) return "delta-pos";
  if (value < 0) return "delta-neg";
  return "delta-zero";
}
function fmtDateTime(iso, withTime = true) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const options = { month: "2-digit", day: "2-digit", timeZone: "Asia/Shanghai" };
  if (withTime) {
    options.hour = "2-digit";
    options.minute = "2-digit";
    options.hour12 = false;
  }
  return new Intl.DateTimeFormat("zh-CN", options).format(date);
}
function fmtShortStars(value) {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}
function fmtWeeks(range) {
  if (!range || range[0] === null || range[0] === undefined) return "—";
  return `${range[0]}–${range[1]} 周`;
}

// -- API --------------------------------------------------------------------
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload && payload.error) message = payload.error;
    } catch (_) {
      /* ignore */
    }
    throw new Error(message);
  }
  return response.json();
}

function queryParams(includePaging = true) {
  const params = new URLSearchParams();
  params.set("preset", state.preset);
  params.set("sort", state.sort);
  params.set("order", state.order);
  params.set("min_stars", String(state.filters.minStars));
  if (includePaging) {
    params.set("page", String(state.page));
    params.set("page_size", String(state.pageSize));
  }
  const filters = state.filters;
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.language) params.set("language", filters.language);
  if (filters.license) params.set("license", filters.license);
  if (filters.paidVersion) params.set("paid_version", filters.paidVersion);
  if (filters.complexity) params.set("complexity", filters.complexity);
  if (filters.minDelta7 !== "") params.set("min_delta7", filters.minDelta7);
  if (filters.minRepro !== "") params.set("min_repro", filters.minRepro);
  if (filters.minBiz !== "") params.set("min_biz", filters.minBiz);
  if (filters.maxCompetitors !== "") params.set("max_competitors", filters.maxCompetitors);
  if (filters.risk) params.set("risk", filters.risk);
  if (filters.includeGpl) params.set("include_gpl", "1");
  if (filters.includeGiant) params.set("include_giant", "1");
  if (filters.watchedOnly) params.set("watched", "1");
  return params;
}

// -- theme ------------------------------------------------------------------
function initTheme() {
  const paramTheme = new URLSearchParams(location.search).get("theme");
  const saved = localStorage.getItem("repo-scout-theme");
  const theme =
    paramTheme === "light" || paramTheme === "dark"
      ? paramTheme
      : saved === "light" || saved === "dark"
        ? saved
        : "dark";
  document.documentElement.dataset.theme = theme;
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("repo-scout-theme", next);
}

// -- toast ------------------------------------------------------------------
function showToast(message, kind = "") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show ${kind}`.trim();
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    toast.className = "toast";
  }, 3200);
}

// -- status / summary -------------------------------------------------------
function renderStatus(meta) {
  const pill = $("#status-pill");
  const text = $("#status-text");
  const snapshot = $("#snapshot-text");
  if (!meta) return;
  if (meta.last_run && meta.last_run.status === "failed") {
    pill.dataset.state = "error";
    text.textContent = "采集失败 · 查看本地 ingest_runs";
  } else if (meta.stale) {
    pill.dataset.state = "warn";
    text.textContent = `数据超过 12 小时未更新${meta.snapshot_age_hours ? `（${meta.snapshot_age_hours}h）` : ""}`;
  } else {
    pill.dataset.state = "ok";
    text.textContent = `快照 ${fmtDateTime(meta.snapshot_at)} · 候选 ${meta.candidate_pool}`;
  }
  snapshot.textContent = "本地 SQLite · 只读";
  snapshot.title = meta.snapshot_at ? `快照 ${fmtDateTime(meta.snapshot_at)}` : "暂无快照";
}

function renderSummary(meta) {
  const container = $("#summary");
  container.replaceChildren();
  const gates = meta.gates || {};
  const hidden = (gates.g3?.count || 0) + (gates.g5?.count || 0) + (gates.g6?.count || 0);
  const cells = [
    {
      label: "候选池",
      value: fmtInt(meta.candidate_pool),
      note: "非镜像 / 清单 / 教程",
    },
    {
      label: "默认可见",
      value: fmtInt(meta.visible_default),
      note: `预设：${meta.preset.label}`,
    },
    {
      label: "闸门隐藏",
      value: fmtInt(hidden),
      note: `GPL ${gates.g3?.count || 0} · 巨石 ${gates.g5?.count || 0} · 低星 ${gates.g6?.count || 0}`,
    },
    {
      label: "数据快照",
      value: fmtDateTime(meta.snapshot_at),
      note: meta.stale ? "已超过 12 小时，建议重跑采集" : `收藏 ${meta.watched || 0} 个`,
    },
  ];
  for (const cell of cells) {
    const wrapper = el("div", "summary-cell");
    append(
      wrapper,
      el("span", "summary-label", cell.label),
      el("span", "summary-value", cell.value),
      el("span", "summary-note", cell.note),
    );
    container.appendChild(wrapper);
  }
}

function renderPresets(meta) {
  const container = $("#presets");
  container.replaceChildren();
  for (const preset of meta.presets || []) {
    const button = el("button", "preset", preset.label);
    button.type = "button";
    button.title = preset.hint || "";
    button.setAttribute("aria-pressed", preset.key === meta.preset.key ? "true" : "false");
    button.addEventListener("click", () => {
      if (state.preset === preset.key) return;
      state.preset = preset.key;
      state.sort = preset.sort || "total";
      state.page = 1;
      syncSortSelect();
      loadList();
    });
    container.appendChild(button);
  }
}

function renderDaily() {
  const band = $("#daily-band");
  const list = $("#daily-list");
  const active = hasActiveFilters();
  if (active || state.page !== 1 || state.preset !== "balanced" || !state.daily?.items?.length) {
    band.hidden = true;
    return;
  }
  band.hidden = false;
  list.replaceChildren();
  state.daily.items.slice(0, 5).forEach((item, index) => {
    const row = el("li", "daily-item");
    const rank = el("span", "daily-rank", String(index + 1).padStart(2, "0"));
    const nameWrap = el("div", "daily-name");
    const link = el("a", "", item.full_name);
    link.href = `#/repo/${item.id}`;
    const reason = el("span", "daily-reason", item.reason);
    append(nameWrap, link, reason);
    const delta = el("span", `daily-metric ${deltaClass(item.growth.d7)}`, `Δ7d ${fmtSigned(item.growth.d7)}`);
    const license = el("span", `daily-license license-${item.license.risk}`, item.license.spdx || item.license.label);
    const total = el("span", "daily-total", fmtScore(item.scores.total));
    append(row, rank, nameWrap, delta, license, total);
    row.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      location.hash = `#/repo/${item.id}`;
    });
    list.appendChild(row);
  });
}

// -- filters ----------------------------------------------------------------
function readFilters() {
  const filters = state.filters;
  filters.q = $("#q").value;
  filters.language = $("#language").value;
  filters.license = $("#license").value;
  filters.paidVersion = $("#paid-version").value;
  filters.complexity = $("#complexity").value;
  filters.minStars = Number($("#min-stars").value || 0);
  filters.minDelta7 = $("#min-delta7").value;
  filters.minRepro = $("#min-repro").value;
  filters.minBiz = $("#min-biz").value;
  filters.maxCompetitors = $("#max-competitors").value;
  filters.risk = $("#risk").value;
  filters.includeGpl = $("#include-gpl").checked;
  filters.includeGiant = $("#include-giant").checked;
  filters.watchedOnly = $("#watched-only").checked;
}

function syncFilterControls() {
  const f = state.filters;
  $("#q").value = f.q;
  $("#language").value = f.language;
  $("#license").value = f.license;
  $("#paid-version").value = f.paidVersion;
  $("#complexity").value = f.complexity;
  $("#min-stars").value = f.minStars;
  $("#min-delta7").value = f.minDelta7;
  $("#min-repro").value = f.minRepro;
  $("#min-biz").value = f.minBiz;
  $("#max-competitors").value = f.maxCompetitors;
  $("#risk").value = f.risk;
  $("#include-gpl").checked = f.includeGpl;
  $("#include-giant").checked = f.includeGiant;
  $("#watched-only").checked = f.watchedOnly;
}

function hasActiveFilters() {
  const f = state.filters;
  return Boolean(
    f.q.trim() ||
      f.language ||
      f.license ||
      f.paidVersion ||
      f.complexity ||
      f.minDelta7 !== "" ||
      f.minRepro !== "" ||
      f.minBiz !== "" ||
      f.maxCompetitors !== "" ||
      f.risk ||
      f.includeGpl ||
      f.includeGiant ||
      f.watchedOnly ||
      Number(f.minStars) !== 500,
  );
}

function activeFilterCount() {
  const f = state.filters;
  let count = 0;
  if (f.q.trim()) count++;
  if (f.language) count++;
  if (f.license) count++;
  if (f.paidVersion) count++;
  if (f.complexity) count++;
  if (f.minDelta7 !== "") count++;
  if (f.minRepro !== "") count++;
  if (f.minBiz !== "") count++;
  if (f.maxCompetitors !== "") count++;
  if (f.risk) count++;
  if (f.includeGpl) count++;
  if (f.includeGiant) count++;
  if (f.watchedOnly) count++;
  if (Number(f.minStars) !== 500) count++;
  return count;
}

function renderFacets(meta) {
  const select = $("#language");
  const current = state.filters.language;
  select.replaceChildren();
  select.appendChild(el("option", "", "全部"));
  select.firstChild.value = "";
  for (const [language, count] of Object.entries(meta.facets?.languages || {})) {
    const option = el("option", "", `${language} (${count})`);
    option.value = language;
    select.appendChild(option);
  }
  select.value = current;
}

function resetFilters() {
  state.filters = { ...DEFAULT_FILTERS };
  syncFilterControls();
  state.page = 1;
  loadList();
}

// -- table ------------------------------------------------------------------
function skeleton() {
  const body = $("#table-body");
  const template = $("#skeleton-row");
  body.replaceChildren();
  for (let i = 0; i < 7; i++) {
    body.appendChild(template.content.cloneNode(true));
  }
  $("#table-empty").hidden = true;
}

function scoreCell(value, grade, total = false) {
  const wrap = el("span", `score-cell${total ? " total-cell" : ""}`);
  const number = el("span", "score-num", fmtScore(value));
  if (grade) number.appendChild(el("small", "", grade));
  const bar = el("span", "mini-bar");
  const fill = el("span", "");
  fill.style.width = `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
  bar.appendChild(fill);
  append(wrap, number, bar);
  return wrap;
}

function licenseBadge(license) {
  const badge = el("span", "license-badge");
  const dot = el("span", `license-dot ${license.risk}`);
  append(badge, dot, el("strong", "", license.spdx || "—"), el("span", "", license.label));
  badge.title = `${license.spdx || "无 License"} · ${license.label}`;
  return badge;
}

function riskChips(risks) {
  const wrap = el("span", "risk-list");
  if (!risks || risks.length === 0) {
    wrap.appendChild(el("span", "chip none", "—"));
    return wrap;
  }
  for (const risk of risks) {
    wrap.appendChild(el("span", `chip ${risk.severity}`, risk.label));
  }
  return wrap;
}

function actionButton(iconName, label, onClick, pressed = null) {
  const button = el("button", "icon-btn");
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  if (pressed !== null) button.setAttribute("aria-pressed", pressed ? "true" : "false");
  button.appendChild(icon(iconName));
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
  return button;
}

function renderTable(payload) {
  const body = $("#table-body");
  const empty = $("#table-empty");
  const items = payload.items || [];
  const meta = payload.meta;
  body.replaceChildren();
  $("#result-count").textContent = `${fmtInt(payload.total)} 个结果 · 第 ${meta.page} / ${Math.max(1, Math.ceil(payload.total / meta.page_size))} 页`;
  if (!items.length) {
    const filtered = hasActiveFilters();
    empty.hidden = false;
    empty.replaceChildren();
    const title = el("strong", "", filtered ? "没有匹配的项目" : "还没有可展示的数据");
    const text = el(
      "p",
      "",
      filtered
        ? "当前筛选条件过窄。可以放宽分数或星数限制，或显示被默认隐藏的 GPL / 巨石项目。"
        : "先运行 ingest、analyze 与 biz-analyze，生成本地评分后再回来。",
    );
    const action = el("button", "btn", filtered ? "重置筛选" : "刷新数据");
    action.type = "button";
    action.addEventListener("click", () => (filtered ? resetFilters() : loadList()));
    append(empty, title, text, action);
    $("#pagination").hidden = true;
    return;
  }
  empty.hidden = true;
  const rankOffset = (meta.page - 1) * meta.page_size;
  items.forEach((item, index) => {
    const row = el("tr");
    row.addEventListener("click", (event) => {
      if (event.target.closest("a, button")) return;
      location.hash = `#/repo/${item.id}`;
    });

    const rank = el("td");
    rank.appendChild(el("span", "rank", String(rankOffset + index + 1).padStart(2, "0")));

    const repo = el("td", "repo-cell");
    const line = el("div", "repo-line");
    const nameLink = el("a", "repo-name");
    nameLink.href = `#/repo/${item.id}`;
    append(nameLink, el("span", "repo-owner", `${item.owner} / `), el("span", "", item.name));
    line.appendChild(nameLink);
    line.appendChild(el("span", "repo-stars", `${fmtInt(item.stars)} ★`));
    const desc = el("p", "repo-desc", item.description || "暂无描述");
    append(repo, line, desc);

    const d24 = el("td", "col-delta");
    d24.appendChild(el("span", `num ${deltaClass(item.growth.d24)}`, fmtSigned(item.growth.d24)));
    const d7 = el("td", "col-delta");
    d7.appendChild(el("span", `num ${deltaClass(item.growth.d7)}`, fmtSigned(item.growth.d7)));
    const d30 = el("td", "col-delta col-hide-md");
    d30.appendChild(el("span", `num ${deltaClass(item.growth.d30)}`, fmtSigned(item.growth.d30)));

    const velocity = el("td", "col-velocity col-hide-md");
    velocity.appendChild(
      el("span", "num", item.growth.velocity === null ? "—" : `${fmtOne(item.growth.velocity)}/天`),
    );

    const repro = el("td", "col-score");
    repro.appendChild(scoreCell(item.scores.repro, item.repro.grade));
    const biz = el("td", "col-score");
    biz.appendChild(scoreCell(item.scores.biz));
    const total = el("td", "col-score");
    total.appendChild(scoreCell(item.scores.total, null, true));

    const license = el("td", "col-license");
    license.appendChild(licenseBadge(item.license));

    const risks = el("td", "col-risk");
    risks.appendChild(riskChips(item.risks));

    const competitors = el("td", "col-comp col-hide-sm");
    competitors.appendChild(
      el(
        "span",
        "num",
        item.biz.competitor_count === null ? "?" : fmtInt(item.biz.competitor_count),
      ),
    );

    const actions = el("td", "col-actions");
    const group = el("span", "action-group");
    group.appendChild(actionButton("arrow", "查看详情", () => (location.hash = `#/repo/${item.id}`)));
    const watchBtn = actionButton(
      "star",
      item.watch.watched ? "取消收藏" : "收藏",
      () => toggleWatch(item),
      item.watch.watched,
    );
    watchBtn.classList.add("watch-btn");
    group.appendChild(watchBtn);
    group.appendChild(actionButton("copy", "复制 clone 命令", () => copyClone(item)));
    actions.appendChild(group);

    append(row, rank, repo, d24, d7, d30, velocity, repro, biz, total, license, risks, competitors, actions);
    body.appendChild(row);
  });
  renderPagination(meta, payload.total);
}

function renderPagination(meta, total) {
  const pagination = $("#pagination");
  const pages = Math.max(1, Math.ceil(total / meta.page_size));
  if (pages <= 1) {
    pagination.hidden = true;
    return;
  }
  pagination.hidden = false;
  $("#page-info").textContent = `第 ${meta.page} / ${pages} 页`;
  $("#prev-page").disabled = meta.page <= 1;
  $("#next-page").disabled = meta.page >= pages;
}

function resultText(payload) {
  return `${fmtInt(payload.total)} 个结果 · 第 ${payload.meta.page} / ${Math.max(1, Math.ceil(payload.total / payload.meta.page_size))} 页`;
}

function emptyState(payload, filtered) {
  const wrapper = el("div", filtered ? "" : "");
  const title = el("strong", "", filtered ? "没有匹配的项目" : "还没有可展示的数据");
  const text = el(
    "p",
    "",
    filtered
      ? "当前筛选条件过窄。可以放宽分数或星数限制，或显示被默认隐藏的 GPL / 巨石项目。"
      : "先在上方的「采集与分析流程」里运行采集和评分，再回来查看。",
  );
  const action = el("button", "btn", filtered ? "重置筛选" : "刷新数据");
  action.type = "button";
  action.addEventListener("click", () => (filtered ? resetFilters() : loadAll()));
  append(wrapper, title, text, action);
  return wrapper;
}

function renderCards(payload) {
  const grid = $("#card-grid");
  grid.replaceChildren();
  const items = payload.items || [];
  $("#result-count").textContent = resultText(payload);
  if (!items.length) {
    const empty = emptyState(payload, hasActiveFilters());
    empty.className = "card-empty";
    grid.appendChild(empty);
    $("#pagination").hidden = true;
    return;
  }
  const rankOffset = (payload.meta.page - 1) * payload.meta.page_size;
  items.forEach((item, index) => {
    const card = el("article", "repo-card");
    card.addEventListener("click", (event) => {
      if (event.target.closest("a, button")) return;
      location.hash = `#/repo/${item.id}`;
    });

    const head = el("div", "card-head");
    const rank = el("span", "card-rank", String(rankOffset + index + 1).padStart(2, "0"));
    const titleWrap = el("div", "card-title-wrap");
    const title = el("a", "card-title");
    title.href = `#/repo/${item.id}`;
    title.appendChild(el("span", "card-owner", `${item.owner} / `));
    title.appendChild(el("span", "", item.name));
    const sub = el("div", "card-sub");
    append(
      sub,
      el("span", "", `${fmtInt(item.stars)} ★`),
      el("span", "", item.language || "未标注语言"),
      el("span", "", item.repro.deep ? "cloc 深分析" : "LoC 估算"),
    );
    append(titleWrap, title, sub);
    const watch = actionButton(
      "star",
      item.watch.watched ? "取消收藏" : "收藏",
      () => toggleWatch(item),
      item.watch.watched,
    );
    watch.classList.add("watch-btn");
    append(head, rank, titleWrap, watch);

    const desc = el("p", "card-desc", item.description || "暂无描述，打开详情查看 README 与 topics。");
    const topicWrap = el("div", "card-topics");
    const topics = item.topics.slice(0, 5);
    if (topics.length) {
      for (const topic of topics) topicWrap.appendChild(el("span", "pill", topic));
    } else {
      topicWrap.appendChild(el("span", "pill", "无 topics"));
    }

    const metrics = el("div", "card-metrics");
    append(
      metrics,
      cardMetric("Δ7d", fmtSigned(item.growth.d7), deltaClass(item.growth.d7)),
      cardMetric("增速", item.growth.velocity === null ? "—" : `${fmtOne(item.growth.velocity)}/天`),
      cardMetric("竞品", item.biz.competitor_count === null ? "?" : fmtInt(item.biz.competitor_count)),
      cardMetric("商业化", paidVersionLabel(item.biz.paid_version)),
    );

    const scoreRow = el("div", "card-score-row");
    const total = el("span", "card-total", fmtScore(item.scores.total));
    total.appendChild(el("small", "", "/100"));
    const subs = el("div", "card-sub-scores");
    append(
      subs,
      cardSubScore("复刻", item.scores.repro),
      cardSubScore("商业", item.scores.biz),
    );
    append(scoreRow, total, subs);

    const tags = el("div", "card-tags");
    tags.appendChild(licenseBadge(item.license));
    for (const risk of item.risks.slice(0, 3)) {
      tags.appendChild(el("span", `chip ${risk.severity}`, risk.label));
    }

    const reason = el("p", "card-reason", item.reason);
    const actions = el("div", "card-actions");
    const detail = el("button", "btn", "详情");
    detail.type = "button";
    detail.addEventListener("click", () => (location.hash = `#/repo/${item.id}`));
    const copy = el("button", "btn btn-ghost", "复制 clone");
    copy.type = "button";
    copy.addEventListener("click", () => copyClone(item));
    const github = el("a", "btn btn-ghost", "GitHub");
    github.href = item.url;
    github.target = "_blank";
    github.rel = "noopener noreferrer";
    append(actions, detail, copy, github);
    append(card, head, desc, topicWrap, metrics, scoreRow, tags, reason, actions);
    grid.appendChild(card);
  });
  renderPagination(payload.meta, payload.total);
}

function cardMetric(label, value, className = "") {
  const wrapper = el("div");
  wrapper.appendChild(el("span", "card-metric-label", label));
  wrapper.appendChild(el("span", `card-metric-value ${className}`.trim(), value));
  return wrapper;
}
function cardSubScore(label, value) {
  const wrapper = el("span", "card-sub-score");
  wrapper.appendChild(document.createTextNode(label));
  wrapper.appendChild(el("strong", "", fmtScore(value)));
  return wrapper;
}

function applyView() {
  const cards = state.view === "cards";
  $("#table-shell").hidden = cards;
  $("#card-grid").hidden = !cards;
  $("#view-cards").setAttribute("aria-pressed", cards ? "true" : "false");
  $("#view-table").setAttribute("aria-pressed", cards ? "false" : "true");
  if (!state.data) return;
  if (cards) renderCards(state.data);
  else renderTable(state.data);
}

function syncSortSelect() {
  const select = $("#sort-select");
  if (Array.from(select.options).some((option) => option.value === state.sort)) {
    select.value = state.sort;
  }
}

function syncSortHeaders() {
  $$(".th-sort").forEach((button) => {
    const active = button.dataset.sort === state.sort;
    button.dataset.active = active ? "true" : "false";
    if (active) button.dataset.order = state.order;
    else delete button.dataset.order;
  });
}

// -- list loading -----------------------------------------------------------
async function loadList() {
  if (state.abort) state.abort.abort();
  state.abort = new AbortController();
  skeleton();
  try {
    const payload = await api(`/api/repos?${queryParams().toString()}`, { signal: state.abort.signal });
    state.data = payload;
    state.meta = payload.meta;
    renderStatus(payload.meta);
    renderPresets(payload.meta);
    renderSummary(payload.meta);
    renderFacets(payload.meta);
    syncFilterControls();
    syncSortHeaders();
    renderDaily();
    applyView();
    $("#reset-filters").textContent = activeFilterCount() ? `重置 (${activeFilterCount()})` : "重置";
  } catch (error) {
    if (error.name === "AbortError") return;
    $("#table-body").replaceChildren();
    const empty = $("#table-empty");
    empty.hidden = false;
    empty.replaceChildren(
      el("strong", "", "数据读取失败"),
      el("p", "", error.message || "无法连接本地服务"),
    );
    showToast(`加载失败：${error.message}`, "error");
  }
}

async function loadDaily(force = false) {
  if (state.daily && !force) return;
  try {
    state.daily = await api("/api/daily");
    renderDaily();
  } catch (_) {
    state.daily = null;
  }
}

// -- detail -----------------------------------------------------------------
async function loadDetail(repoId) {
  $("#list-view").hidden = true;
  $("#detail-view").hidden = false;
  const container = $("#detail-content");
  container.replaceChildren(el("p", "hint", "正在读取项目详情…"));
  try {
    const params = new URLSearchParams();
    params.set("preset", state.preset);
    params.set("min_stars", String(state.filters.minStars));
    const payload = await api(`/api/repos/${repoId}?${params.toString()}`);
    state.detail = payload;
    state.detailId = repoId;
    renderDetail(payload);
    window.scrollTo({ top: 0, behavior: "auto" });
  } catch (error) {
    container.replaceChildren();
    const back = el("a", "detail-back", "← 返回列表");
    back.href = "#/";
    append(
      container,
      back,
      el("h2", "", "无法读取项目详情"),
      el("p", "hint", error.message),
    );
  }
}

function renderDetail(payload) {
  const item = payload.repo;
  const container = $("#detail-content");
  container.replaceChildren();

  const back = el("a", "detail-back");
  back.href = "#/";
  back.appendChild(icon("back"));
  back.appendChild(el("span", "", "返回列表"));

  const header = el("div", "detail-header");
  const main = el("div");
  const title = el("h1", "detail-title");
  const titleLink = el("a");
  titleLink.href = item.url;
  titleLink.target = "_blank";
  titleLink.rel = "noopener noreferrer";
  titleLink.appendChild(el("span", "detail-owner", `${item.owner} / `));
  titleLink.appendChild(el("span", "", item.name));
  title.appendChild(titleLink);
  const desc = el("p", "detail-desc", item.description || "暂无描述");
  const meta = el("div", "detail-meta");
  append(
    meta,
    metaItem("Stars", fmtInt(item.stars), true),
    metaItem("Forks", fmtInt(item.forks), true),
    metaItem("Issues", fmtInt(item.issues), true),
    metaItem("语言", item.language || "未标注"),
    metaItem("首次发现", fmtDateTime(item.first_seen_at, false)),
    metaItem("最近推送", fmtDateTime(item.pushed_at, false)),
  );
  const topics = el("div", "pill-list");
  for (const topic of item.topics.slice(0, 8)) topics.appendChild(el("span", "pill", topic));
  if (!item.topics.length) topics.appendChild(el("span", "pill", "无 topics"));
  const tags = el("div", "detail-tags");
  tags.appendChild(licenseBadge(item.license));
  for (const risk of item.risks) tags.appendChild(el("span", `chip ${risk.severity}`, risk.label));
  const reason = el("p", "detail-reason");
  append(reason, el("strong", "", "一句话结论："), document.createTextNode(item.reason));
  const actions = el("div", "detail-actions");
  const watchAction = el("button", "btn", item.watch.watched ? "取消收藏" : "收藏");
  watchAction.type = "button";
  watchAction.appendChild(icon("star"));
  watchAction.addEventListener("click", () => toggleWatch(item));
  const github = el("a", "btn", "打开 GitHub");
  github.href = item.url;
  github.target = "_blank";
  github.rel = "noopener noreferrer";
  github.appendChild(icon("external"));
  const copy = el("button", "btn", "复制 clone");
  copy.type = "button";
  copy.appendChild(icon("copy"));
  copy.addEventListener("click", () => copyClone(item));
  append(actions, watchAction, github, copy);
  append(main, title, desc, meta, topics, tags, reason, actions);

  const score = el("div", "detail-score");
  const scoreLabel = el("div", "detail-score-label", `综合分 · α ${item.scores.alpha}`);
  const scoreValue = el("div", "detail-score-value", fmtScore(item.scores.total));
  scoreValue.appendChild(el("small", "", "/ 100"));
  const split = el("div", "detail-score-split");
  split.appendChild(splitItem("复刻分", item.scores.repro));
  split.appendChild(splitItem("商业分", item.scores.biz));
  append(score, scoreLabel, scoreValue, split);
  append(header, main, score);

  const grid = el("div", "detail-grid");
  const left = el("div", "detail-column");
  const right = el("div", "detail-column");
  left.appendChild(renderGrowth(payload));
  left.appendChild(renderRepro(payload));
  right.appendChild(renderCommercial(payload));
  right.appendChild(renderRisk(payload));
  right.appendChild(renderOps(payload));
  append(grid, left, right);
  append(container, back, header, grid);
}

function metaItem(label, value, mono = false) {
  const span = el("span");
  span.appendChild(el("span", "", `${label} `));
  span.appendChild(el("span", mono ? "mono" : "", value));
  return span;
}
function splitItem(label, value) {
  const wrapper = el("div", "split-item");
  append(wrapper, el("span", "", label), el("strong", "", fmtScore(value)));
  return wrapper;
}

function sectionHead(title, note) {
  const head = el("div", "section-head");
  const h2 = el("h2", "", title);
  const p = el("p", "", note || "");
  append(head, h2, p);
  return head;
}

function statGrid(stats) {
  const grid = el("div", "stat-grid");
  for (const stat of stats) {
    const cell = el("div", "stat");
    cell.appendChild(el("span", "stat-label", stat.label));
    const value = el("span", "stat-value", stat.value);
    if (stat.unit) value.appendChild(el("small", "", stat.unit));
    cell.appendChild(value);
    if (stat.note) cell.appendChild(el("span", "stat-note", stat.note));
    grid.appendChild(cell);
  }
  return grid;
}

function renderGrowth(payload) {
  const item = payload.repo;
  const section = el("section", "detail-section");
  section.appendChild(sectionHead("增长", `指标时间 ${fmtDateTime(item.growth.metrics_at)} · 置信度 ${item.growth.history_confidence}`));
  section.appendChild(
    statGrid([
      { label: "Δ24h", value: fmtSigned(item.growth.d24), note: item.growth.d24 === null ? "数据不足" : "趋势优先" },
      { label: "Δ7d", value: fmtSigned(item.growth.d7), note: "官方周级历史" },
      { label: "Δ30d", value: fmtSigned(item.growth.d30) },
      { label: "7 日增速", value: fmtOne(item.growth.velocity), unit: "/天" },
      {
        label: "加速率",
        value: item.growth.acceleration_available ? fmtPercent(item.growth.acceleration) : "—",
        note: item.growth.acceleration_available ? "" : "数据不足",
      },
      { label: "TrendScore", value: fmtScore(item.growth.trend_score) },
    ]),
  );
  const lineBlock = el("div", "chart-block");
  const lineHead = el("div", "chart-head");
  append(lineHead, el("span", "chart-title", "30 周累计星数"), el("span", "chart-sub", "官方 stargazers/history"));
  const lineBody = el("div", "chart-body");
  lineBlock.appendChild(lineHead);
  lineBlock.appendChild(lineBody);
  section.appendChild(lineBlock);
  renderLineChart(lineBody, payload.star_weeks, item.full_name);

  const barBlock = el("div", "chart-block");
  const barHead = el("div", "chart-head");
  append(barHead, el("span", "chart-title", "近 30 天日增量"), el("span", "chart-sub", "6 小时快照聚合"));
  const barBody = el("div", "chart-body");
  barBlock.appendChild(barHead);
  barBlock.appendChild(barBody);
  section.appendChild(barBlock);
  renderBarChart(barBody, payload.daily, item.full_name);

  section.appendChild(dataTables(payload));
  return section;
}

function renderRepro(payload) {
  const item = payload.repo;
  const repro = item.repro;
  const section = el("section", "detail-section");
  section.appendChild(sectionHead("复刻报告", repro.deep ? "LoC 来自 cloc 深分析" : "LoC 来自 languages API 估算"));
  const facts = el("dl", "facts");
  append(
    facts,
    fact("代码量 LoC", repro.loc === null ? "未知" : fmtInt(repro.loc)),
    fact("LoC 方法", `${repro.loc_method || "—"} · ${repro.loc_confidence || "—"}`),
    fact("运行时依赖", repro.dep_count === null ? "未识别根清单" : fmtInt(repro.dep_count)),
    fact("依赖清单", repro.dep_manifest || "—"),
    fact("外部服务", repro.ext_services.length ? repro.ext_services.join("、") : "未发现"),
    fact("工程结构", `${repro.monorepo ? "monorepo" : "单包"} · CI ${repro.ci_workflows ?? "—"} · src ${repro.src_files ?? "—"} 文件`),
    fact("复杂度", `${repro.grade} · ${fmtOne(repro.complexity)} / 100`),
    fact("预估周期", fmtWeeks(repro.replication_weeks)),
  );
  section.appendChild(facts);
  const bars = el("div", "score-bars");
  const labels = {
    loc: "代码体量",
    dep: "依赖",
    ext: "外部服务",
    lang: "语言匹配",
    license: "License",
    struct: "工程结构",
  };
  for (const [key, label] of Object.entries(labels)) {
    bars.appendChild(scoreBar(label, repro.subscores[key]));
  }
  section.appendChild(bars);
  if (repro.low_confidence.length) {
    section.appendChild(el("p", "hint", `低置信度字段：${repro.low_confidence.join("、")}`));
  }
  return section;
}

function renderCommercial(payload) {
  const item = payload.repo;
  const biz = item.biz;
  const section = el("section", "detail-section");
  section.appendChild(sectionHead("商业分析", `商业化：${paidVersionLabel(biz.paid_version)}`));
  section.appendChild(
    statGrid([
      { label: "市场验证", value: fmtScore(biz.market) },
      { label: "商业化空白", value: fmtScore(biz.whitespace) },
      { label: "竞品稀缺", value: fmtScore(biz.competition), note: biz.competitor_count === null ? "未知" : `${biz.competitor_count} 个竞品` },
      { label: "受众匹配", value: fmtScore(biz.audience), note: biz.audience_topic || "无 topic 命中" },
      { label: "付费意愿", value: fmtScore(biz.paid_intent), note: `${biz.paid_intent_hits} 条 issue 命中` },
      { label: "差异化", value: fmtScore(biz.differentiation), note: `${biz.monetization_paths.length} 条变现路径` },
    ]),
  );
  const bars = el("div", "score-bars");
  bars.appendChild(scoreBar("市场验证", biz.market));
  bars.appendChild(scoreBar("商业化空白", biz.whitespace));
  bars.appendChild(scoreBar("竞品稀缺", biz.competition));
  bars.appendChild(scoreBar("受众匹配", biz.audience));
  bars.appendChild(scoreBar("差异化", biz.differentiation));
  section.appendChild(bars);

  if (biz.competitor_query) {
    const queryWrap = el("p", "hint");
    queryWrap.appendChild(document.createTextNode("竞品查询："));
    queryWrap.appendChild(el("code", "", biz.competitor_query));
    section.appendChild(queryWrap);
  }
  if (biz.monetization_paths.length) {
    const paths = el("div", "pill-list");
    for (const path of biz.monetization_paths) paths.appendChild(el("span", "pill", path));
    section.appendChild(paths);
  }

  const issues = payload.commercial?.paid_issues || [];
  if (issues.length) {
    const list = el("ul", "evidence-list");
    for (const issue of issues) {
      const row = el("li", "evidence-item");
      const link = el("a", "", issue.title || `#${issue.number}`);
      link.href = issue.url || item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const hits = el("p", "", `命中：${(issue.hits || []).join("、")}`);
      append(row, link, hits);
      list.appendChild(row);
    }
    section.appendChild(list);
  } else {
    section.appendChild(el("p", "hint", "最近 100 条 open issues 中未发现付费意愿关键词。"));
  }

  const evidence = payload.commercial?.paid_evidence || [];
  if (evidence.length) {
    const list = el("ul", "evidence-list");
    for (const itemEvidence of evidence.slice(0, 5)) {
      const row = el("li", "evidence-item");
      row.appendChild(el("p", "", `${itemEvidence.source || "README"} · ${itemEvidence.keyword || ""}：${itemEvidence.line || ""}`));
      list.appendChild(row);
    }
    section.appendChild(list);
  }
  return section;
}

function renderRisk(payload) {
  const item = payload.repo;
  const section = el("section", "detail-section");
  section.appendChild(sectionHead("风险与合规", "仅供风险提示，不构成法律意见"));
  const banner = el("div", `license-banner ${item.license.risk}`);
  const dot = el("span", `license-dot ${item.license.risk}`);
  const text = el("div");
  append(
    text,
    el("strong", "", `${item.license.spdx || "无 License"} · ${item.license.label}`),
    el("p", "", licenseAdvice(item.license.risk)),
  );
  append(banner, dot, text);
  section.appendChild(banner);

  const risks = el("div", "risk-list");
  if (item.risks.length) {
    for (const risk of item.risks) risks.appendChild(el("span", `chip ${risk.severity}`, risk.label));
  } else {
    risks.appendChild(el("span", "chip none", "未发现显著风险"));
  }
  section.appendChild(risks);

  const checklist = el("ul", "checklist");
  for (const line of ["确认 License 与原项目版权声明", "检查商标与品牌名是否可复用", "检查素材、图片与数据来源", "确认托管/商用条款与地域合规"]) {
    checklist.appendChild(el("li", "", line));
  }
  section.appendChild(checklist);
  if (item.data_quality.low_confidence.length) {
    section.appendChild(el("p", "hint", `数据置信度较低：${item.data_quality.low_confidence.join("、")}`));
  }
  return section;
}

function renderOps(payload) {
  const item = payload.repo;
  const section = el("section", "detail-section");
  section.appendChild(sectionHead("我的操作", "收藏、备注与反馈会写入本地数据库"));

  const windowBox = el("div", "window-box");
  const windowValue = el("div", "window-value", item.window.days === null ? "—" : `约 ${item.window.days} 天`);
  windowBox.appendChild(windowValue);
  const basis = el("ul");
  for (const line of item.window.basis || []) basis.appendChild(el("li", "", line));
  windowBox.appendChild(basis);
  windowBox.appendChild(el("p", "window-note", item.window.note || "参考值"));
  section.appendChild(windowBox);

  const form = el("div", "ops-form");
  const statusLabel = el("label", "", "关注状态");
  statusLabel.htmlFor = "watch-status";
  const status = el("select");
  status.id = "watch-status";
  for (const [value, label] of [
    ["watching", "关注中"],
    ["investigating", "考察中"],
    ["planned", "已立项"],
    ["abandoned", "已放弃"],
  ]) {
    const option = el("option", "", label);
    option.value = value;
    status.appendChild(option);
  }
  status.value = item.watch.status || "watching";
  const noteLabel = el("label", "", "备注");
  noteLabel.htmlFor = "watch-note";
  const note = el("textarea");
  note.id = "watch-note";
  note.placeholder = "记录你的判断、复刻计划或放弃原因";
  note.value = item.watch.note || "";
  const actions = el("div", "ops-actions");
  const save = el("button", "btn btn-primary", item.watch.watched ? "保存关注" : "加入关注");
  save.type = "button";
  save.addEventListener("click", async () => {
    try {
      const result = await api("/api/watch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_pk: item.id,
          action: "add",
          status: status.value,
          note: note.value,
        }),
      });
      applyWatch(item.id, result.watch);
      showToast("关注状态已保存");
    } catch (error) {
      showToast(`保存失败：${error.message}`, "error");
    }
  });
  const remove = el("button", "btn btn-danger", "取消关注");
  remove.type = "button";
  remove.addEventListener("click", () => toggleWatch(item));
  append(actions, save, remove);
  append(form, statusLabel, status, noteLabel, note, actions);
  section.appendChild(form);

  const feedback = el("div", "feedback-row");
  const useful = el("button", "btn", "有用");
  useful.type = "button";
  useful.addEventListener("click", () => sendFeedback(item, "useful"));
  const notUseful = el("button", "btn btn-ghost", "没用");
  notUseful.type = "button";
  notUseful.addEventListener("click", () => sendFeedback(item, "not_useful"));
  append(feedback, useful, notUseful);
  section.appendChild(feedback);

  const cloneWrap = el("div", "clone-cmd");
  cloneWrap.appendChild(el("code", "", `git clone --depth 1 ${item.url}.git`));
  const copy = el("button", "btn btn-ghost", "复制");
  copy.type = "button";
  copy.addEventListener("click", () => copyClone(item));
  cloneWrap.appendChild(copy);
  section.appendChild(cloneWrap);
  section.appendChild(el("p", "hint", "复制代码前请再次确认 License 与商标风险。"));
  return section;
}

function fact(label, value) {
  const wrapper = el("div");
  append(wrapper, el("dt", "", label), el("dd", "", value));
  return wrapper;
}

function scoreBar(label, value) {
  const row = el("div", "score-row");
  row.appendChild(el("span", "score-row-label", label));
  const track = el("span", "score-track");
  const fill = el("span", "score-fill");
  const numeric = Number(value);
  fill.style.width = `${Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : 0}%`;
  if (Number.isFinite(numeric) && numeric < 40) fill.classList.add("bad");
  else if (Number.isFinite(numeric) && numeric < 65) fill.classList.add("warn");
  track.appendChild(fill);
  row.appendChild(track);
  row.appendChild(el("span", "score-row-value", fmtScore(value)));
  return row;
}

function paidVersionLabel(value) {
  return value === "likely" ? "疑似已商业化" : value === "possible" ? "可能已商业化" : "未见明显商业化";
}
function licenseAdvice(risk) {
  if (risk === "green") return "可商用；保留版权声明与 NOTICE 即可。";
  if (risk === "yellow") return "可商用，但修改部分需要开源；请阅读具体条款。";
  if (risk === "red") return "传染性开源；默认隐藏，仅建议研究思路，不建议直接复制代码。";
  if (risk === "gray") return "无 License：默认保留所有权利，仅可重写思路，不可复制代码。";
  return "License 信息不明确，需人工复核。";
}

// -- charts -----------------------------------------------------------------
let tooltipEl = null;
function ensureTooltip() {
  if (!tooltipEl) {
    tooltipEl = el("div", "chart-tooltip");
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}
function showChartTooltip(clientX, clientY, content) {
  const tooltip = ensureTooltip();
  tooltip.replaceChildren();
  for (const line of content) tooltip.appendChild(el("div", "", line));
  tooltip.classList.add("show");
  tooltip.style.left = `${Math.min(window.innerWidth - 220, clientX + 14)}px`;
  tooltip.style.top = `${Math.max(8, clientY - 16)}px`;
}
function hideChartTooltip() {
  if (tooltipEl) tooltipEl.classList.remove("show");
}

function renderLineChart(container, weeks, name) {
  container.replaceChildren();
  const points = weeks
    .map((week) => ({ ...week, value: week.stars_end }))
    .filter((week) => week.value !== null && week.value !== undefined);
  if (points.length < 2) {
    container.appendChild(el("div", "chart-empty", "官方周级历史不足，暂无法绘制曲线。"));
    return;
  }
  const width = 720;
  const height = 238;
  const margin = { top: 14, right: 16, bottom: 26, left: 52 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const values = points.map((point) => point.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const x = (index) => margin.left + (index / (points.length - 1)) * plotW;
  const y = (value) => margin.top + (1 - (value - min) / (max - min)) * plotH;
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    tabindex: "0",
    "aria-label": `${name} 最近 ${points.length} 周累计星数曲线`,
  });
  for (let tick = 0; tick <= 4; tick++) {
    const value = min + ((max - min) * tick) / 4;
    const lineY = y(value);
    root.appendChild(svg("line", { x1: margin.left, x2: width - margin.right, y1: lineY, y2: lineY, class: "chart-grid-line" }));
    const label = svg("text", { x: margin.left - 8, y: lineY + 3, "text-anchor": "end", class: "chart-axis" });
    label.textContent = fmtShortStars(value);
    root.appendChild(label);
  }
  const linePath = points.map((point, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`).join(" ");
  const areaPath = `M${x(0)},${margin.top + plotH} ${linePath.replace(/^M/, "L")} L${x(points.length - 1)},${margin.top + plotH} Z`;
  root.appendChild(svg("path", { d: areaPath, class: "chart-area" }));
  root.appendChild(svg("path", { d: linePath, class: "chart-line" }));
  const last = points[points.length - 1];
  root.appendChild(svg("circle", { cx: x(points.length - 1), cy: y(last.value), r: 4, class: "chart-dot" }));
  const labelIndexes = [0, Math.floor((points.length - 1) / 2), points.length - 1];
  for (const index of labelIndexes) {
    const label = svg("text", { x: x(index), y: height - 8, "text-anchor": index === 0 ? "start" : index === points.length - 1 ? "end" : "middle", class: "chart-axis" });
    label.textContent = points[index].week_start.slice(5);
    root.appendChild(label);
  }
  const cursor = svg("line", { x1: 0, x2: 0, y1: margin.top, y2: margin.top + plotH, class: "chart-cursor", opacity: "0" });
  const cursorDot = svg("circle", { r: 4, class: "chart-dot", opacity: "0" });
  root.appendChild(cursor);
  root.appendChild(cursorDot);
  let activeIndex = points.length - 1;
  const showAt = (index, clientX, clientY) => {
    activeIndex = Math.max(0, Math.min(points.length - 1, index));
    const point = points[activeIndex];
    const px = x(activeIndex);
    cursor.setAttribute("x1", px);
    cursor.setAttribute("x2", px);
    cursor.setAttribute("opacity", "1");
    cursorDot.setAttribute("cx", px);
    cursorDot.setAttribute("cy", y(point.value));
    cursorDot.setAttribute("opacity", "1");
    const rect = root.getBoundingClientRect();
    const scaleX = rect.width / width;
    const scaleY = rect.height / height;
    showChartTooltip(clientX ?? rect.left + px * scaleX, clientY ?? rect.top + y(point.value) * scaleY, [
      point.week_start,
      `累计 ${fmtInt(point.value)}`,
      `本周 +${fmtInt(point.delta)}`,
    ]);
  };
  const overlay = svg("rect", { x: margin.left, y: margin.top, width: plotW, height: plotH, fill: "transparent" });
  overlay.style.cursor = "crosshair";
  overlay.addEventListener("pointermove", (event) => {
    const rect = root.getBoundingClientRect();
    const relative = (event.clientX - rect.left) * (width / rect.width);
    const index = Math.round(((relative - margin.left) / plotW) * (points.length - 1));
    showAt(index, event.clientX, event.clientY);
  });
  overlay.addEventListener("pointerleave", () => {
    cursor.setAttribute("opacity", "0");
    cursorDot.setAttribute("opacity", "0");
    hideChartTooltip();
  });
  root.appendChild(overlay);
  root.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      showAt(activeIndex - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      showAt(activeIndex + 1);
    }
  });
  root.addEventListener("focus", () => showAt(activeIndex));
  root.addEventListener("blur", () => {
    cursor.setAttribute("opacity", "0");
    cursorDot.setAttribute("opacity", "0");
    hideChartTooltip();
  });
  container.appendChild(root);
}

function renderBarChart(container, daily, name) {
  container.replaceChildren();
  if (!daily || daily.length === 0) {
    container.appendChild(el("div", "chart-empty", "快照不足，暂无法绘制日增量。"));
    return;
  }
  const width = 720;
  const height = 188;
  const margin = { top: 12, right: 16, bottom: 26, left: 52 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const values = daily.map((point) => point.delta);
  const maxValue = Math.max(0, ...values);
  const minValue = Math.min(0, ...values);
  const span = maxValue - minValue || 1;
  const y = (value) => margin.top + (1 - (value - minValue) / span) * plotH;
  const zeroY = y(0);
  const step = plotW / Math.max(1, daily.length);
  const barWidth = Math.max(2, Math.min(18, step - 2));
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `${name} 最近 ${daily.length} 天日增量柱状图`,
  });
  for (let tick = 0; tick <= 2; tick++) {
    const value = minValue + (span * tick) / 2;
    const lineY = y(value);
    root.appendChild(svg("line", { x1: margin.left, x2: width - margin.right, y1: lineY, y2: lineY, class: "chart-grid-line" }));
    const label = svg("text", { x: margin.left - 8, y: lineY + 3, "text-anchor": "end", class: "chart-axis" });
    label.textContent = fmtSigned(Math.round(value));
    root.appendChild(label);
  }
  daily.forEach((point, index) => {
    const cx = margin.left + step * index + step / 2;
    const top = point.delta >= 0 ? y(point.delta) : zeroY;
    const bottom = point.delta >= 0 ? zeroY : y(point.delta);
    const rect = svg("rect", {
      x: cx - barWidth / 2,
      y: top,
      width: barWidth,
      height: Math.max(1, bottom - top),
      rx: Math.min(3, barWidth / 3),
      class: point.delta >= 0 ? "chart-bar chart-bar-pos" : "chart-bar chart-bar-neg",
      tabindex: "0",
      "aria-label": `${point.date} 日增量 ${fmtSigned(point.delta)}`,
    });
    const show = (event) => {
      const box = rect.getBoundingClientRect();
      showChartTooltip(event?.clientX ?? box.left + box.width / 2, event?.clientY ?? box.top, [
        point.date,
        `日增量 ${fmtSigned(point.delta)}`,
        `累计 ${fmtInt(point.stars)}`,
      ]);
    };
    rect.addEventListener("pointerenter", show);
    rect.addEventListener("pointermove", show);
    rect.addEventListener("focus", () => show());
    rect.addEventListener("pointerleave", hideChartTooltip);
    rect.addEventListener("blur", hideChartTooltip);
    root.appendChild(rect);
  });
  for (const index of [0, daily.length - 1]) {
    const label = svg("text", {
      x: margin.left + step * index + step / 2,
      y: height - 8,
      "text-anchor": index === 0 ? "start" : "end",
      class: "chart-axis",
    });
    label.textContent = daily[index].date.slice(5);
    root.appendChild(label);
  }
  container.appendChild(root);
}

function dataTables(payload) {
  const details = el("details", "data-details");
  details.appendChild(el("summary", "", `查看数据表（${payload.star_weeks.length} 周 / ${payload.daily.length} 天）`));
  const weeklyTable = el("table");
  const weeklyHead = el("thead");
  const headRow = el("tr");
  for (const label of ["周起始", "新增", "周末累计"]) headRow.appendChild(el("th", "", label));
  weeklyHead.appendChild(headRow);
  const weeklyBody = el("tbody");
  for (const week of payload.star_weeks.slice(-12).reverse()) {
    const row = el("tr");
    append(row, el("td", "", week.week_start), el("td", "", fmtSigned(week.delta)), el("td", "", fmtInt(week.stars_end)));
    weeklyBody.appendChild(row);
  }
  append(weeklyTable, weeklyHead, weeklyBody);
  details.appendChild(weeklyTable);
  if (payload.daily.length) {
    const dailyTable = el("table");
    const dailyHead = el("thead");
    const dailyHeadRow = el("tr");
    for (const label of ["日期", "日增量", "累计"]) dailyHeadRow.appendChild(el("th", "", label));
    dailyHead.appendChild(dailyHeadRow);
    const dailyBody = el("tbody");
    for (const point of payload.daily.slice().reverse()) {
      const row = el("tr");
      append(row, el("td", "", point.date), el("td", "", fmtSigned(point.delta)), el("td", "", fmtInt(point.stars)));
      dailyBody.appendChild(row);
    }
    append(dailyTable, dailyHead, dailyBody);
    details.appendChild(dailyTable);
  }
  return details;
}

// -- actions ----------------------------------------------------------------
async function toggleWatch(item) {
  try {
    const result = await api("/api/watch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repo_pk: item.id,
        action: item.watch.watched ? "remove" : "add",
        status: item.watch.status || "watching",
        note: item.watch.note || "",
      }),
    });
    applyWatch(item.id, result.watch);
    showToast(result.watch.watched ? "已加入关注" : "已取消关注");
  } catch (error) {
    showToast(`操作失败：${error.message}`, "error");
  }
}

function applyWatch(repoId, watch) {
  if (state.data?.items) {
    for (const item of state.data.items) {
      if (item.id === repoId) item.watch = watch;
    }
  }
  if (state.daily?.items) {
    for (const item of state.daily.items) {
      if (item.id === repoId) item.watch = watch;
    }
  }
  if (state.detail?.repo?.id === repoId) {
    state.detail.repo.watch = watch;
    if (!$("#detail-view").hidden) renderDetail(state.detail);
  }
  if (!$("#list-view").hidden && state.data) {
    renderTable(state.data);
    renderDaily();
  }
}

async function sendFeedback(item, verdict) {
  try {
    await api("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_pk: item.id, verdict }),
    });
    showToast(verdict === "useful" ? "已记录：有用" : "已记录：没用");
  } catch (error) {
    showToast(`反馈失败：${error.message}`, "error");
  }
}

async function copyClone(item) {
  const command = `git clone --depth 1 ${item.url}.git`;
  try {
    await navigator.clipboard.writeText(command);
    showToast("clone 命令已复制");
  } catch (_) {
    showToast("复制失败，请手动复制", "error");
  }
}

function exportCsv() {
  const params = queryParams(false);
  window.location.href = `/api/export.csv?${params.toString()}`;
}

// -- rail -------------------------------------------------------------------
function setRailOpen(open) {
  const rail = $("#rail");
  const scrim = $("#rail-scrim");
  const toggle = $("#rail-toggle");
  rail.classList.toggle("open", open);
  scrim.hidden = !open;
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    const first = rail.querySelector("input, select, button");
    if (first) setTimeout(() => first.focus(), 200);
  }
}

// -- pipeline console ------------------------------------------------------
const STAGE_STATUS_LABELS = {
  ok: "已更新",
  stale: "待更新",
  empty: "未运行",
  running: "运行中",
  error: "失败",
  warn: "部分完成",
};
const JOB_STATUS_LABELS = {
  queued: "排队中",
  running: "运行中",
  success: "完成",
  partial: "部分完成",
  failed: "失败",
};

function pipelineOptions(action) {
  const numberValue = (selector) => {
    const value = $(selector).value;
    return value === "" ? undefined : Number(value);
  };
  const force = $("#opt-force").checked;
  const competition = $("#opt-competition").checked;
  const analyze = numberValue("#opt-analyze-limit");
  const deep = numberValue("#opt-deep-limit");
  const biz = numberValue("#opt-biz-limit");
  if (action === "ingest") {
    return { periods: null, enrich: true, snapshot: true, star_history: true };
  }
  if (action === "metrics") return {};
  if (action === "analyze") return { limit: analyze, force };
  if (action === "deep-analyze") return { limit: deep, force };
  if (action === "biz-analyze") return { limit: biz, force, competition };
  if (action === "full") {
    return {
      analyze_limit: analyze,
      deep_limit: deep,
      biz_limit: biz,
      force,
      deep: $("#opt-deep").checked,
      competition,
    };
  }
  return {};
}

function jobResultSummary(job) {
  const result = job.result;
  if (!result) return "";
  if (job.action === "ingest") {
    return `抓取 ${fmtInt(result.fetched || 0)} 条 · 补全 ${fmtInt(result.enriched || 0)} · 请求 ${fmtInt(result.requests || 0)}`;
  }
  if (job.action === "metrics") {
    return `评分 ${fmtInt(result.scored || 0)} · Δ7d 覆盖 ${fmtInt(result.delta_7d || 0)} · 加速率可用 ${fmtInt(result.acceleration || 0)}`;
  }
  if (job.action === "analyze") {
    return `分析 ${fmtInt(result.analyzed || 0)} · 跳过 ${fmtInt(result.skipped || 0)} · 失败 ${fmtInt(result.failed || 0)} · 请求 ${fmtInt(result.requests || 0)}`;
  }
  if (job.action === "deep-analyze") {
    return `深分析 ${fmtInt(result.analyzed || 0)} · 过大 ${fmtInt(result.skipped_large || 0)} · 失败 ${fmtInt(result.failed || 0)}`;
  }
  if (job.action === "biz-analyze") {
    return `分析 ${fmtInt(result.analyzed || 0)} · 失败 ${fmtInt(result.failed || 0)} · 竞品查询 ${fmtInt(result.competition_used || 0)} · 请求 ${fmtInt(result.requests || 0)}`;
  }
  if (job.action === "full") {
    return `采集 ${fmtInt(result.ingest?.fetched || 0)} 条 · 浅分析 ${fmtInt(result.analyze?.analyzed || 0)} · 商业 ${fmtInt(result.biz?.analyzed || 0)}`;
  }
  return "";
}

function renderJob(job) {
  const panel = $("#pipeline-job");
  if (!job) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("#job-title").textContent = `${job.label} · #${job.id}`;
  const status = $("#job-status");
  status.textContent = JOB_STATUS_LABELS[job.status] || job.status;
  status.className = `job-status ${job.status}`;
  $("#job-stage").textContent = job.stage || "—";
  const percent = Math.round((Number(job.progress) || 0) * 100);
  $("#job-percent").textContent = `${percent}%`;
  $("#job-progress").style.width = `${percent}%`;
  const timeParts = [];
  if (job.started_at) timeParts.push(`开始 ${fmtDateTime(job.started_at)}`);
  if (job.finished_at) timeParts.push(`结束 ${fmtDateTime(job.finished_at)}`);
  $("#job-time").textContent = timeParts.join(" · ");
  const summary = jobResultSummary(job);
  $("#job-result").textContent = summary
    ? `${summary}${job.error ? ` · 错误：${String(job.error).split("\n")[0]}` : ""}`
    : job.error
      ? `错误：${String(job.error).split("\n")[0]}`
      : "";
  const logs = (job.logs || []).slice(-14).map((entry) => entry.message);
  $("#job-log").textContent = logs.join("\n");
  $("#job-log").scrollTop = $("#job-log").scrollHeight;
}

function renderPipeline(payload) {
  state.pipeline = payload;
  const warnings = $("#pipeline-warnings");
  warnings.replaceChildren();
  if (payload.warnings && payload.warnings.length) {
    warnings.hidden = false;
    for (const warning of payload.warnings) warnings.appendChild(el("div", "", warning));
  } else {
    warnings.hidden = true;
  }

  const stages = $("#pipeline-stages");
  stages.replaceChildren();
  const active = payload.active;
  const busy = Boolean(active || state.activeJobId);
  for (const stage of payload.stages || []) {
    const item = el("li", "pipeline-stage");
    item.dataset.status = stage.status;
    const top = el("div", "stage-top");
    append(
      top,
      el("span", `stage-dot ${stage.status}`),
      el("span", "stage-label", stage.label),
      el("span", "stage-status", STAGE_STATUS_LABELS[stage.status] || stage.status),
    );
    const detail = el("div", "stage-detail", stage.detail);
    const meta = el("div", "stage-meta");
    const time = el("span", "stage-time", stage.updated_at ? fmtDateTime(stage.updated_at) : "—");
    time.title = stage.updated_at || "";
    const run = el("button", "stage-run", stage.run_label || "运行");
    run.type = "button";
    run.disabled = busy;
    run.addEventListener("click", () => submitJob(stage.action, pipelineOptions(stage.action)));
    append(meta, time, run);
    append(item, top, detail, meta);
    stages.appendChild(item);
  }

  const config = payload.config || {};
  const setOption = (selector, value) => {
    const input = $(selector);
    if (document.activeElement !== input && value !== undefined && value !== null) {
      input.value = value;
    }
  };
  setOption("#opt-analyze-limit", config.analysis_limit);
  setOption("#opt-deep-limit", config.deep_limit);
  setOption("#opt-biz-limit", config.biz_limit);

  const lastJob = active || (payload.jobs && payload.jobs[0]);
  renderJob(lastJob || null);
  if (active && state.activeJobId !== active.id) startJobPolling(active.id);
  if (!active && state.activeJobId) {
    stopJobPolling();
    state.activeJobId = null;
  }
  $("#run-full").disabled = busy;
}

async function loadPipeline() {
  try {
    const payload = await api("/api/pipeline");
    renderPipeline(payload);
  } catch (error) {
    showToast(`流程状态读取失败：${error.message}`, "error");
  }
}

function submitJob(action, options) {
  if (state.pipeline?.active || state.activeJobId) {
    showToast("已有任务在运行，请等待完成");
    return;
  }
  api("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, options }),
  })
    .then((result) => {
      const job = result.job;
      state.activeJobId = job.id;
      renderJob(job);
      startJobPolling(job.id);
      showToast(`已提交：${job.label}`);
      loadPipeline();
    })
    .catch((error) => showToast(`提交失败：${error.message}`, "error"));
}

function startJobPolling(jobId) {
  stopJobPolling();
  state.activeJobId = jobId;
  const tick = async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      renderJob(job);
      if (["success", "partial", "failed"].includes(job.status)) {
        state.activeJobId = null;
        stopJobPolling();
        showToast(
          job.status === "failed" ? `任务失败：${job.label}` : `任务完成：${job.label}`,
          job.status === "failed" ? "error" : "",
        );
        await loadPipeline();
        await loadList();
        await loadDaily(true);
        if (state.detailId) loadDetail(state.detailId);
        return;
      }
    } catch (error) {
      showToast(`任务状态读取失败：${error.message}`, "error");
    }
    state.jobTimer = setTimeout(tick, 1200);
  };
  tick();
}

function stopJobPolling() {
  if (state.jobTimer) clearTimeout(state.jobTimer);
  state.jobTimer = null;
}

async function loadAll() {
  state.daily = null;
  await loadList();
  await loadDaily(true);
  await loadPipeline();
  if (state.detailId) loadDetail(state.detailId);
}

// -- routing / init ---------------------------------------------------------
function route() {
  const hash = location.hash || "#/";
  const match = hash.match(/^#\/repo\/(\d+)$/);
  if (match) {
    loadDetail(Number(match[1]));
  } else {
    state.detailId = null;
    state.detail = null;
    $("#detail-view").hidden = true;
    $("#list-view").hidden = false;
    if (!state.data) loadList();
  }
}

function bindEvents() {
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#export-btn").addEventListener("click", exportCsv);
  $("#refresh-btn").addEventListener("click", loadAll);
  $("#pipeline-refresh").addEventListener("click", loadPipeline);
  $("#run-full").addEventListener("click", () =>
    submitJob("full", pipelineOptions("full")),
  );
  $("#view-cards").addEventListener("click", () => {
    state.view = "cards";
    localStorage.setItem("repo-scout-view", state.view);
    applyView();
  });
  $("#view-table").addEventListener("click", () => {
    state.view = "table";
    localStorage.setItem("repo-scout-view", state.view);
    applyView();
  });
  $("#reset-filters").addEventListener("click", resetFilters);
  $("#rail-toggle").addEventListener("click", () => setRailOpen(true));
  $("#rail-close").addEventListener("click", () => setRailOpen(false));
  $("#rail-scrim").addEventListener("click", () => setRailOpen(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setRailOpen(false);
      if (state.detailId !== null) location.hash = "#/";
    }
  });

  $("#q").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      readFilters();
      state.page = 1;
      loadList();
    }, 220);
  });
  for (const id of [
    "language",
    "license",
    "paid-version",
    "complexity",
    "min-delta7",
    "min-repro",
    "min-biz",
    "max-competitors",
    "risk",
    "include-gpl",
    "include-giant",
    "watched-only",
  ]) {
    $(`#${id}`).addEventListener("change", () => {
      readFilters();
      state.page = 1;
      loadList();
    });
  }
  $("#min-stars").addEventListener("change", () => {
    readFilters();
    state.page = 1;
    loadList();
  });
  $("#sort-select").addEventListener("change", (event) => {
    state.sort = event.target.value;
    state.page = 1;
    loadList();
  });
  $("#prev-page").addEventListener("click", () => {
    if (state.page > 1) {
      state.page--;
      loadList();
    }
  });
  $("#next-page").addEventListener("click", () => {
    state.page++;
    loadList();
  });
  $$(".th-sort").forEach((button) => {
    button.addEventListener("click", () => {
      const sort = button.dataset.sort;
      if (state.sort === sort) state.order = state.order === "desc" ? "asc" : "desc";
      else {
        state.sort = sort;
        state.order = "desc";
      }
      state.page = 1;
      syncSortSelect();
      syncSortHeaders();
      loadList();
    });
  });
  window.addEventListener("hashchange", route);
}

async function init() {
  initTheme();
  bindEvents();
  await loadList();
  await loadDaily();
  await loadPipeline();
  route();
}

init();
