"""
Skill: generate-report
Read all analysis files and generate a 6-page PDF report summary
with specific sections for each analysis type.
"""
import os
import subprocess
from skills.base import BaseSkill
from utils import logger, save_markdown, load_markdown, ensure_company_dir
from config import COLORS


class GenerateReportSkill(BaseSkill):
    name = "generate-report"
    description = (
        "Generate a 6-page PDF report from all analysis files "
        "with specific sections for each type of analysis"
    )

    def _build_html_report(
        self, ticker: str, company_name: str, date_latest: str,
        date_prior: str, sections: dict
    ) -> str:
        """Build a styled HTML report from the analysis sections."""

        c = COLORS
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Earnings Report — {company_name} ({ticker})</title>
<style>
  @page {{
    size: A4;
    margin: 15mm 18mm 20mm 18mm;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    color: {c['text_primary']};
    background: {c['primary']};
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .page {{
    page-break-after: always;
    padding: 0;
    position: relative;
  }}
  .page:last-child {{ page-break-after: avoid; }}

  /* Cover page */
  .cover {{
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    text-align: center;
    background: linear-gradient(160deg, {c['primary']} 0%, {c['secondary']} 100%);
  }}
  .cover-logo {{
    width: 80px;
    height: 80px;
    border-radius: 16px;
    object-fit: contain;
    margin-bottom: 20px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    border: 2px solid rgba(255,255,255,0.15);
  }}
  .cover-badge {{
    display: inline-block;
    background: {c['accent_positive']};
    color: {c['primary']};
    padding: 6px 22px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 11pt;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-bottom: 28px;
  }}
  .cover h1 {{
    font-size: 32pt;
    font-weight: 800;
    margin-bottom: 10px;
    color: {c['text_primary']};
  }}
  .cover h2 {{
    font-size: 16pt;
    font-weight: 400;
    color: {c['text_secondary']};
    margin-bottom: 40px;
  }}
  .cover .meta {{
    font-size: 10pt;
    color: {c['text_secondary']};
    border-top: 1px solid {c['border']};
    padding-top: 20px;
    margin-top: 20px;
  }}

  /* Content pages */
  .content {{
    background: {c['primary']};
    padding: 12mm 0;
  }}
  .section-title {{
    font-size: 17pt;
    font-weight: 700;
    color: {c['accent_positive']};
    border-bottom: 2px solid {c['accent_positive']};
    padding-bottom: 6px;
    margin-bottom: 14px;
    margin-top: 6px;
  }}
  .subsection-title {{
    font-size: 12pt;
    font-weight: 700;
    color: {c['text_primary']};
    margin-top: 14px;
    margin-bottom: 6px;
  }}
  p {{
    margin-bottom: 8px;
    text-align: justify;
    color: {c['text_primary']};
  }}
  .card {{
    background: {c['secondary']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 14px;
  }}
  .card-accent {{
    border-left: 4px solid {c['accent_positive']};
  }}
  .card-warn {{
    border-left: 4px solid {c['accent_negative']};
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0 16px 0;
    font-size: 9.5pt;
  }}
  th {{
    background: {c['secondary']};
    color: {c['accent_positive']};
    text-align: left;
    padding: 7px 10px;
    border-bottom: 2px solid {c['accent_positive']};
    font-weight: 700;
  }}
  td {{
    padding: 6px 10px;
    border-bottom: 1px solid {c['border']};
    color: {c['text_primary']};
  }}
  tr:nth-child(even) td {{ background: rgba(30,41,59,0.4); }}
  .positive {{ color: {c['accent_positive']}; font-weight: 600; }}
  .negative {{ color: {c['accent_negative']}; font-weight: 600; }}
  ul, ol {{
    margin: 6px 0 10px 22px;
    color: {c['text_primary']};
  }}
  li {{ margin-bottom: 4px; }}
  .footer {{
    position: fixed;
    bottom: 8mm;
    left: 18mm;
    right: 18mm;
    text-align: center;
    font-size: 8pt;
    color: {c['text_secondary']};
    border-top: 1px solid {c['border']};
    padding-top: 4px;
  }}
  blockquote {{
    border-left: 3px solid {c['accent_positive']};
    padding: 6px 14px;
    margin: 8px 0;
    color: {c['text_secondary']};
    font-style: italic;
    background: rgba(30,41,59,0.3);
    border-radius: 0 6px 6px 0;
  }}
  .score-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin: 10px 0;
  }}
  .score-item {{
    background: {c['secondary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 10px 14px;
    text-align: center;
  }}
  .score-item .label {{
    font-size: 8.5pt;
    color: {c['text_secondary']};
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .score-item .value {{
    font-size: 18pt;
    font-weight: 800;
    margin-top: 2px;
  }}
</style>
</head>
<body>

<!-- PAGE 1: Cover -->
<div class="page cover">
  {{logo_html}}
  <div class="cover-badge">Quarterly Earnings Analysis</div>
  <h1>{company_name}</h1>
  <h2>{ticker} &mdash; Financial Report Comparison</h2>
  <div class="meta">
    Comparing periods: {date_prior} &amp; {date_latest}<br>
    Generated by Earnings Research AI Pipeline
  </div>
</div>

<!-- PAGE 2: Financial Numbers -->
<div class="page content">
  <div class="section-title">Financial Performance</div>
  <div class="card card-accent">
    {self._md_to_html(sections.get('numbers_latest', 'No data available.'))}
  </div>
</div>

<!-- PAGE 3: Strategic Goals -->
<div class="page content">
  <div class="section-title">Strategic Goals &amp; Direction</div>
  <div class="card card-accent">
    {self._md_to_html(sections.get('goals_latest', 'No data available.'))}
  </div>
</div>

<!-- PAGE 4: Tonal Analysis -->
<div class="page content">
  <div class="section-title">Communication &amp; Tonal Analysis</div>
  <div class="card card-accent">
    {self._md_to_html(sections.get('tone', 'No data available.'))}
  </div>
</div>

<!-- PAGE 5: Stock Price Analysis -->
<div class="page content">
  <div class="section-title">Stock Price Analysis &amp; Market Reaction</div>
  <div class="card card-accent">
    {self._md_to_html(sections.get('price', 'No data available.'))}
  </div>
</div>

<!-- PAGE 6: Comparative Summary -->
<div class="page content">
  <div class="section-title">Comparative Summary &amp; Outlook</div>
  <div class="card card-accent">
    {self._md_to_html(sections.get('compare', 'No data available.'))}
  </div>
</div>

<div class="footer">
  {company_name} ({ticker}) &mdash; Earnings Analysis Report &mdash;
  Periods: {date_prior} &amp; {date_latest}
</div>

</body>
</html>"""
        return html

    def _md_to_html(self, md_text: str) -> str:
        """Ask Claude to convert markdown to styled HTML fragments."""
        # Quick local conversion for simple markdown patterns
        import re
        html = md_text

        # Remove top-level h1 (we have section titles already)
        html = re.sub(r'^# .+$', '', html, flags=re.MULTILINE)

        # Headers
        html = re.sub(r'^### (.+)$', r'<div class="subsection-title">\1</div>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.+)$', r'<div class="subsection-title" style="font-size:13pt">\1</div>', html, flags=re.MULTILINE)

        # Bold
        html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
        # Italic
        html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)

        # Tables - convert markdown tables to HTML tables
        lines = html.split('\n')
        in_table = False
        table_lines = []
        result_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('|') and stripped.endswith('|'):
                if not in_table:
                    in_table = True
                    table_lines = []
                table_lines.append(stripped)
            else:
                if in_table:
                    result_lines.append(self._convert_md_table(table_lines))
                    in_table = False
                    table_lines = []
                result_lines.append(line)

        if in_table:
            result_lines.append(self._convert_md_table(table_lines))

        html = '\n'.join(result_lines)

        # Bullet lists
        html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'(<li>.*</li>\n?)+', lambda m: f'<ul>{m.group(0)}</ul>', html)

        # Numbered lists
        html = re.sub(r'^\d+\. (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)

        # Blockquotes
        html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)

        # Paragraphs for remaining plain text blocks
        paragraphs = html.split('\n\n')
        processed = []
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            if p.startswith('<'):
                processed.append(p)
            elif not any(p.startswith(tag) for tag in ['<div', '<table', '<ul', '<ol', '<li', '<blockquote']):
                processed.append(f'<p>{p}</p>')
            else:
                processed.append(p)

        return '\n'.join(processed)

    def _convert_md_table(self, lines: list) -> str:
        """Convert markdown table lines to an HTML table."""
        if len(lines) < 2:
            return '\n'.join(lines)

        rows = []
        for line in lines:
            cells = [c.strip() for c in line.strip('|').split('|')]
            rows.append(cells)

        # First row is header, second is separator (skip it)
        header = rows[0]
        data_rows = [r for i, r in enumerate(rows) if i > 1 or (i == 1 and not all(
            set(c.strip()) <= {'-', ':', ' '} for c in r
        ))]

        # Filter out separator rows
        data_rows = [r for r in data_rows if not all(
            set(c.strip()) <= {'-', ':', ' '} for c in r
        )]

        html = '<table>\n<thead><tr>'
        for h in header:
            html += f'<th>{h}</th>'
        html += '</tr></thead>\n<tbody>'

        for row in data_rows:
            html += '<tr>'
            for cell in row:
                # Color positive/negative values
                css_class = ''
                if any(ind in cell.lower() for ind in ['↑', 'increase', 'improved', 'positive']):
                    css_class = ' class="positive"'
                elif any(ind in cell.lower() for ind in ['↓', 'decrease', 'declined', 'negative']):
                    css_class = ' class="negative"'
                html += f'<td{css_class}>{cell}</td>'
            html += '</tr>\n'

        html += '</tbody></table>'
        return html

    def execute(self, ticker: str, company_name: str, report_dates: list, **kwargs) -> dict:
        if len(report_dates) < 2:
            return {
                "success": False,
                "error": "Need at least 2 report dates",
                "ticker": ticker,
            }

        date_latest = report_dates[0]
        date_prior = report_dates[1]
        logger.info(f"Generating PDF report for {ticker}")

        company_dir = ensure_company_dir(ticker)

        # Load all analysis sections
        sections = {}
        file_map = {
            "numbers_latest": f"{date_latest}_numbers.md",
            "numbers_prior": f"{date_prior}_numbers.md",
            "goals_latest": f"{date_latest}_goals.md",
            "goals_prior": f"{date_prior}_goals.md",
            "tone": f"{date_latest}_tone.md",
            "price": f"{date_prior}_{date_latest}_price.md",
            "compare": f"{date_prior}_{date_latest}_compare.md",
        }

        for key, filename in file_map.items():
            try:
                sections[key] = load_markdown(ticker, filename)
                logger.info(f"Loaded {filename}")
            except FileNotFoundError:
                sections[key] = f"*Analysis not available ({filename})*"
                logger.warning(f"Missing: {filename}")

        # Also try to load news
        try:
            sections["news"] = load_markdown(ticker, "news.md")
        except FileNotFoundError:
            sections["news"] = "*News summary not available*"

        # Ask Claude to create a condensed executive summary for the report
        summary_prompt_system = """You are a financial report writer. Create a concise 
executive summary (max 300 words) suitable for the cover area of a PDF report. 
Synthesize the key findings including financial performance, strategic direction,
communication tone, and stock price movements. Output ONLY the summary text, 
no headers or markdown."""

        summary_prompt_user = f"""Company: {company_name} ({ticker})
Periods: {date_prior} and {date_latest}

Comparison highlights:
{sections.get('compare', 'Not available')[:3000]}

Stock price analysis:
{sections.get('price', 'Not available')[:2000]}

News context:
{sections.get('news', 'Not available')[:1500]}

Write a punchy executive summary that covers financial results, strategic shifts,
management tone, and how the stock price reacted."""

        try:
            exec_summary = self.claude.call(
                system_prompt=summary_prompt_system,
                user_prompt=summary_prompt_user,
                max_tokens=500,
                temperature=0.3,
            )
            sections["exec_summary"] = exec_summary
        except Exception as e:
            logger.warning(f"Executive summary generation failed: {e}")
            sections["exec_summary"] = ""

        # Build HTML
        html_content = self._build_html_report(
            ticker, company_name, date_latest, date_prior, sections
        )

        # Insert logo if available
        logo_path = os.path.join(company_dir, "logo.jpeg")
        if os.path.exists(logo_path) and os.path.getsize(logo_path) > 500:
            import base64
            with open(logo_path, "rb") as lf:
                logo_b64 = base64.b64encode(lf.read()).decode("utf-8")
            logo_html = f'<img class="cover-logo" src="data:image/jpeg;base64,{logo_b64}" alt="{company_name} Logo">'
        else:
            logo_html = ""
        html_content = html_content.replace("{logo_html}", logo_html)

        # Save HTML
        html_path = os.path.join(company_dir, "report.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"HTML report saved: {html_path}")

        # Convert to PDF
        pdf_path = os.path.join(company_dir, "report.pdf")
        try:
            result = subprocess.run(
                [
                    "wkhtmltopdf",
                    "--quiet",
                    "--page-size", "A4",
                    "--margin-top", "0mm",
                    "--margin-bottom", "0mm",
                    "--margin-left", "0mm",
                    "--margin-right", "0mm",
                    "--enable-local-file-access",
                    "--print-media-type",
                    html_path, pdf_path,
                ],
                capture_output=True, text=True, timeout=120,
            )
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
                logger.info(f"PDF report generated: {pdf_path} ({os.path.getsize(pdf_path)} bytes)")
            else:
                logger.warning("PDF generation produced small/missing file")
        except Exception as e:
            logger.error(f"PDF generation failed: {e}")

        return {
            "success": os.path.exists(pdf_path),
            "result": "Report generated successfully",
            "html_path": html_path,
            "pdf_path": pdf_path if os.path.exists(pdf_path) else None,
            "ticker": ticker,
            "report_dates": report_dates,
        }