/**
 * ADR 1000-Punkte-Rechner — UN-Nummern-Datenbank JavaScript
 *
 * Data browser/editor for UN numbers with search, filters,
 * pagination, inline editing, and a category distribution chart.
 * German UI, PC-optimized.
 */

// ── State ───────────────────────────────────────────────────────────────
let currentPage = 1;
let totalPages = 1;
let totalItems = 0;
let perPage = 50;
let expandedRowId = null;
let editingRowId = null;

// ── DOM References ──────────────────────────────────────────────────────
const searchInput    = document.getElementById('searchInput');
const filterCategory = document.getElementById('filterCategory');
const filterClass    = document.getElementById('filterClass');
const resetBtn       = document.getElementById('resetFiltersBtn');
const newUnBtn       = document.getElementById('newUnBtn');
const tbody          = document.getElementById('unTableBody');
const noDataRow      = document.getElementById('noDataRow');
const resultCount    = document.getElementById('resultCount');
const totalCountDisp = document.getElementById('totalCountDisplay');
const filterSummary  = document.getElementById('filterSummary');
const filterSummaryText = document.getElementById('filterSummaryText');
const paginationTop  = document.getElementById('paginationTop');
const paginationBottom = document.getElementById('paginationBottom');
const paginationInfo = document.getElementById('paginationInfo');
const categoryChart  = document.getElementById('categoryChart');
const dbSummary      = document.getElementById('dbSummary');

// Modal
const unModal         = new bootstrap.Modal(document.getElementById('unModal'));
const unModalLabel    = document.getElementById('unModalLabel');
const unForm          = document.getElementById('unForm');
const unEditId        = document.getElementById('unEditId');
const saveUnBtn       = document.getElementById('saveUnBtn');

// Toast
const liveToast   = document.getElementById('liveToast');
const toastMessage = document.getElementById('toastMessage');

// ── Debounce ────────────────────────────────────────────────────────────
let debounceTimer = null;
function debounce(fn, delay = 350) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(fn, delay);
}

// ── Initialization ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadPage(1);

    searchInput.addEventListener('input', () => {
        debounce(() => { currentPage = 1; loadPage(1); });
    });
    filterCategory.addEventListener('change', () => {
        currentPage = 1; loadPage(1);
    });
    filterClass.addEventListener('change', () => {
        currentPage = 1; loadPage(1);
    });
    resetBtn.addEventListener('click', resetFilters);
    newUnBtn.addEventListener('click', () => openUnModal(null));
    saveUnBtn.addEventListener('click', saveUnEntry);

    unForm.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveUnEntry();
        }
    });
});

