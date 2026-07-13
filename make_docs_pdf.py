import os
import sys
import re
import datetime
from fpdf import FPDF

# ============================================================
# 絵文字の置換テーブル
# ============================================================
EMOJI_MAP = {
    "🎙️": "",
    "📋": "",
    "⚠️": "【重要】",
    "📁": "",
    "👥": "",
    "🎉": "",
    "🛠️": "",
    "❌": "・",
    "👉": "→",
    "📄": "",
    "🗂️": "",
    "📞": "",
    "🔴": "●",
    "💡": "【ヒント】",
    "🔍": "【確認】",
    "🌟": "",
    "✏️": "",
}

# ============================================================
# フォント検出
# ============================================================
def find_japanese_fonts():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    font_dir = os.path.join(windir, "Fonts")
    
    # 候補リスト (Regular, Bold)
    candidates = [
        # 1. 游ゴシック
        {"r": os.path.join(font_dir, "YuGothR.ttc"), "b": os.path.join(font_dir, "YuGothB.ttc")},
        # 2. メイリオ
        {"r": os.path.join(font_dir, "meiryo.ttc"), "b": os.path.join(font_dir, "meiryob.ttc")},
        # 3. MSゴシック
        {"r": os.path.join(font_dir, "msgothic.ttc"), "b": os.path.join(font_dir, "msgothic.ttc")},
    ]
    
    for c in candidates:
        if os.path.exists(c["r"]) and os.path.exists(c["b"]):
            return c["r"], c["b"]
            
    for c in candidates:
        if os.path.exists(c["r"]):
            return c["r"], c["r"]
            
    return None, None

# ============================================================
# Markdown パース処理
# ============================================================
def parse_markdown(md_text):
    lines = md_text.split('\n')
    blocks = []
    
    in_code = False
    code_lang = ""
    code_lines = []
    
    in_table = False
    table_lines = []
    
    in_quote = False
    quote_lines = []
    quote_type = "NOTE"
    
    for line in lines:
        stripped = line.strip()
        
        # 1. コードブロック
        if stripped.startswith('```'):
            if in_code:
                blocks.append({
                    'type': 'code',
                    'lang': code_lang,
                    'text': '\n'.join(code_lines)
                })
                in_code = False
                code_lines = []
            else:
                in_code = True
                code_lang = stripped[3:].strip()
            continue
            
        if in_code:
            code_lines.append(line)
            continue
            
        # 2. テーブル
        if stripped.startswith('|'):
            if not in_table:
                in_table = True
            table_lines.append(line)
            continue
        elif in_table:
            blocks.append({
                'type': 'table',
                'lines': table_lines
            })
            in_table = False
            table_lines = []
            
        # 3. 引用 / アラート
        if line.startswith('>'):
            content = line[1:].strip()
            if not in_quote:
                in_quote = True
                alert_match = re.search(r'\[!(IMPORTANT|WARNING|TIP|NOTE|CAUTION)\]', content)
                if alert_match:
                    quote_type = alert_match.group(1)
                    content = content[alert_match.end():].strip()
                else:
                    quote_type = "NOTE"
                quote_lines = [content] if content else []
            else:
                quote_lines.append(content)
            continue
        elif in_quote:
            blocks.append({
                'type': 'quote',
                'alert_type': quote_type,
                'text': '\n'.join(quote_lines)
            })
            in_quote = False
            quote_lines = []
            
        # 4. 水平線
        if stripped == '---':
            blocks.append({'type': 'hr'})
            continue
            
        # 5. 空行
        if stripped == '':
            blocks.append({'type': 'empty'})
            continue
            
        # 6. 見出し
        heading_match = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            blocks.append({
                'type': 'heading',
                'level': level,
                'text': text
            })
            continue
            
        # 7. リスト
        list_match = re.match(r'^([\s]*)([-\*\+])\s+(.*)$', line)
        if list_match:
            indent = len(list_match.group(1))
            text = list_match.group(3)
            blocks.append({
                'type': 'list',
                'indent': indent,
                'ordered': False,
                'text': text
            })
            continue
            
        # 8. 番号付きリスト
        num_list_match = re.match(r'^([\s]*)(\d+)\.\s+(.*)$', line)
        if num_list_match:
            indent = len(num_list_match.group(1))
            num = num_list_match.group(2)
            text = num_list_match.group(3)
            blocks.append({
                'type': 'list',
                'indent': indent,
                'ordered': True,
                'num': num,
                'text': text
            })
            continue
            
        # 9. 通常段落
        blocks.append({
            'type': 'paragraph',
            'text': stripped
        })
        
    if in_table:
        blocks.append({'type': 'table', 'lines': table_lines})
    if in_quote:
        blocks.append({'type': 'quote', 'alert_type': quote_type, 'text': '\n'.join(quote_lines)})
        
    return blocks

# ============================================================
# インライン要素のマークダウン記号除去
# ============================================================
def strip_markdown(text):
    for emoji, replacement in EMOJI_MAP.items():
        text = text.replace(emoji, replacement)
        
    # 太字の除去
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    # インラインコードの除去
    text = re.sub(r'`(.*?)`', r'\1', text)
    # リンクの除去
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1', text)
    
    return text

