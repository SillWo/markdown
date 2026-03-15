#!/usr/bin/env python3
"""
Markdown to PDF Converter with Mermaid Diagram Support
Использует Playwright (Chromium) для рендеринга Mermaid и генерации PDF.
Node.js и mmdc НЕ требуются.
"""

import re
import base64
import tempfile
import os
import sys
import json
import shutil
import argparse
import zipfile
import urllib.request
from pathlib import Path
from typing import Optional, Tuple, List

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# ──────────────────────────────────────────────────────────────
# Пути к ресурсам
# ──────────────────────────────────────────────────────────────
def _res_dir() -> Path:
    """Папка с ресурсами (mermaid.min.js). При запуске из .exe — _MEIPASS."""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _get_mermaid_js() -> str:
    """Возвращает содержимое mermaid.min.js, при необходимости скачивает."""
    path = _res_dir() / 'mermaid.min.js'
    if not path.exists():
        print('⬇ Скачиваю mermaid.min.js...', flush=True)
        url = 'https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js'
        urllib.request.urlretrieve(url, path)
        print(f'✓ mermaid.min.js скачан ({path.stat().st_size // 1024} КБ)', flush=True)
    return path.read_text(encoding='utf-8')


# ──────────────────────────────────────────────────────────────
# Конвертер
# ──────────────────────────────────────────────────────────────
class MarkdownToPDFConverter:

    def __init__(self, page_format: str = 'A4', mermaid_scale: float = 1.0):
        self.page_format   = page_format.upper()
        self.mermaid_scale = mermaid_scale
        self.temp_dir: Optional[str] = None
        self.diagram_pngs: List[str] = []

        self._page_sizes = {
            'A3':     (1122, 1587),
            'A4':     (794,  1123),
            'A5':     (559,  794),
            'LETTER': (816,  1056),
            'LEGAL':  (816,  1344),
        }

    # ── Проверка зависимостей ─────────────────────────────────
    def _check_dependencies(self) -> Tuple[bool, str]:
        try:
            import markdown       # noqa: F401
        except ImportError:
            return False, "pip install markdown"
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False, "pip install playwright && playwright install chromium"
        return True, "OK"

    # ── Рендеринг одной Mermaid-диаграммы ────────────────────
    def _render_mermaid(self, code: str, idx: int,
                        mermaid_js: str) -> Optional[str]:
        """
        1. Рендерит Mermaid → SVG через Playwright + mermaid.js
        2. На отдельной странице делает PNG-скриншот для ZIP
        3. Патчит SVG (убирает фиксированные размеры) для PDF
        Возвращает сырой SVG-текст или None при ошибке.
        """
        font_size = max(10, int(14 * self.mermaid_scale))

        # HTML для рендеринга Mermaid → SVG
        render_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body {{ margin: 0; padding: 10px; background: white; }}
  #container {{ display: inline-block; }}
