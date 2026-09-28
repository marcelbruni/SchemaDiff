---
name: eplan-schema-diff
description: Compare two revisions of an EPLAN schema PDF and produce one marked change document in the STUDER "Techniker/Monteur" style - old sheet with the old value in pink, new sheet with the new value in green, sheet shifts not marked, finished with a completeness check. Use whenever two schema/Schema/EPLAN/Elektroschema PDFs of the same machine are to be compared, whenever the user asks for a "Diff", "Vergleich", "Änderungen markieren" or "was hat sich geändert" between two revisions of a drawing set, and whenever a marked change document for a technician or fitter is requested.
---

# EPLAN-Schemarevisionen vergleichen

Ziel ist ein Dokument, das ein Monteur benutzen kann: nur die geänderten Blätter, Inhalt
schwarz und lesbar, Änderungen mit Textmarker hervorgehoben. **Kein Overlay der beiden
Versionen** — das wurde verworfen, weil ersetzter Text übereinanderliegt und unlesbar wird.

## In einem Durchgang

```
python ~/.claude/skills/eplan-schema-diff/eplan_diff.py ALT.pdf NEU.pdf -o Aenderungen.pdf --report report.txt
```

Das Skript macht Blattzuordnung, Text- und Grafikvergleich, baut das PDF und prüft am Schluss
die Vollständigkeit. Laufzeit für ~360 Blatt: rund 7 Minuten, also im Hintergrund starten.
Exit-Code 1 bedeutet, dass eine Differenz nicht markiert wurde — dann nicht abliefern.

Erst danach, falls der Benutzer etwas beanstandet, an den Konstanten drehen. Nicht vorher
Zwischenstände bauen.

## Aufbau des Ergebnisses

- Seite 1: Übersicht mit Legende und Tabelle der geänderten Blätter.
- Danach pro geändertem Blatt: **zuerst ALT (rosa), direkt danach NEU (grün)**.
- Oben links auf jedem Blatt ein Badge `ALT - Blatt 83 - 3 markierte Stellen`.
- PDF-Lesezeichen pro geändertem Blatt.
- Die Quellblätter werden als Vektorseiten übernommen, nicht gerastert (scharf, ~4 MB statt 70 MB).

## Regeln, die das Ergebnis bestimmen

1. **Blattzuordnung** über Funktions- und Blattnummer aus dem Schriftfeld, dann `difflib` über
   diese Schlüssel. So bleibt die Zuordnung stabil, wenn Blätter eingefügt werden.
2. **Zeile ist die Vergleichseinheit**, nicht Block und nicht Span. Zeilen entstehen innerhalb
   eines Textblocks über y-Bänder; die Spans einer Zeile werden nach x sortiert, sonst kippt die
   Reihenfolge zwischen zwei Exporten (`Star-Tec` mal vor, mal nach dem Hersteller) und erzeugt
   Scheinänderungen.
3. **Pro Blattpaar vergleichen, nie global.** Ein global ausgerichteter Zeilenvergleich verrutscht
   über Blattgrenzen, sobald ein Blatt eingefügt wurde.
4. **Ersetzung an gleicher Stelle**: geflaggte alte und neue Zeile, deren Rechtecke sich auf dem
   zugeordneten Blatt zu ≥ 50 % überlappen, sind ein Paar. Innerhalb des Paares nur die
   abweichenden Spans markieren — und **beide** Blätter aufnehmen, ALT mit dem alten Wert.
   Gepaart werden nur Zeilen, die auf **keiner** Seite als verschoben erkannt sind (Regel 5).
   Sonst wird eine hereingerutschte Zeile mit der verdrängten gepaart, und unveränderter
   Text leuchtet (z. B. eine Inhaltsverzeichnis-Gruppe, die ein Blatt weiter gerutscht ist).