// ── Data Loading ────────────────────────────────────────────────────────
async function loadPage(page) {
    currentPage = page;
    const params = new URLSearchParams();
    const q = searchInput.value.trim();
    const cat = filterCategory.value;
    const cls = filterClass.value;

    if (q) params.set('q', q);
    if (cat) params.set('category', cat);
    if (cls) params.set('class', cls);
    params.set('page', page);
    params.set('per_page', perPage);

    try {
        const resp = await fetch(`/api/un-database?${params.toString()}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        totalItems = data.total;
        totalPages = data.pages;
        currentPage = data.page;
        renderTable(data.items);
        renderPagination();
        updateSummary();
    } catch (err) {
        console.error('Fehler beim Laden der UN-Daten:', err);
        showToast('Fehler beim Laden der Datenbank', 'danger');
    }
}

async function loadStats() {
    try {
        const resp = await fetch('/api/un-database/stats');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const stats = await resp.json();
        renderCategoryChart(stats.category_distribution);
        dbSummary.textContent = `${stats.total_count || 0} UN-Nummern gespeichert`;
    } catch (err) {
        console.error('Fehler beim Laden der Statistik:', err);
        categoryChart.innerHTML = '<div class="text-center text-muted w-100 align-self-center">Statistik nicht verfügbar</div>';
    }
}

// ── Rendering ───────────────────────────────────────────────────────────
function renderTable(items) {
    // Remove all rows except noDataRow
    tbody.querySelectorAll('tr:not(#noDataRow)').forEach(r => r.remove());

    if (!items || items.length === 0) {
        noDataRow.style.display = '';
        noDataRow.querySelector('td').innerHTML = `
            <i class="bi bi-inbox" style="font-size: 2rem;"></i>
            <p class="mt-2 mb-0">Keine UN-Nummern gefunden.</p>`;
        resultCount.textContent = '0';
        return;
    }

    noDataRow.style.display = 'none';
    resultCount.textContent = totalItems;

    items.forEach(item => {
        const tr = document.createElement('tr');
        tr.dataset.id = item.id;
        tr.className = 'un-row';
        tr.style.cursor = 'pointer';

        const factor = item.transport_category === 4 ? '—' : (item.points_factor != null ? item.points_factor : 0);
        const catBadgeClass = getCategoryBadgeClass(item.transport_category);

        tr.innerHTML = `
            <td class="fw-semibold font-monospace">UN ${escapeHtml(item.un_number)}</td>
            <td>
                <span class="d-block text-truncate" style="max-width:300px;"
                      title="${escapeHtml(item.substance_name_de || '')}">
                    ${escapeHtml(item.substance_name_de || '—')}
                </span>
            </td>
            <td><span class="badge bg-secondary">${escapeHtml(item.hazard_class || '—')}</span></td>
            <td>${escapeHtml(item.packing_group || '—')}</td>
            <td><span class="badge ${catBadgeClass}">${item.transport_category != null ? item.transport_category : '—'}</span></td>
            <td class="text-end">${factor}</td>
            <td><span class="font-monospace">${escapeHtml(item.tunnel_code || '—')}</span></td>
            <td class="text-end text-nowrap" onclick="event.stopPropagation();">
                <button class="btn btn-sm btn-outline-primary edit-btn me-1"
                        data-id="${item.id}" title="Bearbeiten">
                    <i class="bi bi-pencil"></i>
                </button>
            </td>
        `;

        // Click to expand
        tr.addEventListener('click', () => toggleExpand(tr, item));

        // Edit button
        tr.querySelector('.edit-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            toggleInlineEdit(tr, item);
        });

        tbody.appendChild(tr);
    });
}

function renderPagination() {
    const renderButtons = (container) => {
        container.innerHTML = '';

        // Prev
        const prevBtn = document.createElement('button');
        prevBtn.className = `btn btn-outline-secondary btn-sm${currentPage <= 1 ? ' disabled' : ''}`;
        prevBtn.innerHTML = '<i class="bi bi-chevron-left"></i> Zurück';
        prevBtn.disabled = currentPage <= 1;
        prevBtn.addEventListener('click', () => { if (currentPage > 1) loadPage(currentPage - 1); });
        container.appendChild(prevBtn);

        // Page indicator
        const info = document.createElement('span');
        info.className = 'btn btn-sm btn-outline-secondary disabled';
        info.textContent = `Seite ${currentPage} / ${totalPages}`;
        container.appendChild(info);

        // Next
        const nextBtn = document.createElement('button');
        nextBtn.className = `btn btn-outline-secondary btn-sm${currentPage >= totalPages ? ' disabled' : ''}`;
        nextBtn.innerHTML = 'Vor <i class="bi bi-chevron-right"></i>';
        nextBtn.disabled = currentPage >= totalPages;
        nextBtn.addEventListener('click', () => { if (currentPage < totalPages) loadPage(currentPage + 1); });
        container.appendChild(nextBtn);
    };

    renderButtons(paginationTop);
    renderButtons(paginationBottom);

    const start = Math.min((currentPage - 1) * perPage + 1, totalItems);
    const end = Math.min(currentPage * perPage, totalItems);
    paginationInfo.textContent = totalItems > 0
        ? `Zeige ${start}–${end} von ${totalItems} Einträgen`
        : '';
}

function updateSummary() {
    const q = searchInput.value.trim();
    const cat = filterCategory.value;
    const cls = filterClass.value;

    const parts = [];
    if (q) parts.push(`Suche: "${q}"`);
    if (cat) parts.push(`Bef.-Kat.: ${cat}`);
    if (cls) parts.push(`Klasse: ${cls}`);

    if (parts.length > 0) {
        filterSummary.style.display = '';
        filterSummaryText.textContent = parts.join(' • ');
        totalCountDisp.textContent = totalItems;
    } else {
        filterSummary.style.display = 'none';
    }
}

function renderCategoryChart(distribution) {
    if (!distribution || Object.keys(distribution).length === 0) {
        categoryChart.innerHTML = '<div class="text-center text-muted w-100 align-self-center">Keine Daten verfügbar</div>';
        return;
    }

    const maxCount = Math.max(...Object.values(distribution), 1);
    const colors = {
        0: '#6c757d',
        1: '#dc3545',
        2: '#fd7e14',
        3: '#0d6efd',
        4: '#198754'
    };
    const labels = {
        0: 'Kat. 0\n(Faktor 0)',
        1: 'Kat. 1\n(Faktor 50)',
        2: 'Kat. 2\n(Faktor 3)',
        3: 'Kat. 3\n(Faktor 1)',
        4: 'Kat. 4\n(unbegrenzt)'
    };

    let html = '';
    for (let cat = 0; cat <= 4; cat++) {
        const count = distribution[cat] || 0;
        const pct = maxCount > 0 ? Math.round((count / maxCount) * 100) : 0;
        const color = colors[cat] || '#adb5bd';
        html += `
            <div class="text-center" style="flex:1; min-width:80px;">
                <div class="fw-semibold small mb-1">${count}</div>
                <div style="background:${color}; height:${Math.max(pct, 4)}%; border-radius:4px 4px 0 0; min-height:8px; transition: height 0.5s ease;"></div>
                <div class="small text-muted mt-1" style="white-space:pre-line; font-size:0.7rem;">${labels[cat] || `Kat. ${cat}`}</div>
            </div>`;
    }
    categoryChart.innerHTML = html;
}

// ── Row Expansion ───────────────────────────────────────────────────────
function toggleExpand(tr, item) {
    // If in edit mode, don't expand
    if (tr.classList.contains('editing')) return;

    // Check if we need to collapse
    const existingDetail = tr.nextElementSibling;
    if (existingDetail && existingDetail.classList.contains('detail-row')) {
        existingDetail.remove();
        return;
    }

    // Collapse any other expanded row
    tbody.querySelectorAll('.detail-row').forEach(r => r.remove());

    const detailTr = document.createElement('tr');
    detailTr.className = 'detail-row';
    detailTr.innerHTML = `
        <td colspan="8" class="bg-light">
            <div class="row p-3">
                <div class="col-md-4">
                    <h6 class="fw-semibold small text-uppercase text-muted mb-2">Details</h6>
                    <table class="table table-sm table-borderless mb-0 small">
                        <tr><td class="text-muted pe-3">UN-Nummer:</td><td class="fw-semibold font-monospace">UN ${escapeHtml(item.un_number)}</td></tr>
                        <tr><td class="text-muted pe-3">Bezeichnung (DE):</td><td>${escapeHtml(item.substance_name_de || '—')}</td></tr>
                        <tr><td class="text-muted pe-3">Bezeichnung (EN):</td><td>${escapeHtml(item.substance_name_en || '—')}</td></tr>
                        <tr><td class="text-muted pe-3">Gefahrklasse:</td><td>${escapeHtml(item.hazard_class || '—')}</td></tr>
                        <tr><td class="text-muted pe-3">Verpackungsgruppe:</td><td>${escapeHtml(item.packing_group || '—')}</td></tr>
                    </table>
                </div>
                <div class="col-md-4">
                    <h6 class="fw-semibold small text-uppercase text-muted mb-2">Transport & Menge</h6>
                    <table class="table table-sm table-borderless mb-0 small">
                        <tr><td class="text-muted pe-3">Bef.-Kategorie:</td><td>${item.transport_category != null ? item.transport_category : '—'}</td></tr>
                        <tr><td class="text-muted pe-3">Punktefaktor:</td><td>${item.transport_category === 4 ? 'unbegrenzt' : (item.points_factor != null ? item.points_factor : '—')}</td></tr>
                        <tr><td class="text-muted pe-3">Tunnelcode:</td><td class="font-monospace">${escapeHtml(item.tunnel_code || '—')}</td></tr>
                        <tr><td class="text-muted pe-3">Max. Menge:</td><td>${item.max_quantity_per_transport != null ? item.max_quantity_per_transport + ' kg/L' : '—'}</td></tr>
                    </table>
                </div>
                <div class="col-md-4">
                    <h6 class="fw-semibold small text-uppercase text-muted mb-2">ADR & Vorschriften</h6>
                    <table class="table table-sm table-borderless mb-0 small">
                        <tr><td class="text-muted pe-3">ADR-Version:</td><td>${escapeHtml(item.adr_version || 'ADR 2025')}</td></tr>
                        <tr><td class="text-muted pe-3">Sondervorschriften:</td><td>${escapeHtml(item.special_provisions || '—')}</td></tr>
                    </table>
                </div>
            </div>
        </td>`;
    tr.after(detailTr);
}

// ── Inline Editing ──────────────────────────────────────────────────────
function toggleInlineEdit(tr, item) {
    // Collapse any expanded detail
    const detailRow = tr.nextElementSibling;
    if (detailRow && detailRow.classList.contains('detail-row')) {
        detailRow.remove();
    }

    if (tr.classList.contains('editing')) {
        // Cancel edit — restore original
        tr.classList.remove('editing');
        tr.style.cursor = 'pointer';
        renderSingleRow(tr, item);
        return;
    }

    // Collapse any other editing row
    tbody.querySelectorAll('tr.editing').forEach(r => {
        r.classList.remove('editing');
        r.style.cursor = 'pointer';
    });

    tr.classList.add('editing');
    tr.style.cursor = 'default';

    // Replace all cells with edit fields
    const catVal = item.transport_category != null ? item.transport_category : '';
    const factorVal = item.points_factor != null ? item.points_factor : '';
    const tunnelVal = item.tunnel_code || '';
    const specVal = item.special_provisions || '';

    tr.innerHTML = `
        <td class="fw-semibold font-monospace">UN ${escapeHtml(item.un_number)}</td>
        <td><span class="d-block text-truncate" style="max-width:220px;" title="${escapeHtml(item.substance_name_de || '')}">${escapeHtml(item.substance_name_de || '—')}</span></td>
        <td><span class="badge bg-secondary">${escapeHtml(item.hazard_class || '—')}</span></td>
        <td>${escapeHtml(item.packing_group || '—')}</td>
        <td>
            <select class="form-select form-select-sm edit-cat" style="width:70px;">
                <option value="0" ${catVal === 0 ? 'selected' : ''}>0</option>
                <option value="1" ${catVal === 1 ? 'selected' : ''}>1</option>
                <option value="2" ${catVal === 2 ? 'selected' : ''}>2</option>
                <option value="3" ${catVal === 3 ? 'selected' : ''}>3</option>
                <option value="4" ${catVal === 4 ? 'selected' : ''}>4</option>
            </select>
        </td>
        <td><input type="number" class="form-control form-control-sm edit-factor" value="${factorVal}" step="0.01" min="0" style="width:80px;"></td>
        <td><input type="text" class="form-control form-control-sm edit-tunnel" value="${escapeHtml(tunnelVal)}" maxlength="10" style="width:90px;"></td>
        <td class="text-end text-nowrap">
            <button class="btn btn-sm btn-success save-inline-btn me-1" title="Speichern">
                <i class="bi bi-check-lg"></i>
            </button>
            <button class="btn btn-sm btn-outline-secondary cancel-inline-btn" title="Abbrechen">
                <i class="bi bi-x-lg"></i>
            </button>
        </td>
    `;

    // Attach listeners
    tr.querySelector('.save-inline-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        await saveInlineEdit(tr, item);
    });
    tr.querySelector('.cancel-inline-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        tr.classList.remove('editing');
        tr.style.cursor = 'pointer';
        renderSingleRow(tr, item);
    });
}

function renderSingleRow(tr, item) {
    const factor = item.transport_category === 4 ? '—' : (item.points_factor != null ? item.points_factor : 0);
    const catBadgeClass = getCategoryBadgeClass(item.transport_category);

    tr.innerHTML = `
        <td class="fw-semibold font-monospace">UN ${escapeHtml(item.un_number)}</td>
        <td>
            <span class="d-block text-truncate" style="max-width:300px;"
                  title="${escapeHtml(item.substance_name_de || '')}">
                ${escapeHtml(item.substance_name_de || '—')}
            </span>
        </td>
        <td><span class="badge bg-secondary">${escapeHtml(item.hazard_class || '—')}</span></td>
        <td>${escapeHtml(item.packing_group || '—')}</td>
        <td><span class="badge ${catBadgeClass}">${item.transport_category != null ? item.transport_category : '—'}</span></td>
        <td class="text-end">${factor}</td>
        <td><span class="font-monospace">${escapeHtml(item.tunnel_code || '—')}</span></td>
        <td class="text-end text-nowrap" onclick="event.stopPropagation();">
            <button class="btn btn-sm btn-outline-primary edit-btn me-1"
                    data-id="${item.id}" title="Bearbeiten">
                <i class="bi bi-pencil"></i>
            </button>
        </td>
    `;

    tr.querySelector('.edit-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleInlineEdit(tr, item);
    });
}

async function saveInlineEdit(tr, originalItem) {
    const category = parseInt(tr.querySelector('.edit-cat').value);
    const factor = parseFloat(tr.querySelector('.edit-factor').value) || null;
    const tunnel = tr.querySelector('.edit-tunnel').value.trim();
    const updateData = {
        transport_category: category,
        points_factor: factor,
        tunnel_code: tunnel || null,
    };

    try {
        const resp = await fetch(`/api/un-database/${originalItem.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updateData),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.error || `HTTP ${resp.status}`);
        }

        const updated = await resp.json();
        tr.classList.remove('editing');
        tr.style.cursor = 'pointer';
        renderSingleRow(tr, updated);
        showToast('UN-Nummer erfolgreich aktualisiert.', 'success');
        loadStats();
    } catch (err) {
        console.error('Fehler beim Speichern:', err);
        showToast(`Fehler: ${err.message}`, 'danger');
    }
}

