<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <strong>Deutsch</strong> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.id.md">Bahasa Indonesia</a> · <a href="README.it.md">Italiano</a>
</p>

<p align="center"><img src="assets/superlive-ai.png" width="112" alt="SuperLive AI icon"></p>
<h1 align="center">Personen- und Fahrzeugbewegung in erfassten Überwachungskamerabildern erkennen</h1>
<p align="center">SuperLive AI Monitor · Unterstützt die Android-Emulatoren MuMu und MEmu</p>
<p align="center"> <img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Windows"> <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12"> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a> </p>
<p align="center"><a href="#quick-start">Schnellstart</a> · <a href="#workflow">Ablauf</a> · <a href="#features">Funktionen</a> · <a href="#demo">Demo</a> · <a href="#faq">FAQ</a> · <a href="#documentation">Dokumentation</a></p>

SuperLive AI Monitor ergänzt das sichtbare Kamerabild von SuperLive in MuMu unter Windows um ereignisgesteuerte Aufnahmen. Wähle einen polygonalen Bereich: YOLO erkennt dort Personen und Fahrzeuge, und abgeschlossene Videos werden über Telegram versendet. Eine RTSP-Adresse der Kamera ist nicht erforderlich.

Dies ist ein unabhängiges Community-Projekt ohne Verbindung zu den Anbietern von SuperLive oder MuMu. Die zwölf README-Sprachen betreffen die Dokumentation; die Programmoberfläche verwendet derzeit traditionelles Chinesisch.

**Aktualisierung vom 21.10.2026:** Bewegungen von Personen oder Fahrzeugen (Autos, Motorräder, Busse, Lkw) können eine Aufnahme auslösen. Die 120-Sekunden-Sendepause und 120–300 Sekunden pro Clip bleiben; tägliche CSV-Berichte sind ausgenommen. Telegram unterscheidet Personen, Fahrzeugtypen und tatsächlich gelesene Kennzeichen; unbestätigte bleiben als solche markiert. Vollbild und überlappende Ausschnitte werden geprüft, der OCR-Prozess startet bei Fehlern automatisch neu. Bis zu sieben Tage Erkennungshistorie sind per Mausrad, Scrollleiste und PageUp/PageDown verfügbar, einschließlich nachträglich ausgewerteter Originalvideos, fehlender Erkennung, unbestätigter Kandidaten und Fehler.

Bestätigte Treffer der privaten Kennzeichenliste mit höchstens zwei Zeichenänderungen werden standardmäßig aufgeklappt; andere Ergebnisse bleiben eingeklappt erhalten. Ein Klick auf den Videodateinamen markiert die Originaldatei im Windows-Datei-Explorer.

Erkennungsstatus, Bildqualität und Stapelfortschritt bleiben bei den unteren Bedienelementen. Bei aktivierter Kennzeichenerkennung stehen passende Einträge dauerhaft rechts, nach taiwanischem Kalenderdatum mit Tagesanzahl gruppiert, neueste zuerst. In schmalen Fenstern steht die Liste unter dem Kamerabild; dessen Seitenverhältnis bleibt erhalten. „其他紀錄“ öffnet die übrigen Einträge darunter und springt dorthin; erneutes Schließen führt zu den passenden Einträgen zurück.

<a id="quick-start"></a>
## Schnellstart

1. Installiere MuMu und SuperLive, melde dich an und öffne das gewünschte Livebild. Die Windows-Sitzung muss angemeldet bleiben.
2. Lade dieses Repository herunter oder klone es. Starte `installer\INSTALL_FROM_REPO.bat` oder führe den folgenden Befehl aus.
3. Richte mit `installer\SET_TELEGRAM.bat` deinen eigenen Bot und Chat lokal ein. Das Repository enthält keine Zugangsdaten.
4. Führe bei geöffnetem Livebild `installer\VERIFY_ALL.bat` aus.
5. Starte `%USERPROFILE%\Downloads\SuperLive_AI\Start_AI_Monitor.bat`; beende die Überwachung mit `Stop_AI_Monitor.bat`.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

**Bereits installiert?** Nutze `installer\UPDATE_FROM_REPO.bat`. Das Update sichert und beendet die laufenden Programme, erhält Bereichs-, Telegram- und Speichereinstellungen und lässt die Überwachung anschließend gestoppt. Starte sie nach der Kontrolle des Livebilds manuell neu. Es werden kein Windows-Autostart, keine geplante Aufgabe und kein Dienst eingerichtet.