5. **Verschiebung nicht markieren — aber nur echte Verschiebung.** Eine Zeile gilt als verschoben,
   wenn **derselbe Text in der anderen Version seinen Platz verlassen hat**, also dort vom
   Blattvergleich selbst als geändert geflaggt ist. Jedes Vorkommen wird genau einmal
   zugeordnet, nächstgelegenes Blatt zuerst; die Distanz spielt sonst keine Rolle.
   **Nie genügt, dass der Text irgendwo in der anderen Version unverändert steht** — ein neuer
   Artikel mit gängiger Artikelnummer oder Hersteller (`Fritz Studer`, `Mask: 255.255.255.0`)
   wäre sonst als Verschiebung verworfen. Auch keine Fensterbreite in Blättern.
   **Ein neu eingefügtes Blatt ist kein Verschiebungsziel**: was dort landet, hat sein altes
   Blatt wirklich verlassen. Ausnahme: ein **Umbruchblatt** (Regel 7) ist ein normales
   Verschiebungsziel.
   **Wiederholte Kopfzeile:** Eine Gruppen-Überschrift (Stückliste `=2801 Module power supply…`,
   Inhaltsverzeichnis `==4.01 Tool movements`) steht auf jedem Blatt, über das die Gruppe läuft.
   Bricht die Gruppe nach einer Einfügung woanders um, taucht die Überschrift auf einem Blatt
   auf, ohne anderswo zu verschwinden. Deshalb gilt als zweite Stufe — **erst nach** der Paarung
   aus Regel 4, damit ein ersetzter Wert vorher als Ersetzung erkannt ist: eine übrig gebliebene
   Zeile ist nicht markiert, wenn ihr Text in der anderen Version auf dem zugeordneten Blatt oder
   dessen direktem Nachbarn steht. Weiter weg zählt nicht.
6. **Blätter ohne Markierung weglassen**, auch einzeln. Hat nur das NEU-Blatt Marker, kommt das
   ALT-Blatt nicht mit.
7. **Komplett neues oder entfallenes Blatt**: nur die Blattnummer unten rechts markieren, kein
   Rahmen, keine Flächenmarkierung.
   **Umbruchblatt weglassen:** Ein Blatt ohne Gegenstück, dessen Zeilen (ohne Schriftfeld)
   ausnahmslos in der anderen Version ihren Platz verlassen haben oder wiederholte Kopfzeilen
   sind (Regel 5), ist nur entstanden, weil davor Inhalt
   dazukam und der Rest weitergeschoben wurde (typisch: letzte Stücklistenseite). Es enthält
   nichts Neues, kommt nicht in den Diff und wird im Log als „Inhalt nur umgebrochen“ gelistet.
   Entfallene Blätter analog.
   **Umbruchblatt mit Ergänzungen:** Stammen mindestens 40 % der Zeichen eines Blatts ohne
   Gegenstück aus Zeilen, die in der anderen Version ihren Platz verlassen haben, ist es ein
   Umbruchblatt, auf dem zusätzlich Neues steht (letzte Stücklistenseite mit zwei neuen
   Artikeln). Dann nicht „komplett neu", sondern wie ein normales Blatt: umgebrochene Zeilen und
   wiederholte Kopfzeilen frei, nur die wirklich neuen Zeilen markiert (Regel 12 gilt), Badge
   `NEU - Blatt 244 - neu durch Umbruch - 2 markierte Stellen`. Gezählt wird **nach Zeichen und
   nur echte Verschiebung**, nicht nach Zeilen: kurze generische Texte (`/`, `SH`, `1`) stehen
   auf jedem Schema und liessen ein echtes neues Blatt sonst bis 65 % „umgebrochen" aussehen.
   Gemessen: Umbruchblatt 59 %, echte neue/entfallene Blätter 3–21 %.
8. **Marker eng am Text**: Rechteck-Annotation mit Blendmode Multiply, 1 pt Rand.
   **Nie `add_highlight_annot`** — die rendert als Ellipse weit über den Text hinaus.
