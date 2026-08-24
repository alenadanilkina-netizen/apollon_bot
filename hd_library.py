"""
HD Library — индексирует файлы библиотеки Дизайна Человека
и выдаёт релевантный контекст по карте конкретного человека.
"""

import re
import os
from pathlib import Path
from functools import lru_cache

LIB_DIR = Path(__file__).parent


def _clean_library_excerpt(value: str, limit: int = 600) -> str:
    """Убирает OCR-колонтитулы и служебные страницы из короткой выдержки."""
    if not value:
        return ''
    cleaned = re.sub(r'===\s*стр\.?\s*\d+\s*===', ' ', value, flags=re.IGNORECASE)
    cleaned = re.sub(r'Данный перевод не является официальным\..*?запрещены[»\"\.]?', ' ', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'Страница\s*[\u00a0 ]*\d+', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n\s*[-—]?\s*\.{3,}\s*\n', '\n', cleaned)
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()[:limit]

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
    # В файле есть оглавление и несколько повторных заголовков страниц. Берём
    # последнее вхождение каждых ворот — это основной текст, а не оглавление.
    gate_matches = list(gate_pattern.finditer(text))
    latest = {}
    for match in gate_matches:
        latest[int(match.group(1))] = match
    selected = sorted(latest.values(), key=lambda match: match.start())

    for i, match in enumerate(selected):
        gate_num = int(match.group(1))
        start = match.start()
        end = selected[i+1].start() if i+1 < len(selected) else len(text)
        gate_text = text[start:end]

        # Обычно заголовки выглядят как «13.1». В разделе первых ворот
        # вторая линия в исходном переводе записана сокращённо: «2. Любовь
        # это свет». Оба формата — часть одного и того же источника, поэтому
        # принимаем и полный, и сокращённый заголовок, но только с начала строки.
        line_pattern = re.compile(rf'(?m)^\s*(?:{gate_num}\.)?([1-6])(?:[\.\s]|$)')
        line_matches = list(line_pattern.finditer(gate_text))

        lines = {}
        for j, lm in enumerate(line_matches):
            line_num = int(lm.group(1))
            if line_num in lines:
                continue
            ls = lm.start()
            # Берём до следующего уникального заголовка линии.
            next_line = next((n for n in line_matches[j+1:] if int(n.group(1)) not in lines), None)
            le = next_line.start() if next_line else len(gate_text)
            lines[line_num] = _clean_library_excerpt(gate_text[ls:le], 600)

        index[gate_num] = {
            'full': _clean_library_excerpt(gate_text[:300], 300),
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
            candidate = f"Канал {nums} — {name}: {desc}"
            # В локальном конспекте некоторые каналы встречаются повторно:
            # поздняя запись иногда содержит только «см. выше». Сохраняем
            # наиболее полное описание, а не последнее попавшееся.
            previous = index.get(key, '')
            placeholder = not desc or 'см. выше' in desc.lower() or len(desc) < 80
            previous_placeholder = not previous or 'см. выше' in previous.lower() or len(previous) < 120
            if not previous or (not placeholder and (previous_placeholder or len(candidate) > len(previous))):
                index[key] = candidate
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


# ─── PHS ПЕРЕМЕННЫЕ ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _build_phs_index() -> dict:
    """
    Строит индекс PHS из hd_phs_index.txt.
    Возвращает {
      'env': {1: text, 2: text, ..., 6: text},   # 6 типов Среды
      'det': {1: text, ..., 6: text},             # 6 типов Детерминации
      'mot': {1: text, ..., 6: text},             # 6 Мотиваций
      'cog': {1: text, ..., 6: text},             # 6 Когниций
    }
    """
    text = _load('hd_phs_index.txt')
    if not text:
        return {}

    index = {'env': {}, 'det': {}, 'mot': {}, 'cog': {}}

    # Секция среды
    env_pattern = re.compile(r'###\s+(CAVE|MARKET|KITCHEN|MOUNTAIN|VALLEY|SHORE).*?— Нодальный Цвет (\d+)\n(.*?)(?=###|\Z)', re.DOTALL | re.IGNORECASE)
    for m in env_pattern.finditer(text):
        num = int(m.group(2))
        index['env'][num] = m.group(3).strip()[:1500]

    # Секция детерминации
    det_pattern = re.compile(r'###\s+COLOR (\d+)\s*—\s*([^\[]+)\[([^\]]+)\]\n(.*?)(?=###|\Z)', re.DOTALL)
    for m in det_pattern.finditer(text):
        num = int(m.group(1))
        index['det'][num] = f"{m.group(2).strip()} [{m.group(3)}]\n{m.group(4).strip()[:1500]}"

    # Мотивации и когниции — из строк "Линия X →"
    mot_block = re.search(r'МОТИВАЦИИ.*?(?=КОГНИЦИИ|\Z)', text, re.DOTALL)
    if mot_block:
        for m in re.finditer(r'Линия (\d+) → (.+)', mot_block.group()):
            index['mot'][int(m.group(1))] = m.group(2).strip()

    cog_block = re.search(r'КОГНИЦИИ.*', text, re.DOTALL)
    if cog_block:
        for m in re.finditer(r'Линия (\d+) → (.+)', cog_block.group()):
            index['cog'][int(m.group(1))] = m.group(2).strip()

    return index


ENV_NAMES = {1:"Пещера (Cave)", 2:"Рынок (Market)", 3:"Кухня (Kitchen)",
             4:"Гора (Mountain)", 5:"Долина (Valley)", 6:"Берег (Shore)"}
DET_NAMES = {1:"Последовательный (Consecutive)", 2:"Вкус (Taste)", 3:"Открытый (Open/Thirst)",
             4:"Прикосновение (Touch)", 5:"Звук (Sound)", 6:"Свет (Light)"}
MOT_NAMES = {1:"Страх (Fear)", 2:"Надежда (Hope)", 3:"Желание (Desire)",
             4:"Потребность (Need)", 5:"Вина (Guilt)", 6:"Невинность (Innocence)"}
COG_NAMES = {1:"Выживание (Survival)", 2:"Жертва (Sacrifice)", 3:"Фантазия (Fantasy)",
             4:"Вероятность (Probability)", 5:"Эмпатия (Empathy)", 6:"Солидарность (Solidarity)"}

# Связка ворот с центрами нужна для чтения по цепочке
# «планета → ворота → линия → центр → канал/контур». Она дублирует только
# канонический список расчёта из server.py, чтобы библиотека не импортировала
# исполняемый MCP-сервер и не создавала циклическую зависимость.
GATE_TO_CENTER = {}
for _center_name, _center_gates in {
    "Голова": [64, 61, 63],
    "Аджна": [47, 24, 4, 17, 43, 11],
    "Горло": [62, 23, 56, 35, 45, 12, 33, 8, 20, 31, 16],
    "Я/Самость": [1, 2, 10, 13, 15, 25, 46, 7],
    "Эго": [21, 40, 26, 51],
    "Сакральный": [5, 14, 29, 59, 9, 3, 42, 27, 34],
    "Селезёнка": [48, 57, 32, 28, 18, 50, 44],
    "Солнечное сплетение": [6, 37, 22, 36, 30, 55, 49],
    "Корень": [53, 60, 52, 58, 38, 54, 19, 41, 39],
}.items():
    for _gate_num in _center_gates:
        GATE_TO_CENTER[_gate_num] = _center_name


def get_phs_context(hd_data: dict) -> str:
    """Возвращает отдельный слой переменных, не смешивая его с линиями.

    В карте используются вложенные уровни Gate.Line.Color.Tone.Base.
    Determination берётся с дизайнной стороны Солнца/Земли, Environment — с
    дизайнных узлов, View — с личностных узлов, Motivation — с личностного
    Солнца. Линия остаётся самостоятельным уровнем и читается через Line
    Companion в ``get_hd_context``.
    """
    raw = hd_data.get('raw', '')
    if not raw:
        return ''

    conscious = re.search(r'СОЗНАТЕЛЬНЫЕ ВОРОТА.*?(?=БЕССОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)
    design = re.search(r'БЕССОЗНАТЕЛЬНЫЕ ВОРОТА.*', raw, re.DOTALL)
    conscious_text = conscious.group(0) if conscious else ''
    design_text = design.group(0) if design else ''

    def find_detail(section: str, planet: str):
        pattern = (
            rf'^\s*{re.escape(planet)}\s+Ворота\s+'
            r'(\d+)\.(\d+)\.(\d+)\.(\d+)\.(\d+)'
        )
        match = re.search(pattern, section, re.MULTILINE)
        if not match:
            return None
        gate, line, color, tone, base = map(int, match.groups())
        return {'gate': gate, 'line': line, 'color': color, 'tone': tone, 'base': base}

    phs_index = _build_phs_index()

    def describe(detail, label, names, source_excerpt=''):
        if not detail:
            return f"{label}: нет полного расчёта"
        color = detail['color']
        name = names.get(color, f"цвет {color}")
        result = (
            f"{label}: {detail['gate']}.{detail['line']}.{color}.{detail['tone']}.{detail['base']} "
            f"— {name}; тон {detail['tone']}, база {detail['base']}"
        )
        if source_excerpt:
            result += f"\n  Справочный тезис: {_clean_library_excerpt(source_excerpt, 260)}"
        else:
            result += (
                "\n  Локального проверенного тезисного фрагмента для этого слоя пока нет; "
                "не достраивать механику по общим словам."
            )
        return result

    # Названия цветовых слоёв из локальной библиотеки; они не заменяют
    # индивидуальное чтение тона и базы.
    det_names = {1: 'последовательность', 2: 'вкус', 3: 'открытость/жажда',
                 4: 'прикосновение', 5: 'звук', 6: 'свет'}
    env_names = {1: 'пещеры', 2: 'рынки', 3: 'кухни', 4: 'горы', 5: 'долины', 6: 'берега'}
    view_names = {1: 'выживание', 2: 'возможность', 3: 'власть',
                  4: 'желание', 5: 'вероятность', 6: 'личное'}
    motivation_names = {1: 'страх', 2: 'надежда', 3: 'желание',
                        4: 'потребность', 5: 'вина', 6: 'невинность'}

    sections = ["=== ПЕРЕМЕННЫЕ: отдельный слой от линий ==="]
    design_sun = find_detail(design_text, 'Солнце')
    design_earth = find_detail(design_text, 'Земля')
    design_node = find_detail(design_text, 'С.Узел')
    design_south_node = find_detail(design_text, 'Ю.Узел')
    personality_node = find_detail(conscious_text, 'С.Узел')
    personality_south_node = find_detail(conscious_text, 'Ю.Узел')
    personality_sun = find_detail(conscious_text, 'Солнце')
    personality_earth = find_detail(conscious_text, 'Земля')

    def paired(detail, label):
        if not detail:
            return ''
        return (
            f"\n  Парная точка {label}: "
            f"{detail['gate']}.{detail['line']}.{detail['color']}.{detail['tone']}.{detail['base']}"
        )

    sections.append(describe(
        design_sun, 'Тело и питание', det_names,
        phs_index.get('det', {}).get(design_sun['color'], '') if design_sun else ''
    ) + paired(design_earth, 'оси тела'))
    sections.append(describe(
        design_node, 'Среда', env_names,
        phs_index.get('env', {}).get(design_node['color'], '') if design_node else ''
    ) + paired(design_south_node, 'узловой оси среды'))
    sections.append(describe(personality_node, 'Взгляд', view_names) +
                    paired(personality_south_node, 'узловой оси взгляда'))
    sections.append(describe(personality_sun, 'Мотивация', motivation_names) +
                    paired(personality_earth, 'оси мотивации'))
    sections.append(
        "Правило чтения: цвет задаёт слой переменной, тон уточняет способ восприятия, "
        "база пока передаётся как расчётное значение без самостоятельного текста. "
        "Не смешивать эти значения с линией ворот."
    )
    return '\n'.join(sections)


# ─── КНИГА ЛЮБВИ ─────────────────────────────────────────────────────────────

# Ворота любви из Love Book (Ra Uru Hu)
# Анти-мундан (G-центр): 25, 15, 46, 10(трансцендентный)
# Мундан (личные): 10, 44, 40, 58, 41, 28, 55
LOVE_GATES = {25, 15, 46, 10, 44, 40, 58, 41, 28, 55}

@lru_cache(maxsize=1)
def _build_love_index() -> dict:
    """
    Строит индекс ворот любви из hd_love_book.txt.
    Возвращает {gate_num: excerpt} — ключевые абзацы о каждых воротах.
    """
    text = _load('hd_love_book.txt')
    index = {}

    # Паттерны заголовков в книге: "Gate 25", "Gate 44" и т.д.
    gate_pattern = re.compile(
        r'(?:Gate|gate)\s+(\d+)[^\n]{0,60}\n(.*?)(?=(?:Gate|gate)\s+\d+|===|$)',
        re.DOTALL
    )
    for m in gate_pattern.finditer(text):
        gn = int(m.group(1))
        if gn in LOVE_GATES:
            excerpt = m.group(2).strip()[:1200]
            if gn not in index:
                index[gn] = excerpt
            else:
                index[gn] += "\n" + excerpt[:400]

    return index


def get_love_context(hd_data: dict) -> str:
    """
    Возвращает описания ворот любви из Love Book для ворот, присутствующих в карте.
    """
    raw = hd_data.get('raw', '')
    if not raw:
        return ''

    love_idx = _build_love_index()
    gates_idx = _build_gates_index()

    # Найти все ворота в карте пользователя
    gate_pattern = re.compile(r'Ворота\s+(\d+)\.(\d+)')
    user_gates = {int(m.group(1)) for m in gate_pattern.finditer(raw)}

    # Ворота любви которые есть в карте
    present_love_gates = user_gates & LOVE_GATES

    sections = []

    # Каналы любви (37-40, 59-6, 19-49 — племенные; 44-26, 29-46 и др.)
    LOVE_CHANNELS = {(37, 40), (59, 6), (19, 49), (44, 26), (29, 46), (41, 30)}
    channels_match = re.search(r'КАНАЛЫ[^\n]*:\n(.*?)(?=СОЗНАТЕЛЬНЫЕ|БЕССОЗНАТЕЛЬНЫЕ|КРЕСТ|$)', raw, re.DOTALL)
    present_channels = set()
    if channels_match:
        for line in channels_match.group(1).strip().split('\n'):
            m = re.match(r'(\d+)-(\d+)', line.strip())
            if m:
                ch = (int(m.group(1)), int(m.group(2)))
                if ch in LOVE_CHANNELS or (ch[1], ch[0]) in LOVE_CHANNELS:
                    present_channels.add(ch)

    if present_love_gates or present_channels:
        parts = []
        for gn in sorted(present_love_gates):
            love_text = love_idx.get(gn, '')
            gate_data = gates_idx.get(gn, {})
            gate_full = gate_data.get('full', '')[:200]
            entry = f"Ворота {gn} (любовь):"
            if gate_full:
                entry += f"\n  Суть: {gate_full}"
            if love_text:
                entry += f"\n  Из Love Book: {love_text[:600]}"
            parts.append(entry)

        if present_channels:
            parts.append(f"Каналы отношений в карте: {', '.join(f'{a}-{b}' for a,b in present_channels)}")

        if parts:
            sections.append("=== ВОРОТА ЛЮБВИ (из Love Book Ra Uru Hu) ===\n" + '\n\n'.join(parts))

    return '\n\n'.join(sections)


# ─── КРЕСТ ВОПЛОЩЕНИЯ ────────────────────────────────────────────────────────

def get_cross_context(hd_data: dict) -> str:
    """
    Возвращает описания 4 ворот Креста воплощения из Line Companion.
    Используется в блоке призвания/миссии.
    """
    raw = hd_data.get('raw', '')
    if not raw:
        return ''

    gates_idx = _build_gates_index()
    sections = []

    # Парсим крест из HD raw
    cross_match = re.search(
        r'КРЕСТ ВОПЛОЩЕНИЯ.*?\n'
        r'\s*Ось Личности:.*?Солнце\s+(\d+)\.(\d+).*?Земля\s+(\d+)\.(\d+).*?\n'
        r'\s*Ось Дизайна:.*?Солнце\s+(\d+)\.(\d+).*?Земля\s+(\d+)\.(\d+)',
        raw, re.DOTALL
    )

    if not cross_match:
        return ''

    ps_g, ps_l = int(cross_match.group(1)), int(cross_match.group(2))
    pe_g, pe_l = int(cross_match.group(3)), int(cross_match.group(4))
    ds_g, ds_l = int(cross_match.group(5)), int(cross_match.group(6))
    de_g, de_l = int(cross_match.group(7)), int(cross_match.group(8))

    gate_quartet = [
        (ps_g, ps_l, "Солнце Личности — сознательная тема жизни"),
        (pe_g, pe_l, "Земля Личности — сознательное заземление"),
        (ds_g, ds_l, "Солнце Дизайна — бессознательная движущая сила"),
        (de_g, de_l, "Земля Дизайна — бессознательное заземление"),
    ]

    cross_texts = []
    for gate_num, line_num, role in gate_quartet:
        gate_data = gates_idx.get(gate_num, {})
        gate_full = gate_data.get('full', '')[:200]
        line_text = gate_data.get('lines', {}).get(line_num, '')[:500]
        if gate_full or line_text:
            entry = f"Ворота {gate_num}.{line_num} [{role}]:\n"
            if gate_full:
                entry += f"  Суть ворот: {gate_full}\n"
            if line_text:
                entry += f"  Линия {line_num}: {line_text}"
            cross_texts.append(entry)

    if cross_texts:
        sections.append(
            "=== КРЕСТ ВОПЛОЩЕНИЯ — описания из Line Companion ===\n"
            + "\n\n".join(cross_texts)
        )

    return '\n\n'.join(sections)


# ─── ПРОФИЛИ (из Баннел + Ra Uru Hu) ────────────────────────────────────────

PROFILE_DESCRIPTIONS = {
    "1/3": {
        "name": "Следователь / Мученик",
        "theme": "Самодостаточность через накопление знаний, опыт через ошибки",
        "line1": "1я линия — Следователь: фундамент безопасности — знание. Без почвы под ногами паника. Нужно изучить тему досконально перед действием. В отношениях — нужно знать всё о партнёре, иначе тревога. В деньгах — действует только когда уверен в инструменте. Страх неизведанного. Сила — в глубине, не широте.",
        "line3": "3я линия — Мученик: учится через прямой опыт, через пробы и ошибки. То что 'не работает' — это не провал, это данные. Жизнь строится методом исключения. В отношениях — может пережить несколько союзов пока находит правильный. В карьере — меняет направления. Сила — адаптивность и знание что НЕ работает.",
        "strategy": "В отношениях: нужно время изучить человека (1я), и ошибки — нормальная часть пути (3я). В карьере: сначала глубокое изучение, потом практика. Решения принимать только после достаточного исследования.",
        "trap": "Ловушка 1й: парализующая потребность знать всё до начала. Ловушка 3й: стыд за 'неудачи' вместо признания их ценности."
    },
    "1/4": {
        "name": "Следователь / Оппортунист",
        "theme": "Фундамент через знание, влияние через сеть близких",
        "line1": "1я линия — Следователь: безопасность через знание и фундамент. Действует только когда изучил. Паника без почвы.",
        "line4": "4я линия — Оппортунист: жизнь строится через свой круг. Возможности приходят через людей которых уже знает. Новые люди — через рекомендации от своих. В отношениях влюбляется только в тех кто уже в его орбите. Холодные контакты почти никогда не работают.",
        "strategy": "В карьере: делиться знаниями в своей сети — преподавать, консультировать своих. В отношениях: партнёр приходит из своего круга или через знакомых. Расширять круг через качество, не количество.",
        "trap": "Ловушка: замкнутость на одних и тех же людях, страх выйти за круг. Или наоборот — растворение в потребностях своей сети."
    },
    "2/4": {
        "name": "Отшельник / Оппортунист",
        "theme": "Природный талант (не видит сам), реализация через сеть",
        "line2": "2я линия — Отшельник: таланты природные, не требующие усилий — поэтому сам их не видит. Нужно уединение для восстановления и 'варки'. Других видит насквозь, себя — нет. Лучшее что может сделать — позволить другим называть его таланты. Не любит когда его тревожат без приглашения.",
        "line4": "4я линия — Оппортунист: реализация через близкий круг. Всё лучшее в жизни приходит через людей которых уже знает — работа, любовь, возможности.",
        "strategy": "В отношениях: партнёр приходит из своего окружения или по рекомендации. В карьере: нужно позволять другим признавать твои таланты — они видят лучше. Нужно время в одиночестве + качественный круг.",
        "trap": "Ловушка: игнорировать приглашения ('мне не нужна помощь'). Или брать первое что пришло из страха упустить."
    },
    "3/5": {
        "name": "Мученик / Еретик",
        "theme": "Опыт через ошибки, универсальные решения для других",
        "line3": "3я линия — Мученик: учится через прямой опыт. Ошибки — метод познания. 'Я пробовал, не сработало' — ценнейшая информация. Жизнь нелинейная, с большим количеством изменений курса.",
        "line5": "5я линия — Еретик: на него проецируют. Другие видят в нём решение своих проблем — и он может их находить. Но если не оправдывает ожиданий — проекция переворачивается. Нужно избирательно выбирать кому помогать и с какой проблемой. Публичная роль, широкое влияние.",
        "strategy": "В карьере: опыт через пробы (3я) даёт уникальное знание что работает, 5я превращает это в универсальный инструмент для других. В отношениях: несколько союзов — норма. Партнёр видит в нём спасителя — это ловушка.",
        "trap": "Ловушка 3й: стыд за ошибки. Ловушка 5й: брать на себя чужие проекции и пытаться им соответствовать."
    },
    "4/6": {
        "name": "Оппортунист / Ролевая модель",
        "theme": "Влияние через сеть, мудрость через три фазы жизни",
        "line4": "4я линия — Оппортунист: всё строится через близкий круг. Работа, любовь, возможности — через людей которых уже знает. Холодные контакты не работают.",
        "line6": "6я линия — Ролевая модель: жизнь в трёх фазах. 1-30 лет: эксперименты, ошибки, 'проживание 3й линии'. 30-50: на крыше — наблюдает, отстраняется, ищет истину. После 50 (или Сатурн-ретурн): спускается с крыши как живое воплощение мудрости. Другие видят в нём образец — и это правда.",
        "strategy": "В отношениях: до 30 — пробы, после — глубокая серьёзная связь или ни одной. В карьере: после 'периода на крыше' выходит с мощным авторитетом. Нужно дать себе время созреть.",
        "trap": "Ловушка: торопиться с выводами о себе до завершения фаз. Или застрять 'на крыше' и не спускаться."
    },
    "5/1": {
        "name": "Еретик / Следователь",
        "theme": "Практические решения для мира, фундамент через знание",
        "line5": "5я линия — Еретик: на него проецируют роль спасителя или злодея. Обладает реальной практической силой решать проблемы, но нужно быть избирательным. Широкое влияние, публичность.",
        "line1": "1я линия — Следователь: нужен фундамент, знания, исследование. Прежде чем решать проблему других — нужно изучить её досконально. Это то что делает 5ю линию реально эффективной, а не просто популярной.",
        "strategy": "В карьере: сначала изучить (1я), потом предложить решение миру (5я). В отношениях: партнёр видит в нём идеал — важно не соответствовать ожиданиям а быть собой. Избирательность в том кому и чем помогать.",
        "trap": "Ловушка: браться за всех у кого есть проблема. Репутация важна — одна 'не оправданная' проекция может разрушить всё."
    },
    "6/2": {
        "name": "Ролевая модель / Отшельник",
        "theme": "Три фазы мудрости, природный талант который видят другие",
        "line6": "6я линия — Ролевая модель: три фазы (см. 4/6). До 30 — живёт как 3я линия, ошибки и эксперименты. 30-50 — 'на крыше', наблюдение, дистанция, поиск смысла. После — воплощённая мудрость, ролевая модель.",
        "line2": "2я линия — Отшельник: природные таланты которые не видит в себе. Нужно уединение. Другие тянутся к нему и называют его таланты — это ценная информация. Важно не убегать от признания.",
        "strategy": "В карьере: нужно время для созревания (6я) и уединения (2я). После 'периода на крыше' выходит с природным талантом и реальной мудростью. В отношениях: серьёзные союзы после 30.",
        "trap": "Ловушка: слишком долго оставаться 'на крыше'. Или не видеть своих талантов потому что они даются без усилий."
    },
    "6/3": {
        "name": "Ролевая модель / Мученик",
        "theme": "Опыт через ошибки, три фазы, воплощённая мудрость",
        "line6": "6я линия — три фазы жизни. Настоящая сила приходит после 30-40 лет.",
        "line3": "3я линия — учится через прямой опыт, пробы и ошибки. Это не слабость — это метод. Особенно до 30 лет жизнь очень насыщена переменами и 'провалами'.",
        "strategy": "Переломы и ошибки первых 30 лет — это сырьё для мудрости второй половины жизни. Не торопиться с выводами кто я и что умею.",
        "trap": "Ловушка: стыдиться ошибок первой фазы. Или не замечать что уже вошёл в 'период на крыше'."
    },
}

def get_profile_context(hd_data: dict) -> str:
    """Возвращает детальное описание профиля из Баннел для карты пользователя."""
    raw = hd_data.get('raw', '')
    if not raw:
        return ''

    profile_match = re.search(r'ПРОФИЛЬ:\s*(\d+/\d+)', raw)
    if not profile_match:
        return ''

    profile_key = profile_match.group(1)
    data = PROFILE_DESCRIPTIONS.get(profile_key)
    if not data:
        return (
            f"=== ПРОФИЛЬ {profile_key} ===\n"
            "Подробного проверенного описания этого профиля нет в локальной библиотеке. "
            "Не достраивать его по памяти и не выдавать общие формулы за расчёт."
        )

    result = f"=== ПРОФИЛЬ {profile_key} — {data['name']} ===\n"
    result += f"Тема: {data['theme']}\n\n"
    l1_key = [k for k in data if k.startswith('line')][0]
    l2_key = [k for k in data if k.startswith('line')][1] if len([k for k in data if k.startswith('line')]) > 1 else None
    result += data[l1_key] + "\n\n"
    if l2_key:
        result += data[l2_key] + "\n\n"
    result += f"Стратегия профиля: {data['strategy']}\n"
    result += f"Ловушка: {data['trap']}"
    return result


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

    sections = [
        "=== ПРОВЕРКА ИСТОЧНИКОВ HD ===\n"
        "Расчётные факты взяты из текущей карты. Описания типов и центров — из "
        "локального справочника с указанным авторством; линии — из рабочего перевода "
        "Line Companion, подтверждённого пользователем. Технические колонтитулы "
        "очищаются, а если текст источника не найден, смысл не достраивать."
    ]

    # ── Тип ──
    type_match = re.search(r'ТИП:\s*(.+)', raw)
    if type_match:
        hd_type = type_match.group(1).strip()
        types_idx = _build_types_index()
        found = False
        for key, val in types_idx.items():
            if 'ТИП' in key and hd_type.lower() in key.lower():
                sections.append(f"=== ТИП: {hd_type} ===\n{val}")
                found = True
                break
        if not found:
            sections.append(
                f"=== ТИП: {hd_type} ===\n"
                "Проверенного подробного описания этого типа нет в локальной библиотеке. "
                "Использовать только рассчитанные стратегию и авторитет; не достраивать механику по памяти."
            )

    # ── Авторитет ──
    auth_match = re.search(r'АВТОРИТЕТ:\s*(.+)', raw)
    if auth_match:
        authority = auth_match.group(1).strip()
        types_idx = _build_types_index()
        for key, val in types_idx.items():
            if 'АВТОРИТЕТ' in key and authority.lower() in key.lower():
                sections.append(f"=== АВТОРИТЕТ: {authority} ===\n{val}")
                break

    # ── Профиль ──
    profile_ctx = get_profile_context(hd_data)
    if profile_ctx:
        sections.append(profile_ctx)

    # ── Все планеты, ворота и линии ──
    # Раньше в контекст попадали только Солнце/Луна/Земля и активные каналы.
    # Из-за этого Меркурий, Венера, Марс и внешние планеты могли теряться, а
    # модель достраивала их «по общему впечатлению». Теперь каждая активация
    # передаётся с указанием стороны и выдержкой из Line Companion.

    # ── Парсим ворота с планетами (для синтеза) ──
    # Формат: "Солнце       Ворота 55.5   Рыбы 4°39'"
    planet_gate_pattern = re.compile(
        r'(Солнце|Земля|Луна|Меркурий|Венера|Марс|Юпитер|Сатурн|Уран|Нептун|Плутон|С\.Узел|Ю\.Узел)'
        r'\s+Ворота\s+(\d+)\.(\d+)',
        re.IGNORECASE
    )
    # Собираем: {gate_num: [(planet, line, is_conscious)]}
    gate_planets = {}
    gates_idx = _build_gates_index()
    conscious_section = re.search(r'СОЗНАТЕЛЬНЫЕ ВОРОТА.*?:(.*?)(?=БЕССОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)
    unconscious_section = re.search(r'БЕССОЗНАТЕЛЬНЫЕ ВОРОТА.*?:(.*?)$', raw, re.DOTALL)

    for section_text, is_conscious in [
        (conscious_section.group(1) if conscious_section else '', True),
        (unconscious_section.group(1) if unconscious_section else '', False)
    ]:
        for m in planet_gate_pattern.finditer(section_text):
            planet = m.group(1)
            gate_num = int(m.group(2))
            line_num = int(m.group(3))
            if gate_num not in gate_planets:
                gate_planets[gate_num] = []
            gate_planets[gate_num].append((planet, line_num, is_conscious))

    planet_line_texts = []
    for gate_num, planet_list in gate_planets.items():
        gate_data = gates_idx.get(gate_num, {})
        for planet, line_num, is_conscious in planet_list:
            side = "личность" if is_conscious else "дизайн тела"
            line_text = _clean_library_excerpt(gate_data.get('lines', {}).get(line_num, ''), 420)
            center_name = GATE_TO_CENTER.get(gate_num, 'центр не найден')
            item = f"{planet}: ворота {gate_num}.{line_num} ({side}; центр: {center_name})"
            if line_text:
                item += f"\n  Описание линии из Line Companion: {line_text}"
            planet_line_texts.append(item)
    if planet_line_texts:
        sections.append(
            "=== ПЛАНЕТЫ, ВОРОТА И ЛИНИИ (полная карта) ===\n" +
            "\n\n".join(planet_line_texts)
        )

    # ── Каналы — синтез с планетами и контуром ──
    channels_match = re.search(r'КАНАЛЫ[^\n]*:\n(.*?)(?=СОЗНАТЕЛЬНЫЕ|БЕССОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)
    channels_idx = _build_channels_index()
    channel_texts = []

    if channels_match:
        for line in channels_match.group(1).strip().split('\n'):
            line = line.strip().lstrip('·•- ')
            m = re.match(r'(\d+)-(\d+)', line)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                desc = channels_idx.get((a, b)) or channels_idx.get((b, a)) or ''

                # Планеты активирующие каждые ворота канала
                gate_info_parts = []
                for gate_num in [a, b]:
                    planets_for_gate = gate_planets.get(gate_num, [])
                    if planets_for_gate:
                        for planet, line_num, is_conscious in planets_for_gate:
                            kind = 'сознательные' if is_conscious else 'бессознательные'
                            # Описание линии из Line Companion
                            gate_data = gates_idx.get(gate_num, {})
                            line_text = _clean_library_excerpt(gate_data.get('lines', {}).get(line_num, ''), 300)
                            center_name = GATE_TO_CENTER.get(gate_num, 'центр не найден')
                            gate_info_parts.append(
                                f"  Ворота {gate_num}.{line_num} [{kind}; центр: {center_name}] "
                                f"активирует {planet}:\n  {line_text}"
                            )

                channel_block = desc or (
                    f"Канал {a}-{b}: проверенного описания нет в локальной библиотеке. "
                    "Учитывать только сам факт рассчитанного канала и не достраивать смысл по памяти."
                )
                if gate_info_parts:
                    channel_block += "\n" + "\n".join(gate_info_parts)
                channel_texts.append(channel_block)

    if channel_texts:
        sections.append("=== КАНАЛЫ (синтез: канал → ворота → планета → линия) ===\n" + '\n\n'.join(channel_texts))

    # ── Одиночные ворота (не входящие в каналы) — только Солнце и Луна ──
    # Это самые важные ворота для личности
    channels_gates = set()
    if channels_match:
        for line in channels_match.group(1).strip().split('\n'):
            m = re.match(r'(\d+)-(\d+)', line.strip())
            if m:
                channels_gates.add(int(m.group(1)))
                channels_gates.add(int(m.group(2)))

    key_planets = {'Солнце', 'Луна', 'Земля'}
    solo_gate_texts = []
    seen = set()
    for gate_num, planet_list in gate_planets.items():
        if gate_num in channels_gates:
            continue
        for planet, line_num, is_conscious in planet_list:
            if planet in key_planets and gate_num not in seen:
                seen.add(gate_num)
                kind = 'сознательные' if is_conscious else 'бессознательные'
                gate_data = gates_idx.get(gate_num, {})
                line_text = _clean_library_excerpt(gate_data.get('lines', {}).get(line_num, ''), 350)
                solo_gate_texts.append(
                    f"Ворота {gate_num}.{line_num} ({planet}, {kind}):\n{line_text}"
                )

    if solo_gate_texts:
        sections.append("=== КЛЮЧЕВЫЕ ОДИНОЧНЫЕ ВОРОТА (Солнце/Луна/Земля) ===\n" + '\n\n'.join(solo_gate_texts))

    # ── Состояния центров: определённый / неопределённый / открытый ──
    centers_idx = _build_centers_index()
    def parse_centers(label):
        match = re.search(rf'{label}[^\n]*:\n(.*?)(?=ОПРЕДЕЛЁННЫЕ|НЕОПРЕДЕЛЁННЫЕ|ОТКРЫТЫЕ|КАНАЛЫ|СОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)
        if not match:
            return []
        values = [part.strip().lstrip('·•- ') for part in match.group(1).replace(',', '\n').splitlines() if part.strip()]
        return [value for value in values if value.lower().rstrip('.') not in {'нет', 'none', 'n/a'}]

    def center_text(center, status, limit=500):
        for key, val in centers_idx.items():
            if center.lower() == key.lower() or center.lower() in key.lower():
                marker = {'defined': 'Определённый', 'undefined': 'Неопределённый', 'open': 'Открытый'}[status]
                section = re.search(rf'\*\*{marker}[^\*]*\*\*[:\s]*(.*?)(?=\*\*|$)', val, re.DOTALL)
                return (section.group(1).strip() if section else val.strip())[:limit]
        return 'описание в библиотеке не найдено'

    center_sections = [
        ('ОПРЕДЕЛЁННЫЕ ЦЕНТРЫ', 'defined', 'устойчивые механики'),
        ('НЕОПРЕДЕЛЁННЫЕ ЦЕНТРЫ', 'undefined', 'восприимчивость при наличии отдельных активаций'),
        ('ОТКРЫТЫЕ ЦЕНТРЫ', 'open', 'восприимчивость без собственных активаций'),
    ]
    for label, status, title in center_sections:
        entries = [f"{center}: {center_text(center, status)}" for center in parse_centers(label)]
        if entries:
            body = '\n\n'.join(entries)
        else:
            body = 'нет центров в этой категории; не добавлять свойства этой категории.'
        sections.append(f"=== {label} ({title}) ===\n" + body)

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
