"""
HD Library — индексирует файлы библиотеки Дизайна Человека
и выдаёт релевантный контекст по карте конкретного человека.
"""

import re
import os
from pathlib import Path
from functools import lru_cache

LIB_DIR = Path(__file__).parent

# ─── ЗАГРУЗКА ФАЙЛОВ ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load(filename: str) -> str:
    path = LIB_DIR / filename
    if path.exists():
        return path.read_text(encoding='utf-8')
    return ''

# ─── ИНДЕКС ВОРОТ (из Line Companion) ────────────────────────────────────────

@lru_cache(maxsize=1)
def _build_gates_index() -> dict:
    """Строит индекс {gate_num: {line_num: text}} из hd_lines_all_gates.txt"""
    text = _load('hd_lines_all_gates.txt')
    index = {}

    # Находим каждую гексаграмму
    gate_pattern = re.compile(r'Гексаграмма\s+(\d+)[^\n]*\n', re.IGNORECASE)
    gate_matches = list(gate_pattern.finditer(text))

    for i, match in enumerate(gate_matches):
        gate_num = int(match.group(1))
        start = match.start()
        end = gate_matches[i+1].start() if i+1 < len(gate_matches) else len(text)
        gate_text = text[start:end]

        # Разбиваем по линиям (Линия 1, Линия 2, ...)
        line_pattern = re.compile(r'Линия\s+(\d)', re.IGNORECASE)
        line_matches = list(line_pattern.finditer(gate_text))

        lines = {}
        for j, lm in enumerate(line_matches):
            line_num = int(lm.group(1))
            ls = lm.start()
            le = line_matches[j+1].start() if j+1 < len(line_matches) else len(gate_text)
            lines[line_num] = gate_text[ls:le].strip()[:600]

        index[gate_num] = {
            'full': gate_text[:300].strip(),
            'lines': lines
        }

    return index


@lru_cache(maxsize=1)
def _build_channels_index() -> dict:
    """Строит индекс {(a,b): text} из hd_channels_gates.md"""
    text = _load('hd_channels_gates.md')
    index = {}

    pattern = re.compile(r'##\s+КАНАЛ\s+([\d\-]+):([^\n]*)\n(.*?)(?=##|$)', re.DOTALL)
    for m in pattern.finditer(text):
        nums = m.group(1).strip()
        name = m.group(2).strip()
        desc = m.group(3).strip()[:500]
        parts = nums.split('-')
        if len(parts) == 2:
            key = (int(parts[0]), int(parts[1]))
            index[key] = f"Канал {nums} — {name}: {desc}"
    return index


@lru_cache(maxsize=1)
def _build_centers_index() -> dict:
    """Строит индекс {center_name: text} из hd_centers.md"""
    text = _load('hd_centers.md')
    index = {}

    pattern = re.compile(r'##\s+([^\n]+)\n(.*?)(?=##|$)', re.DOTALL)
    for m in pattern.finditer(text):
        name = m.group(1).strip()
        desc = m.group(2).strip()[:800]
        index[name] = desc
        # Добавляем короткие алиасы
        for alias in [name.split('/')[0].strip(), name.split('(')[0].strip()]:
            index[alias] = desc
    return index


@lru_cache(maxsize=1)
def _build_types_index() -> dict:
    """Строит индекс типов и авторитетов из hd_types_authority.md"""
    text = _load('hd_types_authority.md')
    index = {}

    pattern = re.compile(r'##\s+(ТИП|АВТОРИТЕТ):\s*([^\n]+)\n(.*?)(?=##|$)', re.DOTALL)
    for m in pattern.finditer(text):
        kind = m.group(1).strip()
        name = m.group(2).strip()
        desc = m.group(3).strip()[:800]
        index[f"{kind}:{name}"] = desc
    return index


# ─── ГЛАВНАЯ ФУНКЦИЯ ─────────────────────────────────────────────────────────

