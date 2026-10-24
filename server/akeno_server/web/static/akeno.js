// AKENO website helpers: local times, a live countdown, copying and downloading codes, confirmations.
(function () {
  "use strict";
  var lang = document.documentElement.lang || "en";
  function fmt(date) {
    return date.toLocaleString(lang === "de" ? "de-DE" : undefined,
      { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  document.querySelectorAll("time[datetime]").forEach(function (el) {
    var d = new Date(el.getAttribute("datetime"));
    if (!isNaN(d)) { el.title = el.textContent; el.textContent = fmt(d); }
  });
  function remaining(ms) {
    if (ms <= 0) { return lang === "de" ? "abgelaufen" : "expired"; }
    var s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600),
      m = Math.floor(s % 3600 / 60), sec = s % 60;
    var days = lang === "de" ? (d === 1 ? "Tag" : "Tage") : (d === 1 ? "day" : "days");
    return (d ? d + " " + days + " " : "") + h + " h " + m + " min" + (d ? "" : " " + sec + " s");
  }
  var counters = document.querySelectorAll("[data-expires]");
  if (counters.length) {
    var offset = 0, server = document.body.getAttribute("data-server-time");
    if (server) { offset = new Date(server).getTime() - Date.now(); }
    var tick = function () {
      counters.forEach(function (el) {
        el.textContent = remaining(new Date(el.getAttribute("data-expires")).getTime() - (Date.now() + offset));
      });
    };
    tick();
    setInterval(tick, 1000);
  }
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.getAttribute("data-copy"));
      if (!src || !navigator.clipboard) { return; }
      navigator.clipboard.writeText(src.textContent.trim()).then(function () {
        var old = btn.textContent; btn.textContent = btn.getAttribute("data-done") || "Copied";
        setTimeout(function () { btn.textContent = old; }, 1800);
      });
    });
  });
  document.querySelectorAll("[data-download]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.getAttribute("data-download"));
      var blob = new Blob([src.value || src.textContent], { type: "text/csv;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = btn.getAttribute("data-filename") || "akeno-codes.csv";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });
  });
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) { e.preventDefault(); }
    });
  });
  document.querySelectorAll("input.code-input").forEach(function (input) {
    input.addEventListener("input", function () {
      var body = input.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
      if (body.indexOf("AKENO") === 0) { body = body.slice(5); }
      body = body.slice(0, 16);
      var groups = body.match(/.{1,4}/g) || [];
      input.value = body ? "AKENO-" + groups.join("-") : "";
    });
  });
})();
