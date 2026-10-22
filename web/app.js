const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = (p) => fetch(p).then(r => r.json());
const pct = (x) => x == null ? "" : Math.round(x * 100) + "%";
const fmt = (x, d = 1) => x == null ? "" : (+x).toFixed(d);

let META = null;

// ---------- navigation ----------
$$("nav button").forEach(b => b.onclick = () => {
  $$("nav button").forEach(x => x.classList.toggle("active", x === b));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + b.dataset.view));
  if (b.dataset.view === "board") drawBoard();
});

async function init() {
  META = await api("/api/meta");
  $("#meta").textContent = `${META.cohorts.reduce((a, c) => a + c.n, 0)} subjects · ${META.n_checks} checks · ${META.targets.length} targets`;
  const cats = [...new Set(META.targets.map(t => t.category))];
  cats.forEach(c => $("#board-cat").append(new Option(c, c)));
  META.cohorts.forEach(c => $("#subj-cohort").append(new Option(`${c.cohort} (${c.n})`, c.cohort)));
  buildPlanForm();
  drawBoard();
  loadTable();
  loadTargets();
  loadSubjects();
}

// ---------- board ----------
["#board-min-n", "#board-min-lift", "#board-cat", "#board-controllable"].forEach(s => $(s).onchange = drawBoard);

async function drawBoard() {
  const minN = +$("#board-min-n").value, minLift = +$("#board-min-lift").value;
  const cat = $("#board-cat").value, ctrl = $("#board-controllable").checked;
  const g = await api(`/api/graph?min_n=${minN}&min_lift=${minLift}`);
  let targets = g.targets.filter(t => !cat || t.category === cat);
  let inputs = g.inputs.filter(i => !ctrl || i.controllable);
  const tk = new Set(targets.map(t => t.key)), ik = new Set(inputs.map(i => i.key));
  let edges = g.edges.filter(e => tk.has(e.target) && ik.has(e.input));
  const used = new Set(edges.flatMap(e => [e.input, e.target]));
  inputs = inputs.filter(i => used.has(i.key)); targets = targets.filter(t => used.has(t.key));

  const box = $("#board"); box.innerHTML = "";
  const W = box.clientWidth, H = box.clientHeight;
  const svg = d3.select(box).append("svg").attr("viewBox", [0, 0, W, H]);
  const xIn = W * 0.18, xOut = W * 0.72;
  const yIn = d3.scalePoint().domain(inputs.map(i => i.key)).range([40, H - 40]).padding(0.5);
  // sort targets by category then page-one rate so related sites cluster
  targets.sort((a, b) => a.category.localeCompare(b.category) || b.page_one_rate - a.page_one_rate);
  const yOut = d3.scalePoint().domain(targets.map(t => t.key)).range([30, H - 30]).padding(0.5);
  const pos = {};
  inputs.forEach(i => pos["in:" + i.key] = [xIn, yIn(i.key)]);
  targets.forEach(t => pos["out:" + t.key] = [xOut, yOut(t.key)]);
  const wScale = d3.scaleLinear().domain([minLift, d3.max(edges, e => Math.min(e.lift, 20)) || 5]).range([0.6, 7]).clamp(true);
  const rScale = d3.scaleSqrt().domain([0, 1]).range([4, 16]);

  const link = svg.append("g").selectAll("path").data(edges).join("path")
    .attr("class", "link")
    .attr("stroke-width", e => wScale(Math.min(e.lift, 20)))
    .attr("d", e => { const [x1, y1] = pos["in:" + e.input], [x2, y2] = pos["out:" + e.target]; const mx = (x1 + x2) / 2; return `M${x1},${y1}C${mx},${y1} ${mx},${y2} ${x2},${y2}`; });

  const node = svg.append("g").selectAll("g").data([...inputs.map(i => ({ ...i, kind: "input" })), ...targets.map(t => ({ ...t, kind: "target" }))])
    .join("g").attr("class", d => `node ${d.kind} ${d.category || ""}`)
    .attr("transform", d => `translate(${pos[(d.kind === "input" ? "in:" : "out:") + d.key]})`);
  node.append("circle").attr("r", d => d.kind === "input" ? 6 : rScale(d.page_one_rate || 0));
  node.append("text").attr("x", d => d.kind === "input" ? -12 : 20).attr("dy", 4)
    .attr("text-anchor", d => d.kind === "input" ? "end" : "start")
    .text(d => d.kind === "input" ? `${d.label}${d.noise > 1 ? " ·" + d.noise : ""}` : `${d.label}  ${pct(d.page_one_rate)}`);

  svg.append("text").attr("x", xIn).attr("y", 18).attr("text-anchor", "end").attr("class", "hint").style("fill", "var(--muted)").text("inputs you control");
  svg.append("text").attr("x", xOut + 20).attr("y", 18).attr("class", "hint").style("fill", "var(--muted)").text("pages that appear · page-one rate");

  let locked = null;
  const hit = (e, n) => n && ((n.kind === "input" && e.input === n.key) || (n.kind === "target" && e.target === n.key));
  function focus(n) {
    const keep = new Set(edges.filter(e => hit(e, n)).flatMap(e => ["in:" + e.input, "out:" + e.target]));
    if (n) keep.add((n.kind === "input" ? "in:" : "out:") + n.key);
    node.classed("dim", d => n && !keep.has((d.kind === "input" ? "in:" : "out:") + d.key));
    link.classed("dim", e => n && !hit(e, n)).classed("hi", e => n && hit(e, n));
    const d = $("#board-detail");
    if (!n) { d.textContent = ""; return; }
    const es = edges.filter(e => hit(e, n)).sort((a, b) => b.lift - a.lift);
    d.innerHTML = es.map(e => {
      const other = n.kind === "input" ? g.targets.find(t => t.key === e.target).label : g.inputs.find(i => i.key === e.input).label;
      return `<span class="tag">${other}: ${pct(e.p_with)} vs ${pct(e.p_without)} · lift ${fmt(e.lift)} · n ${e.n}${e.lag != null ? " · " + e.lag + "d" : ""}</span>`;
    }).join(" ");
  }
  node.on("mouseenter", (ev, d) => !locked && focus(d)).on("mouseleave", () => !locked && focus(null))
    .on("click", (ev, d) => { locked = (locked && locked.key === d.key && locked.kind === d.kind) ? null : d; focus(locked); });
  svg.on("click", (ev) => { if (ev.target.tagName === "svg") { locked = null; focus(null); } });
}

