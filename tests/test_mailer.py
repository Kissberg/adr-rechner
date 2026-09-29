"""
Tests des Mailversands über die in der Anwendung gepflegten Einstellungen.

Hintergrund: Die Zugangsdaten des Mailservers liegen bewusst NICHT in
Umgebungsvariablen, sondern in der Tabelle `settings` — ein Betrieb soll das
Postfach wechseln können, ohne den Container neu zu erzeugen, und ein
ausgeliefertes Image soll keine fremden Zugangsdaten mitbringen.

Zwei Bedingungen sind dabei nicht verhandelbar:
  1. Ohne hinterlegte Zugangsdaten darf die Kontoeinrichtung nicht
     scheitern und keine Fehlermeldung erscheinen.
  2. Das Postfachpasswort darf weder in einer Antwort noch im Protokoll
     erscheinen — auch nicht im Fehlerfall.

Versendet wird in diesen Tests nichts: smtplib wird durch einen Doppel
ersetzt.
"""

import smtplib
import tempfile

import pytest

import database
import mailer
import settings_store
import auth


@pytest.fixture()
def db(monkeypatch):
    """Frische Datenbank im Temp-Verzeichnis."""
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", f"{tmp}/adr-test.db")
    database.init_db()
    yield


def _konfiguriere(**werte):
    basis = {
        "smtp_host": "smtp.example.de",
        "smtp_port": "587",
        "smtp_starttls": "1",
        "smtp_user": "absender@example.de",
        "smtp_password": "geheim-nicht-im-log",
        "mail_from": "absender@example.de",
        "mail_app_name": "ADR 1000-Punkte-Rechner",
        "mail_app_url": "http://192.168.178.144:5050",
    }
    basis.update(werte)
    return settings_store.set_mail_settings(basis, actor="admin")


class FakeSMTP:
    """Minimaler Doppel für smtplib.SMTP — sammelt die Aufrufe."""

    gesendet = []
    angemeldet = None
    gestartet = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = \
            host, port, timeout, context

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return (250, b"ok")

    def starttls(self, context=None):
        FakeSMTP.gestartet = True

    def login(self, user, password):
        FakeSMTP.angemeldet = (user, password)

    def send_message(self, msg):
        FakeSMTP.gesendet.append(msg)

    def quit(self):
        pass


@pytest.fixture()
def fake_smtp(monkeypatch):
    FakeSMTP.gesendet = []
    FakeSMTP.angemeldet = None
    FakeSMTP.gestartet = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


# ─────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────

def test_ohne_einstellungen_gilt_als_nicht_konfiguriert(db):
    assert mailer.smtp_configured() is False
    assert settings_store.mail_settings_configured() is False


def test_vollstaendige_angaben_gelten_als_konfiguriert(db):
    _konfiguriere()
    assert mailer.smtp_configured() is True


def test_unvollstaendige_angaben_gelten_nicht_als_konfiguriert(db):
    _konfiguriere(smtp_password="")
    assert mailer.smtp_configured() is False


def test_standardport_ist_587(db):
    _konfiguriere(smtp_port="")
    assert mailer.smtp_config()["port"] == 587


def test_absender_faellt_auf_benutzer_zurueck(db):
    _konfiguriere(mail_from="")
    assert mailer.smtp_config()["mail_from"] == "absender@example.de"


# ─────────────────────────────────────────────────────────────────────
# Das Passwort darf die Anwendung nicht verlassen
# ─────────────────────────────────────────────────────────────────────

def test_passwort_wird_nicht_ausgegeben(db):
    _konfiguriere()
    oeffentlich = settings_store.mail_public_settings()
    assert "smtp_password" not in oeffentlich
    assert oeffentlich["password_set"] is True
    assert "geheim-nicht-im-log" not in str(oeffentlich)


def test_leeres_passwort_laesst_das_gespeicherte_stehen(db):
    _konfiguriere()
    settings_store.set_mail_settings({"smtp_password": "", "smtp_host": "neu.de"},
                                     actor="admin")
    cfg = settings_store.get_mail_settings()
    assert cfg["smtp_host"] == "neu.de"
    assert cfg["smtp_password"] == "geheim-nicht-im-log"


def test_passwort_kann_ausdruecklich_geloescht_werden(db):
    _konfiguriere()
    settings_store.set_mail_settings({"smtp_host": "smtp.example.de"},
                                     actor="admin", clear_password=True)
    assert settings_store.get_mail_settings()["smtp_password"] == ""
    assert settings_store.mail_settings_configured() is False


def test_aenderung_landet_im_audit_log_aber_ohne_werte(db):
    import audit
    _konfiguriere()
    audit.log(audit.UPDATE, "settings", None,
              "Einstellungen für den Mailversand geändert: smtp_host")
    conn = database.get_db()
    try:
        zeilen = conn.execute(
            "SELECT detail FROM audit_log WHERE entity = 'settings'").fetchall()
    finally:
        conn.close()
    zusammen = " ".join(r["detail"] or "" for r in zeilen)
    assert "smtp_host" in zusammen
    assert "geheim-nicht-im-log" not in zusammen


def test_unbekannte_schluessel_werden_ignoriert(db):
    settings_store.set_mail_settings({"boese": "wert", "smtp_host": "ok.de"},
                                     actor="admin")
    alle = settings_store.get_settings()
    assert "boese" not in alle
    assert alle["smtp_host"] == "ok.de"


# ─────────────────────────────────────────────────────────────────────
# Fehlende Voraussetzungen — dürfen die Kontoeinrichtung nicht stoppen
# ─────────────────────────────────────────────────────────────────────

