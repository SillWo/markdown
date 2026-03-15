#!/usr/bin/env python3
"""
MD2PDF Converter — GUI оболочка
Требует: pip install tkinterdnd2
"""

# HiDPI/QHD: Per-Monitor DPI Awareness до создания окна
import ctypes as _ctypes, sys as _sys
if _sys.platform == 'win32':
    try:
        _ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            _ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            _ctypes.windll.user32.SetProcessDPIAware()

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import subprocess
import sys
import os
from pathlib import Path

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


# ─── Цвета и стиль ───────────────────────────────────────────
BG          = "#1e1e2e"
BG_CARD     = "#2a2a3e"
BG_INPUT    = "#313145"
ACCENT      = "#7c5cbf"
ACCENT_DARK = "#5a3f9a"
SUCCESS     = "#4caf7d"
ERROR       = "#e05c5c"
TEXT        = "#e0e0f0"
TEXT_DIM    = "#888aaa"
BORDER      = "#3a3a55"
WHITE       = "#ffffff"

FONT_TITLE  = ("Segoe UI", 18, "bold")
FONT_LABEL  = ("Segoe UI", 10)
FONT_SMALL  = ("Segoe UI", 9)
FONT_MONO   = ("Consolas", 9)
FONT_BTN    = ("Segoe UI", 10, "bold")