def get_hd_context(hd_data: dict) -> str:
    """
    Принимает данные HD-карты из server.py и возвращает
    релевантные описания из библиотеки для передачи Claude.

    hd_data содержит ключи из RAW-текста: тип, авторитет, центры, каналы, ворота.
    """
    raw = hd_data.get('raw', '')
    if not raw:
        return ''

    sections = []

    # ── Тип ──
    type_match = re.search(r'ТИП:\s*(.+)', raw)
    if type_match:
        hd_type = type_match.group(1).strip()
        types_idx = _build_types_index()
        for key, val in types_idx.items():
            if 'ТИП' in key and hd_type.lower() in key.lower():
                sections.append(f"=== ТИП: {hd_type} ===\n{val}")
                break

    # ── Авторитет ──
    auth_match = re.search(r'АВТОРИТЕТ:\s*(.+)', raw)
    if auth_match:
        authority = auth_match.group(1).strip()
        types_idx = _build_types_index()
        for key, val in types_idx.items():
            if 'АВТОРИТЕТ' in key and authority.lower() in key.lower():
                sections.append(f"=== АВТОРИТЕТ: {authority} ===\n{val}")
                break

    # ── Определённые центры ──
    centers_block = re.search(r'ОПРЕДЕЛЁННЫЕ ЦЕНТРЫ[^\n]*:\n(.*?)(?=НЕОПРЕДЕЛЁННЫЕ|КАНАЛЫ|$)', raw, re.DOTALL)
    undef_block = re.search(r'НЕОПРЕДЕЛЁННЫЕ ЦЕНТРЫ[^\n]*:\n(.*?)(?=КАНАЛЫ|СОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)

    centers_idx = _build_centers_index()
    defined_centers = []
    undefined_centers = []

    if centers_block:
        for line in centers_block.group(1).strip().split('\n'):
            c = line.strip().lstrip('·•- ')
            if c:
                defined_centers.append(c)

    if undef_block:
        for line in undef_block.group(1).strip().split('\n'):
            c = line.strip().lstrip('·•- ')
            if c:
                undefined_centers.append(c)

    center_texts = []
    for c in defined_centers:
        for key, val in centers_idx.items():
            if c.lower() in key.lower() or key.lower() in c.lower():
                # Берём только блок "Определённый"
                defined_part = re.search(r'\*\*Определённый[^\*]*\*\*[:\s]*(.*?)(?=\*\*|$)', val, re.DOTALL)
                text = defined_part.group(1).strip()[:400] if defined_part else val[:400]
                center_texts.append(f"Центр {c} (определённый): {text}")
                break

    for c in undefined_centers:
        for key, val in centers_idx.items():
            if c.lower() in key.lower() or key.lower() in c.lower():
                open_part = re.search(r'\*\*Открытый[^\*]*\*\*[:\s]*(.*?)(?=\*\*|$)', val, re.DOTALL)
                text = open_part.group(1).strip()[:400] if open_part else val[:400]
                center_texts.append(f"Центр {c} (открытый): {text}")
                break

    if center_texts:
        sections.append("=== ЦЕНТРЫ ===\n" + '\n\n'.join(center_texts))

    # ── Каналы ──
    channels_match = re.search(r'КАНАЛЫ[^\n]*:\n(.*?)(?=СОЗНАТЕЛЬНЫЕ|БЕССОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)
    channels_idx = _build_channels_index()
    channel_texts = []

    if channels_match:
        for line in channels_match.group(1).strip().split('\n'):
            line = line.strip().lstrip('·•- ')
            m = re.match(r'(\d+)-(\d+)', line)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                desc = channels_idx.get((a, b)) or channels_idx.get((b, a))
                if desc:
                    channel_texts.append(desc)

    if channel_texts:
        sections.append("=== КАНАЛЫ ===\n" + '\n\n'.join(channel_texts))

    # ── Ворота (сознательные + бессознательные) ──
    gates_idx = _build_gates_index()
    gate_texts = []
    seen_gates = set()

    gate_pattern = re.compile(r'Ворота\s+(\d+)\.(\d+)', re.IGNORECASE)
    for m in gate_pattern.finditer(raw):
        gate_num = int(m.group(1))
        line_num = int(m.group(2))
        if gate_num in seen_gates:
            continue
        seen_gates.add(gate_num)

        gate_data = gates_idx.get(gate_num, {})
        line_text = gate_data.get('lines', {}).get(line_num) or gate_data.get('full', '')
        if line_text:
            gate_texts.append(f"Ворота {gate_num}.{line_num}: {line_text[:400]}")

    if gate_texts:
        sections.append("=== ВОРОТА ===\n" + '\n\n'.join(gate_texts[:20]))  # max 20 ворот

    return '\n\n'.join(sections)


if __name__ == '__main__':
    # Быстрый тест
    test_raw = """Дата: 23.02.1981  09:50  UTC+1

ТИП:         Проектор
СТРАТЕГИЯ:   Ждать приглашения
АВТОРИТЕТ:   Эмоциональный
НЕ-Я ТЕМА:  Горечь
ПРОФИЛЬ:     5/1

ОПРЕДЕЛЁННЫЕ ЦЕНТРЫ (5):
  Я/Самость, Горло, Селезёнка, Корень, Солнечное сплетение

НЕОПРЕДЕЛЁННЫЕ ЦЕНТРЫ:
  Голова, Аджна, Эго, Сакральный

КАНАЛЫ (3):
  13-33
  18-58
  19-49

СОЗНАТЕЛЬНЫЕ ВОРОТА:
  Солнце       Ворота 55.5
  Меркурий     Ворота 49.5
  Венера       Ворота 49.6
  Луна         Ворота 32.4
"""
    result = get_hd_context({'raw': test_raw})
    print(result[:3000])
    print(f'\n\nИТОГО СИМВОЛОВ: {len(result)}')
