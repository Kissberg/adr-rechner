/**
 * ADR 1000-Punkte-Rechner — Calculator JavaScript
 *
 * Live calculation of dangerous goods transport points per ADR 1.1.3.6.
 * German UI, PC-optimized.
 */

// ── State ───────────────────────────────────────────────────────────────
let itemRows = [];          // Array of DOM row elements
let allUnData = [];         // Cached UN search results
let customers = [];
let shippingAddresses = [];
let nextRowId = 0;

// ── DOM References ──────────────────────────────────────────────────────
const tbody = document.getElementById('itemsTableBody');
const totalDisplay = document.getElementById('totalPointsDisplay');
const addRowBtn = document.getElementById('addRowBtn');
const submitBtn = document.getElementById('submitBtn');
const resultPoints = document.getElementById('resultPoints');
const resultStatus = document.getElementById('resultStatus');
const submitFeedback = document.getElementById('submitFeedback');
const customerSelect = document.getElementById('customerSelect');
const addressSelect = document.getElementById('addressSelect');

// ── Global: active custom dropdown ──────────────────────────────────────
let activeDropdown = null;  // { wrapper, input, list, data }

// ── Initialization ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadCustomers();
    loadShippingAddresses();
    addRow();
});

// Close custom dropdown on outside click
document.addEventListener('click', (e) => {
    if (activeDropdown && !activeDropdown.wrapper.contains(e.target)) {
        closeDropdown();
    }
});

// ── Data Loading ───────────────────────────────────────────────────────
async function loadCustomers() {
    try {
        const resp = await fetch('/api/kunden');
        customers = await resp.json();
        customerSelect.innerHTML = '<option value="">— Bitte wählen —</option>';
        customers.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = `${c.name} (${c.city})`;
            customerSelect.appendChild(opt);
        });
    } catch (err) {
        console.error('Fehler beim Laden der Kunden:', err);
    }
}

async function loadShippingAddresses() {
    try {
        const resp = await fetch('/api/shipping-addresses');
        shippingAddresses = await resp.json();
        addressSelect.innerHTML = '<option value="">— Bitte wählen —</option>';
        shippingAddresses.forEach(a => {
            const opt = document.createElement('option');
            opt.value = a.id;
            opt.textContent = `${a.name}, ${a.street}, ${a.zip} ${a.city}`;
            addressSelect.appendChild(opt);
        });
    } catch (err) {
        console.error('Fehler beim Laden der Versandadressen:', err);
    }
}

// ── UN Search with Custom Dropdown ──────────────────────────────────────
let searchTimeout = null;

async function searchUN(query, wrapper) {
    if (!query || query.length < 1) {
        closeDropdown();
        return;
    }

    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(async () => {
        try {
            const resp = await fetch(`/api/un-search?q=${encodeURIComponent(query)}`);
            const data = await resp.json();
            if (data.length === 0) {
                closeDropdown();
                return;
            }
            buildDropdown(wrapper, data);
        } catch (err) {
            console.error('Fehler bei UN-Suche:', err);
        }
    }, 200);
}