</style></head>
<body>
<div id="container"></div>
<script>{mermaid_js}</script>
<script>
mermaid.initialize({{
  startOnLoad: false,
  theme: 'default',
  themeVariables: {{ fontSize: '{font_size}px' }},
  flowchart: {{ useMaxWidth: true, curve: 'basis' }},
  sequence:  {{ useMaxWidth: true }},
  gantt:     {{ useMaxWidth: true }}
}});
(async () => {{
  try {{
    const {{ svg }} = await mermaid.render('d{idx}', {json.dumps(code)});
    document.getElementById('container').innerHTML = svg;
    document.title = 'ok';
  }} catch(e) {{
    document.title = 'err:' + e.message;
  }}
}})();
</script>
</body></html>"""

        from playwright.sync_api import sync_playwright
        import re as _re

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()

                # ── Страница 1: рендерим Mermaid → получаем SVG ──
                page1 = browser.new_page(viewport={'width': 1600, 'height': 900})
                page1.set_content(render_html)
                page1.wait_for_function("document.title !== ''", timeout=30_000)

                title = page1.title()
                if title.startswith('err:'):
                    print(f'  ⚠ Mermaid #{idx+1}: {title[4:200]}', file=sys.stderr)
                    browser.close()
                    return None

                # Получаем SVG как есть (с оригинальными размерами)
                svg_original = page1.eval_on_selector(
                    '#container svg', 'el => el.outerHTML')

                # ── Страница 2: скриншот SVG в нужном размере ──
                # Вычисляем натуральные размеры SVG
                w = page1.eval_on_selector('#container svg',
                    'el => el.getBoundingClientRect().width')
                h = page1.eval_on_selector('#container svg',
                    'el => el.getBoundingClientRect().height')

                if w and h and w > 0 and h > 0:
                    vw = max(int(w) + 40, 400)
                    vh = max(int(h) + 40, 200)
                else:
                    vw, vh = 1200, 800

                screenshot_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: white; padding: 16px; display: inline-block; }}
  svg {{ display: block; }}
</style></head>
<body>{svg_original}</body></html>"""

                page2 = browser.new_page(viewport={'width': vw, 'height': vh})
                page2.set_content(screenshot_html)
                page2.wait_for_load_state('domcontentloaded')

                png_path = os.path.join(self.temp_dir, f'diagram_{idx:03d}.png')
                # Скриншотим только body (обрезаем по контенту)
                body_el = page2.query_selector('body')
                if body_el:
                    body_el.screenshot(path=png_path)
                else:
                    page2.screenshot(path=png_path, full_page=True)

                if os.path.exists(png_path) and os.path.getsize(png_path) > 0:
                    self.diagram_pngs.append(png_path)

                # ── Патчим SVG для PDF: убираем фиксированные размеры ──
                svg_for_pdf = page1.eval_on_selector(
                    '#container svg',
                    """el => {
                        el.removeAttribute('width');
                        el.removeAttribute('height');
                        el.style.maxWidth = '100%';
                        return el.outerHTML;
                    }"""
                )

                browser.close()
                return svg_for_pdf

        except Exception as e:
            print(f'  ⚠ Ошибка рендеринга #{idx+1}: {e}', file=sys.stderr)
            return None

    # ── Обработка всех блоков ```mermaid ─────────────────────
    def _process_mermaid(self, md_text: str, mermaid_js: str) -> str:
        pattern  = re.compile(r'```mermaid\s*\n(.*?)```', re.DOTALL)
        diagrams = []

        def replacer(m):
            diagrams.append(m.group(1).strip())
            return f'<!-- MERMAID_{len(diagrams)-1} -->'

        md_text = pattern.sub(replacer, md_text)

        for idx, code in enumerate(diagrams):
            print(f'  Рендеринг диаграммы #{idx+1}/{len(diagrams)}...',
                  flush=True)
            data_uri = self._render_mermaid(code, idx, mermaid_js)

            if data_uri:
                # Вставляем SVG инлайн — Playwright PDF гарантированно его рендерит
                tag = (
                    f'<div class="mermaid-wrap mermaid-inline">'
                    f'{data_uri}'
                    f'</div>'
                )
            else:
                tag = (
                    f'<div class="mermaid-error">'
                    f'⚠ Не удалось отрендерить диаграмму #{idx+1}</div>'
                )

            md_text = md_text.replace(f'<!-- MERMAID_{idx} -->', tag)

        return md_text

    # ── ZIP с PNG-диаграммами ─────────────────────────────────
    def _create_diagrams_zip(self, output_pdf: str) -> Optional[str]:
        if not self.diagram_pngs:
            return None
        pdf_path = Path(output_pdf)
        zip_path = str(pdf_path.parent / (pdf_path.stem + '_diagrams.zip'))
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, png in enumerate(self.diagram_pngs, 1):
                if os.path.exists(png):
                    zf.write(png, f'diagram_{i:03d}.png')
        return zip_path

    # ── HTML-шаблон ──────────────────────────────────────────
    def _build_html(self, body_html: str, title: str) -> str:
        w_px, h_px = self._page_sizes.get(self.page_format, (794, 1123))
        content_w  = w_px - 112
        max_diag_h = int((h_px - 112) * 0.92)

        return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="UTF-8"><title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px; line-height: 1.65; color: #222;
    background: #fff; padding: 56px; max-width: {w_px}px;
  }}
  h1 {{ font-size:1.9em; border-bottom:2px solid #2980b9; padding-bottom:.3em; margin:1.4em 0 .5em; color:#1a252f; }}
  h2 {{ font-size:1.5em; border-bottom:1px solid #bdc3c7; padding-bottom:.2em; margin:1.3em 0 .4em; color:#2c3e50; }}
  h3 {{ font-size:1.2em; margin:1.2em 0 .4em; color:#2c3e50; }}
  h4,h5,h6 {{ margin:1em 0 .3em; color:#34495e; }}
  h1,h2,h3,h4,h5,h6 {{ page-break-after:avoid; }}
  p  {{ margin:.7em 0; text-align:justify; }}
  a  {{ color:#2980b9; }}
  ul,ol {{ margin:.7em 0 .7em 1.8em; }}
  li    {{ margin:.3em 0; }}
  blockquote {{
    border-left:4px solid #3498db; padding:.5em 1em;
    margin:1em 0; background:#f4f9ff; page-break-inside:avoid;
  }}
  table {{
    border-collapse:collapse; width:100%;
    margin:1em 0; page-break-inside:avoid; font-size:.92em;
  }}
  th,td {{ border:1px solid #ddd; padding:7px 10px; text-align:left; }}
  th    {{ background:#2980b9; color:#fff; }}
  tr:nth-child(even) {{ background:#f5f5f5; }}
  code {{
    font-family:'Consolas','Courier New',monospace;
    font-size:.88em; background:#f4f4f4; padding:1px 5px; border-radius:3px;
  }}
  pre {{
    background:#f8f8f8; border:1px solid #ddd; border-radius:4px;
    padding:.9em 1em; overflow-x:auto;
    page-break-inside:avoid; font-size:.88em; line-height:1.5;
  }}
  pre code {{ background:none; padding:0; }}
  hr  {{ border:none; border-top:1px solid #ccc; margin:1.5em 0; }}
  img {{ max-width:100%; height:auto; }}
  .mermaid-wrap {{
    display:block; width:100%; max-width:{content_w}px;
    margin:1.2em auto; page-break-inside:avoid; text-align:center;
  }}
  .mermaid-img {{
    display:block; max-width:100%; max-height:{max_diag_h}px;
    width:auto; height:auto; object-fit:contain; margin:0 auto;
  }}
  .mermaid-inline svg {{
    max-width:100%;
    max-height:{max_diag_h}px;
    width:auto;
    height:auto;
    display:block;
    margin:0 auto;
  }}
  .mermaid-error {{
    padding:1em; border:2px dashed #e74c3c; color:#c0392b;
    border-radius:4px; margin:1em 0; page-break-inside:avoid;
  }}
</style>
</head><body>{body_html}</body></html>"""

    # ── HTML → PDF через Playwright ──────────────────────────
    def _html_to_pdf(self, html_path: str, pdf_path: str) -> None:
        from playwright.sync_api import sync_playwright

        w_px, h_px = self._page_sizes.get(self.page_format, (794, 1123))
        margin = '1.5cm'

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page    = browser.new_page()
            page.goto(Path(html_path).resolve().as_uri(),
                      wait_until='networkidle')
            page.pdf(
                path=pdf_path,
                width=f'{w_px}px', height=f'{h_px}px',
                margin=dict(top=margin, bottom=margin,
                            left=margin, right=margin),
                print_background=True,
            )
            browser.close()

    # ── Главный метод ────────────────────────────────────────
    def convert(self, input_md: str, output_pdf: str,
                title: Optional[str] = None) -> bool:

        self.temp_dir     = tempfile.mkdtemp()
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
            print(f'📄 Конвертация: {src.name}', flush=True)

            # Загружаем mermaid.js один раз на всю конвертацию
            print('🔷 Загрузка Mermaid.js...', flush=True)
            mermaid_js = _get_mermaid_js()

            print('🔷 Обработка диаграмм Mermaid...', flush=True)
            md_text = self._process_mermaid(md_text, mermaid_js)

            print('📝 Конвертация Markdown → HTML...', flush=True)
            import markdown
            md_proc   = markdown.Markdown(extensions=[
                'tables', 'fenced_code', 'codehilite', 'nl2br', 'sane_lists',
            ])
            body_html = md_proc.convert(md_text)
            full_html = self._build_html(body_html, doc_title)

            html_path = os.path.join(self.temp_dir, 'doc.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(full_html)

            print('📑 Генерация PDF через Playwright...', flush=True)
            self._html_to_pdf(html_path, output_pdf)

            size_kb = os.path.getsize(output_pdf) // 1024
            print(f'✅ PDF готов: {output_pdf}  ({size_kb} КБ)', flush=True)

            # ZIP с PNG-диаграммами
            zip_path = self._create_diagrams_zip(output_pdf)
            if zip_path:
                zip_kb = os.path.getsize(zip_path) // 1024
                print(
                    f'🖼️ Диаграммы: {len(self.diagram_pngs)} шт'
                    f' → {zip_path}  ({zip_kb} КБ)',
                    flush=True
                )
                print(f'DIAGRAMS_ZIP:{zip_path}', flush=True)

            return True

        except Exception as exc:
            print(f'❌ Ошибка: {exc}', file=sys.stderr)
            import traceback; traceback.print_exc()
            return False
        finally:
            shutil.rmtree(self.temp_dir, ignore_errors=True)


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description='Конвертер Markdown → PDF (без Node.js)')
    p.add_argument('input')
    p.add_argument('output')
    p.add_argument('--title',  '-t')
    p.add_argument('--scale',  '-s', type=float, default=1.0)
    p.add_argument('--format', '-f', default='A4')
    args = p.parse_args()

    ok = MarkdownToPDFConverter(
        page_format=args.format,
        mermaid_scale=args.scale,
    ).convert(args.input, args.output, args.title)
    sys.exit(0 if ok else 1)

#
if __name__ == '__main__':
    main()
