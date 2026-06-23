/**
 * ADR PDF Import — Client-side JavaScript
 *
 * Handles:
 *   - Drag & drop file upload
 *   - Preview (parse without saving)
 *   - Import (parse + save to database)
 *   - Version history loading
 */

(function () {
  "use strict";

  // ── DOM Elements ──
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("pdfFile");
  const fileInfo = document.getElementById("fileInfo");
  const fileNameDisplay = document.getElementById("fileNameDisplay");
  const clearFileBtn = document.getElementById("clearFileBtn");
  const previewBtn = document.getElementById("previewBtn");
  const importBtn = document.getElementById("importBtn");
  const progressSpinner = document.getElementById("progressSpinner");
  const versionInput = document.getElementById("versionName");
  const previewArea = document.getElementById("previewArea");
  const previewCount = document.getElementById("previewCount");
  const previewTableBody = document.getElementById("previewTableBody");
  const importResult = document.getElementById("importResult");
  const importResultAlert = document.getElementById("importResultAlert");
  const historyTableBody = document.getElementById("historyTableBody");

  let selectedFile = null;
  let previewData = null;

  // ── File Selection ──

  function selectFile(file) {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      alert("Bitte wählen Sie eine PDF-Datei aus.");
      return;
    }
    selectedFile = file;
    fileNameDisplay.textContent = file.name;
    fileInfo.classList.remove("d-none");
    previewBtn.disabled = false;
    importBtn.disabled = false;
    // Reset preview
    hidePreview();
    hideResult();
  }

  function clearFile() {
    selectedFile = null;
    fileInput.value = "";
    fileInfo.classList.add("d-none");
    fileNameDisplay.textContent = "";
    previewBtn.disabled = true;
    importBtn.disabled = true;
    hidePreview();
    hideResult();
  }

  // ── Drag & Drop ──

  dropZone.addEventListener("click", () => fileInput.click());

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.style.backgroundColor = "#e3f2fd";
    dropZone.style.borderColor = "#0d6efd";
  });

  dropZone.addEventListener("dragleave", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.style.backgroundColor = "#f8f9fa";
    dropZone.style.borderColor = "";
  });

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.style.backgroundColor = "#f8f9fa";
    dropZone.style.borderColor = "";
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      selectFile(files[0]);
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files.length > 0) {
      selectFile(fileInput.files[0]);
    }
  });

  clearFileBtn.addEventListener("click", clearFile);

  // ── Show/Hide Helpers ──

  function showPreview() {
    previewArea.classList.remove("d-none");
  }

  function hidePreview() {
    previewArea.classList.add("d-none");
    previewTableBody.innerHTML = "";
    previewCount.textContent = "0 Einträge";
  }

  function showResult(alertClass, icon, title, message) {
    importResult.classList.remove("d-none");
    importResultAlert.className = "alert " + alertClass;
    importResultAlert.innerHTML =
      '<i class="bi ' + icon + ' fs-5 me-2"></i>' +
      "<strong>" + title + "</strong><br>" + message;
  }

  function hideResult() {
    importResult.classList.add("d-none");
    importResultAlert.innerHTML = "";
  }

  function setLoading(loading) {
    if (loading) {
      progressSpinner.classList.remove("d-none");
      previewBtn.disabled = true;
      importBtn.disabled = true;
    } else {
      progressSpinner.classList.add("d-none");
      previewBtn.disabled = !selectedFile;
      importBtn.disabled = !selectedFile;
    }
  }

  // ── API Calls ──

  /**
   * Submit FormData to the given endpoint and return parsed JSON.
   */
  async function submitFormData(endpoint) {
    if (!selectedFile) {
      throw new Error("Keine Datei ausgewählt.");
    }

    const formData = new FormData();
    formData.append("pdfFile", selectedFile);
    formData.append("versionName", versionInput.value.trim() || "ADR 2025");

    const response = await fetch(endpoint, {
      method: "POST",
      body: formData,
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Unbekannter Fehler (" + response.status + ")");
    }

    return data;
  }

  /**
   * Render preview table from parsed data.
   */
  function renderPreviewTable(entries) {
    previewTableBody.innerHTML = "";

    if (!entries || entries.length === 0) {
      previewTableBody.innerHTML =
        '<tr><td colspan="7" class="text-center text-muted py-4">' +
        '<i class="bi bi-exclamation-circle me-2"></i>' +
        "Keine Einträge erkannt. Überprüfen Sie das PDF-Format." +
        "</td></tr>";
      previewCount.textContent = "0 Einträge";
      return;
    }

    previewCount.textContent = entries.length + " Einträge";

    entries.forEach((entry) => {
      const tr = document.createElement("tr");

      tr.innerHTML = [
        "<td><code>" + escapeHtml(entry.un_number || "") + "</code></td>",
        "<td>" + escapeHtml(truncate(entry.substance_name_de || "", 60)) + "</td>",
        "<td>" + escapeHtml(entry.hazard_class || "-") + "</td>",
        "<td>" + escapeHtml(entry.packing_group || "-") + "</td>",
        "<td>" + escapeHtml(String(entry.transport_category ?? "-")) + "</td>",
        "<td><code>" + escapeHtml(entry.tunnel_code || "-") + "</code></td>",
        "<td><small>" + escapeHtml(truncate(entry.special_provisions || "-", 40)) + "</small></td>",
      ].join("");

      previewTableBody.appendChild(tr);
    });
  }

  // ── Preview Button ──

  previewBtn.addEventListener("click", async () => {
    setLoading(true);
    hideResult();

    try {
      const data = await submitFormData("/api/adr/preview");
      previewData = data.entries || data; // Handle both wrapped and raw
      renderPreviewTable(previewData);
      showPreview();

      // Scroll to preview
      previewArea.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      showResult(
        "alert-danger",
        "bi-x-circle",
        "Fehler bei der Vorschau",
        err.message
      );
    } finally {
      setLoading(false);
    }
  });

  // ── Import Button ──

  importBtn.addEventListener("click", async () => {
    if (!confirm(
      "⚠️ Möchten Sie die Daten wirklich importieren?\n\n" +
      "Bestehende UN-Nummern werden mit den neuen Daten überschrieben. " +
      "Neue UN-Nummern werden hinzugefügt."
    )) {
      return;
    }

    setLoading(true);
    hideResult();

    try {
      const data = await submitFormData("/api/adr/import");

      // Show result
      const total = (data.imported || 0) + (data.updated || 0);
      let msg =
        "<strong>" + total + "</strong> Einträge verarbeitet: " +
        (data.imported || 0) + " neu importiert, " +
        (data.updated || 0) + " aktualisiert.";

      if (data.errors && data.errors.length > 0) {
        msg +=
          "<br><span class='text-warning'>⚠️ " +
          data.errors.length +
          " Fehler/Warnungen</span>";
        if (data.errors.length <= 5) {
          msg +=
            "<br><small>" +
            data.errors.join("<br>") +
            "</small>";
        }
      }

      const hasErrors = data.errors && data.errors.length > 0;
      const allErrors = data.errors && data.errors.length === total && total > 0;

      showResult(
        allErrors
          ? "alert-danger"
          : hasErrors
          ? "alert-warning"
          : "alert-success",
        allErrors
          ? "bi-x-circle"
          : hasErrors
          ? "bi-exclamation-triangle"
          : "bi-check-circle",
        allErrors
          ? "Import fehlgeschlagen"
          : "Import erfolgreich",
        msg
      );

      // Refresh history
      loadHistory();

      // Clear the file selection after successful import
      if (!allErrors) {
        // Don't clear file so user can re-import if needed
      }
    } catch (err) {
      showResult(
        "alert-danger",
        "bi-x-circle",
        "Fehler beim Import",
        err.message
      );
    } finally {
      setLoading(false);
    }
  });

  // ── Version History ──

  async function loadHistory() {
    try {
      const response = await fetch("/api/adr/versions");
      const data = await response.json();

      historyTableBody.innerHTML = "";

      if (!data || data.length === 0) {
        historyTableBody.innerHTML =
          '<tr><td colspan="6" class="text-center text-muted py-3">' +
          "Keine Import-Vorgänge vorhanden." +
          "</td></tr>";
        return;
      }

      data.forEach((row) => {
        const tr = document.createElement("tr");

        const importDate = row.import_date
          ? new Date(row.import_date).toLocaleString("de-DE", {
              day: "2-digit",
              month: "2-digit",
              year: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })
          : "-";

        const fileName = row.file_path
          ? row.file_path.split("/").pop().split("\\").pop()
          : "-";

        tr.innerHTML = [
          "<td>" + row.id + "</td>",
          "<td><span class='badge bg-primary'>" + escapeHtml(row.version) + "</span></td>",
          "<td>" + importDate + "</td>",
          "<td><small>" + escapeHtml(fileName) + "</small></td>",
          "<td>" + (row.entries_imported || 0) + "</td>",
          "<td>" + (row.entries_updated || 0) + "</td>",
        ].join("");

        historyTableBody.appendChild(tr);
      });
    } catch (err) {
      historyTableBody.innerHTML =
        '<tr><td colspan="6" class="text-center text-danger py-3">' +
        "Fehler beim Laden des Verlaufs: " + escapeHtml(err.message) +
        "</td></tr>";
    }
  }

  // ── Utility Functions ──

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function truncate(text, maxLen) {
    if (!text) return "";
    return text.length > maxLen ? text.substring(0, maxLen) + "…" : text;
  }

  // ── Initialization ──

  document.addEventListener("DOMContentLoaded", () => {
    loadHistory();
  });
})();
