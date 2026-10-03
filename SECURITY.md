# Sicherheitsrichtlinie

## Gemeldete Schwachstellen

Bitte **keine Schwachstellen in öffentliche GitHub-Issues** — sie können
angreifbare Installationen beschreiben, bevor ein Fix vorliegt.

Kontakt: Über das GitHub-Profil (Kissberg) ein Security-Advisory anfragen
oder eine E-Mail über die Kontaktadresse im Profil. Alternativ kann
GitHub Sicherheitslücken direkt über *„Report a vulnerability"* (Security
Tab des Repositories) gemeldet werden — die Meldung bleibt dabei privat.

Erwartete Rückmeldung: innerhalb von **7 Tagen** eine Eingangsbestätigung,
innerhalb von **90 Tagen** eine Einschätzung und — falls bestätigt — ein
Fix oder ein dokumentierter Ausweichweg.

## Umfang

Angemeldet sind alle Versionen ab 4.x. Ältere Versionen erhalten keine
Fixes; der Upgrade-Weg ist im CHANGELOG beschrieben.

## Was als Schwachstelle zählt

Beispiele: Umgehung der Anmeldepflicht oder Rollenprüfung, XSS über
Stammdaten oder Sendungstitel, SQL-Injection, CSRF auf ändernden
Endpunkten, Abfluss von Zugangsdaten (Postfachpasswort, Sitzungs-
Cookies), Unterwandern des Audit-Logs.

## Besondere Hinweise für Gefahrgutdaten

Fehlerhafte Stammdaten (falsche Beförderungskategorie, falscher
Punktfaktor) sind **keine** klassische Sicherheitslücke, aber ein
Rechtsrisiko. Hinweise dazu bitte als Issue mit Quellenangabe
(ADR-Tabelle, BAM-Datei) melden — der Abgleich gegen das amtliche
ADR-PDF ist Teil des Importvorgangs (siehe README, Abschnitt
ADR-Datenimport).