// ---------- plan ----------
function buildPlanForm() {
  const inp = $("#plan-inputs"); inp.innerHTML = "";
  META.inputs.filter(i => i.controllable).forEach(i => {
    inp.insertAdjacentHTML("beforeend", `<label><input type="checkbox" value="${i.key}"> ${i.label} <span class="noise">n${i.noise}${i.region && i.region !== "global" ? " " + i.region : ""}</span></label>`);
  });
  const pg = $("#plan-pages"); pg.innerHTML = "";
  META.targets.filter(t => t.category !== "people_search").forEach(t => {
    pg.insertAdjacentHTML("beforeend", `<label><input type="checkbox" value="${t.key}"> ${t.label}</label>`);
  });
  $("#plan-run").onclick = runPlan;
}

async function runPlan() {
  const inputs = $$("#plan-inputs input:checked").map(x => x.value).join(",");
  const pages = $$("#plan-pages input:checked").map(x => x.value).join(",");
  const r = await api(`/api/simulate?inputs=${inputs}&pages=${pages}&country=${$("#plan-country").value}&max_noise=${$("#plan-noise").value}`);
  renderPlan(r, $("#plan-out"));
}

function renderPlan(r, el) {
  if (!r.steps.length) { el.innerHTML = `<p class="hint">Nothing left to recommend at this noise level, or not enough data yet. Run more checks and <code>analyse</code>.</p>`; return; }
  const tl = (k) => (META.targets.find(t => t.key === k) || {}).label || k;
  el.innerHTML = `<h2>Plan</h2><p class="hint">${r.steps.length} steps · predicted new pages: ${r.predicted_pages.length}</p>` +
    r.steps.map((s, i) => `<div class="step"><h4>${i + 1}. ${s.label} <span class="hint">noise ${s.noise} · score ${s.score}</span></h4>
      <div class="pages"><div class="lab hint">expected page</div><div class="hint">P</div><div class="hint">lag</div><div class="hint">page one</div>
      ${s.expected_pages.map(p => `<div class="lab">${p.label}</div><div>${pct(p.p)}</div><div>${p.lag_days != null ? p.lag_days + "d" : "–"}</div><div>${pct(p.page_one_rate)}</div>`).join("")}</div></div>`).join("") +
    `<h3>Predicted page set after all steps</h3><p>${[...r.have_pages, ...r.predicted_pages].map(k => `<span class="tag ${r.have_pages.includes(k) ? "" : "on"}">${tl(k)}</span>`).join("")}</p>`;
}

// ---------- trigger table ----------
async function loadTable() {
  const rows = await api("/api/trigger-table");
  const il = Object.fromEntries(META.inputs.map(i => [i.key, i.label])), tl = Object.fromEntries(META.targets.map(t => [t.key, t.label]));
  const tb = $("#table tbody");
  let sortKey = "lift", dir = -1;
  const render = () => {
    const f = $("#table-filter").value.toLowerCase();
    const data = rows.filter(r => !f || il[r.input].toLowerCase().includes(f) || tl[r.target].toLowerCase().includes(f))
      .sort((a, b) => ((a[sortKey] ?? -1) > (b[sortKey] ?? -1) ? 1 : -1) * dir);
    tb.innerHTML = data.map(r => `<tr><td>${il[r.input]}</td><td>${tl[r.target]}</td><td class="num">${r.n_with}</td><td class="num">${r.n_without}</td>
      <td class="num"><span class="bar" style="width:${r.p_with * 60}px"></span>${pct(r.p_with)}</td><td class="num">${pct(r.p_without)}</td>
      <td class="num">${r.lift == null ? "–" : fmt(r.lift)}</td><td class="num">${r.lag == null ? "–" : r.lag}</td><td class="num">${r.n_lag}</td></tr>`).join("");
  };
  const keys = ["input", "target", "n_with", "n_without", "p_with", "p_without", "lift", "lag", "n_lag"];
  $$("#table th").forEach((th, i) => th.onclick = () => { dir = sortKey === keys[i] ? -dir : -1; sortKey = keys[i]; render(); });
  $("#table-filter").oninput = render;
  render();
}

