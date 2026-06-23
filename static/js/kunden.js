/**
 * ADR 1000-Punkte-Rechner — Kundenverwaltung JavaScript
 *
 * Full CRUD for customers with search, Excel import, and template download.
 * German UI, PC-optimized.
 */

// ── State ───────────────────────────────────────────────────────────────
let allCustomers = [];
let deleteTargetId = null;

// ── DOM References ──────────────────────────────────────────────────────
const searchInput           = document.getElementById('searchInput');
const customersTableBody    = document.getElementById('customersTableBody');
const customerCount         = document.getElementById('customerCount');
const addCustomerBtn        = document.getElementById('addCustomerBtn');
const noCustomersRow        = document.getElementById('noCustomersRow');

// Modal elements
const customerModal         = new bootstrap.Modal(document.getElementById('customerModal'));
const deleteConfirmModal    = new bootstrap.Modal(document.getElementById('deleteConfirmModal'));
const customerModalLabel    = document.getElementById('customerModalLabel');
const customerForm          = document.getElementById('customerForm');
const customerIdInput       = document.getElementById('customerId');
const saveCustomerBtn       = document.getElementById('saveCustomerBtn');
const confirmDeleteBtn      = document.getElementById('confirmDeleteBtn');
const deleteConfirmMessage  = document.getElementById('deleteConfirmMessage');

// Excel elements
const excelUploadForm       = document.getElementById('excelUploadForm');
const uploadExcelBtn        = document.getElementById('uploadExcelBtn');
const importResult          = document.getElementById('importResult');
const excelFileInput        = document.getElementById('excelFile');

// ── Initialization ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadCustomers();

    // Search
    searchInput.addEventListener('input', () => {
        renderCustomers(searchInput.value);
    });

    // Add button → open modal in "add" mode
    addCustomerBtn.addEventListener('click', () => {
        openCustomerModal(null);
    });

    // Save button
    saveCustomerBtn.addEventListener('click', saveCustomer);

    // Delete confirmation
    confirmDeleteBtn.addEventListener('click', executeDelete);

    // Excel upload
    excelUploadForm.addEventListener('submit', (e) => {
        e.preventDefault();
        uploadExcel();
    });

    // Reset import form when modal is opened
    document.getElementById('excelImportModal').addEventListener('show.bs.modal', () => {
        excelUploadForm.reset();
        importResult.style.display = 'none';
        importResult.innerHTML = '';
    });

    // Allow pressing Enter in the form to trigger save
    customerForm.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveCustomer();
        }
    });
});

// ── Data Loading ───────────────────────────────────────────────────────
async function loadCustomers() {
    try {
        const resp = await fetch('/api/kunden');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        allCustomers = await resp.json();
        renderCustomers(searchInput.value);
    } catch (err) {
        console.error('Fehler beim Laden der Kunden:', err);
        showToast('Fehler beim Laden der Kunden', 'danger');
    }
}

