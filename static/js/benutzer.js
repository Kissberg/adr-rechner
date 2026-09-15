/*
 * benutzer.js — Benutzerverwaltung
 *
 * Legt Konten an, setzt Passwörter zurück und deaktiviert Konten.
 * Ein von einem Administrator gesetztes oder erzeugtes Passwort wird
 * genau einmal in der Antwort angezeigt und niemals geloggt.
 */

const MIN_PW = (window.MIN_PASSWORD_LENGTH || 12);

function esc(value) {
    const div = document.createElement('div');
    div.textContent = value === null || value === undefined ? '' : String(value);
    return div.innerHTML;
}

function showAlert(el, kind, text) {
    el.className = 'alert alert-' + kind;
    el.textContent = text;
    el.classList.remove('d-none');
}

function showPasswordOnce(username, password) {
    document.getElementById('showPwValue').value = password;
    const modal = new bootstrap.Modal(document.getElementById('showPwModal'));
    modal.show();
}

async function api(url, options) {
    const res = await fetch(url, Object.assign({
        headers: {'Content-Type': 'application/json'}
    }, options || {}));
    let data = {};
    try { data = await res.json(); } catch (e) { /* leer */ }
    return {ok: res.ok, status: res.status, data: data};
}

function roleBadge(role) {
    return role === 'admin'
        ? '<span class="badge bg-primary">Administrator</span>'
        : '<span class="badge bg-secondary">Benutzer</span>';
}

function statusBadge(user) {
    if (!user.active) {
        return '<span class="badge bg-dark">deaktiviert</span>';
    }
    if (user.must_change_password) {
        return '<span class="badge bg-warning text-dark">Passwortwechsel offen</span>';
    }
    return '<span class="badge bg-success">aktiv</span>';
}

function passwordCell(user) {
    if (user.must_change_password) {
        return '<span class="text-warning">muss geändert werden</span>';
    }
    if (!user.password_changed_at) {
        return '<span class="text-muted">—</span>';
    }
    return '<span class="text-muted small">' + esc(user.password_changed_at) + '</span>';
}

function render(users) {
    const tbody = document.getElementById('userRows');
    if (!users.length) {
        tbody.innerHTML =
            '<tr><td colspan="7" class="text-center text-muted py-4">' +
            'Keine Benutzer vorhanden.</td></tr>';
        return;
    }

    tbody.innerHTML = users.map(function (u) {
        const actions = [];

        actions.push(
            '<button class="btn btn-sm btn-outline-warning me-1" ' +
            'data-act="reset" data-id="' + u.id + '" data-name="' + esc(u.username) + '" ' +
            'title="Passwort zurücksetzen">' +
            '<i class="bi bi-key"></i></button>'
        );

        if (u.active) {
            actions.push(
                '<button class="btn btn-sm btn-outline-secondary me-1" ' +
                'data-act="deactivate" data-id="' + u.id + '" ' +
                'data-name="' + esc(u.username) + '" title="Deaktivieren"' +
                (u.is_self ? ' disabled' : '') + '>' +
                '<i class="bi bi-person-dash"></i></button>'
            );
        } else {
            actions.push(
                '<button class="btn btn-sm btn-outline-success me-1" ' +
                'data-act="activate" data-id="' + u.id + '" ' +
                'title="Wieder aktivieren">' +
                '<i class="bi bi-person-check"></i></button>'
            );
        }

        const nextRole = u.role === 'admin' ? 'user' : 'admin';
        actions.push(
            '<button class="btn btn-sm btn-outline-primary" ' +
            'data-act="role" data-id="' + u.id + '" data-role="' + nextRole + '" ' +
            'data-name="' + esc(u.username) + '" ' +
            'title="Rolle ändern zu ' + (nextRole === 'admin' ? 'Administrator' : 'Benutzer') + '"' +
            (u.is_self ? ' disabled' : '') + '>' +
            '<i class="bi bi-arrow-left-right"></i></button>'
        );

        return '<tr>' +
            '<td>' + esc(u.username) +
                (u.is_self ? ' <span class="badge bg-light text-dark">Sie</span>' : '') +
            '</td>' +
            '<td>' + roleBadge(u.role) + '</td>' +
            '<td>' + statusBadge(u) + '</td>' +
            '<td>' + passwordCell(u) + '</td>' +
            '<td class="small text-muted">' + esc(u.last_login || '—') + '</td>' +
            '<td class="small text-muted">' + esc(u.created_by || '—') + '</td>' +
            '<td class="text-end text-nowrap">' + actions.join('') + '</td>' +
        '</tr>';
    }).join('');
}

async function loadUsers() {
    const result = await api('/api/users');
    if (!result.ok) {
        render([]);
        showAlert(document.getElementById('pageAlert'), 'danger',
                  result.data.error || 'Benutzer konnten nicht geladen werden.');
        return [];
    }
    render(result.data.users || []);
    return result.data.users || [];
}