// ── Add New UN Number ───────────────────────────────────────────────────
function openUnModal(data) {
    unForm.reset();
    unEditId.value = '';
    document.querySelectorAll('#unForm .is-invalid').forEach(el => el.classList.remove('is-invalid'));

    if (data) {
        unModalLabel.innerHTML = '<i class="bi bi-pencil me-2"></i>UN-Nummer bearbeiten';
        unEditId.value = data.id;
        document.getElementById('unNumber').value = data.un_number || '';
        document.getElementById('unNameDe').value = data.substance_name_de || '';
        document.getElementById('unNameEn').value = data.substance_name_en || '';
        document.getElementById('unHazardClass').value = data.hazard_class || '';
        document.getElementById('unPackingGroup').value = data.packing_group || '';
        document.getElementById('unTransportCat').value = data.transport_category != null ? data.transport_category : '';
        document.getElementById('unPointsFactor').value = data.points_factor != null ? data.points_factor : '';
        document.getElementById('unTunnelCode').value = data.tunnel_code || '';
        document.getElementById('unMaxQty').value = data.max_quantity_per_transport != null ? data.max_quantity_per_transport : '';
        document.getElementById('unAdrVersion').value = data.adr_version || 'ADR 2025';
        document.getElementById('unSpecialProvisions').value = data.special_provisions || '';
    } else {
        unModalLabel.innerHTML = '<i class="bi bi-database-add me-2"></i>Neue UN-Nummer';
        document.getElementById('unAdrVersion').value = 'ADR 2025';
    }

    unModal.show();
}

