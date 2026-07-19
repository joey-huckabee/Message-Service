/* Run-status board renderer — hand-authored, no external dependencies (L2-DASH-018).
   Reads the embedded run summaries from #runs-data and renders a filterable
   table; per-run stage detail is fetched lazily from the same-origin
   GET /runs/{run_id} endpoint on row expansion. */
(function () {
  "use strict";

  var IN_WORK = ["INITIATED", "AGGREGATING", "READY", "SENDING"];
  var TERMINAL = ["SENT", "FAILED", "ORPHANED"];
  var STATE_STYLE = {
    INITIATED:   { bg:"#eef2f7", ink:"#43566a", line:"#d8e0ea", dot:"#7b8da0", spin:false, label:"Initiated" },
    AGGREGATING: { bg:"#fdf4e3", ink:"#8a5a00", line:"#f0dcae", dot:"#e0930f", spin:true,  label:"Aggregating" },
    READY:       { bg:"#e8f6f4", ink:"#0b6b62", line:"#bfe6e0", dot:"#12a594", spin:false, label:"Ready" },
    SENDING:     { bg:"#e8f0fe", ink:"#1b52c4", line:"#c4d7fb", dot:"#2563eb", spin:true,  label:"Sending" },
    SENT:        { bg:"#e9f6ec", ink:"#1a7a37", line:"#c3e6cd", dot:"#22a13f", spin:false, label:"Sent" },
    FAILED:      { bg:"#fdeceb", ink:"#b3261e", line:"#f3c9c6", dot:"#e0453b", spin:false, label:"Failed" },
    ORPHANED:    { bg:"#f0ecf4", ink:"#6b4a86", line:"#ddd0e8", dot:"#8a63b0", spin:false, label:"Orphaned" }
  };
  var STAGE_COLOR = {
    PENDING:"#8a99a8", IN_PROGRESS:"#e0930f", SUBMITTED:"#2563eb",
    ACCEPTED:"#22a13f", RETRIED:"#e0930f", TIMEOUT:"#b3261e", FAILED:"#e0453b"
  };

  var dataEl = document.getElementById("runs-data");
  var RUNS = dataEl ? JSON.parse(dataEl.textContent) : [];
  var rowsEl = document.getElementById("rows");
  var sumEl = document.getElementById("summary");
  var countEl = document.getElementById("count");
  var quick = "inwork";
  var onlyState = null;
  var stageCache = {};

  function esc(s) {
    return String(s).replace(/[&<>]/g, function (c) {
      return { "&":"&amp;", "<":"&lt;", ">":"&gt;" }[c];
    });
  }
  function shortId(id) { return String(id).slice(0, 8); }
  function badge(state) {
    var s = STATE_STYLE[state] || { bg:"#eee", ink:"#333", line:"#ccc", dot:"#888", spin:false, label:state };
    var style = "--b-bg:" + s.bg + ";--b-ink:" + s.ink + ";--b-line:" + s.line + ";--b-dot:" + s.dot;
    return '<span class="badge ' + (s.spin ? "spin" : "") + '" style="' + style + '">' +
           '<span class="dot"></span>' + esc(s.label) + "</span>";
  }
  function rel(iso) {
    if (!iso) return "";
    var mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    var hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    return Math.round(hrs / 24) + "d ago";
  }
  function inSet(state) {
    if (quick === "all") return true;
    if (quick === "inwork") return IN_WORK.indexOf(state) !== -1;
    return TERMINAL.indexOf(state) !== -1;
  }

  function renderSummary() {
    var order = IN_WORK.concat(TERMINAL);
    var counts = {};
    order.forEach(function (s) { counts[s] = 0; });
    RUNS.forEach(function (r) { if (counts[r.state] !== undefined) counts[r.state]++; });
    var inwork = IN_WORK.reduce(function (a, s) { return a + counts[s]; }, 0);
    var cards = [{ key:"inwork", n:inwork, l:"In work", cls:"inwork" }].concat(
      order.map(function (s) {
        return { key:s, n:counts[s], l:(STATE_STYLE[s] ? STATE_STYLE[s].label : s), cls:"" };
      })
    );
    sumEl.innerHTML = cards.map(function (c) {
      var active = (quick === "inwork" && c.key === "inwork") || (onlyState && c.key === onlyState);
      return '<div class="stat ' + c.cls + " " + (active ? "active" : "") + '" data-key="' + c.key + '">' +
             '<div class="n">' + c.n + '</div><div class="l">' + esc(c.l) + "</div></div>";
    }).join("");
    Array.prototype.forEach.call(sumEl.querySelectorAll(".stat"), function (el) {
      el.onclick = function () {
        var k = el.dataset.key;
        if (k === "inwork") { setQuick("inwork"); }
        else { quick = "all"; syncQuickButtons(); onlyState = (onlyState === k) ? null : k; render(); }
      };
    });
  }

  function setQuick(q) { quick = q; onlyState = null; syncQuickButtons(); render(); }
  function syncQuickButtons() {
    Array.prototype.forEach.call(document.querySelectorAll("#quick button"), function (b) {
      b.classList.toggle("on", b.dataset.q === quick);
    });
  }

  function stageRowsHtml(stages) {
    if (!stages.length) return '<tr><td class="muted">no stages declared</td><td></td><td></td></tr>';
    return stages.map(function (s) {
      var c = STAGE_COLOR[s.state] || "#8a99a8";
      var sub = s.submitted_at ? rel(s.submitted_at) : '<span class="muted">not submitted</span>';
      return '<tr><td style="font-family:var(--mono);color:var(--ink-soft)">' + esc(s.stage_id) + "</td>" +
             '<td><span style="color:' + c + ';font-weight:600">●</span> ' + esc(s.state) + "</td>" +
             '<td class="muted">' + sub + "</td></tr>";
    }).join("");
  }

  function loadStages(runId, innerEl) {
    if (stageCache[runId]) { innerEl.innerHTML = stageCache[runId]; return; }
    innerEl.innerHTML = '<div class="stage-h">Stages</div><div class="muted">loading…</div>';
    fetch("/runs/" + encodeURIComponent(runId), { headers: { "Accept": "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("status " + resp.status);
        return resp.json();
      })
      .then(function (detail) {
        var stages = detail.stages || [];
        var html = '<div class="stage-h">Stages · ' + stages.length +
                   ' · full run id <span class="rid">' + esc(runId) + "</span></div>" +
                   '<table class="stages"><tbody>' + stageRowsHtml(stages) + "</tbody></table>";
        stageCache[runId] = html;
        innerEl.innerHTML = html;
      })
      .catch(function (err) {
        innerEl.innerHTML = '<div class="stage-h">Stages</div>' +
                            '<div class="muted">could not load stage detail (' + esc(err.message) + ")</div>";
      });
  }

  function render() {
    renderSummary();
    var list = RUNS.filter(function (r) { return inSet(r.state); })
                   .filter(function (r) { return !onlyState || r.state === onlyState; });
    list.sort(function (a, b) { return new Date(b.updated_at) - new Date(a.updated_at); });

    if (!list.length) {
      rowsEl.innerHTML = '<tr><td colspan="6" class="empty">No runs match this filter.</td></tr>';
      countEl.textContent = "0 runs shown";
      return;
    }

    rowsEl.innerHTML = list.map(function (r, i) {
      var tags = (r.tags && r.tags.length)
        ? r.tags.map(function (t) { return '<span class="tag">' + esc(t) + "</span>"; }).join(" ")
        : '<span class="muted">—</span>';
      var attach = r.attachment_mode === "PER_STAGE" ? "per-stage" : "aggregated";
      return (
        '<tr class="run" data-i="' + i + '" data-run="' + esc(r.run_id) + '">' +
        "<td>" + badge(r.state) + "</td>" +
        '<td><span class="caret">▶</span> <span class="rid">' + esc(shortId(r.run_id)) + "</span></td>" +
        '<td class="pipe">' + esc(r.pipeline_type) + ' <span class="attach">· ' + attach + "</span></td>" +
        "<td>" + tags + "</td>" +
        '<td class="muted" title="' + esc(r.created_at) + '">' + rel(r.created_at) + "</td>" +
        '<td class="muted" title="' + esc(r.updated_at) + '">' + rel(r.updated_at) + "</td>" +
        "</tr>" +
        '<tr class="detail hidden" data-d="' + i + '"><td colspan="6"><div class="inner"></div></td></tr>'
      );
    }).join("");

    countEl.textContent = list.length + " run" + (list.length === 1 ? "" : "s") + " shown";

    Array.prototype.forEach.call(rowsEl.querySelectorAll("tr.run"), function (tr) {
      tr.onclick = function () {
        var d = rowsEl.querySelector('tr.detail[data-d="' + tr.dataset.i + '"]');
        var opening = d.classList.contains("hidden");
        tr.classList.toggle("open");
        d.classList.toggle("hidden");
        if (opening) { loadStages(tr.dataset.run, d.querySelector(".inner")); }
      };
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("#quick button"), function (b) {
    b.onclick = function () { setQuick(b.dataset.q); };
  });
  render();
})();
