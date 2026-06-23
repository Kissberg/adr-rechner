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
const unDatalist = document.getElementById('unDatalist');

// ── Initialization ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadCustomers();
    loadShippingAddresses();
    addRow();  // Start with one empty row
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

// ── UN Search ──────────────────────────────────────────────────────────
let searchTimeout = null;

async function searchUN(query, rowElement) {
    if (!query || query.length < 1) return;

    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(async () => {
        try {
            const resp = await fetch(`/api/un-search?q=${encodeURIComponent(query)}`);
            const data = await resp.json();
            allUnData = data;

            // Update datalist
            unDatalist.innerHTML = '';
            data.forEach(item => {
                const option = document.createElement('option');
                option.value = item.un_number;
                option.textContent = `${item.un_number} — ${item.substance_name_de}`;
                unDatalist.appendChild(option);
            });

            // Populate the row's datalist reference
            const listId = `un-list-${rowElement.dataset.rowId}`;
            let listEl = document.getElementById(listId);
            if (!listEl) {
                listEl = document.createElement('datalist');
                listEl.id = listId;
                rowElement.appendChild(listEl);
            }
            listEl.innerHTML = '';
            data.forEach(item => {
                const option = document.createElement('option');
                option.value = item.un_number;
                option.textContent = `${item.un_number} — ${item.substance_name_de}`;
                listEl.appendChild(option);
            });
        } catch (err) {
            console.error('Fehler bei UN-Suche:', err);
        }
    }, 200);
}

// ── Row Selection ──────────────────────────────────────────────────────
function onUNSelected(rowElement) {
    const input = rowElement.querySelector('.un-input');
    const unNumber = input.value.trim();

    if (!unNumber) {
        clearRowFields(rowElement);
        recalcAll();
        return;
    }

    // Find the matching UN data (from all cached results, or try exact DB match)
    let match = allUnData.find(d => d.un_number === unNumber);
    if (!match) {
        // Exact match not in current results — clear and return
        clearRowFields(rowElement);
        recalcAll();
        return;
    }

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

    // Store data on the row
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

    tr.innerHTML = `
        <td>
            <input type="text" class="form-control form-control-sm un-input"
                   list="un-list-${rowId}" placeholder="z.B. 1203"
                   autocomplete="off">
            <datalist id="un-list-${rowId}"></datalist>
        </td>
        <td>
            <span class="name-display d-block text-truncate" style="max-width:220px;" title="">—</span>
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
    const unInput = tr.querySelector('.un-input');
    const qtyInput = tr.querySelector('.qty-input');
    const deleteBtn = tr.querySelector('.delete-row-btn');

    // UN number input — search on type
    unInput.addEventListener('input', () => {
        searchUN(unInput.value, tr);
    });

    // UN number selected (change event)
    unInput.addEventListener('change', () => {
        onUNSelected(tr);
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
    // Clear previous feedback
    submitFeedback.style.display = 'none';
    submitFeedback.innerHTML = '';

    // Validate form
    const errors = [];

    // Check items
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
                quantity: quantity,
                unit: unit,
                num_packages: numPackages,
                package_type: packageType,
            });
        }
    });

    // Check customer
    const customerId = customerSelect.value;
    if (!customerId) {
        errors.push('Bitte wählen Sie einen Kunden aus.');
        customerSelect.classList.add('is-invalid');
    } else {
        customerSelect.classList.remove('is-invalid');
    }

    // Check shipping address
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

    // Submit
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

        // Update result display
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

        // Redirect to transport document after short delay
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
