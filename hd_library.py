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


def get_phs_context(hd_data: dict) -> str:
    """
    По карте HD вычисляет 4 переменных и возвращает их описания из PHS книг.

    Переменные:
      Детерминация = линия Сознательного Солнца (Personality Sun)
      Среда        = линия Дизайнного Северного Узла (Design North Node)
      Мотивация    = линия Сознательной Земли (Personality Earth)
      Когниция     = линия Дизайнной Земли (Design Earth)
    """
    raw = hd_data.get('raw', '')
    if not raw:
        return ''

    phs = _build_phs_index()
    if not phs:
        return ''

    # Парсим нужные 4 линии
    def get_line(section_text, planet):
        m = re.search(rf'{planet}\s+Ворота\s+\d+\.(\d+)', section_text)
        return int(m.group(1)) if m else None

    con_section = re.search(r'СОЗНАТЕЛЬНЫЕ ВОРОТА.*?(?=БЕССОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)
    unc_section = re.search(r'БЕССОЗНАТЕЛЬНЫЕ ВОРОТА.*', raw, re.DOTALL)

    con_text = con_section.group() if con_section else ''
    unc_text = unc_section.group() if unc_section else ''

    det_line = get_line(con_text, 'Солнце')    # Personality Sun → Детерминация
    env_line = get_line(unc_text, 'С\\.Узел')  # Design North Node → Среда
    mot_line = get_line(con_text, 'Земля')     # Personality Earth → Мотивация
    cog_line = get_line(unc_text, 'Земля')     # Design Earth → Когниция

    sections = []

    # Детерминация (питание/тело)
    if det_line:
        side = "Left (Активный)" if det_line <= 3 else "Right (Пассивный)"
        name = DET_NAMES.get(det_line, f"Color {det_line}")
        desc = phs['det'].get(det_line, '')
        sections.append(
            f"=== ДЕТЕРМИНАЦИЯ: {name} [{side}] ===\n"
            f"Тип питания и работы с телом. Линия Личностного Солнца: {det_line}\n"
            + (desc[:800] if desc else "")
        )

    # Среда (окружение)
    if env_line:
        side = "Active (движение)" if env_line <= 3 else "Passive (постоянство)"
        name = ENV_NAMES.get(env_line, f"Color {env_line}")
        desc = phs['env'].get(env_line, '')
        sections.append(
            f"=== СРЕДА: {name} [{side}] ===\n"
            f"Оптимальная среда для здоровья и эффективности. Линия Дизайнного Узла: {env_line}\n"
            + (desc[:800] if desc else "")
        )

    # Мотивация
    if mot_line:
        side = "Left" if mot_line <= 3 else "Right"
        name = MOT_NAMES.get(mot_line, f"Мотивация {mot_line}")
        desc = phs['mot'].get(mot_line, '')
        sections.append(
            f"=== МОТИВАЦИЯ: {name} [{side}] ===\n"
            f"Что движет изнутри. Линия Личностной Земли: {mot_line}\n"
            + (desc if desc else "")
        )

    # Когниция
    if cog_line:
        side = "Left" if cog_line <= 3 else "Right"
        name = COG_NAMES.get(cog_line, f"Когниция {cog_line}")
        desc = phs['cog'].get(cog_line, '')
        sections.append(
            f"=== КОГНИЦИЯ: {name} [{side}] ===\n"
            f"Как воспринимает и обрабатывает мир. Линия Дизайнной Земли: {cog_line}\n"
            + (desc if desc else "")
        )

    return '\n\n'.join(sections)


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
        return f"Профиль {profile_key}"

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

    # ── Профиль ──
    profile_ctx = get_profile_context(hd_data)
    if profile_ctx:
        sections.append(profile_ctx)

    # ── Парсим ворота с планетами (для синтеза) ──
    # Формат: "Солнце       Ворота 55.5   Рыбы 4°39'"
    planet_gate_pattern = re.compile(
        r'(Солнце|Земля|Луна|Меркурий|Венера|Марс|Юпитер|Сатурн|Уран|Нептун|Плутон|С\.Узел|Ю\.Узел)'
        r'\s+Ворота\s+(\d+)\.(\d+)',
        re.IGNORECASE
    )
    # Собираем: {gate_num: [(planet, line, is_conscious)]}
    gate_planets = {}
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

    # ── Каналы — синтез с планетами и контуром ──
    channels_match = re.search(r'КАНАЛЫ[^\n]*:\n(.*?)(?=СОЗНАТЕЛЬНЫЕ|БЕССОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)
    channels_idx = _build_channels_index()
    gates_idx = _build_gates_index()
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
                            line_text = gate_data.get('lines', {}).get(line_num, '')[:300]
                            gate_info_parts.append(
                                f"  Ворота {gate_num}.{line_num} [{kind}] активирует {planet}:\n  {line_text}"
                            )

                channel_block = f"{desc}"
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
                line_text = gate_data.get('lines', {}).get(line_num, '')[:350]
                solo_gate_texts.append(
                    f"Ворота {gate_num}.{line_num} ({planet}, {kind}):\n{line_text}"
                )

    if solo_gate_texts:
        sections.append("=== КЛЮЧЕВЫЕ ОДИНОЧНЫЕ ВОРОТА (Солнце/Луна/Земля) ===\n" + '\n\n'.join(solo_gate_texts))

    # ── Открытые центры (уязвимости и мудрость) ──
    undef_block = re.search(r'НЕОПРЕДЕЛЁННЫЕ ЦЕНТРЫ[^\n]*:\n(.*?)(?=КАНАЛЫ|СОЗНАТЕЛЬНЫЕ|$)', raw, re.DOTALL)
    centers_idx = _build_centers_index()
    undef_texts = []
    if undef_block:
        for c in undef_block.group(1).strip().split('\n'):
            c = c.strip().lstrip('·•- ')
            if not c:
                continue
            for key, val in centers_idx.items():
                if c.lower() in key.lower() or key.lower() in c.lower():
                    open_part = re.search(r'\*\*Открытый[^\*]*\*\*[:\s]*(.*?)(?=\*\*|$)', val, re.DOTALL)
                    text = open_part.group(1).strip()[:350] if open_part else val[:350]
                    undef_texts.append(f"Открытый центр {c}: {text}")
                    break
    if undef_texts:
        sections.append("=== ОТКРЫТЫЕ ЦЕНТРЫ (уязвимости и потенциальная мудрость) ===\n" + '\n\n'.join(undef_texts))

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