// ── Rendering ──────────────────────────────────────────────────────────
function renderCustomers(query) {
    const q = (query || '').toLowerCase().trim();
    let filtered = allCustomers;
    if (q) {
        filtered = allCustomers.filter(c =>
            (c.name || '').toLowerCase().includes(q) ||
            (c.city || '').toLowerCase().includes(q) ||
            (c.street || '').toLowerCase().includes(q) ||
            (c.zip || '').toLowerCase().includes(q)
        );
    }

    customerCount.textContent = filtered.length;

    // Clear existing rows (except the "no customers" row if it's still there)
    customersTableBody.innerHTML = '';

    if (filtered.length === 0) {
        const tr = document.createElement('tr');
        tr.id = 'noCustomersRow';
        tr.innerHTML = `
            <td colspan="8" class="text-center text-muted py-4">
                <i class="bi bi-inbox" style="font-size: 2rem;"></i>
                <p class="mt-2 mb-0">${q ? 'Keine Kunden gefunden.' : 'Keine Kunden vorhanden.'}</p>
            </td>`;
        customersTableBody.appendChild(tr);
        return;
    }

    filtered.forEach(c => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="fw-semibold">${escapeHtml(c.name)}</td>
            <td>${escapeHtml(c.street || '—')}</td>
            <td>${escapeHtml(c.zip || '—')}</td>
            <td>${escapeHtml(c.city || '—')}</td>
            <td>${escapeHtml(c.country || 'Deutschland')}</td>
            <td>${escapeHtml(c.phone || '—')}</td>
            <td>${c.email ? `<a href="mailto:${escapeHtml(c.email)}">${escapeHtml(c.email)}</a>` : '—'}</td>
            <td class="text-end text-nowrap">
                <button class="btn btn-sm btn-outline-primary edit-customer-btn me-1"
                        data-id="${c.id}" title="Bearbeiten">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger delete-customer-btn"
                        data-id="${c.id}" data-name="${escapeHtml(c.name)}" title="Löschen">
                    <i class="bi bi-trash"></i>
                </button>
            </td>`;
        customersTableBody.appendChild(tr);
    });

    // Attach event listeners to action buttons
    document.querySelectorAll('.edit-customer-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.id);
            const customer = allCustomers.find(c => c.id === id);
            if (customer) openCustomerModal(customer);
        });
    });

    document.querySelectorAll('.delete-customer-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            deleteTargetId = parseInt(btn.dataset.id);
            const name = btn.dataset.name;
            deleteConfirmMessage.textContent =
                `Möchten Sie den Kunden „${name}” wirklich löschen? Diese Aktion kann nicht rückgängig gemacht werden.`;
            deleteConfirmModal.show();
        });
    });
}

// ── Customer Modal ─────────────────────────────────────────────────────
function openCustomerModal(customer) {
    customerForm.reset();
    customerIdInput.value = '';

    if (customer) {
        // Edit mode
        customerModalLabel.innerHTML = '<i class="bi bi-pencil me-2"></i>Kunde bearbeiten';
        customerIdInput.value = customer.id;
        document.getElementById('custName').value = customer.name || '';
        document.getElementById('custStreet').value = customer.street || '';
        document.getElementById('custZip').value = customer.zip || '';
        document.getElementById('custCity').value = customer.city || '';
        document.getElementById('custCountry').value = customer.country || 'Deutschland';
        document.getElementById('custContact').value = customer.contact || '';
        document.getElementById('custPhone').value = customer.phone || '';
        document.getElementById('custEmail').value = customer.email || '';
    } else {
        // Add mode
        customerModalLabel.innerHTML = '<i class="bi bi-person-plus me-2"></i>Neuer Kunde';
        document.getElementById('custCountry').value = 'Deutschland';
    }

    // Clear any validation styles
    customerForm.querySelectorAll('.is-invalid').forEach(el => el.classList.remove('is-invalid'));
    customerModal.show();
}

// ── Save Customer (POST/PUT) ───────────────────────────────────────────
async function saveCustomer() {
    // Validate
    const nameInput = document.getElementById('custName');
    const name = nameInput.value.trim();
    if (!name) {
        nameInput.classList.add('is-invalid');
        nameInput.focus();
        return;
    }
    nameInput.classList.remove('is-invalid');

    const id = customerIdInput.value;
    const isEdit = !!id;

    const data = {
        name: name,
        street: document.getElementById('custStreet').value.trim(),
        zip: document.getElementById('custZip').value.trim(),
        city: document.getElementById('custCity').value.trim(),
        country: document.getElementById('custCountry').value.trim(),
        contact: document.getElementById('custContact').value.trim(),
        phone: document.getElementById('custPhone').value.trim(),
        email: document.getElementById('custEmail').value.trim(),
    };

    saveCustomerBtn.disabled = true;
    saveCustomerBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Speichere...';

    try {
        const url = isEdit ? `/api/kunden/${id}` : '/api/kunden';
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

        customerModal.hide();
        showToast(isEdit ? 'Kunde erfolgreich aktualisiert.' : 'Kunde erfolgreich angelegt.', 'success');
        await loadCustomers();
    } catch (err) {
        console.error('Fehler beim Speichern:', err);
        showToast(`Fehler beim Speichern: ${err.message}`, 'danger');
    } finally {
        saveCustomerBtn.disabled = false;
        saveCustomerBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Speichern';
    }
}

// ── Delete Customer ────────────────────────────────────────────────────
async function executeDelete() {
    if (!deleteTargetId) return;

    confirmDeleteBtn.disabled = true;
    confirmDeleteBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Lösche...';

    try {
        const resp = await fetch(`/api/kunden/${deleteTargetId}`, { method: 'DELETE' });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.error || `HTTP ${resp.status}`);
        }

        deleteConfirmModal.hide();
        showToast('Kunde erfolgreich gelöscht.', 'success');
        await loadCustomers();
    } catch (err) {
        console.error('Fehler beim Löschen:', err);
        showToast(`Fehler beim Löschen: ${err.message}`, 'danger');
    } finally {
        confirmDeleteBtn.disabled = false;
        confirmDeleteBtn.innerHTML = '<i class="bi bi-trash me-1"></i>Löschen';
        deleteTargetId = null;
    }
}

// ── Excel Upload ───────────────────────────────────────────────────────
async function uploadExcel() {
    const file = excelFileInput.files[0];
    if (!file) {
        excelFileInput.classList.add('is-invalid');
        return;
    }
    excelFileInput.classList.remove('is-invalid');

    uploadExcelBtn.disabled = true;
    uploadExcelBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Importiere...';
    importResult.style.display = 'none';
    importResult.innerHTML = '';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/kunden', {
            method: 'POST',
            body: formData,
        });

        const result = await resp.json();

        if (!resp.ok) {
            throw new Error(result.error || `HTTP ${resp.status}`);
        }

        // Show result
        displayImportResult(result);
        await loadCustomers();

    } catch (err) {
        console.error('Fehler beim Excel-Import:', err);
        importResult.style.display = 'block';
        importResult.innerHTML = `
            <div class="alert alert-danger small mb-0">
                <i class="bi bi-exclamation-triangle me-1"></i>
                <strong>Fehler:</strong> ${escapeHtml(err.message)}
            </div>`;
    } finally {
        uploadExcelBtn.disabled = false;
        uploadExcelBtn.innerHTML = '<i class="bi bi-upload me-1"></i>Import starten';
    }
}

function displayImportResult(result) {
    importResult.style.display = 'block';

    let html = '<div class="alert alert-success small mb-0">';
    html += '<i class="bi bi-check-circle me-1"></i>';
    html += '<strong>Import abgeschlossen</strong><br>';
    html += `Neu importiert: <strong>${result.imported || 0}</strong> &bull; `;
    html += `Aktualisiert: <strong>${result.updated || 0}</strong>`;

    if (result.errors && result.errors.length > 0) {
        html += '<hr class="my-2">';
        html += '<span class="text-danger"><i class="bi bi-exclamation-triangle me-1"></i>Fehler:</span>';
        html += '<ul class="mb-0 mt-1 ps-3">';
        result.errors.forEach(err => {
            html += `<li class="text-danger">${escapeHtml(err)}</li>`;
        });
        html += '</ul>';
    }

    html += '</div>';
    importResult.innerHTML = html;
}

// ── Utilities ──────────────────────────────────────────────────────────
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showToast(message, type) {
    // Create a simple toast-like alert at the top
    const container = document.querySelector('main .container') || document.body;
    const existing = document.getElementById('liveToast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'liveToast';
    toast.className = `alert alert-${type} alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3 shadow`;
    toast.style.zIndex = '9999';
    toast.style.minWidth = '300px';
    toast.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Schließen"></button>`;

    container.appendChild(toast);

    // Auto-dismiss after 4 seconds
    setTimeout(() => {
        if (toast.parentNode) toast.remove();
    }, 4000);
}