9. **Grafikvergleich** nur ausserhalb der Textbereiche und verschiebungstolerant. Entschieden wird
   **pro Grafikstelle, nie pro Blatt**: eine frühere Regel „≥ 4 verschobene Zeilen = Grafikvergleich
   fürs ganze Blatt aus" liess einen entfernten Gerätekasten samt Steckern unmarkiert, weil
   generische Pin-Texte (`1`, `n.c.`) als verschoben galten.
   - **Gesuchte Versätze:** senkrecht bis ±220 px, dazu jeder Versatz, um den die einmal
     vorkommenden Textzeilen des Blattpaars gewandert sind (auch waagrecht — ein
     Inhaltsverzeichnis-Eintrag wandert in die andere Spalte). Grafik reist mit ihrem Text.
   - **Deckung ≥ 90 %** unter einem dieser Versätze = verschoben.
   - **Verschobener Text** heisst hier: Zeilen, die nach Regel 5 als verschoben gelten, **und**
     unveränderte Zeilen, deren Position sich geändert hat.
   - Berührt die Stelle (± 6 pt) verschobenen Text, genügt **≥ 50 % Deckung** — ein Kasten an
     gewandertem Inhalt enthält oft ein neues Stück (wiederholter Tabellenkopf) und verfehlt so
     die 90 %. Ohne markierten Text darin entfällt die Stelle ganz.
   - Liegt verschobener Text nur im selben Höhenband (Tabellenrahmen und Kopfbalken laufen über
     die ganze Zeile), genügt ≥ 50 % Deckung, sofern kein markierter Text berührt wird.
   - Gemessen: gewanderte Rahmen 0,60–0,94; entfernter Gerätekasten 0,19; neues Warnschild 0,03.
     Kein Kachel-für-Kachel-Test — kleine Kacheln mit Linienstücken finden sich überall wieder,
     der echte Kasten zerfällt in Fragmente und fällt über `MAX_GRAPHICS_CLUSTERS` weg.
   **Bei der Verschiebungssuche jedes Blatt nur mit seiner eigenen Textmaske beschneiden**, nicht
   mit der vereinigten beider Versionen: Wird in einer Tabelle eine Zeile eingefügt, steht der
   Text der anderen Version genau eine Zeile versetzt und zerschneidet sonst die Tabellenlinien,
   die mitgerutscht sind — dann leuchtet eine unveränderte Gruppe am Blattende grün.
10. **Grüner Grafikmarker wächst in den Text**, aber nur in Text, der selbst grün markiert ist.
    Verschobener und unveränderter Text daneben bleibt frei, auch wenn er das Rechteck berührt.
    Der rosa Marker wächst nicht.
11. **Schriftfeld (Fusszeile) nicht vergleichen** — weder Text noch Grafik (`FIELD_TITLE_BLOCK`,
    nur auf Querformatblättern; Hochformat-Einlagen haben kein Schriftfeld). Bearbeiter, Datum,
    Änderungsnummer, Revision und Historie ändern sich mit jedem Release auf jedem Blatt; ein
    Blatt, auf dem sich nur das ändert, gehört nicht in den Diff. Die Übersichtsseite sagt das in
    der Legende. Für Blattzuordnung (Regel 1) und die Blattnummer-Markierung (Regel 7) wird das
    Schriftfeld weiter gelesen.
12. **Ganzen Artikel markieren.** Ein Artikel ist eine Kette von Zeilen, die bündig (x0 oder x1
    ± 1 pt) mit höchstens 6 pt Abstand untereinander stehen — die Gerätebeschriftung
    `=2650-1W3 / /1.1 / 2 m / DP / 30097585 / 236-9103 / RS PRO`. Ist darin eine Zeile markiert,
    werden alle **geflaggten** Zeilen desselben Artikels mitmarkiert, auch wenn sie einzeln als
    verschoben galten. Unveränderte Zeilen des Artikels bleiben frei. Stücklistenzeilen liegen
    8,9 pt auseinander und ketten deshalb nicht zu einer ganzen Tabelle zusammen.

