/* Embedded metrics dashboard renderer (L3-DASH-017).
 *
 * Hand-authored and fully self-contained: no third-party visualization library
 * and no remotely-loaded assets. Reads the server-embedded metric model (parsed
 * server-side by the L3-DASH-036 parser) from a <script type="application/json">
 * tag and draws each family as an inline-SVG bar chart. Packaged static asset. */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) { for (var k in attrs) { node.setAttribute(k, attrs[k]); } }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function svgEl(tag, attrs, text) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) { for (var k in attrs) { node.setAttribute(k, attrs[k]); } }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function fmt(n) {
    if (n == null || !isFinite(n)) { return "n/a"; }
    if (Math.abs(n) >= 1000) { return n.toLocaleString(undefined, { maximumFractionDigits: 1 }); }
    return String(Math.round(n * 100) / 100);
  }

  function labelText(labels) {
    var keys = Object.keys(labels || {});
    if (keys.length === 0) { return "(no labels)"; }
    return keys.map(function (k) { return k + "=" + labels[k]; }).join(", ");
  }

  /* A horizontal bar chart: rows = [{label, value}]. Returns an <svg>. */
  function barChart(rows, altClass) {
    var rowH = 26, pad = 4, labelW = 130, valueW = 56, chartW = 320;
    var width = labelW + chartW + valueW;
    var height = Math.max(rows.length, 1) * rowH + pad * 2;
    var svg = svgEl("svg", { viewBox: "0 0 " + width + " " + height, role: "img" });
    var max = rows.reduce(function (m, r) { return Math.max(m, r.value || 0); }, 0) || 1;
    svg.appendChild(svgEl("line", {
      x1: labelW, y1: pad, x2: labelW, y2: height - pad, class: "axis"
    }));
    rows.forEach(function (r, i) {
      var y = pad + i * rowH;
      var w = Math.max(0, (r.value || 0) / max) * chartW;
      svg.appendChild(svgEl("text", {
        x: labelW - 6, y: y + rowH / 2 + 4, "text-anchor": "end", class: "bar-label"
      }, r.label));
      svg.appendChild(svgEl("rect", {
        x: labelW, y: y + 4, width: w, height: rowH - 10, rx: 2,
        class: "bar" + (altClass ? " alt" : "")
      }));
      svg.appendChild(svgEl("text", {
        x: width, y: y + rowH / 2 + 4, class: "bar-value"
      }, fmt(r.value)));
    });
    return svg;
  }

  function counterPanel(family) {
    var panel = el("section", { class: "panel" });
    panel.appendChild(el("h2", null, family.name));
    if (family.help) { panel.appendChild(el("p", { class: "help" }, family.help)); }
    var rows = (family.samples || [])
      .map(function (s) { return { label: labelText(s.labels), value: s.value }; })
      .sort(function (a, b) { return b.value - a.value; });
    if (rows.length === 0) {
      panel.appendChild(el("p", { class: "empty" }, "No samples recorded yet."));
    } else {
      panel.appendChild(barChart(rows, false));
    }
    return panel;
  }

  function histogramPanel(family) {
    var panel = el("section", { class: "panel" });
    panel.appendChild(el("h2", null, family.name));
    if (family.help) { panel.appendChild(el("p", { class: "help" }, family.help)); }

    var sum = null, count = null, buckets = [];
    (family.samples || []).forEach(function (s) {
      if (/_sum$/.test(s.name)) { sum = s.value; }
      else if (/_count$/.test(s.name)) { count = s.value; }
      else if (/_bucket$/.test(s.name)) { buckets.push({ label: "≤ " + s.labels.le, value: s.value }); }
    });
    var avg = (count && count > 0 && sum != null) ? sum / count : null;

    var stats = el("div", { class: "stat-row" });
    stats.appendChild(el("span", null, "")).innerHTML = "count <b>" + fmt(count) + "</b>";
    stats.appendChild(el("span", null, "")).innerHTML = "sum <b>" + fmt(sum) + "</b>";
    stats.appendChild(el("span", null, "")).innerHTML = "avg <b>" + fmt(avg) + "</b>";
    panel.appendChild(stats);

    if (buckets.length === 0) {
      panel.appendChild(el("p", { class: "empty" }, "No observations recorded yet."));
    } else {
      panel.appendChild(barChart(buckets, true));
    }
    return panel;
  }

  function isRenderable(family) {
    // Skip prometheus_client's _created timestamp gauges; render counters and
    // histograms, which carry the operationally meaningful values.
    if (/_created$/.test(family.name)) { return false; }
    return family.type === "counter" || family.type === "histogram";
  }

  function render() {
    var dataNode = document.getElementById("metrics-data");
    var root = document.getElementById("panels");
    if (!dataNode || !root) { return; }
    var families;
    try { families = JSON.parse(dataNode.textContent); } catch (e) { families = []; }
    var shown = 0;
    families.filter(isRenderable).forEach(function (family) {
      root.appendChild(family.type === "histogram" ? histogramPanel(family) : counterPanel(family));
      shown += 1;
    });
    if (shown === 0) {
      root.appendChild(el("p", { class: "empty" }, "No metrics to display yet."));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
