---
name: eplan-schema-diff
description: Compare two revisions of an EPLAN schema PDF and produce one marked change document in the STUDER "Techniker/Monteur" style - old sheet with the old value in pink, new sheet with the new value in green, sheet shifts not marked, finished with a completeness check. Use whenever two schema/Schema/EPLAN/Elektroschema PDFs of the same machine are to be compared, whenever the user asks for a "Diff", "Vergleich", "Änderungen markieren" or "was hat sich geändert" between two revisions of a drawing set, and whenever a marked change document for a technician or fitter is requested.
---

# EPLAN-Schemarevisionen vergleichen

Ziel ist ein Dokument, das ein Monteur benutzen kann: nur die geänderten Blätter, Inhalt
schwarz und lesbar, Änderungen mit Textmarker hervorgehoben. **Kein Overlay der beiden
Versionen** — das wurde verworfen, weil ersetzter Text übereinanderliegt und unlesbar wird.

## In einem Durchgang

Dieses Projekt ist die einzige Quelle. Der Skill-Ordner `~/.claude/skills/eplan-schema-diff/`
enthält nur einen kurzen Verweis hierher und keine Kopie von Skript, GUI oder Regeln — zwei
Stände sind schon einmal auseinandergelaufen. Ändert sich die `description` im Frontmatter
dieser Datei, muss sie dort nachgezogen werden; alles andere nicht.