# ============================================================
# PDF クラス定義
# ============================================================
class MarkdownPDF(FPDF):
    def __init__(self, font_path, font_path_b):
        super().__init__()
        self.font_path = font_path
        self.font_path_b = font_path_b
        self.set_auto_page_break(auto=True, margin=20)
        self.add_font("Japanese", "", font_path)
        self.add_font("Japanese", "B", font_path_b)
        self.set_font("Japanese", "", 10)
        self.set_text_color(55, 65, 81)
        
    def footer(self):
        self.set_y(-15)
        self.set_font("Japanese", "", 8)
        self.set_text_color(156, 163, 175)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def render_blocks(self, blocks):
        self.add_page()
        
        i = 0
        while i < len(blocks):
            block = blocks[i]
            
            if block['type'] == 'empty':
                self.ln(3)
                i += 1
                continue
                
            elif block['type'] == 'hr':
                self.ln(5)
                self.set_draw_color(229, 231, 235)
                self.line(20, self.get_y(), 190, self.get_y())
                self.ln(5)
                i += 1
                continue
                
            elif block['type'] == 'heading':
                level = block['level']
                text = block['text']
                for em, rep in EMOJI_MAP.items():
                    text = text.replace(em, rep)
                text = text.strip()
                
                if level == 1:
                    self.ln(8)
                    self.set_font("Japanese", "B", 18)
                    self.set_text_color(30, 58, 138)
                    self.cell(0, 12, text, new_x="LMARGIN", new_y="NEXT", align="L")
                    self.set_draw_color(30, 58, 138)
                    self.set_line_width(0.8)
                    self.line(20, self.get_y() - 1, 190, self.get_y() - 1)
                    self.set_line_width(0.2)
                    self.ln(6)
                elif level == 2:
                    self.ln(6)
                    current_y = self.get_y()
                    if current_y > 250:
                        self.add_page()
                        current_y = self.get_y()
                    
                    self.set_fill_color(37, 99, 235)
                    self.rect(20, current_y + 1, 3, 7, style="F")
                    
                    self.set_font("Japanese", "B", 13)
                    self.set_text_color(37, 99, 235)
                    self.set_x(25)
                    self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT", align="L")
                    self.ln(3)
                else:
                    self.ln(4)
                    self.set_font("Japanese", "B", 11)
                    self.set_text_color(31, 41, 55)
                    self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT", align="L")
                    self.ln(2)
                i += 1
                
            elif block['type'] == 'paragraph':
                self.set_font("Japanese", "", 10)
                self.set_text_color(55, 65, 81)
                clean_text = strip_markdown(block['text'])
                self.multi_cell(0, 6, clean_text, border=0, align="L", new_x="LMARGIN", new_y="NEXT")
                self.ln(2)
                i += 1
                
            elif block['type'] == 'list':
                self.set_font("Japanese", "", 10)
                self.set_text_color(55, 65, 81)
                
                indent = 6 + (block['indent'] * 2)
                original_l_margin = self.l_margin
                self.set_left_margin(original_l_margin + indent)
                
                prefix = f"{block['num']}. " if block['ordered'] else "・ "
                clean_text = strip_markdown(block['text'])
                
                self.multi_cell(0, 6, prefix + clean_text, border=0, align="L", new_x="LMARGIN", new_y="NEXT")
                
                self.set_left_margin(original_l_margin)
                self.ln(2)
                i += 1
                
            elif block['type'] == 'code':
                self.ln(2)
                text = block['text']
                for em, rep in EMOJI_MAP.items():
                    text = text.replace(em, rep)
                
                lines = text.split('\n')
                line_h = 5
                block_h = len(lines) * line_h + 6
                
                if self.get_y() + block_h > 270:
                    self.add_page()
                
                current_y = self.get_y()
                self.set_fill_color(243, 244, 246)
                self.rect(20, current_y, 170, block_h, style="F")
                self.set_draw_color(229, 231, 235)
                self.rect(20, current_y, 170, block_h, style="D")
                
                self.set_y(current_y + 3)
                self.set_font("Japanese", "", 8.5)
                self.set_text_color(31, 41, 55)
                
                for line in lines:
                    self.set_x(24)
                    self.multi_cell(162, line_h, line, border=0, new_x="LMARGIN", new_y="NEXT")
                
                self.set_y(current_y + block_h)
                self.ln(3)
                i += 1
                
            elif block['type'] == 'quote':
                self.ln(2)
                alert_type = block['alert_type']
                text = block['text']
                clean_text = strip_markdown(text)
                
                if alert_type in ['IMPORTANT', 'WARNING', 'CAUTION']:
                    fill_r, fill_g, fill_b = 254, 242, 242
                    border_r, border_g, border_b = 239, 68, 68
                    label = "【重要】"
                elif alert_type == 'TIP':
                    fill_r, fill_g, fill_b = 240, 253, 244
                    border_r, border_g, border_b = 34, 197, 94
                    label = "【ヒント】"
                else:
                    fill_r, fill_g, fill_b = 239, 246, 255
                    border_r, border_g, border_b = 59, 130, 246
                    label = "【注記】"
                
                self.set_font("Japanese", "", 9.5)
                try:
                    text_h = self.multi_cell(155, 5.5, clean_text, border=0, dry_run=True)
                    block_h = text_h + 8
                except Exception:
                    approx_lines = max(1, len(clean_text) * 2 // 80)
                    block_h = approx_lines * 5.5 + 8
                
                if self.get_y() + block_h > 270:
                    self.add_page()
                
                current_y = self.get_y()
                self.set_fill_color(fill_r, fill_g, fill_b)
                self.rect(20, current_y, 170, block_h, style="F")
                self.set_fill_color(border_r, border_g, border_b)
                self.rect(20, current_y, 2, block_h, style="F")
                
                self.set_y(current_y + 4)
                self.set_x(25)
                self.set_font("Japanese", "B", 9.5)
                self.set_text_color(border_r, border_g, border_b)
                self.write(5.5, label)
                
                self.set_font("Japanese", "", 9.5)
                self.set_text_color(55, 65, 81)
                
                original_l_margin = self.l_margin
                self.set_left_margin(25)
                self.multi_cell(160, 5.5, clean_text, border=0, align="L", new_x="LMARGIN", new_y="NEXT")
                self.set_left_margin(original_l_margin)
                
                self.set_y(current_y + block_h)
                self.ln(3)
                i += 1
                
            elif block['type'] == 'table':
                self.ln(2)
                lines = block['lines']
                
                header_line = lines[0]
                data_lines = lines[2:] if len(lines) > 2 else []
                
                def parse_row(row_str):
                    parts = row_str.strip().split('|')
                    if parts and parts[0] == '':
                        parts.pop(0)
                    if parts and parts[-1] == '':
                        parts.pop()
                    return [p.strip() for p in parts]
                
                headers = parse_row(header_line)
                rows = [parse_row(r) for r in data_lines]
                
                col_count = len(headers)
                if col_count == 0:
                    i += 1
                    continue
                
                col_widths = []
                total_w = 170
                if col_count == 2:
                    col_widths = [int(total_w * 0.35), int(total_w * 0.65)]
                else:
                    col_widths = [int(total_w / col_count)] * col_count
                
                row_h = 7
                table_h = (len(rows) + 1) * row_h + 4
                if self.get_y() + table_h > 270:
                    self.add_page()
                
                self.set_font("Japanese", "B", 9.5)
                self.set_text_color(31, 41, 55)
                self.set_fill_color(229, 236, 246)
                
                start_x = 20
                for idx, h_text in enumerate(headers):
                    w = col_widths[idx]
                    for em, rep in EMOJI_MAP.items():
                        h_text = h_text.replace(em, rep)
                    self.multi_cell(w, row_h, h_text, border=1, align="C", fill=True,
                                    new_x="RIGHT" if idx < col_count - 1 else "LMARGIN",
                                    new_y="LAST" if idx < col_count - 1 else "NEXT")
                
                self.set_font("Japanese", "", 9)
                self.set_text_color(55, 65, 81)
                
                for row_idx, row in enumerate(rows):
                    if row_idx % 2 == 0:
                        self.set_fill_color(255, 255, 255)
                    else:
                        self.set_fill_color(248, 250, 252)
                    
                    for idx, cell_text in enumerate(row):
                        w = col_widths[idx]
                        for em, rep in EMOJI_MAP.items():
                            cell_text = cell_text.replace(em, rep)
                        cell_text = strip_markdown(cell_text)
                        self.multi_cell(w, row_h, cell_text, border=1, align="L", fill=True,
                                        new_x="RIGHT" if idx < col_count - 1 else "LMARGIN",
                                        new_y="LAST" if idx < col_count - 1 else "NEXT")
                self.ln(3)
                i += 1

# ============================================================
# メイン処理
# ============================================================
def convert_md_to_pdf(md_path, pdf_path):
    print(f"変換中: {os.path.basename(md_path)} -> {os.path.basename(pdf_path)}")
    
    if not os.path.exists(md_path):
        print(f"エラー: 元ファイルが見つかりません {md_path}")
        return False
        
    with open(md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()
        
    blocks = parse_markdown(md_text)
    
    font_r, font_b = find_japanese_fonts()
    if not font_r:
        print("エラー: 日本語フォントが見つかりません。")
        return False
        
    pdf = MarkdownPDF(font_r, font_b)
    pdf.render_blocks(blocks)
    pdf.output(pdf_path)
    print(f"成功: {pdf_path}")
    return True
if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. manual.md
    md2 = os.path.join(base_dir, "manual.md")
    pdf2 = os.path.join(base_dir, "manual.pdf")
    convert_md_to_pdf(md2, pdf2)
    
    # 2. manual_EN.md
    md_en = os.path.join(base_dir, "manual_EN.md")
    pdf_en = os.path.join(base_dir, "manual_EN.pdf")
    convert_md_to_pdf(md_en, pdf_en)
