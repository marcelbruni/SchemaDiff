"""Drag-and-drop front end for eplan_diff: drop two schema revisions, get the marked change document."""

import contextlib
import functools
import io
import os
import queue
import re
import sys
import threading
import tkinter as tk
import traceback
from collections import Counter
from datetime import date
from tkinter import filedialog, messagebox, ttk

import pymupdf
from tkinterdnd2 import DND_FILES, TkinterDnD

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import eplan_diff  # noqa: E402

REVISION = re.compile(r'[_-](\d{2})(?=[_.])')
DATE = re.compile(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b')
PROGRESS = re.compile(r'Grafikvergleich (\d+)/(\d+)')
PAIRING = re.compile(r'Blattzuordnung: (\d+) Paare, (\d+) nur alt, (\d+) nur neu')
MISSING = re.compile(r'FEHLT: (ALT|NEU) Blatt (\d+)\s+(.*)')
CHANGED = re.compile(r'\((\d+) geaenderte Blaetter\)')
MIN_PAIRED_SHARE = 0.8


def revision(path):
    match = REVISION.search(os.path.basename(path))
    return int(match.group(1)) if match else None


def parse_dates(text):
    """Day first - EPLAN writes 26/05/2026 and 10.10.2024."""
    for match in DATE.finditer(text):
        try:
            if match.group(3):
                yield date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            else:
                yield date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
        except ValueError:
            continue


def serial_number(page, block):
    """The word right of 'Serial number:' - read by position, the text flow puts other cells between."""
    words = page.get_text('words', clip=block)
    for label in words:
        if label[4] != 'number:':
            continue
        if not any(w[4] == 'Serial' and abs(w[1] - label[1]) < 2 and w[2] <= label[0] + 1 for w in words):
            continue
        right = [w for w in words if abs(w[1] - label[1]) < 2 and w[0] > label[2]]
        return min(right, key=lambda w: w[0])[4] if right else None
    return None


@functools.lru_cache(maxsize=8)
def title_block_facts(path):
    """(latest date, machine serial number) out of all title blocks of a schema."""
    latest, serials = None, Counter()
    with eplan_diff.open_schema(path) as doc:
        for page in doc:
            for block in eplan_diff.title_blocks(page):
                latest = max([latest, *parse_dates(page.get_text(clip=block))], key=lambda d: d or date.min)
                serial = serial_number(page, block)
                if serial:
                    serials[serial] += 1
    return latest, serials.most_common(1)[0][0] if serials else None


def latest_change(path):
    return title_block_facts(path)[0]


def machine(path):
    return title_block_facts(path)[1]


def export_time(path):
    with pymupdf.open(path) as doc:
        stamp = (doc.metadata or {}).get('creationDate') or ''
    match = re.match(r'D:(\d{14})', stamp)
    return match.group(1) if match else None


EVIDENCE = (
    ('Änderungsdatum im Schriftfeld', latest_change),
    ('Exportdatum im PDF', export_time),
    ('Revision im Dateinamen', revision),
)


def order_revisions(first, second):
    """(old, new, certain, reason) - decided by the first evidence that tells the two apart.

    The content comes first so oddly named files work too. Any later evidence pointing the other
    way makes the result uncertain; the file time alone is never certain - copying changes it.
    """
    verdicts = []
    for name, read in EVIDENCE:
        a, b = read(first), read(second)
        if a is not None and b is not None and a != b:
            verdicts.append((name, a < b))
    if not verdicts:
        first_is_old = os.path.getmtime(first) <= os.path.getmtime(second)
        old, new = (first, second) if first_is_old else (second, first)
        return old, new, False, 'nur nach Änderungsdatum der Datei - bitte prüfen'
    name, first_is_old = verdicts[0]
    old, new = (first, second) if first_is_old else (second, first)
    conflicts = [other for other, verdict in verdicts[1:] if verdict != first_is_old]
    if conflicts:
        return old, new, False, 'nach %s, aber %s zeigt umgekehrt - bitte prüfen' % (name, ', '.join(conflicts))
    return old, new, True, 'nach %s' % name


def app_folder():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def output_paths(old, new):
    stems = [os.path.splitext(os.path.basename(p))[0][:50] for p in (old, new)]
    base = os.path.join(app_folder(), 'Aenderungen_%s_zu_%s' % tuple(stems))
    return base + '.pdf', base + '_Report.txt'


class QueueWriter(io.TextIOBase):
    def __init__(self, target):
        self.target = target

    def write(self, text):
        for line in text.splitlines():
            if line.strip():
                self.target.put(('log', line))
        return len(text)


class DiffApp:
    def __init__(self, root, initial_files):
        self.root = root
        self.files = []
        self.old = self.new = None
        self.result = None
        self.events = queue.Queue()
        self.log_lines = []
        self.running = False

        root.title('EPLAN Schemavergleich')
        root.geometry('720x520')
        root.minsize(600, 440)

        self.drop_zone = tk.Label(root, text='Altes und neues Schema (PDF) hier hineinziehen\n\n'
                                             'beide gleichzeitig oder nacheinander',
                                  relief='groove', borderwidth=2, bg='#f4f6f8', font=('Segoe UI', 12), height=5)
        self.drop_zone.pack(fill='x', padx=16, pady=(16, 8))
        self.drop_zone.drop_target_register(DND_FILES)
        self.drop_zone.dnd_bind('<<Drop>>', self.on_drop)
        self.drop_zone.bind('<Button-1>', lambda _: self.choose_files())

        files = ttk.Frame(root)
        files.pack(fill='x', padx=16)
        ttk.Label(files, text='Alt:', width=5).grid(row=0, column=0, sticky='w')
        self.old_label = ttk.Label(files, text='-')
        self.old_label.grid(row=0, column=1, sticky='w')
        ttk.Label(files, text='Neu:', width=5).grid(row=1, column=0, sticky='w')
        self.new_label = ttk.Label(files, text='-')
        self.new_label.grid(row=1, column=1, sticky='w')

        buttons = ttk.Frame(root)
        buttons.pack(fill='x', padx=16, pady=8)
        self.swap_button = ttk.Button(buttons, text='Alt/Neu tauschen', command=self.swap, state='disabled')
        self.swap_button.pack(side='left')
        self.start_button = ttk.Button(buttons, text='Vergleich starten', command=self.start, state='disabled')
        self.start_button.pack(side='left', padx=8)
        self.reset_button = ttk.Button(buttons, text='Neu beginnen', command=self.reset)
        self.reset_button.pack(side='left')
        self.open_button = ttk.Button(buttons, text='Ergebnis öffnen', command=self.open_result, state='disabled')
        self.open_button.pack(side='right')

        self.progress = ttk.Progressbar(root, mode='determinate')
        self.progress.pack(fill='x', padx=16)
        self.status = ttk.Label(root, text='Bereit.')
        self.status.pack(fill='x', padx=16, pady=(4, 4))

        self.log = tk.Text(root, height=10, font=('Consolas', 9), state='disabled', wrap='none')
        self.log.pack(fill='both', expand=True, padx=16, pady=(0, 16))

        self.root.after(100, self.poll)
        if initial_files:
            self.add_files(initial_files)

    def on_drop(self, event):
        self.add_files(self.root.tk.splitlist(event.data))

    def choose_files(self):
        if self.running:
            return
        chosen = filedialog.askopenfilenames(title='Schema-PDFs wählen', filetypes=[('PDF', '*.pdf')])
        if chosen:
            self.add_files(chosen)

    def add_files(self, paths):
        if self.running:
            return
        pdfs = [os.path.abspath(p) for p in paths if p.lower().endswith('.pdf')]
        if len(pdfs) < len(paths):
            messagebox.showwarning('Keine PDF', 'Es werden nur PDF-Dateien verglichen. Andere Dateien wurden ignoriert.')
        for path in pdfs:
            if path not in self.files:
                self.files.append(path)
        if len(self.files) > 2:
            messagebox.showwarning('Zu viele Dateien', 'Bitte genau zwei Schemas ziehen - die zuletzt gezogenen '
                                                      'zwei werden verwendet.')
            self.files = self.files[-2:]
        if len(self.files) == 1:
            self.old_label.config(text=os.path.basename(self.files[0]))
            self.status.config(text='Noch ein zweites Schema hineinziehen.')
        elif len(self.files) == 2:
            self.identify()

    def identify(self):
        first, second = self.files
        if os.path.samefile(first, second):
            messagebox.showerror('Gleiche Datei', 'Beide Dateien sind dieselbe. Bitte zwei Revisionen wählen.')
            self.reset()
            return
        self.status.config(text='Erkenne, welches Schema das neuere ist ...')
        self.root.update_idletasks()
        try:
            self.old, self.new, certain, reason = order_revisions(first, second)
        except Exception as error:
            messagebox.showerror('Datei nicht lesbar', 'Eines der PDFs kann nicht gelesen werden:\n\n%s' % error)
            self.reset()
            return
        self.show_files()
        self.status.config(text='Alt/Neu erkannt %s.' % reason)
        self.swap_button.config(state='normal')
        self.start_button.config(state='normal')
        machine_old, machine_new = machine(self.old), machine(self.new)
        if machine_old and machine_new and machine_old != machine_new:
            if not messagebox.askyesno('Andere Maschine?',
                                       'Die Schemas gehören laut Schriftfeld zu verschiedenen Maschinen:\n\n'
                                       'Alt: %s  (%s)\nNeu: %s  (%s)\n\nTrotzdem vergleichen?' % (
                                           machine_old, os.path.basename(self.old),
                                           machine_new, os.path.basename(self.new))):
                return
        if certain:
            self.start()
        else:
            self.status.config(text='Alt/Neu unsicher: %s. Bei Bedarf tauschen, dann "Vergleich starten".' % reason)

    def show_files(self):
        self.old_label.config(text=os.path.basename(self.old))
        self.new_label.config(text=os.path.basename(self.new))

    def swap(self):
        self.old, self.new = self.new, self.old
        self.show_files()

    def reset(self):
        if self.running:
            return
        self.files, self.old, self.new, self.result = [], None, None, None
        self.old_label.config(text='-')
        self.new_label.config(text='-')
        self.swap_button.config(state='disabled')
        self.start_button.config(state='disabled')
        self.open_button.config(state='disabled')
        self.progress.config(value=0)
        self.status.config(text='Bereit.')
        self.clear_log()

    def clear_log(self):
        self.log_lines = []
        self.log.config(state='normal')
        self.log.delete('1.0', 'end')
        self.log.config(state='disabled')

    def start(self):
        if not os.access(app_folder(), os.W_OK):
            messagebox.showerror('Ordner schreibgeschützt', 'Das Ergebnis wird neben das Programm gelegt, dieser '
                                 'Ordner ist aber schreibgeschützt:\n\n%s\n\nBitte das Programm in einen eigenen '
                                 'Ordner kopieren (z. B. Dokumente) und von dort starten.' % app_folder())
            return
        out_path, report_path = output_paths(self.old, self.new)
        self.running = True
        for button in (self.swap_button, self.start_button, self.reset_button, self.open_button):
            button.config(state='disabled')
        self.clear_log()
        self.progress.config(value=0, maximum=1)
        self.status.config(text='Vergleich läuft - bei rund 400 Blatt etwa 7 Minuten ...')
        threading.Thread(target=self.run_diff, args=(out_path, report_path), daemon=True).start()

    def run_diff(self, out_path, report_path):
        argv = sys.argv
        sys.argv = ['eplan_diff', self.old, self.new, '-o', out_path, '--report', report_path]
        try:
            with contextlib.redirect_stdout(QueueWriter(self.events)):
                code = eplan_diff.main()
            self.events.put(('done', (code, out_path, None)))
        except Exception as error:
            self.events.put(('done', (None, out_path, error)))
            self.events.put(('log', traceback.format_exc()))
        finally:
            sys.argv = argv

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == 'log':
                    self.append_log(payload)
                else:
                    self.finish(*payload)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def append_log(self, line):
        self.log_lines.append(line)
        match = PROGRESS.search(line)
        if match:
            self.progress.config(maximum=int(match.group(2)), value=int(match.group(1)))
        self.log.config(state='normal')
        self.log.insert('end', line + '\n')
        self.log.see('end')
        self.log.config(state='disabled')

    def finish(self, code, out_path, error):
        self.running = False
        self.reset_button.config(state='normal')
        self.progress.config(value=self.progress.cget('maximum'))
        if error is not None:
            self.report_error(error, out_path)
            return
        self.result = out_path
        self.open_button.config(state='normal')
        warnings = self.plausibility_warnings()
        if code == 0 and not warnings:
            self.status.config(text='Fertig: %s' % os.path.basename(out_path))
            messagebox.showinfo('Vergleich fertig', 'Das Änderungsdokument ist erstellt:\n\n%s\n\n%s' % (
                out_path, self.changed_summary()))
            return
        if code == 1:
            warnings.insert(0, self.missing_warning())
        elif code not in (0, None):
            warnings.insert(0, 'Das Vergleichsprogramm hat mit dem unbekannten Code %s geendet.' % code)
        self.status.config(text='Fertig mit Warnungen - bitte Meldung lesen.')
        messagebox.showwarning('Vergleich mit Warnungen', 'Das Dokument wurde erstellt, ist aber möglicherweise '
                               'nicht korrekt:\n\n%s\n\nDatei: %s' % ('\n\n'.join(warnings), out_path))

    def changed_summary(self):
        for line in self.log_lines:
            match = CHANGED.search(line)
            if match:
                return '%s geänderte Blätter.' % match.group(1)
        return ''

    def missing_warning(self):
        missing = [MISSING.search(line) for line in self.log_lines]
        missing = [m for m in missing if m]
        places = '\n'.join('  - %s Blatt %s: %s' % (m.group(1), m.group(2), m.group(3)[:60]) for m in missing[:8])
        more = '\n  ... und %d weitere' % (len(missing) - 8) if len(missing) > 8 else ''
        return ('UNVOLLSTÄNDIG: %d Unterschied(e) sind im Dokument nicht markiert. Das Dokument so nicht an die '
                'Montage weitergeben.\n%s%s' % (len(missing), places, more))

    def plausibility_warnings(self):
        warnings = []
        for line in self.log_lines:
            match = PAIRING.search(line)
            if not match:
                continue
            paired, only_old, only_new = (int(v) for v in match.groups())
            if paired < MIN_PAIRED_SHARE * (paired + max(only_old, only_new)):
                warnings.append('Nur %d Blätter konnten einander zugeordnet werden (%d nur alt, %d nur neu). '
                                'Vermutlich stimmt das Schriftfeld nicht mit der bekannten EPLAN-Vorlage überein '
                                'oder die zwei Dateien sind nicht dasselbe Projekt.' % (paired, only_old, only_new))
        return warnings

    def report_error(self, error, out_path):
        text = str(error)
        if 'Permission denied' in text or isinstance(error, PermissionError):
            message = ('Die Zieldatei ist noch geöffnet und kann nicht überschrieben werden:\n\n%s\n\n'
                       'Bitte im PDF-Viewer schliessen und den Vergleich neu starten.' % out_path)
        elif 'password' in text.lower() or 'encrypted' in text.lower():
            message = 'Eines der PDFs ist passwortgeschützt. Bitte eine ungeschützte Version verwenden.'
        elif 'cannot open' in text.lower() or 'no such file' in text.lower():
            message = 'Eine der Dateien kann nicht geöffnet werden:\n\n%s' % text
        else:
            message = 'Der Vergleich ist mit einem Fehler abgebrochen:\n\n%s\n\nDetails stehen im Protokoll.' % text
        self.start_button.config(state='normal')
        self.swap_button.config(state='normal')
        self.status.config(text='Abgebrochen.')
        messagebox.showerror('Vergleich fehlgeschlagen', message)

    def open_result(self):
        if self.result and os.path.exists(self.result):
            os.startfile(self.result)


def main():
    # A windowed exe has no console: stdout/stderr are None and any library warning would crash it.
    sys.stdout = sys.stdout or io.StringIO()
    sys.stderr = sys.stderr or io.StringIO()
    root = TkinterDnD.Tk()
    DiffApp(root, [p for p in sys.argv[1:] if os.path.isfile(p)])
    root.mainloop()


if __name__ == '__main__':
    main()
