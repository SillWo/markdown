#!/usr/bin/env python3
"""
Markdown to PDF Converter with Mermaid Diagram Support
Использует Playwright (Chromium) вместо WeasyPrint — работает на Windows без GTK
"""

import re
import base64
import subprocess
import tempfile
import os
import sys
import hashlib
import json
import shutil
import argparse
import zipfile
from pathlib import Path
from typing import Optional, Tuple, List

# Форсируем UTF-8 для stdout/stderr на Windows (иначе cp1251 не тянет эмодзи)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


class MarkdownToPDFConverter:

    def __init__(self, page_format: str = 'A4', mermaid_scale: float = 1.0):
        self.page_format = page_format.upper()
        self.mermaid_scale = mermaid_scale
        self.temp_dir: Optional[str] = None
        self.diagram_pngs: List[str] = []  # пути к PNG-файлам диаграмм

        self._page_sizes = {
            'A3':     (1122, 1587),
            'A4':     (794,  1123),
            'A5':     (559,  794),
            'LETTER': (816,  1056),
            'LEGAL':  (816,  1344),
        }

    # ──────────────────────────────────────────────
    # Проверка зависимостей
    # ──────────────────────────────────────────────
    @staticmethod
    def _find_mmdc() -> Optional[str]:
        found = shutil.which('mmdc')
        if found:
            return found
        if sys.platform == 'win32':
            candidates = [
                shutil.which('mmdc.cmd'),
                os.path.expandvars(r'%APPDATA%\npm\mmdc.cmd'),
                os.path.expandvars(r'%APPDATA%\npm\mmdc'),
            ]
            for c in candidates:
                if c and os.path.exists(c):
                    return c
        return None

    def _check_dependencies(self) -> Tuple[bool, str]:
        try:
            import markdown  # noqa: F401
        except ImportError:
            return False, "pip install markdown"
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False, "pip install playwright  &&  playwright install chromium"
        if not self._find_mmdc():
            return False, "npm install -g @mermaid-js/mermaid-cli"
        return True, "OK"

    # ──────────────────────────────────────────────
    # Mermaid → SVG  +  PNG (для ZIP-архива)
    # ──────────────────────────────────────────────
    def _render_mermaid(self, code: str, idx: int) -> Optional[str]:
        """Рендерит блок Mermaid в SVG (для PDF) и PNG (для ZIP).
        Возвращает data-URI SVG или None."""
        mmd_path = os.path.join(self.temp_dir, f'diagram_{idx}.mmd')
        svg_path = os.path.join(self.temp_dir, f'diagram_{idx}.svg')
        png_path = os.path.join(self.temp_dir, f'diagram_{idx}.png')
        cfg_path = os.path.join(self.temp_dir, 'mmdc_config.json')

        with open(mmd_path, 'w', encoding='utf-8') as f:
            f.write(code)

        config = {
            "theme": "default",
            "flowchart": {"useMaxWidth": True, "curve": "basis"},
            "sequence":  {"useMaxWidth": True},
            "gantt":     {"useMaxWidth": True}
        }
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(config, f)

        mmdc_bin = self._find_mmdc()
        if not mmdc_bin:
            print('  ⚠ mmdc не найден', file=sys.stderr)
            return None

        base_cmd = [
            mmdc_bin,
            '-i', mmd_path,
            '-c', cfg_path,
            '-s', str(self.mermaid_scale),
            '--pdfFit',
        ]

        def run_mmdc(out_path):
            try:
                result = subprocess.run(
                    base_cmd + ['-o', out_path],
                    capture_output=True, text=True,
                    encoding='utf-8', errors='replace',
                    timeout=60,
                    shell=(sys.platform == 'win32'),
                )
                return result.returncode == 0 and os.path.exists(out_path)
            except Exception as e:
                print(f'  ⚠ mmdc ошибка: {e}', file=sys.stderr)
                return False

        # Рендерим SVG (для вставки в PDF)
        if not run_mmdc(svg_path):
            print(f'  ⚠ Ошибка рендеринга диаграммы #{idx}', file=sys.stderr)
            return None

        # Рендерим PNG (для ZIP-архива)
        if run_mmdc(png_path):
            self.diagram_pngs.append(png_path)

        # Читаем SVG и патчим: убираем фиксированные width/height
        with open(svg_path, 'rb') as f:
            svg_str = f.read().decode('utf-8')

        svg_str = re.sub(r'(<svg\b[^>]*?)\s+width="[^"]*"',  r'\1', svg_str)
        svg_str = re.sub(r'(<svg\b[^>]*?)\s+height="[^"]*"', r'\1', svg_str)

        b64 = base64.b64encode(svg_str.encode('utf-8')).decode('utf-8')
        return f'data:image/svg+xml;base64,{b64}'

    # ──────────────────────────────────────────────
    # Обработка всех блоков ```mermaid
    # ──────────────────────────────────────────────
    def _process_mermaid(self, md_text: str) -> str:
        pattern = re.compile(r'```mermaid\s*\n(.*?)```', re.DOTALL)
        diagrams: list = []

        def replacer(m):
            diagrams.append(m.group(1).strip())
            return f'<!-- MERMAID_{len(diagrams) - 1} -->'

        md_text = pattern.sub(replacer, md_text)

        for idx, code in enumerate(diagrams):
            print(f'  Рендеринг диаграммы #{idx + 1}/{len(diagrams)}...')
            data_uri = self._render_mermaid(code, idx)

            if data_uri:
                tag = (
                    f'<div class="mermaid-wrap">'
                    f'<img src="{data_uri}" alt="Diagram {idx}" class="mermaid-img">'
                    f'</div>'
                )
            else:
                tag = f'<div class="mermaid-error">⚠ Не удалось отрендерить диаграмму #{idx + 1}</div>'

            md_text = md_text.replace(f'<!-- MERMAID_{idx} -->', tag)

        return md_text

    # ──────────────────────────────────────────────
    # ZIP-архив с PNG-диаграммами
    # ──────────────────────────────────────────────
    def _create_diagrams_zip(self, output_pdf: str) -> Optional[str]:
        """Упаковывает все PNG диаграмм в ZIP рядом с PDF. Возвращает путь к ZIP."""
        if not self.diagram_pngs:
            return None

        pdf_path = Path(output_pdf)
        zip_path = str(pdf_path.parent / (pdf_path.stem + '_diagrams.zip'))

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, png in enumerate(self.diagram_pngs, 1):
                if os.path.exists(png):
                    zf.write(png, f'diagram_{i:03d}.png')

        return zip_path

    # ──────────────────────────────────────────────
    # HTML-шаблон
    # ──────────────────────────────────────────────
    def _build_html(self, body_html: str, title: str) -> str:
        w_px, h_px = self._page_sizes.get(self.page_format, (794, 1123))
        content_w  = w_px - 112
        max_diag_h = int((h_px - 112) * 0.92)

        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px; line-height: 1.65; color: #222;
    background: #fff; padding: 56px; max-width: {w_px}px;
  }}
  h1 {{ font-size: 1.9em; border-bottom: 2px solid #2980b9; padding-bottom:.3em; margin: 1.4em 0 .5em; color:#1a252f; }}
  h2 {{ font-size: 1.5em; border-bottom: 1px solid #bdc3c7; padding-bottom:.2em; margin: 1.3em 0 .4em; color:#2c3e50; }}
  h3 {{ font-size: 1.2em; margin: 1.2em 0 .4em; color:#2c3e50; }}
  h4,h5,h6 {{ margin: 1em 0 .3em; color:#34495e; }}
  h1,h2,h3,h4,h5,h6 {{ page-break-after: avoid; }}
  p  {{ margin: .7em 0; text-align: justify; }}
  a  {{ color: #2980b9; }}
  ul,ol {{ margin: .7em 0 .7em 1.8em; }}
  li    {{ margin: .3em 0; }}
  blockquote {{
    border-left: 4px solid #3498db; padding: .5em 1em;
    margin: 1em 0; background: #f4f9ff; page-break-inside: avoid;
  }}
  table {{
    border-collapse: collapse; width: 100%;
    margin: 1em 0; page-break-inside: avoid; font-size: .92em;
  }}
  th,td {{ border: 1px solid #ddd; padding: 7px 10px; text-align: left; }}
  th    {{ background: #2980b9; color: #fff; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
  code {{
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: .88em; background: #f4f4f4;
    padding: 1px 5px; border-radius: 3px;
  }}
  pre {{
    background: #f8f8f8; border: 1px solid #ddd; border-radius: 4px;
    padding: .9em 1em; overflow-x: auto;
    page-break-inside: avoid; font-size: .88em; line-height: 1.5;
  }}
  pre code {{ background: none; padding: 0; }}
  hr {{ border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }}
  img {{ max-width: 100%; height: auto; }}
  .mermaid-wrap {{
    display: block; width: 100%; max-width: {content_w}px;
    margin: 1.2em auto; page-break-inside: avoid; text-align: center;
  }}
  .mermaid-img {{
    display: block; max-width: 100%; max-height: {max_diag_h}px;
    width: auto; height: auto; object-fit: contain; margin: 0 auto;
  }}
  .mermaid-error {{
    padding: 1em; border: 2px dashed #e74c3c; color: #c0392b;
    border-radius: 4px; margin: 1em 0; page-break-inside: avoid;
  }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

    # ──────────────────────────────────────────────
    # Главный метод convert()
    # ──────────────────────────────────────────────
    def convert(self, input_md: str, output_pdf: str,
                title: Optional[str] = None) -> bool:
        self.temp_dir = tempfile.mkdtemp()
        self.diagram_pngs = []

        try:
            ok, msg = self._check_dependencies()
            if not ok:
                print(f'❌ Отсутствует зависимость: {msg}', file=sys.stderr)
                return False

            src = Path(input_md)
            if not src.exists():
                print(f'❌ Файл не найден: {input_md}', file=sys.stderr)
                return False

            md_text   = src.read_text(encoding='utf-8')
            doc_title = title or src.stem
            print(f'📄 Конвертация: {src.name}')

            print('🔷 Обработка диаграмм Mermaid...')
            md_text = self._process_mermaid(md_text)

            print('📝 Конвертация Markdown в HTML...')
            import markdown
            md_proc = markdown.Markdown(extensions=[
                'tables', 'fenced_code', 'codehilite', 'nl2br', 'sane_lists',
            ])
            body_html = md_proc.convert(md_text)
            full_html = self._build_html(body_html, doc_title)

            html_path = os.path.join(self.temp_dir, 'doc.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(full_html)

            print('📑 Генерация PDF через Playwright...')
            self._html_to_pdf_playwright(html_path, output_pdf)

            size_kb = os.path.getsize(output_pdf) // 1024
            print(f'✅ PDF готов: {output_pdf}  ({size_kb} КБ)')

            # Создаём ZIP с PNG-диаграммами (если они есть)
            zip_path = self._create_diagrams_zip(output_pdf)
            if zip_path:
                zip_kb = os.path.getsize(zip_path) // 1024
                print(f'🖼️ Диаграммы: {len(self.diagram_pngs)} шт → {zip_path}  ({zip_kb} КБ)')
                print(f'DIAGRAMS_ZIP:{zip_path}')  # служебная строка для GUI

            return True

        except Exception as exc:
            print(f'❌ Ошибка: {exc}', file=sys.stderr)
            import traceback
            traceback.print_exc()
            return False
        finally:
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ──────────────────────────────────────────────
    # Playwright: HTML → PDF
    # ──────────────────────────────────────────────
    def _html_to_pdf_playwright(self, html_path: str, pdf_path: str) -> None:
        from playwright.sync_api import sync_playwright

        w_px, h_px = self._page_sizes.get(self.page_format, (794, 1123))
        margin = '1.5cm'

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page    = browser.new_page()
            file_url = Path(html_path).resolve().as_uri()
            page.goto(file_url, wait_until='networkidle')
            page.pdf(
                path=pdf_path,
                width=f'{w_px}px', height=f'{h_px}px',
                margin={'top': margin, 'bottom': margin,
                        'left': margin, 'right': margin},
                print_background=True,
            )
            browser.close()


# ──────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Конвертер Markdown → PDF с поддержкой диаграмм Mermaid'
    )
    parser.add_argument('input')
    parser.add_argument('output')
    parser.add_argument('--title',  '-t')
    parser.add_argument('--scale',  '-s', type=float, default=1.0)
    parser.add_argument('--format', '-f', default='A4')
    args = parser.parse_args()

    converter = MarkdownToPDFConverter(
        page_format=args.format,
        mermaid_scale=args.scale,
    )
    ok = converter.convert(args.input, args.output, args.title)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