```
python C:\Projects\SchemaDiff\eplan_diff.py ALT.pdf NEU.pdf -o Aenderungen.pdf --report report.txt
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
   **Verglichen wird die normalisierte Schreibweise** (`normalize`): ohne Gross/Klein, Umlaute =
   `ae/oe/ue`, alle Bindestrich-Varianten = `-`, Leerzeichen zusammengefasst, das zweisprachige
   ` / ` und ein Bindestrich zwischen Buchstaben = Leerzeichen. Ein neu gezeichnetes Blatt
   schreibt `Ueberwachung Oel-Luft Mischer` als `Überwachung Öl-Luft-Mischer` und `Elektro /
   electric` als `Elektro Electric`. In Codes zählt weiter jedes Zeichen (`=2650-1W3`, `+24V`, `/1.1`).
3. **Pro Blattpaar vergleichen, nie global.** Ein global ausgerichteter Zeilenvergleich verrutscht
   über Blattgrenzen, sobald ein Blatt eingefügt wurde.
4. **Ersetzung an gleicher Stelle**: geflaggte alte und neue Zeilen, deren Rechtecke sich auf dem
   zugeordneten Blatt zu ≥ 50 % überlappen, bilden eine **Gruppe** (alle sich überlappenden
   Zeilen beider Seiten zusammen). Innerhalb der Gruppe nur die Spans markieren, die in den Zeilen
   der anderen Seite **nicht an derselben Stelle** stehen (± 3 pt nach Abzug des Blattversatzes)
   — so ist egal, ob ein Export `24 … 24` und `+24V DC` als zwei Zeilen oder als eine schreibt.
   **Nie als reine Menge ohne Position vergleichen:** rücken Bezeichnungen um eine Stelle
   weiter (`X2 X3 X4` → `X3 X0 X4`), ist jede verschobene Position eine Änderung, obwohl die
   Texte in der Zeile vorkommen. **Beide** Blätter aufnehmen, ALT mit dem alten Wert.
   Vor dem Überlappungstest wird der häufigste Versatz der einmal vorkommenden Zeilen des
   Blattpaars herausgerechnet (≥ 3 Zeilen): eine neu gezeichnete Seite sitzt oft ein paar Punkte
   daneben, und kleine Zeilen überlappen sonst ihr Gegenstück nicht mehr.
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
11. **Schriftfeld (Fusszeile) nicht vergleichen** — weder Text noch Grafik (`Template.blocks`,
    nur auf Blättern mit erkannter Vorlage; Hochformat-Einlagen haben kein Schriftfeld). Bearbeiter, Datum,
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
  Einzige Ausnahme: Die Zeile ist nur **verlängert oder gekürzt** — alle ihre Teile stehen in
  einer markierten Zeile der anderen Version (`1059407 15.10.2012` → `… 1216004 24.09.2026`).
  Dann zeigt die andere Seite die Änderung, und auf dieser gibt es nichts zu markieren.
  Ebenso eine Zeile, die nur **anders aufgeteilt** ist: alle ihre Teile stehen auf dem
  zugeordneten Blatt der anderen Version — dort ist nichts geändert.
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
- **Schriftfeld-Vorlagen** stehen als `Template`-Profile im Skript, Koordinaten relativ zur
  Blattgrösse. Erkannt wird pro Blatt **am Beschriftungstext** (`Blatt` bzw. `History` im
  Label-Feld), nicht am Format — ein fremdes Querformatblatt bekäme sonst ein falsches
  Schriftfeld. Blätter ohne erkannte Vorlage werden ganz verglichen.
  - `FUNCTION_TEMPLATE` (aktuell): Schlüssel Funktion + Blatt (`=1041 / 2`), Historie als Streifen
    über die ganze Unterkante.
  - `NUMBER_TEMPLATE` (älter, z. B. 1029-0326): Schlüssel Nummer + Blatt (`10041659 / 032`). Die
    Historie links steht höher als der Rest, und direkt über dem rechten Teil steht Schemainhalt
    (`Sicherheitsschaltkreis …`) — deshalb zwei Rechtecke statt eines Streifens.
  - **Fusszeile mit `Dateiname:`** (A4-Schaltplanübersicht, Lieferantenblätter): kommt in mehreren
    Grössen vor, deshalb über die Beschriftung gefunden statt über feste Brüche — Streifen ab
    `Dateiname:` bis zum Blattende, Blattnummer unter `Seite/Seiten` bzw. `Page`.
  **Bei einer unbekannten Vorlage zuerst ein Profil anlegen**, bevor irgendetwas anderes geändert
  wird. Erkennbar daran, dass die Blattschlüssel auf allen Blättern gleich sind (Rückfall auf die
  ersten 80 Zeichen = Koordinatenrahmen `B C D …`) — dann ist die Blattzuordnung reine Reihenfolge
  und jedes Folgeergebnis wertlos. Prüfen: Schlüssel eindeutig und in beiden Revisionen gleich.
- **Gedrehte Blätter:** Ältere Exporte speichern Querformat als Hochformat mit `/Rotate 90`.
  PyMuPDF liefert Text und Annotationen dann ungedreht, Blattgrösse und Rendering gedreht —
  Masken, Schriftfeld und Marker liegen daneben (Absturz „operands could not be broadcast",
  „rect is infinite or empty"). `open_schema` rechnet die Drehung beim Öffnen in den Inhalt ein
  (`remove_rotation`, pixelgleich). Schemas nie mit `pymupdf.open` direkt öffnen.
- **Verschiedene Blattgrösse:** Ein neu gezeichnetes Blatt kann als A4 kommen, wo es A3 war.
  `match_sheet_sizes` zeichnet das kleinere Blatt als Vektor auf die Grösse des grösseren um —
  sonst liegen alle Positionen um den Faktor daneben und jede Zeile gilt als neu. **Auf solchen
  Blättern nur Text vergleichen, keine Grafik:** neu gezeichnet heisst auch neue Geometrie (die
  Leitungen `24`/`04` lagen alt 21 pt, neu 12 pt auseinander), Deckung unter jedem Versatz 0,20,
  die ganze Zeichnung würde grün. Das Log nennt jedes solche Blatt. Der Positionsvergleich aus
  Regel 4 bekommt dort **12 pt statt 3 pt** Toleranz: bei 3 pt leuchten unveränderte
  Leitungsbeschriftungen (`24`, `04`, `0V DC`, `PE`), bei 12 pt sind sie frei und die neu belegten
  Stecker-Pins und Aderfarben bleiben markiert (gemessen: 3 und 8 pt zu eng, 12 und 16 pt gleich).
- **Zwei Schriftfeld-Rechtecke derselben Vorlage müssen überlappen**, nicht aneinanderstossen:
  eine neue Historienzeile ragt sonst mit ihrer Linie in die Lücke und wird als Grafik markiert.

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
