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
    $("job-extra").querySelectorAll("label").forEach((lab) => {
      const el = lab.querySelector("input, select");
      const g = el?.dataset.group;
      const name = el?.name || "";
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
    });
  });

  function pane(title, obj) {
    return "<h3>" + title + "</h3><pre>" +
      JSON.stringify(obj, null, 2) + "</pre>";
  }

  async function showRun(id) {
    selectedRun = id;
    const rec = await jget("/api/journal/runs/" + id);
    $("run-detail").innerHTML =
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
          "<td>" + (r.regime || "") + (r.trend_waning ? " waning" : "") + "</td>" +
          "<td>" + (r.error ? "yes" : "") + "</td>";
        tr.addEventListener("click", () => showRun(r.run_id));
        tb.appendChild(tr);
      });
    } catch (err) {
      tb.innerHTML = "<tr><td colspan=\"8\">journal load failed</td></tr>";
    }
  }

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