function buildDropdown(wrapper, data) {
    // Remove old dropdown if any
    const oldList = wrapper.querySelector('.custom-un-dropdown');
    if (oldList) oldList.remove();

    const list = document.createElement('div');
    list.className = 'custom-un-dropdown';
    list.style.cssText = 'position:absolute;top:100%;left:0;right:0;z-index:1050;max-height:400px;overflow-y:auto;'
        + 'background:#fff;border:1px solid #dee2e6;border-radius:0 0 6px 6px;box-shadow:0 8px 24px rgba(0,0,0,.12);';

    data.forEach((item, idx) => {
        const entry = document.createElement('div');
        entry.className = 'un-dropdown-item';
        entry.style.cssText = 'padding:8px 12px;cursor:pointer;border-bottom:1px solid #f0f0f0;'
            + 'line-height:1.4;';
        if (idx === 0) entry.style.background = '#f8f9fa';

        // Packing group badge
        const pgBadge = item.packing_group
            ? `<span class="badge bg-secondary me-1" style="font-size:0.7rem;">VG ${item.packing_group}</span>`
            : '';
        const catBadge = `<span class="badge bg-info me-1" style="font-size:0.7rem;">Kat. ${item.transport_category}</span>`;
        const factorText = item.points_factor != null ? `×${item.points_factor}` : '';

        entry.innerHTML = `
            <div style="font-weight:600;color:#0d6efd;">UN ${item.un_number} ${pgBadge} ${catBadge} <small class="text-muted">${factorText}</small></div>
            <div style="font-size:0.85rem;color:#333;word-wrap:break-word;white-space:normal;">${item.substance_name_de}</div>
            <div style="font-size:0.75rem;color:#888;">Klasse ${item.hazard_class || '—'}</div>
        `;

        entry.addEventListener('mousedown', (e) => {
            e.preventDefault(); // prevent blur before click
            selectUNItem(wrapper, item);
        });
        entry.addEventListener('mouseenter', () => {
            list.querySelectorAll('.un-dropdown-item').forEach(el => el.style.background = '');
            entry.style.background = '#e9ecef';
        });

        list.appendChild(entry);
    });

    wrapper.appendChild(list);

    // Track active dropdown
    activeDropdown = { wrapper, input: wrapper.querySelector('.un-input'), list, data };
}

function selectUNItem(wrapper, item) {
    const input = wrapper.querySelector('.un-input');
    const rowElement = wrapper.closest('tr.item-row');

    // Store the DB id for exact variant matching
    input.value = item.un_number;
    rowElement.dataset.unDbId = item.id;

    fillRowFromUN(rowElement, item);
    closeDropdown();
    recalcAll();
}

function closeDropdown() {
    if (!activeDropdown) return;
    const list = activeDropdown.list;
    if (list && list.parentNode) list.remove();
    activeDropdown = null;
}

// ── Row Selection (fallback for manual input without dropdown) ──────────
function onUNSelected(rowElement) {
    const input = rowElement.querySelector('.un-input');
    const unNumber = input.value.trim();

    if (!unNumber) {
        clearRowFields(rowElement);
        recalcAll();
        return;
    }

    // Try by DB id first, then by UN number
    const dbId = rowElement.dataset.unDbId;
    let match = null;
    if (dbId) {
        match = allUnData.find(d => String(d.id) === String(dbId));
    }
    if (!match) {
        match = allUnData.find(d => d.un_number === unNumber);
    }

    if (!match) {
        clearRowFields(rowElement);
        recalcAll();
        return;
    }

    rowElement.dataset.unDbId = match.id;
    fillRowFromUN(rowElement, match);
    recalcAll();
}

function fillRowFromUN(rowElement, data) {
    const nameEl = rowElement.querySelector('.name-display');
    const classEl = rowElement.querySelector('.class-display');
    const catEl = rowElement.querySelector('.cat-display');
    const factorEl = rowElement.querySelector('.factor-display');

    nameEl.textContent = data.substance_name_de || '';
    nameEl.title = data.substance_name_de || '';
    classEl.textContent = data.hazard_class || '';
    catEl.textContent = data.transport_category !== null ? data.transport_category : '';
    factorEl.textContent = data.points_factor !== null ? data.points_factor : 0;

    rowElement.dataset.category = data.transport_category;
    rowElement.dataset.factor = data.points_factor;

    calcRowPoints(rowElement);
}

function clearRowFields(rowElement) {
    rowElement.querySelector('.name-display').textContent = '';
    rowElement.querySelector('.class-display').textContent = '';
    rowElement.querySelector('.cat-display').textContent = '';
    rowElement.querySelector('.factor-display').textContent = '';
    rowElement.querySelector('.points-display').textContent = '0';
    rowElement.dataset.category = '';
    rowElement.dataset.factor = '';
    rowElement.dataset.unDbId = '';
}