<a id="workflow"></a>
## Ablauf

```text
Neue Personen- oder Fahrzeugaktivität im ausgewählten Bereich
  → Aufnahme starten; in den ersten 120 Sekunden keine neuen automatischen Telegram-Sendungen
  → Personen und Fahrzeuge während der gesamten Aufnahme weiter erkennen
  → Bewegung ab Sekunde 120 und vor Sekunde 300: Clip sofort beenden und nächsten starten
  → jeder neue Clip beginnt wieder mit 120 Sekunden Telegram-Sendepause
  → bei 120 Sekunden ohne Bewegung oder spätestens nach 300 Sekunden beenden
  → abgeschlossenen Clip für Telegram in die Warteschlange stellen
  → weiter überwachen; bei neuer Bewegung oder neu eintretender Person erneut aufnehmen
```

Normale Clips dauern **mindestens 120 und höchstens 300 Sekunden**. Eine beim Programmstart erstmals erkannte Person oder ein Fahrzeug zählt als neue Aktivität. Abwesenheit und Stillstand zählen beide als fehlende Bewegung; erkannte Bewegung setzt die Inaktivitätsfrist zurück. Nach Ablauf der ersten 120 Sekunden löst Bewegung zusätzlich den sofortigen Wechsel zum nächsten Clip aus. Eine reglos verbleibende Person startet nach Aufnahmeende nicht sofort einen neuen Clip.

Beim Start wird kein Telegram-Foto gesendet; automatische Telegram-Sendungen umfassen abgeschlossene Videos, bestätigte Kennzeichen und tägliche CSV-Berichte. Die Sendepause jedes neuen Clips gilt auch für ältere Videos und Wiederholungsversuche. Häufige Clipwechsel können die gesamte Warteschlange verzögern. Gesperrt werden neue Sendeaufrufe: Eine bereits laufende Übertragung lässt sich nicht zurückrufen und kann während der Pause zugestellt werden.

Bereichsauswahl oder fehlende gültige Aufnahme-/Erkennungsergebnisse unterbrechen die Beobachtung. Diese unbekannte Zeit zählt nicht als Inaktivität; nach der Wiederherstellung beginnt das Beobachtungsintervall neu.

<a id="features"></a>
## Funktionen

| Funktion | Verhalten |
| --- | --- |
| Erkennungsbereich | Konvexe und konkave Polygone; bisherige Rechtecke bleiben verwendbar |
| Personen- und Fahrzeugzuordnung | Der Erkennungsrahmen muss den Bereich mit positiver Fläche überlappen; reine Randberührung zählt nicht |
| Kamerabild | MuMu-Titel und Menü werden ausgeschlossen; Zeitstempel und Kanalnamen im Kamerabild bleiben erhalten |
| Aufnahmen | Normalerweise 2–5 Minuten; ab Sekunde 120 startet Bewegung sofort den nächsten Clip; Ende auch nach 120 Sekunden ohne Bewegung |
| Meldungen und Versand | Lokale Windows-Meldungen; abgeschlossene H.264-MP4-Videos, Kennzeichen und tägliche CSV-Berichte per Telegram; alle Sendungen warten während der ersten 120 Sekunden jedes Clips |
| Betrieb und Speicher | Separate Watchdogs, Wiederholungsversuche beim Erfassen; standardmäßig 10 FPS und `C:\SuperLive_Recordings` |

Bei 80 % Belegung des Aufnahmelaufwerks beginnt die Löschung geeigneter ältester Videos und endet bei 78 %. Aktuelle Aufnahmen und für ausstehende Übertragungen geschützte Dateien bleiben erhalten. Das Video enthält den vollständigen zugeschnittenen Kamerainhalt, nicht nur die Polygonfläche.

<a id="controls"></a>
## Polygon auswählen

| Taste oder Aktion | Wirkung |
| --- | --- |
| R / Bereich auswählen | Neue Auswahl beginnen |
| Linksklick | Mindestens drei verschiedene Eckpunkte der Reihe nach setzen |
| Rechtsklick / Backspace | Letzten Eckpunkt entfernen |
| Enter / Speichern | Gültiges Polygon ohne sich kreuzende Kanten schließen und speichern |
| Esc / Abbrechen | Bisherigen Bereich beibehalten |
| C / Vollbild | Bereichsbeschränkung aufheben |
| Q / Stopp | Überwachung beenden und laufenden Clip abschließen |