// ---------- targets ----------
async function loadTargets() {
  const g = await api("/api/graph?min_n=1&min_lift=0");
  $("#targets tbody").innerHTML = g.targets.sort((a, b) => b.page_one_rate - a.page_one_rate).map(t => `<tr>
    <td>${t.label}</td><td>${t.category}</td><td>${t.region || ""}</td>
    <td class="num"><span class="bar" style="width:${t.presence_rate * 60}px"></span>${pct(t.presence_rate)}</td>
    <td class="num"><span class="bar" style="width:${t.page_one_rate * 60}px"></span>${pct(t.page_one_rate)}</td>
    <td class="num">${pct(t.page_two_rate)}</td><td class="num">${t.median_position ?? "–"}</td></tr>`).join("");
}

// ---------- subjects ----------
async function loadSubjects() {
  const cohort = $("#subj-cohort").value, q = $("#subj-q").value;
  const r = await api(`/api/subjects?cohort=${encodeURIComponent(cohort)}&q=${encodeURIComponent(q)}&limit=200`);
  $("#subj-count").textContent = `${r.items.length} of ${r.total}`;
  $("#subjects tbody").innerHTML = r.items.map(s => `<tr class="row" data-id="${s.id}"><td>${s.name}${s.is_client ? " ★" : ""}</td><td>${s.company || ""}</td><td>${s.country || ""}</td><td>${s.cohort}</td>
    <td class="num">${s.inputs.length}</td><td class="num">${s.pages.length}/${s.n_checked}</td><td class="hint">${s.last_checked ? s.last_checked.slice(0, 10) : "never"}</td></tr>`).join("");
  $$("#subjects tr.row").forEach(tr => tr.onclick = () => openSubject(tr.dataset.id));
}
$("#subj-cohort").onchange = loadSubjects; $("#subj-q").oninput = loadSubjects;

async function openSubject(id) {
  const s = await api(`/api/subjects/${id}`);
  const il = Object.fromEntries(META.inputs.map(i => [i.key, i.label])), tl = Object.fromEntries(META.targets.map(t => [t.key, t.label]));
  const el = $("#subject-detail");
  el.innerHTML = `<h2>${s.name}</h2><p class="hint">${s.title || ""} · ${s.company || ""} · ${s.country || ""} · ${s.cohort}</p>
    <h3>Inputs</h3><p>${s.inputs.map(i => `<span class="tag ${i.present ? "on" : "off"}" title="${i.url || ""}">${il[i.key] || i.key}${i.effective_at ? " " + i.effective_at.slice(0, 10) : ""}</span>`).join("")}</p>
    <h3>Pages</h3><p>${s.pages.filter(p => p.present).map(p => `<a class="tag on" href="${p.url || "#"}" target="_blank">${tl[p.target]}${p.position ? " #" + p.position : ""}</a>`).join("")}
    ${s.pages.filter(p => !p.present).map(p => `<span class="tag off">${tl[p.target]}</span>`).join("")}</p>
    <div id="subj-plan"></div>`;
  renderPlan(s.plan, $("#subj-plan"));
}


// ---------- jobs ----------
async function pollJobs() {
  if (!$("#view-jobs").classList.contains("active")) return;
  const st = await api("/api/jobs");
  $("#env-status").innerHTML = Object.entries(st.env).map(([k, v]) => `<span class="tag ${v ? "on" : "off"}">${k}</span>`).join(" ");
  $("#job-running").textContent = st.running ? `Running: ${st.running} (since ${st.started.slice(11, 19)} UTC)` : "Idle";
  const pre = $("#job-log"); const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  pre.textContent = st.log.join("\n"); if (atBottom) pre.scrollTop = pre.scrollHeight;
  $("#job-history").innerHTML = st.history.slice().reverse().map(h => `<div class="${h.ok ? "ok" : "bad"}">${h.at.slice(0, 16).replace("T", " ")} ${h.job} ${h.ok ? "ok" : "failed: " + h.error}</div>`).join("");
  $$("#view-jobs button[data-job]").forEach(b => b.disabled = !!st.running);
}
$$("#view-jobs button[data-job]").forEach(b => b.onclick = async () => {
  const job = b.dataset.job; let params = {};
  if (job === "florida_fetch") params = { n: +$("#fl-n").value };
  if (job === "companies_house") params = { n: +$("#ch-n").value };
  if (job === "check") params = { limit: +$("#ck-limit").value, cheap: $("#ck-cheap").checked };
  if (job === "delete_cohort") { params = { cohort: b.dataset.cohort }; if (!confirm(`Delete cohort ${b.dataset.cohort}?`)) return; }
  const r = await fetch(`/api/jobs/${job}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(params) });
  if (!r.ok) alert((await r.json()).detail);
  pollJobs();
});
setInterval(pollJobs, 2000);

init();