def test_ohne_adresse_wird_nicht_versendet(db, fake_smtp):
    _konfiguriere()
    ok, grund = mailer.send_initial_password("", "muenchen01", "Startwort-2026")
    assert ok is False
    assert "E-Mail-Adresse" in grund
    assert fake_smtp.gesendet == []


def test_ohne_konfiguration_wird_nicht_versendet(db, fake_smtp):
    ok, grund = mailer.send_initial_password("a@b.de", "muenchen01", "Startwort-2026")
    assert ok is False
    assert "nicht eingerichtet" in grund
    assert fake_smtp.gesendet == []


# ─────────────────────────────────────────────────────────────────────
# Erfolgreicher Versand
# ─────────────────────────────────────────────────────────────────────

def test_versand_enthaelt_zugangsdaten_und_hinweis(db, fake_smtp):
    _konfiguriere()
    ok, grund = mailer.send_initial_password(
        "m.mustermann@firma.de", "muenchen01", "Startwort-2026",
        role="admin", reason="neu", actor="admin")
    assert (ok, grund) == (True, None)
    assert len(fake_smtp.gesendet) == 1

    msg = fake_smtp.gesendet[0]
    assert msg["To"] == "m.mustermann@firma.de"
    assert msg["From"] == "absender@example.de"
    assert "ADR 1000-Punkte-Rechner" in msg["Subject"]

    text = msg.get_content()
    assert "muenchen01" in text
    assert "Startwort-2026" in text
    assert "Administrator" in text
    assert "http://192.168.178.144:5050" in text
    assert "ersten" in text and "Anmeldung" in text


def test_zuruecksetzung_hat_eigenen_text(db, fake_smtp):
    _konfiguriere()
    mailer.send_initial_password("a@b.de", "muenchen01", "Neues-Wort-2026",
                                 reason="zuruecksetzung")
    assert "zurückgesetzt" in fake_smtp.gesendet[0].get_content()


def test_starttls_wird_verwendet(db, fake_smtp):
    _konfiguriere()
    mailer.send_initial_password("a@b.de", "muenchen01", "Wort-2026-lang")
    assert fake_smtp.gestartet is True


def test_ohne_starttls_wird_implizites_tls_verwendet(db, fake_smtp):
    _konfiguriere(smtp_starttls="0")
    mailer.send_initial_password("a@b.de", "muenchen01", "Wort-2026-lang")
    assert fake_smtp.gesendet
    assert fake_smtp.gestartet is False


# ─────────────────────────────────────────────────────────────────────
# Verbindungsprüfung (ohne Nachricht) und Fehlerbehandlung
# ─────────────────────────────────────────────────────────────────────

def test_verbindungspruefung_ohne_nachricht(db, fake_smtp):
    _konfiguriere()
    ok, meldung = mailer.test_connection()
    assert ok is True
    assert "smtp.example.de" in meldung
    assert fake_smtp.gesendet == []


def test_verbindungspruefung_ohne_angaben(db, fake_smtp):
    ok, meldung = mailer.test_connection()
    assert ok is False
    assert "hinterlegt" in meldung


def test_verbindungspruefung_meldet_falsche_zugangsdaten(db, monkeypatch):
    _konfiguriere()

    class Unauthorized(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"falsch")

    monkeypatch.setattr(smtplib, "SMTP", Unauthorized)
    ok, meldung = mailer.test_connection()
    assert ok is False
    assert "abgelehnt" in meldung


class BrokenSMTP(FakeSMTP):
    def send_message(self, msg):
        raise smtplib.SMTPServerDisconnected("weg")


def test_serverfehler_wird_zurueckgegeben_nicht_geworfen(db, monkeypatch):
    _konfiguriere()
    monkeypatch.setattr(smtplib, "SMTP", BrokenSMTP)
    ok, grund = mailer.send_initial_password("a@b.de", "muenchen01",
                                             "Streng-Geheim-2026")
    assert ok is False
    assert "SMTPServerDisconnected" in grund
    assert "Streng-Geheim-2026" not in grund


def test_passwort_steht_nicht_auf_der_ausgabe(db, fake_smtp, capsys):
    _konfiguriere()
    mailer.send_initial_password("a@b.de", "muenchen01", "Streng-Geheim-2026")
    ausgabe = capsys.readouterr()
    assert "Streng-Geheim-2026" not in ausgabe.out
    assert "Streng-Geheim-2026" not in ausgabe.err


def test_abgelehnter_empfaenger_wird_gemeldet(db, monkeypatch):
    _konfiguriere()

    class Refusing(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPRecipientsRefused({"a@b.de": (550, b"unbekannt")})

    monkeypatch.setattr(smtplib, "SMTP", Refusing)
    ok, grund = mailer.send_initial_password("a@b.de", "muenchen01", "Wort-2026-lang")
    assert ok is False
    assert "abgelehnt" in grund


def test_anmeldefehler_wird_gemeldet(db, monkeypatch):
    _konfiguriere()

    class Unauthorized(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"falsch")

    monkeypatch.setattr(smtplib, "SMTP", Unauthorized)
    ok, grund = mailer.send_initial_password("a@b.de", "muenchen01", "Wort-2026-lang")
    assert ok is False
    assert "Einstellungen" in grund


# ─────────────────────────────────────────────────────────────────────
# Zusammenwirken mit der Benutzerverwaltung
# ─────────────────────────────────────────────────────────────────────

def test_generiertes_passwort_erfuellt_die_richtlinie():
    import secrets
    for _ in range(20):
        pw = secrets.token_urlsafe(12)
        assert auth.validate_password(pw, "muenchen01") is None
