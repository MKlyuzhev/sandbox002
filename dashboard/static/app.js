(function () {
  const $ = (id) => document.getElementById(id);
  const logEl = $("log");
  let es = null;
  let selectedRun = null;
  let schema = { fields: [] };
  let previewTimer = null;

  function chip(id, text, cls) {
    const el = $(id);
    el.textContent = text;
    el.className = "chip" + (cls ? " " + cls : "");
  }

  async function jget(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(path + " " + res.status);
    return res.json();
  }

  function fmtTs(ts) {
    if (!ts) return "—";
    return String(ts).replace("T", " ").slice(0, 19);
  }

  function fmtN(n, digits) {
    if (n == null || n === "") return "";
    const x = Number(n);
    if (!Number.isFinite(x)) return "";
    return x.toFixed(digits);
  }

  function shortId(id) {
    if (!id) return "";
    return id.length > 8 ? id.slice(0, 8) : id;
  }

  async function refreshStrip() {
    try {
      const s = await jget("/api/status");
      const last = s.last_run;
      if (last) {
        const cls = last.error ? "err" : last.action === "wait" ? "wait" : "ok";
        chip("chip-action", "action: " + last.action + " " + (last.instrument || ""), cls);
      } else {
        chip("chip-action", "action: —");
      }
      const models = (s.models || []).join(", ") || "none";
      chip("chip-ollama", "ollama: " + (s.ollama ? "up" : "down") + " [" + models + "]", s.ollama ? "ok" : "err");
      const g = s.gpu || {};
      if (g.ok) {
        const mem = g.memory_used_mib + "/" + g.memory_total_mib + " MiB";
        chip("chip-gpu", "gpu: " + g.utilization_gpu + "% " + mem, "ok");
      } else {
        chip("chip-gpu", "gpu: " + (g.error || "n/a"), "wait");
      }
      const m = s.mt4 || {};
      chip(
        "chip-mt4",
        "mt4: " + (m.ea_ok ? (m.symbol || "") + " " + (m.timeframe || "") : "down"),
        m.ea_ok ? "ok" : "wait"
      );
      const job = s.job || {};
      chip("chip-job", "job: " + (job.running ? job.cmd : "idle"), job.running ? "wait" : "");
    } catch (err) {
      chip("chip-action", "status error", "err");
    }
  }

  function connectStream() {
    if (es) es.close();
    es = new EventSource("/api/jobs/stream");
    es.onmessage = (ev) => {
      let line = ev.data;
      try {
        line = JSON.parse(ev.data);
      } catch (_) {}
      logEl.textContent += line + "\n";
      logEl.scrollTop = logEl.scrollHeight;
      if (typeof line === "string" && line.startsWith("{")) {
        try {
          const rec = JSON.parse(line);
          if (rec.action) {
            $("last-result").textContent =
              "Last result: " + rec.action +
              (rec.risk && rec.risk.reasons && rec.risk.reasons.length
                ? " — " + rec.risk.reasons.join("; ")
                : "");
          }
        } catch (_) {}
      }
      if (typeof line === "string" && (line.startsWith("[exit") || line === "[done]")) {
        refreshStrip();
        loadJournal();
      }
    };
  }

  function fieldInput(f) {
    const wrap = document.createElement("label");
    if (f.type === "bool") wrap.className = "check";
    wrap.append(f.label || f.name);
    let el;
    if (f.type === "enum" && f.choices) {
      el = document.createElement("select");
      if (f.default == null) {
        const blank = document.createElement("option");
        blank.value = "";
        blank.textContent = "(default)";
        blank.selected = true;
        el.appendChild(blank);
      }
      f.choices.forEach((c) => {
        const o = document.createElement("option");
        o.value = c;
        o.textContent = c;
        if (c === f.default) o.selected = true;
        el.appendChild(o);
      });
    } else if (f.type === "bool") {
      el = document.createElement("input");
      el.type = "checkbox";
      el.checked = !!f.default;
    } else {
      el = document.createElement("input");
      el.type = f.type === "int" || f.type === "float" ? "number" : "text";
      if (f.type === "float") el.step = "any";
      if (f.placeholder) el.placeholder = f.placeholder;
      if (f.default != null && f.group === "primary") el.value = f.default;
    }
    el.name = f.name;
    el.dataset.group = f.group;
    wrap.appendChild(el);
    return wrap;
  }

  function renderForm() {
    const primary = $("job-primary");
    const extra = $("job-extra");
    primary.innerHTML = "";
    extra.innerHTML = "";
    (schema.fields || []).forEach((f) => {
      const node = fieldInput(f);
      if (f.group === "primary") primary.appendChild(node);
      else extra.appendChild(node);
    });
    syncExtraVisibility();
    updatePreview();
  }

  function syncExtraVisibility() {
    const cmd = document.querySelector('[name="cmd"]')?.value || "agent.run";
    document.querySelectorAll("#job-primary label, #job-extra label").forEach((lab) => {
      const el = lab.querySelector("input, select");
      const g = el?.dataset.group;
      const name = el?.name || "";
      if (cmd === "agent.mt4_clear") {
        lab.style.display =
          name === "cmd" ||
          name === "instrument" ||
          name === "granularity" ||
          name === "mt4_prefix" ||
          name === "quiet"
            ? ""
            : "none";
        return;
      }
      if (g === "primary") {
        lab.style.display = "";
        return;
      }
      if (cmd === "agent.executor") {
        lab.style.display = g === "executor" ? "" : "none";
        return;
      }
      if (cmd === "agent.walk") {
        const walkRun = [
          "from_time",
          "to_time",
          "balance",
          "risk_fraction",
          "exposure_cap",
          "mt4_prefix",
          "quiet",
          "no_journal",
        ];
        lab.style.display = g === "walk" || walkRun.indexOf(name) >= 0 ? "" : "none";
        return;
      }
      lab.style.display = g === "run" ? "" : "none";
    });
  }

  function collectSpec() {
    const fd = new FormData($("job-form"));
    const cmd = fd.get("cmd") || "agent.run";
    const body = { cmd };
    (schema.fields || []).forEach((f) => {
      if (f.name === "cmd") return;
      const el = $("job-form").elements[f.name];
      if (!el) return;
      const hidden = el.closest("label") && el.closest("label").style.display === "none";
      if (hidden) return;
      if (f.type === "bool") {
        body[f.name] = el.checked;
        return;
      }
      const raw = (el.value || "").trim();
      if (raw === "") return;
      if (f.type === "int") body[f.name] = parseInt(raw, 10);
      else if (f.type === "float") body[f.name] = parseFloat(raw);
      else body[f.name] = raw;
    });
    return body;
  }

  async function updatePreview() {
    const body = collectSpec();
    try {
      const res = await fetch("/api/jobs/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (res.ok) {
        $("argv-preview").textContent = "$ " + (data.argv || []).join(" ");
      } else {
        const detail = data.detail;
        $("argv-preview").textContent = "invalid: " + (
          typeof detail === "string" ? detail : JSON.stringify(detail)
        );
      }
    } catch (_) {
      $("argv-preview").textContent = "$ …";
    }
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(updatePreview, 200);
  }

  $("job-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = collectSpec();
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail;
      logEl.textContent += "[error] " + (
        typeof detail === "string" ? detail : JSON.stringify(detail)
      ) + "\n";
    }
    refreshStrip();
  });

  $("job-form").addEventListener("input", () => {
    syncExtraVisibility();
    schedulePreview();
  });
  $("job-form").addEventListener("change", () => {
    syncExtraVisibility();
    schedulePreview();
  });

  $("btn-stop").addEventListener("click", async () => {
    await fetch("/api/jobs/stop", { method: "POST" });
    refreshStrip();
  });

  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "journal") loadJournal();
      if (btn.dataset.tab === "trades") loadTrades();
      if (btn.dataset.tab === "plans") loadPlans();
    });
  });

  function pane(title, obj) {
    return "<h3>" + title + "</h3><pre>" +
      JSON.stringify(obj, null, 2) + "</pre>";
  }

  async function showRun(id) {
    selectedRun = id;
    const rec = await jget("/api/journal/runs/" + id);
    let extra = "";
    if (rec.walk_id) {
      try {
        const walk = await jget("/api/journal/walks/" + rec.walk_id);
        extra = pane("Walk equity (simulated)", walk.equity);
      } catch (_) {}
    }
    $("run-detail").innerHTML =
      extra +
      pane("Regime", rec.regime) +
      pane("Proposal", rec.proposal) +
      pane("Fill", rec.fill) +
      pane("Policy", rec.risk) +
      pane("Trace", rec.tool_trace);
  }

  async function loadJournal() {
    const tb = document.querySelector("#runs-table tbody");
    if (!tb) return;
    try {
      const data = await jget("/api/journal/runs?limit=80");
      tb.innerHTML = "";
      (data.runs || []).forEach((r) => {
        const tr = document.createElement("tr");
        if (r.run_id === selectedRun) tr.className = "sel";
        tr.innerHTML =
          "<td>" + fmtTs(r.ts) + "</td>" +
          "<td>" + (r.instrument || "") + " " + (r.granularity || "") + "</td>" +
          "<td>" + (r.action || "") + "</td>" +
          "<td>" + (r.side || "") + "</td>" +
          "<td>" + (r.stop != null ? r.stop : "") + "</td>" +
          "<td>" + (r.target != null ? r.target : "") + "</td>" +
          "<td>" + fmtN(r.r_realized, 2) + "</td>" +
          "<td>" + fmtN(r.pnl, 2) + "</td>" +
          "<td>" + fmtN(r.equity_after, 2) + "</td>" +
          "<td>" + shortId(r.walk_id) + "</td>" +
          "<td>" + (r.regime || "") + (r.trend_waning ? " waning" : "") + "</td>" +
          "<td>" + (r.error ? "yes" : "") + "</td>";
        tr.addEventListener("click", () => showRun(r.run_id));
        tb.appendChild(tr);
      });
    } catch (err) {
      tb.innerHTML = "<tr><td colspan=\"12\">journal load failed</td></tr>";
    }
  }

  let selectedTrade = null;

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function fmtUnix(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16);
  }

  async function jpost(path) {
    const res = await fetch(path, { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || (path + " " + res.status));
    return body;
  }

  function tradeMsg(text, bad) {
    const el = $("trade-msg");
    el.textContent = text || "";
    el.className = "hint" + (bad ? " bad" : "");
  }

  async function showTrade(ticket) {
    selectedTrade = ticket;
    $("trade-actions").hidden = false;
    const rec = await jget("/api/trader/trades/" + ticket);
    const o = rec.order || {};
    const revs = (rec.revisions || []).map((r) =>
      fmtUnix(r.ts) + "  SL " + (r.sl ?? "—") + "  TP " + (r.tp ?? "—") + "  (" + r.source + ")"
    );
    const links = (rec.links || []).map((l) =>
      l.object_name + " — " + (l.object ? l.object.type : "?") + ", " + l.source + " (" + (l.reason || "") + ")"
    );
    const analyses = (rec.analyses || []).map((a) =>
      "<div class=\"analysis\"><h4>#" + a.id + " " + esc(a.kind) + " — " + esc(a.author) +
      " — " + fmtUnix(a.created_ts) + " UTC" +
      (a.levels && a.levels.length ? " — " + a.levels.length + " levels" : "") +
      "</h4><pre>" + esc(a.body_md) + "</pre></div>"
    ).join("");
    $("trade-detail").innerHTML =
      "<h3>#" + o.ticket + " " + esc(o.symbol) + " " + esc(o.type) + " " + (o.lots ?? "") + "</h3>" +
      "<pre>open  " + fmtUnix(o.open_time) + " (broker)  @ " + o.open_price +
      "\nclose " + fmtUnix(o.close_time) + " (broker)  @ " + (o.close_price ?? "—") +
      "\nplanned SL " + (o.sl_open ?? "—") + "  TP " + (o.tp_open ?? "—") +
      "\nfinal   SL " + (o.sl ?? "—") + "  TP " + (o.tp ?? "—") +
      "\nP&L " + (o.profit ?? "—") + "   status " + esc(o.status) + "</pre>" +
      "<h3>SL/TP revisions</h3><pre>" + (revs.join("\n") || "none recorded") + "</pre>" +
      "<h3>Linked objects</h3><pre>" + esc(links.join("\n") || "none — select lines in MT4, then Link") + "</pre>" +
      pane("Review (code-scored)", rec.review) +
      "<h3>Analyses</h3>" + (analyses || "<pre>none</pre>");
  }

  async function loadTrades() {
    const tb = document.querySelector("#trades-table tbody");
    if (!tb) return;
    try {
      const data = await jget("/api/trader/trades");
      tb.innerHTML = "";
      (data.trades || []).forEach((t) => {
        const tr = document.createElement("tr");
        if (t.ticket === selectedTrade) tr.className = "sel";
        const scores = [t.model_fit, t.entry_quality, t.exit_quality]
          .map((x) => (x == null ? "—" : fmtN(x, 2))).join(" / ");
        tr.innerHTML =
          "<td>" + t.ticket + "</td>" +
          "<td>" + esc(t.symbol) + " " + esc(t.timeframe || "") + "</td>" +
          "<td>" + esc(t.side) + "</td>" +
          "<td>" + fmtUnix(t.open_time) + "</td>" +
          "<td>" + (t.open_price ?? "") + "</td>" +
          "<td>" + (t.close_price ?? "") + "</td>" +
          "<td>" + fmtN(t.profit, 2) + "</td>" +
          "<td>" + (t.linked || "") + "</td>" +
          "<td>" + (t.analyses || "") + "</td>" +
          "<td>" + scores + "</td>";
        tr.addEventListener("click", () => { showTrade(t.ticket); loadTrades(); });
        tb.appendChild(tr);
      });
    } catch (err) {
      tb.innerHTML = "<tr><td colspan=\"10\">trades load failed</td></tr>";
    }
  }

  $("btn-trade-link").addEventListener("click", async () => {
    if (!selectedTrade) return;
    try {
      const r = await jpost("/api/trader/trades/" + selectedTrade + "/link");
      tradeMsg("Linked: " + (r.linked || []).join(", "));
      showTrade(selectedTrade);
      loadTrades();
    } catch (err) { tradeMsg(err.message, true); }
  });

  $("btn-trade-draw").addEventListener("click", async () => {
    if (!selectedTrade) return;
    const lv = $("chk-trade-levels").checked ? "true" : "false";
    try {
      const r = await jpost("/api/trader/trades/" + selectedTrade + "/draw?analyses=" + lv);
      tradeMsg("Drawn " + r.objects_written + " objects on " + r.timeframe +
        (r.skipped && r.skipped.length ? "; skipped: " + r.skipped.join(", ") : ""));
    } catch (err) { tradeMsg(err.message, true); }
  });

  $("btn-trade-clear").addEventListener("click", async () => {
    if (!selectedTrade) return;
    const rec = await jget("/api/trader/trades/" + selectedTrade);
    try {
      await jpost("/api/trader/replay/clear?symbol=" + encodeURIComponent(rec.order.symbol) +
        "&ticket=" + selectedTrade);
      tradeMsg("Replay cleared.");
    } catch (err) { tradeMsg(err.message, true); }
  });

  // ---------------------------------------------------------------- plans
  // Order tickets for the trader to enter in MT4; nothing here sends orders.

  let selectedPlan = null;
  let planText = "";
  const planChecks = {};  // plan id -> last checks read, kept across re-sizing

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (err) { /* private window: inputs just are not remembered */ }
    return null;
  }

  function planMsg(text, bad) {
    const el = $("plan-msg");
    el.textContent = text || "";
    el.className = "hint" + (bad ? " bad" : "");
  }

  function orderLabel(p) {
    return esc((p.type || "").replace("_", " ")) + " " + (p.price ?? "");
  }

  async function loadPlans() {
    const tb = document.querySelector("#plans-table tbody");
    if (!tb) return;
    try {
      const data = await jget("/api/trader/plans");
      const all = $("chk-plans-all").checked;
      tb.innerHTML = "";
      (data.plans || []).filter((p) => all || p.status === "active").forEach((p) => {
        const tr = document.createElement("tr");
        if (p.id === selectedPlan) tr.className = "sel";
        tr.innerHTML =
          "<td>" + p.id + "</td>" +
          "<td>" + esc(p.symbol) + "</td>" +
          "<td>" + orderLabel(p) + "</td>" +
          "<td>" + (p.sl ?? "") + " / " + (p.tp ?? "") + "</td>" +
          "<td class=\"st-" + esc(p.status) + "\">" + esc(p.status) + "</td>" +
          "<td>" + fmtUnix(p.created_ts) + "</td>" +
          "<td>" + (p.ticket ?? "") + (p.attach_reason ? " (" + esc(p.attach_reason) + ")" : "") + "</td>";
        tr.addEventListener("click", () => { showPlan(p.id); });
        tb.appendChild(tr);
      });
      if (!tb.children.length) tb.innerHTML = "<tr><td colspan=\"7\">no plans</td></tr>";
    } catch (err) {
      tb.innerHTML = "<tr><td colspan=\"7\">plans load failed</td></tr>";
    }
  }

  function sizingText(t) {
    const s = t.sizing;
    if (!s) return t.sizing_error ? "sizing: " + t.sizing_error : "enter a risk amount to size it";
    return "risk " + s.risk_amount + " " + s.account_ccy + "  ->  " + s.lots + " lot" +
      (s.too_small ? "  (below 0.01: risk too small for this stop)" : "") +
      "\nstop " + s.stop_pips + " pips, target " + s.target_pips + " pips, R " + s.r_multiple +
      "\nat " + s.lots + " lot: lose " + s.risk_at_lots + ", win " + s.reward_at_lots + " " + s.account_ccy;
  }

  async function showPlan(id) {
    selectedPlan = id;
    loadPlans();
    $("plan-risk").hidden = false;
    $("plan-actions").hidden = false;
    planMsg("");
    const amount = $("plan-risk-amount").value;
    const ccy = $("plan-risk-ccy").value || store("planCcy") || "";
    const q = new URLSearchParams();
    if (amount) q.set("risk", amount);
    if (ccy) q.set("ccy", ccy);
    $("plan-detail").textContent = "Loading…";
    try {
      const r = await jget("/api/trader/plans/" + id + "?" + q.toString());
      const t = r.ticket;
      const o = t.order;
      if (!amount && o.risk) $("plan-risk-amount").value = o.risk.amount;
      if (!$("plan-risk-ccy").value) $("plan-risk-ccy").value = (t.sizing && t.sizing.account_ccy) || (o.risk && o.risk.ccy) || "USD";
      planText = t.text;
      const d = t.price ? t.price.distance_pips : null;
      const px = t.price
        ? "bid " + t.price.bid + "  ask " + t.price.ask + "  ->  " +
          (o.type.startsWith("sell") ? "bid" : "ask") + " must " + (d < 0 ? "fall " : "rise ") +
          Math.abs(d) + " pips to trigger " + o.price
        : (t.price_error || "no live price");
      const checks = planChecks[id]
        ? checkRows(planChecks[id])
        : (o.checks || []).map((c) => "<tr><td>" + esc(c.label) + "</td><td>" +
          esc(c.timeframe + " " + c.leg + " " + c.line) + "</td><td>" + esc(c.rule) +
          "</td><td colspan=\"3\" class=\"hint\">press Refresh checks</td></tr>").join("");
      $("plan-detail").innerHTML =
        "<h3>#" + r.plan.id + " " + esc(r.plan.symbol) + " — " + esc(r.plan.status) +
        (r.plan.ticket ? " — ticket " + r.plan.ticket : "") + "</h3>" +
        "<h3>Order ticket</h3><pre class=\"ticket\">" + esc(t.text) + "</pre>" +
        "<h3>Size</h3><pre>" + esc(sizingText(t)) + "</pre>" +
        "<h3>Price</h3><pre>" + esc(px) + "</pre>" +
        "<h3>Checks</h3><table id=\"plan-checks\"><thead><tr><th>check</th><th>line</th><th>rule</th>" +
        "<th>last close</th><th>line</th><th>ok</th></tr></thead><tbody>" +
        (checks || "<tr><td colspan=\"6\">none</td></tr>") + "</tbody></table>" +
        "<h3>Plan</h3><pre>" + esc(r.plan.body_md) + "</pre>";
    } catch (err) {
      $("plan-detail").textContent = "plan load failed: " + err.message;
    }
  }

  function checkRows(checks) {
    return checks.map((c) =>
      "<tr><td>" + esc(c.label) + "</td><td>" + esc(c.timeframe + " " + c.leg + " " + c.line) +
      "</td><td>" + esc(c.rule) + "</td><td>" + (c.close ?? "") + " " + esc(c.bar || "") +
      "</td><td>" + (c.line_at_close ?? "") + "</td><td class=\"" +
      (c.ok === true ? "ok" : c.ok === false ? "bad" : "") + "\">" +
      (c.ok === true ? "holds" : c.ok === false ? "FAILED" : esc(c.error || "?")) + "</td></tr>"
    ).join("") || "<tr><td colspan=\"6\">none</td></tr>";
  }

  async function refreshChecks() {
    if (!selectedPlan) return;
    const id = selectedPlan;
    planMsg("Reading the grids…");
    try {
      const r = await jget("/api/trader/plans/" + id + "/checks");
      planChecks[id] = r.checks || [];
      if (id !== selectedPlan) return;
      document.querySelector("#plan-checks tbody").innerHTML = checkRows(planChecks[id]);
      planMsg("Checks read " + new Date().toISOString().slice(11, 16) + " UTC.");
    } catch (err) { planMsg(err.message, true); }
  }

  $("chk-plans-all").addEventListener("change", loadPlans);
  $("btn-plan-size").addEventListener("click", () => {
    store("planCcy", $("plan-risk-ccy").value.toUpperCase());
    if (selectedPlan) showPlan(selectedPlan);
  });
  $("btn-plan-checks").addEventListener("click", refreshChecks);
  $("btn-plan-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(planText);
      planMsg("Order copied. Enter it in MT4 (F9).");
    } catch (err) { planMsg("Copy failed; select the ticket text instead.", true); }
  });
  $("btn-plan-draw").addEventListener("click", async () => {
    if (!selectedPlan) return;
    try {
      const r = await jpost("/api/trader/plans/" + selectedPlan + "/draw");
      planMsg("Drawn " + r.objects_written + " levels on " + r.timeframe + ".");
    } catch (err) { planMsg(err.message, true); }
  });
  $("btn-plan-cancel").addEventListener("click", async () => {
    if (!selectedPlan) return;
    try {
      await jpost("/api/trader/plans/" + selectedPlan + "/status?status=cancelled");
      planMsg("Plan cancelled (the MT4 order, if any, is untouched).");
      showPlan(selectedPlan);
    } catch (err) { planMsg(err.message, true); }
  });
  $("btn-plan-link").addEventListener("click", async () => {
    const ticket = $("plan-link-ticket").value;
    if (!selectedPlan || !ticket) return;
    try {
      await jpost("/api/trader/plans/" + selectedPlan + "/link?ticket=" + encodeURIComponent(ticket));
      planMsg("Linked to ticket " + ticket + ".");
      showPlan(selectedPlan);
    } catch (err) { planMsg(err.message, true); }
  });
  try { $("plan-risk-ccy").value = store("planCcy") || ""; } catch (err) { /* ignore */ }

  function bar(pct) {
    const n = Math.max(0, Math.min(100, Number(pct) || 0));
    return '<div class="bar"><span style="width:' + n + '%"></span></div>';
  }

  async function refreshHost() {
    try {
      const h = await jget("/api/host");
      const g = h.gpu || {};
      const ram = h.ram || {};
      const memPct = g.memory_total_mib
        ? (100 * (g.memory_used_mib || 0) / g.memory_total_mib).toFixed(0)
        : 0;
      const ramUsed = ram.mem_total_mib && ram.mem_available_mib != null
        ? ram.mem_total_mib - ram.mem_available_mib
        : null;
      const ramPct = ram.mem_total_mib && ramUsed != null
        ? (100 * ramUsed / ram.mem_total_mib).toFixed(0)
        : 0;
      $("gpu-panel").innerHTML =
        "<p><strong>" + (g.name || "GPU") + "</strong> " +
        (g.ok ? "" : (g.error || "unavailable")) + "</p>" +
        "<p>util " + (g.utilization_gpu ?? "—") + "%</p>" + bar(g.utilization_gpu) +
        "<p>vram " + (g.memory_used_mib ?? "—") + " / " + (g.memory_total_mib ?? "—") + " MiB</p>" +
        bar(memPct) +
        "<p>power " + (g.power_draw_w ?? "—") + " W</p>" +
        "<p>RAM " + (ramUsed != null ? ramUsed.toFixed(0) : "—") + " / " +
        (ram.mem_total_mib ?? "—") + " MiB</p>" + bar(ramPct) +
        "<p>Ollama " + (h.ollama ? "up" : "down") + " — " +
        ((h.models || []).join(", ") || "no model resident") + "</p>";
    } catch (err) {
      $("gpu-panel").innerHTML = "<p>host poll failed</p>";
    }
  }

  connectStream();
  refreshStrip();
  refreshHost();
  setInterval(refreshStrip, 3000);
  setInterval(refreshHost, 1000);
  jget("/api/jobs/schema")
    .then((data) => {
      schema = data;
      renderForm();
    })
    .catch(() => {
      $("argv-preview").textContent = "schema load failed";
    });
})();
