/*
 * einstellungen.js — Betriebseinstellungen (Mailversand)
 *
 * Das Passwort wird nie ausgelesen: bleibt das Feld leer, behält der Server
 * das gespeicherte. Zum Entfernen gibt es einen eigenen Knopf.
 */

function esc(value) {
    const div = document.createElement('div');
    div.textContent = value === null || value === undefined ? '' : String(value);
    return div.innerHTML;
}

function showAlert(kind, text) {
    const el = document.getElementById('pageAlert');
    el.className = 'alert alert-' + kind;
    el.textContent = text;
    el.classList.remove('d-none');
}

async function api(url, options) {
    const res = await fetch(url, Object.assign({
        headers: {'Content-Type': 'application/json'}
    }, options || {}));
    let data = {};
    try { data = await res.json(); } catch (e) { /* leer */ }
    return {ok: res.ok, status: res.status, data: data};
}

function payload(extra) {
    return Object.assign({
        smtp_host: document.getElementById('smtp_host').value.trim(),
        smtp_port: document.getElementById('smtp_port').value.trim(),
        smtp_starttls: document.getElementById('smtp_starttls').checked,
        smtp_user: document.getElementById('smtp_user').value.trim(),
        smtp_password: document.getElementById('smtp_password').value,
        mail_from: document.getElementById('mail_from').value.trim(),
        mail_app_name: document.getElementById('mail_app_name').value.trim(),
        mail_app_url: document.getElementById('mail_app_url').value.trim()
    }, extra || {});
}

document.getElementById('mailSave')?.addEventListener('click', async function () {
    this.disabled = true;
    const result = await api('/api/settings/mail', {
        method: 'PUT',
        body: JSON.stringify(payload())
    });
    this.disabled = false;

    if (!result.ok) {
        showAlert('danger', result.data.error || 'Speichern fehlgeschlagen.');
        return;
    }

    // Das Passwort wird nie angezeigt: Feld leeren und den Zustand sichtbar
    // nachziehen, damit „hinterlegt" nicht erst nach einem Neuladen stimmt.
    const pwFeld = document.getElementById('smtp_password');
    const hattePasswort = pwFeld.value !== '' || result.data.password_set;
    pwFeld.value = '';
    if (hattePasswort) {
        pwFeld.placeholder = 'hinterlegt — leer lassen zum Beibehalten';
    } else {
        pwFeld.placeholder = 'noch nicht hinterlegt';
    }
    if (result.data.password_set && !document.getElementById('mailClearPassword')) {
        const knopf = document.createElement('button');
        knopf.className = 'btn btn-outline-danger';
        knopf.id = 'mailClearPassword';
        knopf.innerHTML = '<i class="bi bi-x-circle me-1"></i>Passwort löschen';
        document.getElementById('mailTest').after(knopf);
        knopf.addEventListener('click', () => location.reload());
    }
    showAlert('success', 'Einstellungen gespeichert. Ein Neustart ist nicht '
                       + 'erforderlich — neu angelegte Konten nutzen sie sofort.');
});

document.getElementById('mailTest')?.addEventListener('click', async function () {
    this.disabled = true;
    const result = await api('/api/settings/mail/test', {method: 'POST'});
    this.disabled = false;

    if (!result.ok) {
        showAlert('danger', result.data.error || 'Prüfung fehlgeschlagen.');
        return;
    }
    showAlert(result.data.ok ? 'success' : 'warning',
              result.data.message || (result.data.ok ? 'Verbindung steht.'
                                                     : 'Prüfung fehlgeschlagen.'));
});

document.getElementById('mailClearPassword')?.addEventListener('click', async function () {
    if (!confirm('Gespeichertes Postfachpasswort löschen?\n\n' +
                 'Ohne Passwort wird nicht mehr per E-Mail versendet; ' +
                 'Anfangspasswörter werden dann auf dem Bildschirm angezeigt.')) return;
    this.disabled = true;
    const result = await api('/api/settings/mail', {
        method: 'PUT',
        body: JSON.stringify(payload({smtp_password: '', clear_password: true}))
    });
    this.disabled = false;

    if (!result.ok) {
        showAlert('danger', result.data.error || 'Löschen fehlgeschlagen.');
        return;
    }
    showAlert('success', 'Das gespeicherte Passwort wurde entfernt.');
    setTimeout(() => location.reload(), 1200);
});
