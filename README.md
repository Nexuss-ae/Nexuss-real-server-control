# Server Control – Discord-Bot + Admin-Website

Ein deutschsprachiger Startpunkt für einen kleinen Freundes-Discord: FastAPI-Dashboard, Discord-OAuth-Login, Rollenverwaltung, Moderationsaktionen, Geburtstags-Erinnerungen, Willkommensnachrichten und Protokolle.

## Funktionen in dieser Version

- Website-Login über Discord OAuth2; Admin-/Server-Management-Rechte werden geprüft.
- Dashboard mit Bot-Status, Mitgliederzahl, Rollenanzahl, Verwarnungsanzahl und Geburtstagen.
- Rollen per Website hinzufügen/entfernen (Discord-ID des Mitglieds, Rolle auswählen).
- Timeout aus der Website (1–40.320 Minuten), Ban, Unban und optionale Nachrichtenlöschung beim Ban.
- Slash-Commands `/timeout`, `/warn`, `/warnings`, `/birthday`, `/serverinfo`.
- Geburtstage nur mit Tag/Monat; keine Speicherung des Geburtsjahres. Automatische Nachricht im konfigurierten Kanal.
- Willkommensnachrichten mit `{mention}` und `{server}`.
- Moderations-/Aktivitätsprotokoll und konfigurierbarer Logkanal.
- SQLite-Datenbank, keine externe Datenbank oder kostenpflichtige API nötig.

## Wichtige Grenzen / Hinweise

- Das Projekt ist eine funktionsfähige Basis, aber vor öffentlichem Produktiveinsatz bitte testen und absichern.
- Discord muss den Bot eingeladen haben. Bot-Rolle in Discord über die Rollen setzen, die er verwalten soll.
- Für Mitgliederliste und Beitritts-/Verlassensereignisse im Discord Developer Portal unter **Bot → Privileged Gateway Intents** den **Server Members Intent** einschalten.
- Bot-Berechtigungen mindestens: View Channels, Send Messages, Read Message History, Moderate Members, Manage Roles, Ban Members. Erteile nur die benötigten Rechte; kein Administrator-Recht nötig.
- Geburtstags-Loop läuft im 30-Minuten-Takt und nutzt UTC als Datumsreferenz. Für einen Server mit anderer Zeitzone sollte `birthday_loop` auf die gewünschte Zeitzone angepasst werden.
- Das Dashboard bindet Rollen aus dem Discord-Cache ein. Falls Rollen/Mitglieder fehlen, Bot-Status und Members Intent prüfen.
- Die OAuth-Prüfung ist auf einen konfigurierten Server begrenzt. Nur Admins oder Personen mit Server-Management-Recht sollen Zugang erhalten.
- Für die erste Version gibt es noch keinen vollständigen Web-Editor für Role-Panel-Nachrichten, keine Self-Service-Rollenbuttons im Discord und keine automatische Aufhebung von Bans. Das sind sinnvolle Erweiterungen.
- Bei einem einzigen Railway-Service kann SQLite auf einem persistenten Volume gespeichert werden. Ohne Volume können Daten bei Redeploy/Neustart verloren gehen.
- Railway-Guthaben/Abrechnung hängt vom aktuellen Plan und Ressourcenverbrauch ab; keine feste Kostenfreiheit garantiert.

## 1. Discord-Bot erstellen

1. Öffne https://discord.com/developers/applications und erstelle **New Application**.
2. Unter **Bot** den Bot hinzufügen bzw. öffnen. Token erzeugen und sicher kopieren. Niemals in GitHub veröffentlichen.
3. Unter **Bot → Privileged Gateway Intents** `Server Members Intent` aktivieren.
4. Unter **OAuth2 → General** notiere `Client ID` und erstelle/verwende ein `Client Secret`.
5. Unter **OAuth2 → Redirects** die Redirect-URL eintragen: `https://DEINE-DOMAIN.up.railway.app/callback`.
6. Unter **OAuth2 → URL Generator** die Scopes `bot` und `applications.commands` auswählen. Berechtigungen: View Channels, Send Messages, Read Message History, Moderate Members, Manage Roles, Ban Members. Den erzeugten Link öffnen und den Bot auf deinen Server einladen.
7. Aktiviere in Discord den Entwicklermodus: Einstellungen → Erweitert → Entwicklermodus. Rechtsklick auf den Server → Server-ID kopieren.

