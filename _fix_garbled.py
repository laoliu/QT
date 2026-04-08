"""
Fix garbled Chinese in .ts and .py files.
The garbling was caused by: original UTF-8 Chinese → decoded as GBK → re-encoded as UTF-8.
Fix: encode garbled text as GB18030/GBK → decode as UTF-8.
Also handles the boundary ? corruption where a trailing UTF-8 byte was lost.
"""
import re, os, sys

os.chdir(r'D:\work\QT')

DRY_RUN = '--dry-run' in sys.argv
VERBOSE = '--verbose' in sys.argv or '-v' in sys.argv

def is_garbled_chinese(text):
    """Check if text looks like GBK-mojibake (wrong CJK chars that don't make sense)."""
    try:
        recovered = text.encode('gb18030').decode('utf-8')
        # If it decodes cleanly and produces real Chinese, it was garbled
        if re.search(r'[\u4e00-\u9fff]', recovered):
            return True
    except:
        pass
    return False

def encode_to_original_bytes(chunk):
    """Encode garbled Unicode characters back to the original bytes using GBK with € → 0x80 fix."""
    gb_bytes = bytearray()
    for ch in chunk:
        if ch == '\u20ac':  # €: Microsoft CP936 maps 0x80→€
            gb_bytes.append(0x80)
        else:
            try:
                gb_bytes.extend(ch.encode('gbk'))
            except:
                gb_bytes.extend(ch.encode('gb18030'))
    return bytes(gb_bytes)

def fix_garbled_chunk(chunk, trailing_q=False):
    """Try to fix a garbled Chinese chunk. If trailing_q, a '?' was after it (lost byte)."""
    try:
        gb_bytes = encode_to_original_bytes(chunk)
    except:
        return chunk, False
    
    if trailing_q:
        # Check if gb_bytes already decodes cleanly (? was a real question mark)
        try:
            clean = gb_bytes.decode('utf-8')
            return clean, True
        except UnicodeDecodeError:
            pass
        # The ? represents a lost byte. Try all possible continuation bytes.
        # Use frequency-based selection to pick the most likely Chinese character.
        best = None
        best_score = -1
        for b in range(0x80, 0xC0):
            try:
                candidate = (gb_bytes + bytes([b])).decode('utf-8')
                if candidate:
                    last_char = candidate[-1]
                    cp = ord(last_char)
                    score = 0
                    if 0x4e00 <= cp <= 0x9fff:
                        score = 100
                        # Boost very common Chinese chars (general + financial domain)
                        if last_char in '的一是不了人我在有他这为之大来以个中上' \
                                        '们到说时要就出会也你对成那和事只作当想' \
                                        '看文无开手动分次亿户型垒境持前后数据标' \
                                        '题价格点击量化交易市场策略选买卖单号信' \
                                        '率期权股票基金指表现亏损收益率止盈空头' \
                                        '仓位净流入监控报告模板日月年度周期触发' \
                                        '配置管理制通知务完态':
                            score = 200
                    elif 0x3000 <= cp <= 0x303f:  # CJK punct
                        score = 80
                    elif 0xff00 <= cp <= 0xffef:  # Fullwidth
                        score = 70
                    elif 0x2000 <= cp <= 0x206f:  # General punctuation (—, –, ', ", etc.)
                        score = 90
                    elif 0xff00 <= cp <= 0xffef:
                        score = 70
                    if score > best_score:
                        best = candidate
                        best_score = score
            except:
                continue
        if best and best_score > 0:
            return best, True
    
    # Normal decode — only accept CLEAN conversion (no lossy fallback)
    try:
        fixed = bytes(gb_bytes).decode('utf-8')
        return fixed, True
    except:
        pass
    
    return chunk, False

def fix_line(line):
    """Fix garbled Chinese in a single line."""
    result = []
    i = 0
    changed = False
    
    while i < len(line):
        if ord(line[i]) > 127:
            # Collect consecutive non-ASCII chars
            j = i
            while j < len(line) and ord(line[j]) > 127:
                j += 1
            chunk = line[i:j]
            
            # Check if followed by ? (which might be a lost byte)
            trailing_q = False
            if j < len(line) and line[j] == '?':
                try:
                    gb_bytes = encode_to_original_bytes(chunk)
                    gb_bytes.decode('utf-8')
                    # Clean decode - ? is a real question mark
                except UnicodeDecodeError:
                    # Incomplete UTF-8 - ? likely represents lost byte
                    # But skip if followed by another ? (nullish coalescing)
                    next_char = line[j+1] if j+1 < len(line) else ''
                    if next_char != '?':
                        trailing_q = True
                except:
                    pass
            
            fixed, was_fixed = fix_garbled_chunk(chunk, trailing_q)
            
            if was_fixed:
                result.append(fixed)
                changed = True
                if trailing_q:
                    i = j + 1  # skip the ?
                else:
                    i = j
            else:
                result.append(chunk)
                i = j
        else:
            result.append(line[i])
            i += 1
    
    return ''.join(result), changed

def fix_file(filepath):
    """Fix all garbled Chinese in a file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    new_lines = []
    total_changed = 0
    
    for i, line in enumerate(lines):
        fixed_line, changed = fix_line(line)
        new_lines.append(fixed_line)
        if changed:
            total_changed += 1
            if VERBOSE:
                print(f'  L{i+1}: {line.strip()[:80]}')
                print(f'     → {fixed_line.strip()[:80]}')
    
    if total_changed > 0:
        new_content = '\n'.join(new_lines)
        if not DRY_RUN:
            with open(filepath, 'w', encoding='utf-8', newline='') as f:
                f.write(new_content)
        return total_changed
    return 0

# Get files to fix: ALL .ts and .py files under quant-trading/
files = []
skip_prefixes = ('_fix_', '_scan_', '_test_', '_show_', '_spot_', '_check_', '_find_')
for dirpath, dirs, fnames in os.walk('quant-trading'):
    for fn in fnames:
        if fn.endswith('.ts') or fn.endswith('.py'):
            basename = os.path.basename(fn)
            if any(basename.startswith(p) for p in skip_prefixes):
                continue
            fpath = os.path.join(dirpath, fn)
            files.append(fpath)

# Filter: only fix files that actually have garbled content
# (skip files with only valid Chinese)
files_to_fix = []
for f in files:
    text = open(f, 'r', encoding='utf-8').read()
    # Quick check: does fix_line change anything?
    sample_lines = text.split('\n')[:20]
    has_garbled = False
    for sl in sample_lines:
        _, ch = fix_line(sl)
        if ch:
            has_garbled = True
            break
    if not has_garbled:
        # Check more lines (sample from middle/end)
        for sl in sample_lines[20:] + text.split('\n')[-20:]:
            _, ch = fix_line(sl)
            if ch:
                has_garbled = True
                break
    # Full check if not found yet
    if not has_garbled:
        for sl in text.split('\n'):
            _, ch = fix_line(sl)
            if ch:
                has_garbled = True
                break
    if has_garbled:
        files_to_fix.append(f)

print(f"{'[DRY RUN] ' if DRY_RUN else ''}Files to fix: {len(files_to_fix)}")
total_lines_fixed = 0
for f in files_to_fix:
    n = fix_file(f)
    if n > 0:
        print(f"  {'Would fix' if DRY_RUN else 'Fixed'} {f}: {n} lines")
        total_lines_fixed += n

print(f"\nTotal: {total_lines_fixed} lines {'would be' if DRY_RUN else ''} fixed in {len(files_to_fix)} files")
