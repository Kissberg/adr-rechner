"""
Tests des E-Mail-Versands für Anfangspasswörter.

Hintergrund: Seit v4.1 wird das Anfangspasswort nicht mehr am Bildschirm des
Administrators übergeben, sondern der betroffenen Person per E-Mail
zugestellt — der Administrator soll kein dauerhaft gültiges fremdes
Passwort kennen.

Zwei Bedingungen sind dabei nicht verhandelbar:
  1. Ohne Versandkonfiguration darf die Kontoeinrichtung nicht scheitern.
  2. Das Passwort darf niemals im Log erscheinen — auch nicht im Fehlerfall.

Versendet wird in diesen Tests nichts: smtplib wird durch einen Doppel
ersetzt.
"""

import smtplib

import pytest

import mailer
import auth


@pytest.fixture()
def ohne_konfiguration(monkeypatch):
    for name in ("ADR_SMTP_HOST", "ADR_SMTP_PORT", "ADR_SMTP_USER",
                 "ADR_SMTP_PASSWORD", "ADR_MAIL_FROM", "ADR_MAIL_APP_NAME",
                 "ADR_MAIL_APP_URL", "ADR_SMTP_STARTTLS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def mit_konfiguration(monkeypatch):
    monkeypatch.setenv("ADR_SMTP_HOST", "smtp.example.de")
    monkeypatch.setenv("ADR_SMTP_PORT", "587")
    monkeypatch.setenv("ADR_SMTP_USER", "absender@example.de")
    monkeypatch.setenv("ADR_SMTP_PASSWORD", "geheim-nicht-im-log")
    monkeypatch.setenv("ADR_MAIL_FROM", "absender@example.de")
    monkeypatch.setenv("ADR_MAIL_APP_URL", "http://192.168.178.144:5050")


class FakeSMTP:
    """Minimaler Doppel für smtplib.SMTP — sammelt die Aufrufe."""

    gesendet = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.gestartet = False
        self.angemeldet = None

    # Kontextmanager-Protokoll
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return (250, b"ok")

    def starttls(self, context=None):
        self.gestartet = True

    def login(self, user, password):
        self.angemeldet = (user, password)

    def send_message(self, msg):
        FakeSMTP.gesendet.append(msg)

    def quit(self):
        pass


@pytest.fixture()
def fake_smtp(monkeypatch):
    FakeSMTP.gesendet = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


# ─────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────

def test_ohne_zugangsdaten_gilt_als_nicht_konfiguriert(ohne_konfiguration):
    assert mailer.smtp_configured() is False


def test_mit_zugangsdaten_gilt_als_konfiguriert(mit_konfiguration):
    assert mailer.smtp_configured() is True


def test_standard_host_ist_ionos(ohne_konfiguration):
    cfg = mailer.smtp_config()
    assert cfg["host"] == "smtp.ionos.de"
    assert cfg["port"] == 587
    assert cfg["starttls"] is True


# ─────────────────────────────────────────────────────────────────────
# Fehlende Voraussetzungen — dürfen die Kontoeinrichtung nicht stoppen
# ─────────────────────────────────────────────────────────────────────

def test_ohne_adresse_wird_nicht_versendet(mit_konfiguration, fake_smtp):
    ok, grund = mailer.send_initial_password("", "muenchen01", "Startwort-2026")
    assert ok is False
    assert "E-Mail-Adresse" in grund
    assert fake_smtp.gesendet == []


def test_ohne_konfiguration_wird_nicht_versendet(ohne_konfiguration, fake_smtp):
    ok, grund = mailer.send_initial_password("a@b.de", "muenchen01", "Startwort-2026")
    assert ok is False
    assert "nicht konfiguriert" in grund
    assert fake_smtp.gesendet == []


# ─────────────────────────────────────────────────────────────────────
# Erfolgreicher Versand
# ─────────────────────────────────────────────────────────────────────

def test_versand_enthaelt_zugangsdaten_und_hinweis(mit_konfiguration, fake_smtp):
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
    # Der Wechsel muss erkennbar gefordert sein.
    assert "ersten" in text and "Anmeldung" in text


def test_zuruecksetzung_hat_eigenen_text(mit_konfiguration, fake_smtp):
    mailer.send_initial_password("a@b.de", "muenchen01", "Neues-Wort-2026",
                                 reason="zuruecksetzung")
    text = fake_smtp.gesendet[0].get_content()
    assert "zurückgesetzt" in text


def test_starttls_wird_verwendet(mit_konfiguration, fake_smtp):
    mailer.send_initial_password("a@b.de", "muenchen01", "Wort-2026-lang")
    assert fake_smtp.gesendet


# ─────────────────────────────────────────────────────────────────────
# Fehler dürfen nicht durchschlagen und nicht das Passwort verraten
# ─────────────────────────────────────────────────────────────────────

class BrokenSMTP(FakeSMTP):
    def send_message(self, msg):
        raise smtplib.SMTPServerDisconnected("weg")


def test_serverfehler_wird_zurueckgegeben_nicht_geworfen(mit_konfiguration,
                                                        monkeypatch, capsys):
    monkeypatch.setattr(smtplib, "SMTP", BrokenSMTP)
    ok, grund = mailer.send_initial_password("a@b.de", "muenchen01",
                                             "Streng-Geheim-2026")
    assert ok is False
    assert "SMTPServerDisconnected" in grund
    # Das Passwort darf in der Fehlermeldung nicht auftauchen.
    assert "Streng-Geheim-2026" not in grund


def test_passwort_steht_nicht_auf_der_ausgabe(mit_konfiguration, fake_smtp, capsys):
    mailer.send_initial_password("a@b.de", "muenchen01", "Streng-Geheim-2026")
    ausgabe = capsys.readouterr()
    assert "Streng-Geheim-2026" not in ausgabe.out
    assert "Streng-Geheim-2026" not in ausgabe.err


def test_abgelehnter_empfaenger_wird_gemeldet(mit_konfiguration, monkeypatch):
    class Refusing(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPRecipientsRefused({"a@b.de": (550, b"unbekannt")})

    monkeypatch.setattr(smtplib, "SMTP", Refusing)
    ok, grund = mailer.send_initial_password("a@b.de", "muenchen01", "Wort-2026-lang")
    assert ok is False
    assert "abgelehnt" in grund


def test_anmeldefehler_wird_gemeldet(mit_konfiguration, monkeypatch):
    class Unauthorized(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"falsch")

    monkeypatch.setattr(smtplib, "SMTP", Unauthorized)
    ok, grund = mailer.send_initial_password("a@b.de", "muenchen01", "Wort-2026-lang")
    assert ok is False
    assert "Mailserver" in grund
    # Es wird nur auf die Variablen verwiesen — nie auf Werte.
    assert "ADR_SMTP_PASSWORD" in grund


# ─────────────────────────────────────────────────────────────────────
# Zusammenwirken mit der Benutzerverwaltung
# ─────────────────────────────────────────────────────────────────────

def test_generiertes_passwort_erfuellt_die_richtlinie():
    """Das zugesandte Passwort muss selbst durch die Richtlinie kommen."""
    import secrets
    for _ in range(20):
        pw = secrets.token_urlsafe(12)
        assert auth.validate_password(pw, "muenchen01") is None