// ── Points Calculation ─────────────────────────────────────────────────
function calcRowPoints(rowElement) {
    const qtyInput = rowElement.querySelector('.qty-input');
    const factor = parseFloat(rowElement.dataset.factor) || 0;
    const quantity = parseFloat(qtyInput.value) || 0;
    const points = Math.round(quantity * factor * 100) / 100;
    rowElement.querySelector('.points-display').textContent = points;
    return points;
}

function recalcAll() {
    let total = 0;
    tbody.querySelectorAll('tr.item-row').forEach(row => {
        total += calcRowPoints(row);
    });
    total = Math.round(total * 100) / 100;
    totalDisplay.textContent = total;

    // Update result section
    if (total > 0 || tbody.querySelectorAll('tr.item-row').length > 0) {
        resultPoints.textContent = total;
        if (total <= 1000) {
            resultStatus.innerHTML = '<span class="badge bg-success fs-6 px-3 py-2">Freigestellt nach ADR 1.1.3.6</span>'
                + '<br><small class="text-muted mt-2 d-inline-block">Die Beförderung ist von den Vorschriften des ADR freigestellt.</small>';
        } else {
            resultStatus.innerHTML = '<span class="badge bg-danger fs-6 px-3 py-2">Nicht freigestellt — ADR-Vorschriften voll anwendbar</span>'
                + '<br><small class="text-muted mt-2 d-inline-block">Die 1000-Punkte-Grenze wurde überschritten.</small>';
        }
    } else {
        resultPoints.textContent = '—';
        resultStatus.innerHTML = '<span class="text-muted">Geben Sie Gefahrgutpositionen ein, um die Berechnung zu starten.</span>';
    }
}

// ── Row Management ─────────────────────────────────────────────────────
function createRowElement() {
    const rowId = nextRowId++;

    const tr = document.createElement('tr');
    tr.className = 'item-row';
    tr.dataset.rowId = rowId;
    tr.dataset.category = '';
    tr.dataset.factor = '';
    tr.dataset.unDbId = '';

    tr.innerHTML = `
        <td style="position:relative;">
            <input type="text" class="form-control form-control-sm un-input"
                   placeholder="z.B. 1203" autocomplete="off">
        </td>
        <td>
            <span class="name-display d-block text-wrap" style="max-width:300px;word-wrap:break-word;white-space:normal;line-height:1.3;" title="">—</span>
        </td>
        <td>
            <span class="class-display">—</span>
        </td>
        <td>
            <input type="number" class="form-control form-control-sm qty-input"
                   placeholder="0" min="0" step="any" value="">
        </td>
        <td>
            <select class="form-select form-select-sm unit-select">
                <option value="kg">kg</option>
                <option value="L">L</option>
                <option value="Stück">Stück</option>
            </select>
        </td>
        <td>
            <input type="number" class="form-control form-control-sm pkg-num-input"
                   value="1" min="1" step="1">
        </td>
        <td>
            <select class="form-select form-select-sm pkg-type-select">
                <option value="">—</option>
                <option value="Fass">Fass</option>
                <option value="Kanister">Kanister</option>
                <option value="IBC">IBC</option>
                <option value="Karton">Karton</option>
                <option value="Flasche">Flasche</option>
                <option value="Sack">Sack</option>
                <option value="Eimer">Eimer</option>
                <option value="Sonstige">Sonstige</option>
            </select>
        </td>
        <td>
            <span class="cat-display">—</span>
        </td>
        <td>
            <span class="factor-display">—</span>
        </td>
        <td>
            <span class="points-display fw-semibold">0</span>
        </td>
        <td>
            <button type="button" class="btn btn-outline-danger btn-sm delete-row-btn"
                    title="Position entfernen">
                <i class="bi bi-trash"></i>
            </button>
        </td>
    `;

    // Event listeners
    const unTd = tr.querySelector('td:first-child');
    const unInput = tr.querySelector('.un-input');
    const qtyInput = tr.querySelector('.qty-input');
    const deleteBtn = tr.querySelector('.delete-row-btn');

    // UN number input — search on type, show custom dropdown
    unInput.addEventListener('input', () => {
        const val = unInput.value.trim();
        if (val.length >= 1) {
            searchUN(val, unTd);
        } else {
            closeDropdown();
        }
    });

    // UN number selected via Enter/Tab (fallback if dropdown not used)
    unInput.addEventListener('change', () => {
        closeDropdown();
        onUNSelected(tr);
    });

    // Keyboard navigation in dropdown
    unInput.addEventListener('keydown', (e) => {
        const list = unTd.querySelector('.custom-un-dropdown');
        if (!list || list.children.length === 0) return;

        const items = list.querySelectorAll('.un-dropdown-item');
        let activeIdx = -1;
        items.forEach((el, i) => { if (el.style.background) activeIdx = i; });

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            const next = Math.min(activeIdx + 1, items.length - 1);
            items.forEach(el => el.style.background = '');
            items[next].style.background = '#e9ecef';
            items[next].scrollIntoView({ block: 'nearest' });
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            const prev = Math.max(activeIdx - 1, 0);
            items.forEach(el => el.style.background = '');
            items[prev].style.background = '#e9ecef';
            items[prev].scrollIntoView({ block: 'nearest' });
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (activeIdx >= 0) {
                const data = allUnData[activeIdx];
                if (data) selectUNItem(unTd, data);
            }
        } else if (e.key === 'Escape') {
            closeDropdown();
        }
    });

    // Quantity change
    qtyInput.addEventListener('input', () => {
        calcRowPoints(tr);
        recalcAll();
    });

    // Delete button
    deleteBtn.addEventListener('click', () => {
        removeRow(tr);
    });

    return tr;
}