## 2. Railway bereitstellen

1. Lege dieses Projekt in ein privates GitHub-Repository und verbinde es in Railway als neues Projekt. Alternativ die Dateien mit Railway CLI deployen.
2. In Railway beim Service unter **Variables** die Variablen aus `.env.example` setzen:
   - `DISCORD_TOKEN`
   - `DISCORD_CLIENT_ID`
   - `DISCORD_CLIENT_SECRET`
   - `DISCORD_GUILD_ID`
   - `PUBLIC_URL`
   - `SESSION_SECRET` (lang, zufällig, geheim)
   - `DATABASE_PATH=/app/data/servercontrol.db`
3. Unter **Volumes** ein Volume anlegen und auf `/app/data` mounten. Das ist wichtig, damit die SQLite-Daten Redeploys überstehen.
4. Deployment starten. Unter **Networking** eine öffentliche Domain erzeugen; `PUBLIC_URL` auf genau diese HTTPS-Adresse setzen.
5. Nach dem Setzen der Domain die Discord-OAuth2-Redirect-URL im Developer Portal exakt auf `PUBLIC_URL/callback` setzen und erneut deployen.
6. Öffne `PUBLIC_URL`. Melde dich mit einem Discord-Konto an, das Administrator- oder Server-Management-Rechte auf dem konfigurierten Server hat.
7. Prüfe `PUBLIC_URL/health`. `bot_ready` sollte `true` anzeigen.

## 3. Lokal testen (optional)

Python 3.12 installieren, dann im Projektordner:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Variablen aus .env in deine Shell laden oder mit einem lokalen dotenv-Tool setzen
uvicorn main:app --reload
```

Das Projekt lädt `.env` absichtlich nicht automatisch; setze Variablen in deiner Umgebung. Auf Windows PowerShell zum Beispiel:

```powershell
$env:DISCORD_TOKEN="..."
$env:DISCORD_CLIENT_ID="..."
$env:DISCORD_CLIENT_SECRET="..."
$env:DISCORD_GUILD_ID="123456789012345678"
$env:PUBLIC_URL="http://localhost:8000"
$env:SESSION_SECRET="ein-langer-zufaelliger-geheimer-wert"
$env:DATABASE_PATH="./data/servercontrol.db"
uvicorn main:app --reload
```

Für lokales OAuth zusätzlich `http://localhost:8000/callback` als Redirect-URL eintragen.

## 4. Erste Einrichtung im Dashboard

1. Öffne **Einstellungen** und setze die Kanal-IDs für Willkommensnachrichten, Geburtstage und Moderationslogs.
2. Stelle sicher, dass der Bot in diesen Kanälen lesen/schreiben darf.
3. Teste `/birthday 22 10` mit einem Testdatum, prüfe den Datenbankeintrag und ändere ihn im Dashboard, wenn nötig.
4. Teste zunächst einen Timeout mit einem eigenen Testaccount bzw. einem Teammitglied, bevor du Moderationsaktionen im Server nutzt.
5. Teste Rollenverwaltung zuerst mit einer ungefährlichen Testrolle.

## Sicherheit

- `DISCORD_TOKEN`, `DISCORD_CLIENT_SECRET` und `SESSION_SECRET` niemals teilen oder committen.
- Wenn ein Token versehentlich veröffentlicht wird, im Developer Portal sofort zurücksetzen.
- Nutze HTTPS und ein privates Repository.
- Gib dem Bot nur notwendige Discord-Berechtigungen und prüfe die Rollenhierarchie.
- Nutze die Geburtstagsfunktion nur mit Einverständnis der Mitglieder.
- Lege regelmäßig ein Backup des Railway-Volumes an.