async function saveUnEntry() {
    // Validate required fields
    const unNumberInput = document.getElementById('unNumber');
    const unNameDeInput = document.getElementById('unNameDe');
    const unClassInput  = document.getElementById('unHazardClass');
    const unCatInput    = document.getElementById('unTransportCat');

    let valid = true;
    for (const el of [unNumberInput, unNameDeInput, unClassInput, unCatInput]) {
        if (!el.value.trim()) {
            el.classList.add('is-invalid');
            valid = false;
        } else {
            el.classList.remove('is-invalid');
        }
    }

    if (!valid) return;

    const id = unEditId.value;
    const isEdit = !!id;

    const data = {
        un_number: unNumberInput.value.trim(),
        substance_name_de: unNameDeInput.value.trim(),
        substance_name_en: document.getElementById('unNameEn').value.trim() || null,
        hazard_class: unClassInput.value,
        packing_group: document.getElementById('unPackingGroup').value || null,
        transport_category: parseInt(unCatInput.value),
        points_factor: parseFloat(document.getElementById('unPointsFactor').value) || null,
        tunnel_code: document.getElementById('unTunnelCode').value.trim() || null,
        max_quantity_per_transport: parseFloat(document.getElementById('unMaxQty').value) || null,
        adr_version: document.getElementById('unAdrVersion').value.trim() || 'ADR 2025',
        special_provisions: document.getElementById('unSpecialProvisions').value.trim() || null,
    };

    saveUnBtn.disabled = true;
    saveUnBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Speichere...';

    try {
        const url = isEdit ? `/api/un-database/${id}` : '/api/un-database';
        const method = isEdit ? 'PUT' : 'POST';

        const resp = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.error || `HTTP ${resp.status}`);
        }

        unModal.hide();
        showToast(isEdit ? 'UN-Nummer erfolgreich aktualisiert.' : 'Neue UN-Nummer erfolgreich angelegt.', 'success');
        await loadPage(currentPage);
        loadStats();
    } catch (err) {
        console.error('Fehler beim Speichern:', err);
        showToast(`Fehler: ${err.message}`, 'danger');
    } finally {
        saveUnBtn.disabled = false;
        saveUnBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Speichern';
    }
}

// ── Filter Reset ────────────────────────────────────────────────────────
function resetFilters() {
    searchInput.value = '';
    filterCategory.value = '';
    filterClass.value = '';
    currentPage = 1;
    loadPage(1);
}

// ── Helpers ─────────────────────────────────────────────────────────────
function getCategoryBadgeClass(cat) {
    switch(cat) {
        case 0: return 'bg-secondary';
        case 1: return 'bg-danger';
        case 2: return 'bg-warning text-dark';
        case 3: return 'bg-primary';
        case 4: return 'bg-success';
        default: return 'bg-light text-dark';
    }
}

function escapeHtml(text) {
    if (text == null) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

function showToast(message, type) {
    liveToast.className = `alert alert-${type} alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3 shadow`;
    liveToast.style.display = 'block';
    liveToast.style.zIndex = '9999';
    liveToast.style.minWidth = '300px';
    toastMessage.textContent = message;

    // Auto-dismiss
    setTimeout(() => {
        liveToast.style.display = 'none';
    }, 4000);
}