Tastenkürzel benötigen den Fokus im Vorschaufenster. Koordinaten werden relativ zum Kamerainhalt gespeichert. Kann ein alter Bereich nach einem Zuschnitt nicht sicher übertragen werden, wähle ihn erneut oder ausdrücklich das ganze Bild.

<a id="demo"></a>
## Demo

Die **36 Sekunden langen, stummen Beispiele** verwenden den tatsächlichen Vorschau-Renderer mit einer synthetischen Szene, vorgegebenen Personenrahmen und simulierten Aufnahmezuständen. Chinesische und englische Einblendungen zeigen die Sendepause, den Clipwechsel und das erneute Warten. Dies sind keine echten Kameraaufnahmen und kein Nachweis der Erkennungsgenauigkeit. Die 120-sekündigen Intervalle werden für die Darstellung verkürzt.

![Synthetische Demonstration der Polygonauswahl und Aufnahme](docs/media/superlive-demo.gif)

[MP4-Demonstration öffnen oder herunterladen](docs/media/superlive-demo.mp4)

| Polygon auswählen | Aktivität aufzeichnen | Auf erneute Bewegung warten |
| --- | --- | --- |
| ![Eckpunkte auswählen](docs/media/polygon-selection.png) | ![Aufnahme bei Aktivität](docs/media/activity-recording.png) | ![Nach Inaktivität weiter überwachen](docs/media/inactivity-rearm.png) |

<a id="faq"></a>
## FAQ

**Warum wird eine stillstehende Person nicht erneut aufgenommen?** Ein neuer Clip benötigt erneute Bewegung oder eine neu eintretende Person. Beim Programmstart zählt die erste Beobachtung als Aktivität. Eine Telegram-Startmeldung wird nicht gesendet.

**Was gilt als Bewegung?** Änderungen der Position oder Größe des erkannten Personen- oder Fahrzeugrahmens. Kleine Schwankungen werden gefiltert, langsame Verschiebungen sammeln sich an. Dies ist keine Körperhaltungsanalyse: Bewegungen ohne Änderung des Rahmens können unbemerkt bleiben. Kamerabewegung, Verdeckung und Erkennungsfehler können das Ergebnis beeinflussen.

**Ist jeder Clip fünf Minuten lang?** Nein. Normalerweise dauert ein Clip 2–5 Minuten. Bewegung zwischen Sekunde 120 einschließlich und Sekunde 300 beendet ihn sofort und startet den nächsten; 120 Sekunden ohne Bewegung beenden ihn ebenfalls. Manuelles Stoppen oder ein Fehler kann einen kürzeren Teilclip erzeugen. Dieser bleibt lokal erhalten; vor Ablauf der noch geltenden Sendepause beginnt kein Upload.

**Funktioniert das in VMware und ohne Telegram-Verbindung?** CPU-Inferenz ist möglich; VMware-Grafikbeschleunigung stellt kein CUDA bereit. Fehlgeschlagene Videoübertragungen bleiben in der Warteschlange und werden erneut versucht.

<a id="documentation"></a>
## Dokumentation und Sicherheit

[Englische Anleitung](docs/USER_GUIDE.md) · [Anleitung auf traditionellem Chinesisch](docs/USER_GUIDE.zh-TW.md) · [Architektur](docs/ARCHITECTURE.md) · [Aufnahmekonzept](docs/RECORDING.md) · [Änderungen](CHANGELOG.md) · [Sicherheit](SECURITY.md)

Quellcode liegt in `src/`, öffentliche Konfigurationsvorlagen in `config/` und Installationswerkzeuge in `installer/`. Prüfe das echte Kamerabild und den Telegram-Versand in deiner Umgebung; Offline-Tests können diese Einrichtung nicht bestätigen.

Veröffentliche keine echten Tokens, Chat-IDs, lokalen Einstellungen, Protokolle, Kameraaufnahmen oder privaten Sicherungen. Führe vor dem Push `python scripts/security_scan.py .` aus.

## Lizenz

[MIT-Lizenz](LICENSE).

## Daily CSV · 23:00 Asia/Taipei

Täglich um 23:00 (Taiwan) wird eine CSV für 00:00 einschließlich bis 23:00 ausschließlich gesendet und neben den Videos gespeichert. Tagesberichte sind von Aufnahme-Ruhezeiten ausgenommen. Keine Ereignisse nach 23:00 und kein Mitternachtsnachtrag. Auch null Ereignisse werden gemeldet; Fehler und verpasste Termine werden nachgeholt.