class App(TkinterDnD.Tk if DND_AVAILABLE else tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("MD → PDF Converter")

        # Масштаб Tk под системный DPI
        if sys.platform == 'win32':
            try:
                import ctypes
                dpi = ctypes.windll.user32.GetDpiForSystem()
                if dpi and dpi != 96:
                    self.tk.call('tk', 'scaling', dpi / 72)
            except Exception:
                pass

        self.geometry("680x640")
        self.minsize(600, 580)
        self.configure(bg=BG)
        self.resizable(True, True)

        # Состояние
        self.input_path  = tk.StringVar()
        self.output_dir  = tk.StringVar()
        self.page_format = tk.StringVar(value="A4")
        self.scale_var   = tk.DoubleVar(value=1.0)
        self.last_pdf    = None   # путь к последнему PDF
        self.last_zip    = None   # путь к последнему ZIP диаграмм
        self._process    = None   # текущий subprocess

        self._build_ui()
        self._center_window()

    # ── Центрирование ────────────────────────────────────────
    def _center_window(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    # ── Построение UI ────────────────────────────────────────
    def _build_ui(self):
        root = tk.Frame(self, bg=BG, padx=24, pady=20)
        root.pack(fill=tk.BOTH, expand=True)

        # Заголовок
        tk.Label(root, text="MD → PDF Converter",
                 font=FONT_TITLE, bg=BG, fg=WHITE).pack(anchor="w")
        tk.Label(root, text="Конвертер Markdown в PDF с поддержкой диаграмм Mermaid",
                 font=FONT_SMALL, bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(2, 18))

        # ── Карточка: исходный файл ───────────────────────────
        self._section(root, "Исходный файл (.md)")

        drop_frame = tk.Frame(root, bg=BG_INPUT, bd=0,
                              highlightthickness=2,
                              highlightbackground=BORDER,
                              highlightcolor=ACCENT)
        drop_frame.pack(fill=tk.X, pady=(4, 0))

        inner = tk.Frame(drop_frame, bg=BG_INPUT, pady=18)
        inner.pack(fill=tk.X)

        tk.Label(inner, text="⬆", font=("Segoe UI", 22),
                 bg=BG_INPUT, fg=ACCENT).pack()
        self.drop_label = tk.Label(
            inner,
            text="Перетащите .md файл сюда\nили нажмите «Выбрать файл»",
            font=FONT_LABEL, bg=BG_INPUT, fg=TEXT_DIM, justify=tk.CENTER
        )
        self.drop_label.pack()

        tk.Button(
            inner, text="Выбрать файл",
            font=FONT_BTN, bg=ACCENT, fg=WHITE,
            activebackground=ACCENT_DARK, activeforeground=WHITE,
            relief=tk.FLAT, padx=16, pady=6, cursor="hand2",
            command=self._browse_input
        ).pack(pady=(10, 0))

        if DND_AVAILABLE:
            for w in [drop_frame, inner, self.drop_label]:
                w.drop_target_register(DND_FILES)
                w.dnd_bind('<<Drop>>', self._on_drop)
            self._bind_hover(drop_frame, inner, self.drop_label)

        path_frame = tk.Frame(root, bg=BG_CARD, pady=8, padx=12)
        path_frame.pack(fill=tk.X, pady=(6, 0))
        tk.Label(path_frame, text="Файл:", font=FONT_SMALL,
                 bg=BG_CARD, fg=TEXT_DIM).pack(side=tk.LEFT)
        tk.Label(path_frame, textvariable=self.input_path,
                 font=FONT_MONO, bg=BG_CARD, fg=TEXT,
                 anchor="w", width=60).pack(side=tk.LEFT, padx=(6, 0))

        # ── Карточка: папка сохранения ────────────────────────
        self._section(root, "Папка для сохранения PDF")

        out_row = tk.Frame(root, bg=BG)
        out_row.pack(fill=tk.X, pady=(4, 0))

        self.out_entry = tk.Entry(
            out_row, textvariable=self.output_dir,
            font=FONT_LABEL, bg=BG_INPUT, fg=TEXT,
            insertbackground=WHITE, relief=tk.FLAT,
            bd=0, highlightthickness=2,
            highlightbackground=BORDER, highlightcolor=ACCENT
        )
        self.out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                            ipady=8, padx=(0, 8))

        tk.Button(
            out_row, text="Обзор…",
            font=FONT_BTN, bg=BG_CARD, fg=TEXT,
            activebackground=BORDER, activeforeground=WHITE,
            relief=tk.FLAT, padx=14, pady=6, cursor="hand2",
            command=self._browse_output
        ).pack(side=tk.RIGHT)

        # ── Настройки ─────────────────────────────────────────
        self._section(root, "Настройки")

        opts = tk.Frame(root, bg=BG)
        opts.pack(fill=tk.X, pady=(4, 0))

        tk.Label(opts, text="Формат страницы:",
                 font=FONT_LABEL, bg=BG, fg=TEXT).grid(
                     row=0, column=0, sticky="w", padx=(0, 10))

        fmt_combo = ttk.Combobox(
            opts, textvariable=self.page_format,
            values=["A4", "A3", "A5", "Letter", "Legal"],
            state="readonly", width=10, font=FONT_LABEL
        )
        fmt_combo.grid(row=0, column=1, sticky="w")

        tk.Label(opts, text="Масштаб диаграмм:",
                 font=FONT_LABEL, bg=BG, fg=TEXT).grid(
                     row=0, column=2, sticky="w", padx=(30, 10))

        scale_frame = tk.Frame(opts, bg=BG)
        scale_frame.grid(row=0, column=3, sticky="w")

        self.scale_lbl = tk.Label(scale_frame, text="1.0×",
                                   font=FONT_LABEL, bg=BG, fg=ACCENT, width=4)
        self.scale_lbl.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Scale(
            scale_frame, from_=0.5, to=2.0,
            variable=self.scale_var, orient=tk.HORIZONTAL,
            length=140, command=self._update_scale_label
        ).pack(side=tk.LEFT)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox",
                         fieldbackground=BG_INPUT, background=BG_INPUT,
                         foreground=TEXT, selectbackground=ACCENT,
                         bordercolor=BORDER, arrowcolor=TEXT)

        # ── Кнопка конвертации + отмена ──────────────────────
        btn_row = tk.Frame(root, bg=BG)
        btn_row.pack(fill=tk.X, pady=(20, 0))

        self.convert_btn = tk.Button(
            btn_row, text="▶  Конвертировать в PDF",
            font=("Segoe UI", 12, "bold"),
            bg=ACCENT, fg=WHITE,
            activebackground=ACCENT_DARK, activeforeground=WHITE,
            disabledforeground=WHITE,
            relief=tk.FLAT, pady=12, cursor="hand2",
            command=self._start_conversion
        )
        self.convert_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.cancel_btn = tk.Button(
            btn_row, text="✕",
            font=("Segoe UI", 12, "bold"),
            bg=ERROR, fg=WHITE,
            activebackground="#b04040", activeforeground=WHITE,
            disabledforeground="#666",
            relief=tk.FLAT, pady=12, padx=16, cursor="hand2",
            state=tk.DISABLED,
            command=self._cancel_conversion
        )
        self.cancel_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # ── Прогресс / лог ────────────────────────────────────
        self._section(root, "Прогресс")

        log_outer = tk.Frame(root, bg=BORDER, padx=1, pady=1)
        log_outer.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        log_inner = tk.Frame(log_outer, bg=BG_CARD)
        log_inner.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_inner, font=FONT_MONO,
            bg=BG_CARD, fg=TEXT, insertbackground=WHITE,
            relief=tk.FLAT, bd=0, padx=10, pady=8,
            state=tk.DISABLED, wrap=tk.WORD, height=7
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(log_inner, command=self.log_text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=sb.set)

        self.log_text.bind("<Control-c>", self._copy_log)
        self.log_text.bind("<Control-C>", self._copy_log)
        self.log_text.bind("<Control-a>", self._select_all_log)
        self.log_text.bind("<Control-A>", self._select_all_log)

        self.log_text.tag_configure("ok",   foreground=SUCCESS)
        self.log_text.tag_configure("err",  foreground=ERROR)
        self.log_text.tag_configure("info", foreground=TEXT_DIM)
        self.log_text.tag_configure("bold", foreground=WHITE, font=FONT_BTN)

        # ── Кнопки результата (PDF + ZIP) ─────────────────────
        result_row = tk.Frame(root, bg=BG)
        result_row.pack(fill=tk.X, pady=(8, 0))

        self.open_btn = tk.Button(
            result_row, text="📄  Открыть PDF",
            font=FONT_BTN, bg=BG_CARD, fg=TEXT_DIM,
            activebackground=BORDER, activeforeground=WHITE,
            relief=tk.FLAT, pady=8, cursor="hand2",
            state=tk.DISABLED,
            command=self._open_pdf
        )
        self.open_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.zip_btn = tk.Button(
            result_row, text="🖼️  Скачать диаграммы (.zip)",
            font=FONT_BTN, bg=BG_CARD, fg=TEXT_DIM,
            activebackground=BORDER, activeforeground=WHITE,
            relief=tk.FLAT, pady=8, cursor="hand2",
            state=tk.DISABLED,
            command=self._save_diagrams_zip
        )
        self.zip_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(6, 0))

    # ── Вспомогательные виджеты ──────────────────────────────
    def _section(self, parent, text):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill=tk.X, pady=(14, 0))
        tk.Label(f, text=text.upper(), font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=TEXT_DIM).pack(side=tk.LEFT)
        tk.Frame(f, bg=BORDER, height=1).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), pady=6)

    def _bind_hover(self, *widgets):
        def on_enter(_):
            for w in widgets:
                try: w.config(highlightbackground=ACCENT)
                except Exception: pass
        def on_leave(_):
            for w in widgets:
                try: w.config(highlightbackground=BORDER)
                except Exception: pass
        for w in widgets:
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

    # ── Обработчики ──────────────────────────────────────────
    def _update_scale_label(self, val=None):
        self.scale_lbl.config(text=f"{self.scale_var.get():.1f}×")

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Выберите Markdown файл",
            filetypes=[("Markdown files", "*.md *.markdown"), ("All files", "*.*")]
        )
        if path:
            self._set_input(path)

    def _browse_output(self):
        folder = filedialog.askdirectory(title="Выберите папку для сохранения PDF")
        if folder:
            self.output_dir.set(folder)

    def _on_drop(self, event):
        raw = event.data.strip()
        if raw.startswith('{') and raw.endswith('}'):
            raw = raw[1:-1]
        path = raw.split('} {')[0].strip()
        if path.lower().endswith(('.md', '.markdown')):
            self._set_input(path)
        else:
            self._log("Поддерживаются только файлы .md и .markdown", "err")

    def _set_input(self, path: str):
        self.input_path.set(path)
        self.drop_label.config(text=f"✓  {Path(path).name}", fg=SUCCESS)
        if not self.output_dir.get():
            self.output_dir.set(str(Path(path).parent))
        self._log(f"Выбран файл: {path}", "info")

    def _open_pdf(self):
        if self.last_pdf and os.path.exists(self.last_pdf):
            os.startfile(self.last_pdf)

    def _save_diagrams_zip(self):
        """Предлагает сохранить ZIP-архив диаграмм в выбранное место."""
        if not self.last_zip or not os.path.exists(self.last_zip):
            messagebox.showerror("Ошибка", "ZIP-архив не найден.")
            return

        default_name = Path(self.last_zip).name
        default_dir  = Path(self.last_zip).parent

        dst = filedialog.asksaveasfilename(
            title="Сохранить архив диаграмм",
            initialdir=default_dir,
            initialfile=default_name,
            defaultextension=".zip",
            filetypes=[("ZIP архив", "*.zip"), ("Все файлы", "*.*")]
        )
        if not dst:
            return

        import shutil
        shutil.copy2(self.last_zip, dst)
        self._log(f"📦 Архив сохранён: {dst}", "ok")

    # ── Лог ──────────────────────────────────────────────────
    def _log(self, msg: str, tag: str = ""):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ── Конвертация ──────────────────────────────────────────
    def _start_conversion(self):
        md_path = self.input_path.get().strip()
        out_dir = self.output_dir.get().strip()

        if not md_path:
            messagebox.showwarning("Файл не выбран", "Пожалуйста, выберите .md файл.")
            return
        if not os.path.exists(md_path):
            messagebox.showerror("Файл не найден", f"Файл не существует:\n{md_path}")
            return
        if not out_dir:
            out_dir = str(Path(md_path).parent)
            self.output_dir.set(out_dir)

        out_pdf = str(Path(out_dir) / (Path(md_path).stem + ".pdf"))
        self.last_pdf = out_pdf
        self.last_zip = None

        self.convert_btn.config(
            state=tk.DISABLED, text="⏳  Конвертация…", bg=ACCENT_DARK)
        self.open_btn.config(state=tk.DISABLED, fg=TEXT_DIM, bg=BG_CARD)
        self.zip_btn.config(state=tk.DISABLED, fg=TEXT_DIM, bg=BG_CARD)
        self._clear_log()
        self._log("Запуск конвертации…", "bold")

        threading.Thread(
            target=self._run_conversion,
            args=(md_path, out_pdf),
            daemon=True
        ).start()

    def _run_conversion(self, md_path: str, out_pdf: str):
        zip_path = None
        try:
            if getattr(sys, 'frozen', False):
                base_dir = Path(sys._MEIPASS)
            else:
                base_dir = Path(__file__).parent
            converter_path = base_dir / "md2pdf_converter.py"

            cmd = [
                sys.executable, "-u", str(converter_path),
                md_path, out_pdf,
                "--format", self.page_format.get(),
                "--scale",  f"{self.scale_var.get():.1f}",
            ]

            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUNBUFFERED'] = '1'

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                bufsize=1, env=env,
            )
            self._process = process
            self.after(0, lambda: self.cancel_btn.config(state=tk.NORMAL))

            while True:
                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        break
                    continue
                line = line.rstrip()
                if not line:
                    continue

                # Парсим служебную строку с путём к ZIP
                if line.startswith('DIAGRAMS_ZIP:'):
                    zip_path = line.split('DIAGRAMS_ZIP:', 1)[1].strip()
                    continue  # не выводим в лог

                if "✅" in line or "готов" in line.lower():
                    self.after(0, self._log, line, "ok")
                elif "❌" in line or "⚠" in line or "ошибка" in line.lower():
                    self.after(0, self._log, line, "err")
                elif "🖼️" in line:
                    self.after(0, self._log, line, "ok")
                else:
                    self.after(0, self._log, line, "info")

            process.wait()
            success = process.returncode == 0 and os.path.exists(out_pdf)
            self._process = None

        except Exception as exc:
            self._process = None
            self.after(0, self._log, f"Критическая ошибка: {exc}", "err")
            success = False

        self.after(0, self._on_done, success, out_pdf, zip_path)

    def _on_done(self, success: bool, out_pdf: str, zip_path=None):
        self.convert_btn.config(
            state=tk.NORMAL, text="▶  Конвертировать в PDF", bg=ACCENT)
        self.cancel_btn.config(state=tk.DISABLED)

        if success:
            self._log(f"\nФайл сохранён: {out_pdf}", "bold")
            self.open_btn.config(state=tk.NORMAL, bg=SUCCESS, fg=WHITE)

            if zip_path and os.path.exists(zip_path):
                self.last_zip = zip_path
                self.zip_btn.config(state=tk.NORMAL, bg="#3498db", fg=WHITE)
                self._log(f"Архив диаграмм готов к скачиванию", "ok")
        else:
            self._log("\nКонвертация завершилась с ошибкой.", "err")
            self.open_btn.config(state=tk.DISABLED, bg=BG_CARD, fg=TEXT_DIM)
            self.zip_btn.config(state=tk.DISABLED, bg=BG_CARD, fg=TEXT_DIM)

    # ── Отмена ───────────────────────────────────────────────
    def _cancel_conversion(self):
        if self._process and self._process.poll() is None:
            self._process.kill()
            self._process = None
            self._log("⛔  Конвертация отменена пользователем.", "err")
            self.convert_btn.config(
                state=tk.NORMAL, text="▶  Конвертировать в PDF", bg=ACCENT)
            self.cancel_btn.config(state=tk.DISABLED)

    # ── Копирование из лога ──────────────────────────────────
    def _copy_log(self, event=None):
        try:
            text = self.log_text.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            text = self.log_text.get("1.0", tk.END)
        self.clipboard_clear()
        self.clipboard_append(text)
        return "break"

    def _select_all_log(self, event=None):
        self.log_text.tag_add(tk.SEL, "1.0", tk.END)
        self.log_text.mark_set(tk.INSERT, "1.0")
        self.log_text.see(tk.INSERT)
        return "break"


def main():
    if not DND_AVAILABLE:
        print("Подсказка: установите tkinterdnd2 для drag-and-drop:")
        print("  pip install tkinterdnd2")
    app = App()
    app.mainloop()

#
if __name__ == "__main__":
    main()