function addRow() {
    const tr = createRowElement();
    tbody.appendChild(tr);
    itemRows.push(tr);
    recalcAll();
}

function removeRow(rowElement) {
    rowElement.remove();
    itemRows = itemRows.filter(r => r !== rowElement);
    recalcAll();
}

// ── Event Delegation ───────────────────────────────────────────────────
addRowBtn.addEventListener('click', addRow);

// ── Form Submission ────────────────────────────────────────────────────
submitBtn.addEventListener('click', async () => {
    submitFeedback.style.display = 'none';
    submitFeedback.innerHTML = '';

    const errors = [];
    const itemRows = tbody.querySelectorAll('tr.item-row');
    if (itemRows.length === 0) {
        errors.push('Mindestens eine Gefahrgutposition ist erforderlich.');
    }

    const items = [];
    itemRows.forEach(row => {
        const unInput = row.querySelector('.un-input');
        const qtyInput = row.querySelector('.qty-input');
        const unitSelect = row.querySelector('.unit-select');
        const pkgNumInput = row.querySelector('.pkg-num-input');
        const pkgTypeSelect = row.querySelector('.pkg-type-select');
        const nameEl = row.querySelector('.name-display');

        const unNumber = unInput.value.trim();
        const quantity = parseFloat(qtyInput.value);
        const unit = unitSelect.value;
        const numPackages = parseInt(pkgNumInput.value) || 1;
        const packageType = pkgTypeSelect.value;
        const name = nameEl.textContent.trim();
        const dbId = row.dataset.unDbId;

        if (!unNumber) {
            errors.push('UN-Nummer ist für alle Positionen erforderlich.');
            unInput.classList.add('is-invalid');
        } else {
            unInput.classList.remove('is-invalid');
        }

        if (!name || name === '—') {
            errors.push(`UN-Nummer „${unNumber}“ nicht in der Datenbank gefunden.`);
        }

        if (!quantity || quantity <= 0) {
            errors.push('Menge muss für alle Positionen größer als 0 sein.');
            qtyInput.classList.add('is-invalid');
        } else {
            qtyInput.classList.remove('is-invalid');
        }

        if (unNumber && name && name !== '—' && quantity > 0) {
            items.push({
                un_number: unNumber,
                un_db_id: dbId || null,
                quantity: quantity,
                unit: unit,
                num_packages: numPackages,
                package_type: packageType,
            });
        }
    });

    const customerId = customerSelect.value;
    if (!customerId) {
        errors.push('Bitte wählen Sie einen Kunden aus.');
        customerSelect.classList.add('is-invalid');
    } else {
        customerSelect.classList.remove('is-invalid');
    }

    const addressId = addressSelect.value;
    if (!addressId) {
        errors.push('Bitte wählen Sie eine Versandadresse aus.');
        addressSelect.classList.add('is-invalid');
    } else {
        addressSelect.classList.remove('is-invalid');
    }

    if (errors.length > 0) {
        showFeedback('danger', 'Bitte beheben Sie folgende Fehler:', errors);
        return;
    }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Berechne...';

    try {
        const resp = await fetch('/calculate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                items: items,
                customer_id: parseInt(customerId),
                shipping_address_id: parseInt(addressId),
            }),
        });

        const result = await resp.json();

        if (!resp.ok) {
            showFeedback('danger', 'Fehler bei der Berechnung:', [result.error || 'Unbekannter Fehler']);
            return;
        }

        resultPoints.textContent = result.total_points;
        if (result.is_exempt) {
            resultStatus.innerHTML = '<span class="badge bg-success fs-6 px-3 py-2">Freigestellt nach ADR 1.1.3.6</span>'
                + '<br><small class="text-muted mt-2 d-inline-block">Die Beförderung ist von den Vorschriften des ADR freigestellt.</small>';
        } else {
            resultStatus.innerHTML = '<span class="badge bg-danger fs-6 px-3 py-2">Nicht freigestellt — ADR-Vorschriften voll anwendbar</span>'
                + '<br><small class="text-muted mt-2 d-inline-block">Die 1000-Punkte-Grenze wurde überschritten.</small>';
        }

        showFeedback('success',
            `Berechnung erfolgreich! Sendung Nr. ${result.shipment_id} gespeichert.`,
            [`Gesamtpunktzahl: ${result.total_points} Punkte`,
             `Status: ${result.is_exempt ? 'Freigestellt (ADR 1.1.3.6)' : 'Nicht freigestellt'}`,
             `${result.items.length} Position(en) berechnet.`]);

        setTimeout(() => {
            window.location.href = `/befoerderungspapier/${result.shipment_id}`;
        }, 2000);

    } catch (err) {
        console.error('Fehler beim Senden:', err);
        showFeedback('danger', 'Netzwerkfehler', ['Die Berechnung konnte nicht durchgeführt werden. Bitte versuchen Sie es erneut.']);
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-check-lg me-2"></i>Berechnung durchführen &amp; Beförderungspapier erstellen';
    }
});

// ── Feedback Display ───────────────────────────────────────────────────
function showFeedback(type, title, messages) {
    submitFeedback.style.display = 'block';
    const alertClass = type === 'success' ? 'alert-success' : 'alert-danger';
    const icon = type === 'success' ? 'bi-check-circle' : 'bi-exclamation-triangle';

    let html = `<div class="alert ${alertClass}">`;
    html += `<i class="bi ${icon} me-2"></i><strong>${title}</strong>`;
    if (messages && messages.length > 0) {
        html += '<ul class="mb-0 mt-2">';
        messages.forEach(m => { html += `<li>${m}</li>`; });
        html += '</ul>';
    }
    html += '</div>';
    submitFeedback.innerHTML = html;
}