## Vollständigkeitsprüfung — Pflicht

Das Skript prüft am Schluss gegen das **erzeugte PDF**, nicht gegen die eigenen Zwischenwerte:
es liest die Badges und Annotationen zurück und vergleicht sie mit den Rohdokumenten.

- **Harte Bedingung:** Jede Zeile, deren Text in der anderen Version **gar nicht** vorkommt,
  muss im Diff markiert sein. Wird eine nicht gefunden, endet der Lauf mit
  `N DIFFERENZEN NICHT MARKIERT` und Exit-Code 1.
- Zeilen, die in beiden Versionen vorkommen, aber unterschiedlich oft, werden aufgelistet
  (typisch: Gruppen-Kopfzeilen, die wiederholt werden, weil eine Gruppe neu über zwei Blätter
  geht). Diese Liste ist zum Durchsehen da, nicht zum Ignorieren.
- Als Verschiebung verworfene Zeilen und als Layout verworfene Grafikstellen werden gezählt und
  angezeigt. Nichts wird stillschweigend weggelassen.

Zweifelt der Benutzer eine Markierung als Verschiebung an: über den **ganzen Abschnitt** als
Multiset vergleichen und Vorkommen zählen (z. B. alle Stücklistenblätter beider Revisionen),
nicht blattweise schauen. Eine Zeile mit 0 Vorkommen in der alten Version ist echt neu.

## Was nicht anzufassen ist

- Die Deckblätter am Ende der Dokumente sind echt (zweiter Dokumentteil, Trade `#M`) und gehören
  in den Diff, wenn sich dort die Projektrevision ändert.
- Die Schriftfeld-Koordinaten stehen relativ zur Blattgrösse (`FIELD_*`). Bei einer anderen
  EPLAN-Vorlage diese Brüche prüfen, bevor irgendetwas anderes geändert wird — stimmt die
  Blattzuordnung nicht, ist jedes Folgeergebnis wertlos.

## App für Benutzer ohne Claude Code

`app/eplan_diff_app.py` ist ein Drag-and-drop-Fenster um `eplan_diff.py` (importiert es direkt,
keine Kopie der Logik). Exit-Code 1, schwache Blattzuordnung (< 80 % Paare), offene Zieldatei,
schreibgeschützter Ordner und geschützte PDFs werden als verständliche Meldung angezeigt.
Ergebnis: `Aenderungen_<alt>_zu_<neu>.pdf` + `_Report.txt` **im Ordner der exe**.

Alt/Neu wird **aus dem Inhalt** erkannt, damit auch komisch benannte Dateien funktionieren. Das
erste Merkmal, das die zwei Dateien unterscheidet, entscheidet:
1. spätestes Datum in einem Schriftfeld (jüngster Eintrag der Änderungshistorie, Tag zuerst),
2. Erstelldatum in den PDF-Metadaten (EPLAN-Export),
3. Revision im Dateinamen.
Zeigt ein späteres Merkmal in die andere Richtung, startet der Vergleich nicht von selbst,
sondern zeigt die Zuordnung mit „Alt/Neu tauschen" an. Das Dateidatum allein ist nie sicher —
Kopieren und Herunterladen ändern es.

Auch die Prüfung „gleiche Maschine?" liest den Inhalt: die `Serial number` im Schriftfeld, über
die Wortposition gelesen (im Textfluss stehen andere Zellen dazwischen). Nicht über den
Dateinamen — sonst fragt die App bei jedem komisch benannten Paar nach und blockiert.

**Nach jeder Änderung an `eplan_diff.py` die exe neu bauen**, sonst läuft beim Benutzer der alte
Stand: `powershell -File build.ps1` im Skill-Ordner. Ergebnis: `release/EplanSchemaVergleich.exe`.

## Nach dem Lauf

Ist die Zieldatei in einem Viewer offen, schlägt `save` mit „Permission denied" fehl.
Dann den Benutzer bitten, sie zu schliessen, statt unter neuem Namen abzulegen.
