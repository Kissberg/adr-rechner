"""
csrf.py — Schutz gegen Cross-Site Request Forgery (CSRF)

Ein Angreifer kann fremde Browser dazu bringen, requests an diese
Anwendung zu senden (per <form>, <img> oder fetch von einer anderen
Seite). Der Browser hängt das Session-Cookie automatisch an — die
Anwendung kann einen solchen Request nicht vom echten unterscheiden,
**solange** sie sich nur auf das Cookie verlässt.

Der Standardmechanismus dagegen: ein zufälliger Token pro Session, der
in jedes Formular (verstecktes Feld) und in jeden JavaScript-Request
(Header `X-CSRF-Token`) eingebaut wird. Eine fremde Seite kann ihn
nicht lesen (Same-Origin-Policy) und daher nicht mitschicken.

Gültigkeit
  * Der Token lebt in der Session — er wechselt bei jeder Anmeldung
    (`session.clear()` im Anmeldefluss) und bei jeder Abmeldung.
  * Geprüft wird mit `secrets.compare_digest` (konstante Zeit).
  * Sichere Methoden (GET/HEAD/OPTIONS) werden nie geprüft; sie ändern
    in dieser Anwendung nichts.

Warum die Prüfung unter TESTING nicht läuft
  Die bestehende Testsuite meldet sich und ruft Endpunkte direkt auf,
  ohne das Formular zu rendern — dort wäre der Token nicht vorhanden.
  Die Prüfung selbst wird in tests/test_csrf.py mit explizit
  deaktiviertem TESTING denselben Weg erprobt, den der Produktivbetrieb
  nimmt (ungültiger/fehlender Token wird abgewiesen, gültiger
  akzeptiert). TESTING wird im Produktivbetrieb nie gesetzt.

JavaScript-Seite
  static/js/csrf.js ergänzt den Header automatisch bei allen
  same-origin-fetches mit ändernden Methoden — bestehende und künftige
  Aufrufstellen müssen nichts darüber wissen.
"""

from __future__ import annotations

import secrets
from typing import Optional, Tuple

from flask import request, session

CSRF_HEADER = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")

_TOKEN_LENGTH_BYTES = 32


def csrf_token() -> str:
    """Gibt den Session-Token zurück und erzeugt ihn bei Bedarf."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(_TOKEN_LENGTH_BYTES)
        session["_csrf_token"] = token
    return token


def rotate_csrf_token() -> str:
    """Ersetzt den Token durch einen neuen (z. B. nach der Anmeldung)."""
    token = secrets.token_urlsafe(_TOKEN_LENGTH_BYTES)
    session["_csrf_token"] = token
    return token


def _request_token() -> Optional[str]:
    """Nimmt den Token aus dem Header oder aus dem Formular."""
    return (request.headers.get(CSRF_HEADER)
            or request.form.get(CSRF_FORM_FIELD)
            or "").strip() or None


def check_csrf() -> Optional[Tuple[str, int]]:
    """Prüft den Token bei ändernden Methoden.

    Rückgabe None, wenn die Anfrage durchgelassen werden darf; sonst
    (fehlermeldung, status), das der Aufrufer als Antwort zurückgibt.
    """
    if request.method in SAFE_METHODS:
        return None
    if request.blueprint == "oidc":
        # Nur GET-Routen; der Schutz gegen manipulierte Callbacks ist der
        # state-Parameter des OIDC-Standards, nicht der Session-Token.
        return None

    erwartet = session.get("_csrf_token")
    token = _request_token()
    if not erwartet or not token \
            or not secrets.compare_digest(token, erwartet):
        return (
            "Sicherheitsprüfung (CSRF) fehlgeschlagen. Die Seite wurde "
            "möglicherweise zu lange offen gelassen — bitte neu laden "
            "und erneut senden.",
            400,
        )
    return None
