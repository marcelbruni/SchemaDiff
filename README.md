# SchemaDiff – EPLAN-Schemarevisionen vergleichen

Vergleicht zwei Revisionen desselben EPLAN-Schemas (PDF) und erzeugt **ein** Änderungsdokument
für Techniker und Monteure: nur die geänderten Blätter, jeweils das alte Blatt mit dem alten Wert
**rosa**, direkt danach das neue Blatt mit dem neuen Wert **grün**. Inhalt, der nur auf ein anderes
Blatt gerutscht ist, und Änderungen im Schriftfeld (Bearbeiter, Datum, Revision) werden nicht markiert.

## Benutzen – ohne Installation

1. `release/EplanSchemaVergleich.exe` in einen eigenen Ordner kopieren, z. B. unter *Dokumente*.
2. Die Exe starten und **beide Schema-PDFs** ins Fenster ziehen – gleichzeitig oder nacheinander.
   Alternativ beide PDFs direkt auf das Programmsymbol ziehen.
3. Die App erkennt selbst, welches Schema alt und welches neu ist, und startet den Vergleich.
   Bei rund 400 Blatt dauert er etwa 7 Minuten.
4. Das Ergebnis liegt **im selben Ordner wie die Exe**:
   `Aenderungen_<alt>_zu_<neu>.pdf` und dazu ein `_Report.txt`. „Ergebnis öffnen" zeigt es an.

Beim ersten Start warnt Windows SmartScreen, weil die Exe nicht signiert ist:
*Weitere Informationen → Trotzdem ausführen*.

### Welches ist alt, welches neu?

Die App liest den Inhalt, der Dateiname darf beliebig sein. Es entscheidet das erste Merkmal, das
die zwei Dateien unterscheidet:

1. spätestes Änderungsdatum in den Schriftfeldern,
2. Exportdatum in den PDF-Metadaten,
3. Revision im Dateinamen (`_00_`, `_01_`).

Widersprechen sich die Merkmale, startet der Vergleich nicht selbst. Die App zeigt die Zuordnung,
man kann mit **Alt/Neu tauschen** korrigieren und dann **Vergleich starten**.

### Meldungen

| Meldung | Bedeutung |
|---|---|
| **UNVOLLSTÄNDIG** – Unterschiede nicht markiert | Das Dokument ist nicht vollständig. **Nicht an die Montage weitergeben**, sondern melden. |
| Nur wenige Blätter zugeordnet | Andere EPLAN-Vorlage oder nicht dasselbe Projekt – das Ergebnis ist nicht verlässlich. |
| Andere Maschine? | Die Seriennummer im Schriftfeld unterscheidet sich. |
| Zieldatei noch geöffnet | Das alte Ergebnis ist im PDF-Viewer offen – schliessen und neu starten. |
| Ordner schreibgeschützt | Die Exe liegt z. B. unter *Programme* – in einen eigenen Ordner kopieren. |

## Inhalt

| Datei | Zweck |
|---|---|
| `eplan_diff.py` | Der Vergleich selbst – Blattzuordnung, Text- und Grafikvergleich, PDF, Vollständigkeitsprüfung |
| `app/eplan_diff_app.py` | Fenster mit Drag and drop, Alt/Neu-Erkennung, Meldungen; nutzt `eplan_diff.py` direkt |
| `release/EplanSchemaVergleich.exe` | Fertige App, läuft ohne Python |
| `SKILL.md` | Skill für Claude Code – und die Doku, **warum** jede Vergleichsregel so ist, wie sie ist |
| `build.ps1` | Baut die Exe neu |
| `requirements.txt` | Python-Pakete für Skript und Build |

## Kommandozeile

```
pip install -r requirements.txt
python eplan_diff.py ALT.pdf NEU.pdf -o Aenderungen.pdf --report report.txt
```

Exit-Code `0` = vollständig, `1` = mindestens eine Differenz nicht markiert (Ergebnis nicht verwenden).

## Ändern

Die Regeln in `SKILL.md` sind jeweils an einem echten Fehlfall entstanden – vor einer Änderung
den Abschnitt zur betroffenen Regel lesen. Nach jeder Änderung an `eplan_diff.py`:

```
powershell -File build.ps1
```

sonst läuft in der Exe weiter der alte Stand.

Schema-PDFs und Änderungsdokumente sind Maschinendaten und werden über `.gitignore` nicht eingecheckt.