document.getElementById('neuSave')?.addEventListener('click', async function () {
    const box = document.getElementById('neuAlert');
    box.classList.add('d-none');

    const payload = {
        username: document.getElementById('neuName').value.trim(),
        role: document.getElementById('neuRole').value,
        password: document.getElementById('neuPw').value
    };

    this.disabled = true;
    const result = await api('/api/users', {
        method: 'POST',
        body: JSON.stringify(payload)
    });
    this.disabled = false;

    if (!result.ok) {
        showAlert(box, 'danger', result.data.error || 'Anlegen fehlgeschlagen.');
        return;
    }

    bootstrap.Modal.getInstance(document.getElementById('neuModal')).hide();
    document.getElementById('neuName').value = '';
    document.getElementById('neuPw').value = '';
    await loadUsers();
    if (result.data.generated_password) {
        showPasswordOnce(result.data.username, result.data.generated_password);
    } else {
        showAlert(document.getElementById('pageAlert'), 'success',
                  'Benutzer „' + result.data.username + '“ wurde angelegt und ' +
                  'muss das Passwort bei der ersten Anmeldung ändern.');
    }
});

let pwTarget = null;

document.getElementById('pwReset')?.addEventListener('click', async function () {
    if (!pwTarget) return;
    const box = document.getElementById('pwAlert2');
    box.classList.add('d-none');

    this.disabled = true;
    const result = await api('/api/users/' + pwTarget.id + '/password', {
        method: 'POST',
        body: JSON.stringify({password: document.getElementById('pwValue').value})
    });
    this.disabled = false;

    if (!result.ok) {
        showAlert(box, 'danger', result.data.error || 'Zurücksetzen fehlgeschlagen.');
        return;
    }

    bootstrap.Modal.getInstance(document.getElementById('pwModal')).hide();
    document.getElementById('pwValue').value = '';
    await loadUsers();
    if (result.data.generated_password) {
        showPasswordOnce(result.data.username, result.data.generated_password);
    } else {
        showAlert(document.getElementById('pageAlert'), 'success',
                  'Passwort für „' + result.data.username + '“ gesetzt — ' +
                  'Änderung bei der nächsten Anmeldung erzwungen.');
    }
});

document.getElementById('showPwCopy')?.addEventListener('click', function () {
    const input = document.getElementById('showPwValue');
    input.select();
    navigator.clipboard?.writeText(input.value);
    this.textContent = 'Kopiert';
    setTimeout(() => { this.textContent = 'Kopieren'; }, 1500);
});

document.getElementById('userRows')?.addEventListener('click', async function (ev) {
    const btn = ev.target.closest('button[data-act]');
    if (!btn || btn.disabled) return;

    const act = btn.dataset.act;
    const id = btn.dataset.id;
    const name = btn.dataset.name || '';

    if (act === 'reset') {
        pwTarget = {id: id, name: name};
        document.getElementById('pwUser').textContent = name;
        document.getElementById('pwAlert2').classList.add('d-none');
        document.getElementById('pwValue').value = '';
        new bootstrap.Modal(document.getElementById('pwModal')).show();
        return;
    }

    if (act === 'deactivate') {
        if (!confirm('Benutzer „' + name + '“ deaktivieren?\n\n' +
                     'Die Anmeldung wird sofort gesperrt. Das Konto und die ' +
                     'Zuordnung im Audit-Log bleiben erhalten.')) return;
        const r = await api('/api/users/' + id, {method: 'DELETE'});
        await loadUsers();
        showAlert(document.getElementById('pageAlert'),
                  r.ok ? 'success' : 'danger',
                  r.ok ? 'Benutzer „' + name + '“ wurde deaktiviert.'
                       : (r.data.error || 'Deaktivieren fehlgeschlagen.'));
        return;
    }

    if (act === 'activate') {
        const r = await api('/api/users/' + id, {
            method: 'PUT',
            body: JSON.stringify({active: true})
        });
        await loadUsers();
        showAlert(document.getElementById('pageAlert'),
                  r.ok ? 'success' : 'danger',
                  r.ok ? 'Benutzer „' + name + '“ ist wieder aktiv.'
                       : (r.data.error || 'Aktivieren fehlgeschlagen.'));
        return;
    }

    if (act === 'role') {
        const neue = btn.dataset.role;
        const label = neue === 'admin' ? 'Administrator' : 'Benutzer';
        if (!confirm('Rolle von „' + name + '“ auf ' + label + ' ändern?')) return;
        const r = await api('/api/users/' + id, {
            method: 'PUT',
            body: JSON.stringify({role: neue})
        });
        await loadUsers();
        showAlert(document.getElementById('pageAlert'),
                  r.ok ? 'success' : 'danger',
                  r.ok ? 'Rolle von „' + name + '“ ist jetzt ' + label + '.'
                       : (r.data.error || 'Änderung fehlgeschlagen.'));
    }
});

loadUsers();
