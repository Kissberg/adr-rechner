/**
 * Daten & Verifikation — Client-side JavaScript
 *
 * Zwei getrennte Abläufe:
 *   1. BAM-Datei (xlsx/csv) hochladen, prüfen und importieren.
 *      Das ist die einzige Datenquelle für den UN-Bestand.
 *   2. ADR-PDF hochladen und verifizieren:
 *      - eigener Parse von Tabelle A gegen den Datenbestand
 *      - Wortlaut von 1.1.3.6 und 5.4.1.1 samt Änderungshinweis
 *      Das PDF schreibt nichts in die Datenbank.
 */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    bamDrop: $("bamDropZone"), bamInput: $("dataFile"), bamInfo: $("bamFileInfo"),
    bamName: $("bamFileName"), bamClear: $("bamClearBtn"),
    bamPreview: $("bamPreviewBtn"), bamImport: $("bamImportBtn"), bamSpinner: $("bamSpinner"),
    bamCheck: $("bamCheck"),
    version: $("versionName"),
    pdfDrop: $("pdfDropZone"), pdfInput: $("pdfFile"), pdfInfo: $("pdfFileInfo"),
    pdfName: $("pdfFileName"), pdfClear: $("pdfClearBtn"),
    verifyBtn: $("verifyBtn"), verifySpinner: $("verifySpinner"),
    previewArea: $("previewArea"), previewCount: $("previewCount"),
    previewBody: $("previewTableBody"),
    verifyArea: $("verifyArea"), verifySummary: $("verifySummary"),
    verifyDiffs: $("verifyDiffs"), verifyRegulations: $("verifyRegulations"),
    importResult: $("importResult"), importAlert: $("importResultAlert"),
    historyBody: $("historyTableBody"), scanBody: $("scanTableBody"),
  };

  let bamFile = null;
  let pdfFile = null;

  const esc = (v) =>
    v === null || v === undefined ? "" : String(v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // ── Dateiauswahl ──────────────────────────────────────────────────

  function setFile(kind, file) {
    if (!file) return;
    if (kind === "bam") {
      bamFile = file;
      el.bamName.textContent = file.name;
      el.bamInfo.classList.remove("d-none");
      el.bamPreview.disabled = false;
      el.bamImport.disabled = false;
      el.bamCheck.innerHTML = "";
    } else {
      pdfFile = file;
      el.pdfName.textContent = file.name;
      el.pdfInfo.classList.remove("d-none");
      el.verifyBtn.disabled = false;
    }
  }

  function clearFile(kind) {
    if (kind === "bam") {
      bamFile = null; el.bamInput.value = "";
      el.bamInfo.classList.add("d-none");
      el.bamPreview.disabled = true; el.bamImport.disabled = true;
      el.bamCheck.innerHTML = "";
      el.previewArea.classList.add("d-none");
    } else {
      pdfFile = null; el.pdfInput.value = "";
      el.pdfInfo.classList.add("d-none");
      el.verifyBtn.disabled = true;
      el.verifyArea.classList.add("d-none");
    }
  }

  function wireDrop(zone, input, kind) {
    zone.addEventListener("click", () => input.click());
    ["dragenter", "dragover"].forEach((ev) =>
      zone.addEventListener(ev, (e) => {
        e.preventDefault(); e.stopPropagation();
        zone.style.backgroundColor = "#e7f1ff";
      }));
    ["dragleave", "drop"].forEach((ev) =>
      zone.addEventListener(ev, (e) => {
        e.preventDefault(); e.stopPropagation();
        zone.style.backgroundColor = "#f8f9fa";
      }));
    zone.addEventListener("drop", (e) => {
      const f = e.dataTransfer.files[0];
      if (f) setFile(kind, f);
    });
    input.addEventListener("change", () => {
      if (input.files[0]) setFile(kind, input.files[0]);
    });
  }

  wireDrop(el.bamDrop, el.bamInput, "bam");
  wireDrop(el.pdfDrop, el.pdfInput, "pdf");
  el.bamClear.addEventListener("click", () => clearFile("bam"));
  el.pdfClear.addEventListener("click", () => clearFile("pdf"));

  // ── Vorschau ──────────────────────────────────────────────────────

  function renderCheck(check) {
    if (!check) { el.bamCheck.innerHTML = ""; return; }
    const cls = check.ok ? "alert-success" : "alert-danger";
    let html = `<div class="alert ${cls} py-2 mb-0">
      <strong>Strukturprüfung:</strong>
      ${check.entries} Varianten · ${check.un_numbers} UN-Nummern ·
      ${check.with_category} mit Beförderungskategorie ·
      ${check.with_multiplier} mit Punktfaktor`;
    if (check.problems && check.problems.length) {
      html += "<ul class='mb-0 mt-2'>";
      check.problems.forEach((p) => { html += `<li>${esc(p)}</li>`; });
      html += "</ul>";
    }
    html += "</div>";
    el.bamCheck.innerHTML = html;
  }

  el.bamPreview.addEventListener("click", function () {
    if (!bamFile) return;
    const data = new FormData();
    data.append("dataFile", bamFile);
    data.append("versionName", el.version.value);

    el.bamSpinner.classList.remove("d-none");
    fetch("/api/adr/preview", { method: "POST", body: data })
      .then((r) => r.json())
      .then((res) => {
        el.bamSpinner.classList.add("d-none");
        if (res.error) { alert(res.error); return; }
        renderCheck(res.check);

        el.previewBody.innerHTML = (res.entries || []).map((e) => `
          <tr>
            <td><code>${esc(e.un_number)}</code></td>
            <td>${esc(e.variant)}</td>
            <td>${esc(e.substance_name_de)}</td>
            <td>${esc(e.hazard_class)}</td>
            <td>${esc(e.classification_code)}</td>
            <td>${esc(e.packing_group)}</td>
            <td><strong>${esc(e.transport_category)}</strong></td>
            <td>${esc(e.tunnel_code)}</td>
            <td>${esc(e.limited_quantity)}</td>
            <td>${esc(e.hazard_identification_no)}</td>
            <td>${esc(e.multiplier)}</td>
          </tr>`).join("");
        el.previewCount.textContent =
          `${res.count} Einträge` +
          (res.shown < res.count ? ` (Vorschau: ${res.shown})` : "");
        el.previewArea.classList.remove("d-none");
      })
      .catch((e) => {
        el.bamSpinner.classList.add("d-none");
        alert("Vorschau fehlgeschlagen: " + e);
      });
  });

  // ── Import ────────────────────────────────────────────────────────

  el.bamImport.addEventListener("click", function () {
    if (!bamFile) return;
    if (!confirm("Den UN-Datenbestand aus der BAM-Datei neu aufbauen?\n\n" +
                 "Bestehende Einträge werden aktualisiert, neue ergänzt.")) return;

    const data = new FormData();
    data.append("dataFile", bamFile);
    data.append("versionName", el.version.value);

    el.bamSpinner.classList.remove("d-none");
    fetch("/api/adr/import", { method: "POST", body: data })
      .then((r) => r.json().then((b) => ({ status: r.status, body: b })))
      .then(({ status, body }) => {
        el.bamSpinner.classList.add("d-none");
        const ok = status < 400 && !body.error;
        el.importAlert.className = "alert " + (ok ? "alert-success" : "alert-danger");
        let msg = body.error
          ? `<strong>Import abgebrochen.</strong> ${esc(body.error)}`
          : `<strong>Import erfolgreich.</strong> ${body.imported || 0} neu, ` +
            `${body.updated || 0} aktualisiert.`;
        if (body.errors && body.errors.length) {
          msg += `<br>${body.errors.length} Hinweise, z. B.: ` +
                 esc(body.errors.slice(0, 3).join(" | "));
        }
        el.importAlert.innerHTML = msg;
        el.importResult.classList.remove("d-none");
        loadHistory();
      })
      .catch((e) => {
        el.bamSpinner.classList.add("d-none");
        alert("Import fehlgeschlagen: " + e);
      });
  });

  // ── Verifikation ──────────────────────────────────────────────────

  el.verifyBtn.addEventListener("click", function () {
    if (!pdfFile) return;
    const data = new FormData();
    data.append("pdfFile", pdfFile);

    el.verifySpinner.classList.remove("d-none");
    el.verifyArea.classList.add("d-none");
    fetch("/api/adr/verify", { method: "POST", body: data })
      .then((r) => r.json())
      .then((res) => {
        el.verifySpinner.classList.add("d-none");
        if (res.error) { alert(res.error); return; }
        renderVerification(res.verification, res.regulations, res.regulation_error);
        el.verifyArea.classList.remove("d-none");
        loadScans();
      })
      .catch((e) => {
        el.verifySpinner.classList.add("d-none");
        alert("Verifikation fehlgeschlagen: " + e);
      });
  });

  function renderVerification(v, reg, regError) {
    const pct = (v.agreement * 100).toFixed(2);
    const cls = v.agreement >= 0.999 ? "success"
      : v.agreement >= 0.98 ? "warning" : "danger";

    let html = `<div class="row g-3 mb-3">
      <div class="col-md-3"><div class="card bg-light"><div class="card-body py-2">
        <div class="text-muted small">Übereinstimmung</div>
        <div class="fs-4 fw-bold text-${cls}">${pct} %</div></div></div></div>
      <div class="col-md-3"><div class="card bg-light"><div class="card-body py-2">
        <div class="text-muted small">Geprüfte UN-Nummern</div>
        <div class="fs-4 fw-bold">${v.matched} / ${v.total_db}</div></div></div></div>
      <div class="col-md-3"><div class="card bg-light"><div class="card-body py-2">
        <div class="text-muted small">Abweichungen</div>
        <div class="fs-4 fw-bold">${v.differences.length}</div></div></div></div>
      <div class="col-md-3"><div class="card bg-light"><div class="card-body py-2">
        <div class="text-muted small">Zeilen aus dem PDF</div>
        <div class="fs-4 fw-bold">${v.total_pdf}</div></div></div></div>
    </div>`;

    if (v.differences.length) {
      html += `<h6 class="mt-3">Abweichungen (bitte einzeln prüfen)</h6>
        <div class="table-responsive" style="max-height:300px; overflow-y:auto;">
        <table class="table table-sm table-hover">
        <thead class="table-light"><tr>
          <th>UN</th><th>Feld</th><th>Datenbank (BAM)</th><th>PDF</th>
        </tr></thead><tbody>`;
      v.differences.forEach((d) => {
        html += `<tr><td><code>${esc(d.un_number)}</code></td>
          <td>${esc(d.field)}</td>
          <td>${esc(d.database)}</td><td>${esc(d.pdf)}</td></tr>`;
      });
      html += "</tbody></table></div>";
    } else {
      html += `<div class="alert alert-success py-2">
        Keine Abweichungen — Datenbank und PDF stimmen vollständig überein.</div>`;
    }

    if (v.parse_warnings && v.parse_warnings.length) {
      html += `<div class="alert alert-warning py-2 mt-2">
        <strong>${v.parse_warnings.length} Hinweise beim PDF-Parse:</strong>
        <ul class="mb-0 mt-1">`;
      v.parse_warnings.slice(0, 10).forEach((w) => {
        html += `<li>${esc(w)}</li>`;
      });
      html += "</ul></div>";
    }

    el.verifySummary.innerHTML = html;
    el.verifyDiffs.innerHTML = "";
    renderRegulations(reg, regError);
  }

  function renderRegulations(reg, regError) {
    if (!reg) {
      el.verifyRegulations.innerHTML = regError
        ? `<div class="alert alert-warning py-2">
             Vorschriftentexte konnten nicht gelesen werden: ${esc(regError)}</div>`
        : "";
      return;
    }

    let html = `<hr><h5 class="mb-3">Vorschriftentexte</h5>`;

    (reg.changes || []).forEach((c) => {
      const map = {
        first_scan: ["info", "Erste Erfassung"],
        changed: ["warning", "Geändert"],
        unchanged: ["success", "Unverändert"],
      };
      const m = map[c.status] || ["secondary", c.status];
      html += `<div class="alert alert-${m[0]} py-2">
        <strong>${esc(c.section)} — ${m[1]}:</strong> ${esc(c.message)}</div>`;
    });

    Object.keys(reg.sections || {}).forEach((key) => {
      const s = reg.sections[key];
      const head = s.found
        ? `<span class="badge bg-success">gefunden</span>`
        : `<span class="badge bg-secondary">nicht enthalten</span>`;
      let block = `<div class="card mb-3">
        <div class="card-header d-flex justify-content-between align-items-center">
          <span><strong>${esc(s.section)}</strong> — ${esc(s.title)}</span>
          ${head}
        </div><div class="card-body">`;

      if (s.found) {
        block += `<p class="small text-muted mb-2">
          Seiten ${esc((s.pages || []).join(", "))} ·
          Prüfsumme <code>${esc(s.checksum)}</code></p>`;
        const keys = Object.keys(s.markers || {});
        if (keys.length) {
          const missing = keys.filter((k) => !s.markers[k]);
          block += `<p class="mb-2">Schlüsselwerte:
            <span class="badge bg-success">${keys.length - missing.length} gefunden</span>
            ${missing.length
              ? `<span class="badge bg-danger">${missing.length} fehlend: ${esc(missing.join(", "))}</span>`
              : ""}</p>`;
        }
        block += `<details><summary class="mb-2" style="cursor:pointer">
          <strong>Wortlaut anzeigen (${s.text.length} Zeichen)</strong></summary>
          <pre class="border rounded p-3 mb-0" style="max-height:420px; overflow:auto;
               white-space:pre-wrap; font-size:0.8rem;">${esc(s.text)}</pre>
          </details>`;
      }
      if (s.note) {
        block += `<div class="alert alert-warning py-2 mt-2 mb-0">${esc(s.note)}</div>`;
      }
      block += "</div></div>";
      html += block;
    });

    el.verifyRegulations.innerHTML = html;
  }

  // ── Verläufe ──────────────────────────────────────────────────────

  function loadHistory() {
    fetch("/api/adr/versions")
      .then((r) => r.json())
      .then((rows) => {
        if (!Array.isArray(rows) || !rows.length) {
          el.historyBody.innerHTML =
            `<tr><td colspan="6" class="text-center text-muted py-3">
               Noch kein Import erfolgt.</td></tr>`;
          return;
        }
        el.historyBody.innerHTML = rows.map((r) => `
          <tr><td>${esc(r.id)}</td><td>${esc(r.version)}</td>
          <td>${esc(r.import_date)}</td><td>${esc(r.file_path)}</td>
          <td>${esc(r.entries_imported)}</td>
          <td>${esc(r.entries_updated)}</td></tr>`).join("");
      })
      .catch(() => {
        el.historyBody.innerHTML =
          `<tr><td colspan="6" class="text-center text-muted py-3">
             Verlauf nicht verfügbar.</td></tr>`;
      });
  }

  function loadScans() {
    fetch("/api/adr/scans")
      .then((r) => r.json())
      .then((rows) => {
        if (!Array.isArray(rows) || !rows.length) {
          el.scanBody.innerHTML =
            `<tr><td colspan="4" class="text-center text-muted py-3">
               Noch keine Prüfung erfolgt.</td></tr>`;
          return;
        }
        el.scanBody.innerHTML = rows.map((r) => `
          <tr><td><code>${esc(r.section)}</code></td>
          <td><code>${esc(r.checksum)}</code></td>
          <td>${esc(r.pages)}</td><td>${esc(r.scanned_at)}</td></tr>`).join("");
      })
      .catch(() => {
        el.scanBody.innerHTML =
          `<tr><td colspan="4" class="text-center text-muted py-3">
             Nicht verfügbar.</td></tr>`;
      });
  }

  loadHistory();
  loadScans();
})();
