#!/usr/bin/env python3
"""
Astrology + Human Design MCP Server
Протокол: stdio JSON-RPC 2.0 (совместим с Claude Code)
Зависимости: pyswisseph  (pip install pyswisseph)
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
import json
import math
import ctypes
import glob
import re
import itertools

# Preload libsqlite3 so pyswisseph can find it regardless of ldconfig state
for _pattern in ['/usr/lib/*/libsqlite3.so.0', '/usr/lib/libsqlite3.so.0',
                 '/lib/*/libsqlite3.so.0', '/usr/local/lib/libsqlite3.so.0']:
    for _path in glob.glob(_pattern):
        try:
            ctypes.CDLL(_path)
            break
        except Exception:
            pass

import swisseph as swe

# ═══════════════════════════════════════════════════════════════════════════════
#  ДАННЫЕ — АСТРОЛОГИЯ
# ═══════════════════════════════════════════════════════════════════════════════

SIGNS_RU = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
            "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"]

NAKSHATRAS = [
    ("Ашвини",0),("Бхарани",13.333),("Криттика",26.667),
    ("Рохини",40),("Мригашира",53.333),("Ардра",66.667),
    ("Пунарвасу",80),("Пушья",93.333),("Ашлеша",106.667),
    ("Магха",120),("Пурва-Пхалгуни",133.333),("Уттара-Пхалгуни",146.667),
    ("Хаста",160),("Читра",173.333),("Свати",186.667),
    ("Вишакха",200),("Анурадха",213.333),("Джйештха",226.667),
    ("Мула",240),("Пурва-Ашадха",253.333),("Уттара-Ашадха",266.667),
    ("Шравана",280),("Дхаништха",293.333),("Шатабхиша",306.667),
    ("Пурва-Бхадрапада",320),("Уттара-Бхадрапада",333.333),("Ревати",346.667),
]
NAK_RULERS = (["Ке","Ве","Со","Лу","Ма","Ра","Юп","Са","Ме"]*3)

PLANETS = [
    (swe.SUN,     "Солнце"),
    (swe.MOON,    "Луна"),
    (swe.MERCURY, "Меркурий"),
    (swe.VENUS,   "Венера"),
    (swe.MARS,    "Марс"),
    (swe.JUPITER, "Юпитер"),
    (swe.SATURN,  "Сатурн"),
    (swe.URANUS,  "Уран"),
    (swe.NEPTUNE, "Нептун"),
    (swe.PLUTO,   "Плутон"),
    (swe.TRUE_NODE, "С.Узел"),
]

TRADITIONAL_RULERS = {
    0: "Марс", 1: "Венера", 2: "Меркурий", 3: "Луна", 4: "Солнце", 5: "Меркурий",
    6: "Венера", 7: "Марс", 8: "Юпитер", 9: "Сатурн", 10: "Сатурн", 11: "Юпитер",
}

# ═══════════════════════════════════════════════════════════════════════════════
#  ДАННЫЕ — ДИЗАЙН ЧЕЛОВЕКА
# ═══════════════════════════════════════════════════════════════════════════════

# Ворота в порядке градусов тропического зодиака (каждые 5.625°)
# Стандартная карта Мандалы Дизайна Человека
HD_GATES_BY_DEGREE = [
    25,17,21,51,42,3,27,24,2,23,8,20,16,35,45,12,15,52,39,53,
    62,56,31,33,7,4,29,59,40,64,47,6,46,18,48,57,32,50,28,44,
    1,43,14,34,9,5,26,11,10,58,38,54,61,60,41,19,13,49,30,55,
    37,63,22,36,
]

# Каналы: (ворота_A, ворота_B) — все 36 каналов
CHANNELS = [
    (1,8),(2,14),(3,60),(4,63),(5,15),(6,59),(7,31),(9,52),(10,20),
    # Три интеграционных канала, которых не хватало в прежней таблице.
    (10,34),(10,57),(34,57),
    (11,56),(12,22),(13,33),(14,2),(15,5),(16,48),(17,62),(18,58),
    (19,49),(20,10),(21,45),(22,12),(23,43),(24,61),(25,51),(26,44),
    (27,50),(28,38),(29,46),(30,41),(31,7),(32,54),(33,13),(34,20),
    (35,36),(36,35),(37,40),(38,28),(39,55),(40,37),(41,30),(42,53),
    (43,23),(44,26),(45,21),(46,29),(47,64),(48,16),(49,19),(50,27),
    (51,25),(52,9),(53,42),(54,32),(55,39),(56,11),(57,20),(58,18),
    (59,6),(60,3),(61,24),(62,17),(63,4),(64,47),
]

# Центры и их ворота
CENTERS = {
    "Голова":    [64,61,63],
    "Аджна":     [47,24,4,17,43,11],
    "Горло":     [62,23,56,35,12,45,33,8,20,31,16],
    # G-центр: 1, 2, 10, 13, 15, 25, 46, 7.
    "Я/Самость": [1,2,10,13,15,25,46,7],
    "Эго":       [21,40,26,51],
    "Сакральный":[5,14,29,59,9,3,42,27,34],
    "Селезёнка": [48,57,32,28,18,50,44],
    "Солнечное сплетение": [6,37,22,36,30,55,49],
    "Корень":    [53,60,52,58,38,54,19,41,39],
}

# Тип по определённым центрам
def get_type(defined_centers, defined_channels):
    has_sacral   = "Сакральный" in defined_centers
    has_throat   = "Горло" in defined_centers
    has_ego      = "Эго" in defined_centers
    has_sp       = "Солнечное сплетение" in defined_centers
    has_identity = "Я/Самость" in defined_centers

    # Мотор к Горлу?
    motor_centers = {"Эго", "Сакральный", "Солнечное сплетение", "Корень"}
    motor_to_throat = any(
        (a_gate in CENTERS.get(m,"") or b_gate in CENTERS.get(m,""))
        and (a_gate in CENTERS["Горло"] or b_gate in CENTERS["Горло"])
        for m in motor_centers
        for (a_gate, b_gate) in defined_channels
        if m in defined_centers
    )

    if not has_sacral and not has_throat and not has_ego and not has_sp:
        return "Рефлектор"
    if not has_sacral:
        if motor_to_throat:
            return "Манифестор"
        return "Проектор"
    if motor_to_throat and not has_sacral:
        return "Манифестор"
    if has_sacral and motor_to_throat:
        return "Манифестирующий Генератор"
    if has_sacral:
        return "Генератор"
    return "Манифестор"

# Авторитет
def get_authority(defined_centers, defined_channels=None, hd_type=None):
    if "Солнечное сплетение" in defined_centers:
        return "Эмоциональный"
    if "Сакральный" in defined_centers:
        return "Сакральный"
    if "Селезёнка" in defined_centers:
        return "Селезёночный"
    if "Эго" in defined_centers:
        # Эго-авторитет возможен только при канале воли: 21-45 или 25-51.
        if defined_channels and any(set(ch) in ({21, 45}, {25, 51}) for ch in defined_channels):
            return "Эго"
    if "Я/Самость" in defined_centers:
        # Самопроецируемый авторитет — только при реальной связи G-центра с
        # Горлом, а не просто при определённых центрах.
        g_throat = {1, 8, 7, 31, 10, 20, 13, 33}
        if defined_channels and any(set(ch).issubset(g_throat) and
                                    any(g in {1, 7, 10, 13} for g in ch) and
                                    any(g in {8, 31, 20, 33} for g in ch)
                                    for ch in defined_channels):
            return "Я/Самость"
    # У Проектора без внутреннего авторитета решение принимается через
    # обсуждение и среду; это не тот же механизм, что самопроецирование.
    if hd_type == "Проектор" and any(c in defined_centers for c in ("Голова", "Аджна", "Горло")):
        return "Внешний / ментальный"
    return "Лунный / нет внутреннего авторитета"

# ═══════════════════════════════════════════════════════════════════════════════
#  ВЫЧИСЛЕНИЯ
# ═══════════════════════════════════════════════════════════════════════════════

def deg_to_sign(deg):
    deg = deg % 360
    idx = int(deg / 30)
    pos = deg % 30
    d = int(pos)
    m = int((pos - d) * 60)
    return SIGNS_RU[idx], d, m, idx

def get_nakshatra(sid_deg):
    deg = sid_deg % 360
    for i in range(26, -1, -1):
        if deg >= NAKSHATRAS[i][1]:
            pos = deg - NAKSHATRAS[i][1]
            pada = min(int(pos / (13.333/4)) + 1, 4)
            return NAKSHATRAS[i][0], NAK_RULERS[i], pada
    return NAKSHATRAS[0][0], NAK_RULERS[0], 1

def deg_to_gate_substructure(trop_deg):
    """Вернуть ворота и вложенную дуговую структуру Gate.Line.Color.Tone.Base.

    Ворота делят колесо на 64 сектора, внутри ворот идут 6 линий, внутри
    каждой линии — 6 цветов, 6 тонов и 5 баз. Это отдельные уровни измерения;
    ни один из них не подменяет другой.
    """
    HD_OFFSET = 1.75
    deg = (trop_deg + HD_OFFSET) % 360
    gate_width = 360.0 / 64.0
    line_width = gate_width / 6.0
    color_width = line_width / 6.0
    tone_width = color_width / 6.0
    base_width = tone_width / 5.0

    idx = min(int(deg / gate_width), 63)
    gate = HD_GATES_BY_DEGREE[idx]
    pos_in_gate = max(0.0, deg - idx * gate_width)
    line = min(int(pos_in_gate / line_width) + 1, 6)
    pos_in_line = pos_in_gate - (line - 1) * line_width
    color = min(int(pos_in_line / color_width) + 1, 6)
    pos_in_color = pos_in_line - (color - 1) * color_width
    tone = min(int(pos_in_color / tone_width) + 1, 6)
    pos_in_tone = pos_in_color - (tone - 1) * tone_width
    base = min(int(pos_in_tone / base_width) + 1, 5)
    return gate, line, color, tone, base


def deg_to_gate_line(trop_deg):
    """Совместимый короткий вызов: только ворота и линия."""
    gate, line, _color, _tone, _base = deg_to_gate_substructure(trop_deg)
    return gate, line

def birth_to_jd(year, month, day, hour, minute, tz):
    ut = hour - tz + minute/60.0
    # Handle day rollover
    d = day
    if ut < 0:
        ut += 24
        d -= 1
    elif ut >= 24:
        ut -= 24
        d += 1
    return swe.julday(year, month, d, ut)

def calc_planets(jd, sidereal=False):
    flags = swe.FLG_SWIEPH | swe.FLG_SPEED
    if sidereal:
        flags |= swe.FLG_SIDEREAL
    result = {}
    for pid, pname in PLANETS:
        r = swe.calc_ut(jd, pid, flags)
        result[pname] = {"lon": r[0][0], "speed": r[0][3], "retro": r[0][3] < 0}
    return result


def angular_difference(target, current):
    """Кратчайшая разница долгот в диапазоне -180..180 градусов."""
    return (target - current + 180.0) % 360.0 - 180.0


def find_longitude_return(body_id, target_lon, jd_start, tolerance=1e-7, max_iter=80):
    """Найти момент возвращения тела к заданной эклиптической долготе.

    В отличие от грубого правила «один градус = один день» использует
    мгновенную скорость тела из Swiss Ephemeris. Это существенно для Луны и
    убирает систематическую ошибку соляра около 0.2 градуса.
    """
    jd = float(jd_start)
    flags = swe.FLG_SWIEPH | swe.FLG_SPEED
    for _ in range(max_iter):
        values = swe.calc_ut(jd, body_id, flags)[0]
        current = values[0]
        diff = angular_difference(target_lon, current)
        if abs(diff) <= tolerance:
            return jd
        speed = values[3]
        if abs(speed) < 1e-6:
            # Запасной шаг для точки почти остановки; для Солнца и Луны
            # обычно не используется, но не даёт деления на ноль.
            speed = 1.0 if body_id == swe.SUN else 13.2
        step = diff / speed
        # Не позволяем Ньютону перескочить через соседний цикл.
        step = max(-10.0, min(10.0, step))
        jd += step
    return jd

def calc_houses(jd, lat, lon):
    cusps, ascmc = swe.houses(jd, lat, lon, b'P')
    return cusps, ascmc[0], ascmc[1]  # cusps, ASC, MC

def get_house_num(lon: float, cusps: tuple) -> int:
    """Возвращает номер дома (1-12) для данной долготы."""
    lon = lon % 360
    for i in range(12):
        cusp_start = cusps[i] % 360
        cusp_end = cusps[(i + 1) % 12] % 360
        if cusp_start <= cusp_end:
            if cusp_start <= lon < cusp_end:
                return i + 1
        else:  # перекрытие 0°
            if lon >= cusp_start or lon < cusp_end:
                return i + 1
    return 1


MAJOR_ASPECTS = (
    (0, "соединение", 8), (60, "секстиль", 5), (90, "квадрат", 6),
    (120, "трин", 7), (150, "квинконс", 3), (180, "оппозиция", 7),
)

def compute_aspects(planets: dict) -> list[tuple[str, str, str, float]]:
    """Детерминированные мажорные аспекты между натальными планетами."""
    names = list(planets)
    result = []
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            diff = abs(planets[first]["lon"] - planets[second]["lon"]) % 360
            diff = min(diff, 360 - diff)
            matches = [(name, orb, abs(diff - degree))
                       for degree, name, orb in MAJOR_ASPECTS
                       if abs(diff - degree) <= orb]
            if matches:
                aspect, _orb, delta = min(matches, key=lambda item: item[2])
                result.append((first, second, aspect, round(delta, 2)))
    return result

# ═══════════════════════════════════════════════════════════════════════════════
#  ИНСТРУМЕНТЫ MCP
# ═══════════════════════════════════════════════════════════════════════════════

def tool_natal_chart(args):
    """Полная натальная карта: Западная + Джйотиш"""
    year   = int(args["year"])
    month  = int(args["month"])
    day    = int(args["day"])
    hour   = int(args["hour"])
    minute = int(args.get("minute", 0))
    tz     = float(args["timezone"])
    lat    = float(args["lat"])
    lon    = float(args["lon"])
    name   = args.get("name", "")

    swe.set_sid_mode(swe.SIDM_LAHIRI)
    jd = birth_to_jd(year, month, day, hour, minute, tz)
    ayanamsha = swe.get_ayanamsa_ut(jd)

    trop = calc_planets(jd, sidereal=False)
    sid  = calc_planets(jd, sidereal=True)
    cusps, asc, mc = calc_houses(jd, lat, lon)

    lines = []
    if name:
        lines.append(f"═══ НАТАЛЬНАЯ КАРТА: {name} ═══")
    lines.append(f"Дата: {day:02d}.{month:02d}.{year}  {hour:02d}:{minute:02d}  UTC{tz:+.0f}")
    lines.append(f"Координаты: {lat:.4f}°N  {lon:.4f}°E")
    lines.append("")

    # Западная
    lines.append("── ЗАПАДНАЯ КАРТА (Тропический) ──")
    asc_sign, ad, am, _ = deg_to_sign(asc)
    mc_sign, md, mm, _  = deg_to_sign(mc)
    lines.append(f"АСЦ: {asc_sign} {ad}°{am:02d}'   МС: {mc_sign} {md}°{mm:02d}'")
    lines.append(f"{'Планета':<12} {'Знак':<13} {'Градус':<10} {'Дом':<5} R")
    lines.append("─"*48)
    for pname, pdata in trop.items():
        sign, d, m, _ = deg_to_sign(pdata["lon"])
        house = get_house_num(pdata["lon"], cusps)
        r = "℞" if pdata["retro"] else ""
        lines.append(f"{pname:<12} {sign:<13} {d:2d}°{m:02d}'     {house:<5} {r}")
    # Лилит (Чёрная Луна) и Хирон — добавляем отдельно с обработкой отсутствия файлов
    try:
        lilith_r = swe.calc_ut(jd, swe.MEAN_APOG, swe.FLG_SWIEPH)[0]
        lilith_lon = lilith_r[0]
        l_sign, l_d, l_m, _ = deg_to_sign(lilith_lon)
        l_house = get_house_num(lilith_lon, cusps)
        lines.append(f"{'Лилит':<12} {l_sign:<13} {l_d:2d}°{l_m:02d}'     {l_house:<5} ")
    except Exception:
        pass
    # Южный узел — точная противоположность Северному узлу.
    if "С.Узел" in trop:
        south_lon = (trop["С.Узел"]["lon"] + 180) % 360
        s_sign, s_d, s_m, _ = deg_to_sign(south_lon)
        s_house = get_house_num(south_lon, cusps)
        lines.append(f"{'Ю.Узел':<12} {s_sign:<13} {s_d:2d}°{s_m:02d}'     {s_house:<5}")

    lines.append("")
    lines.append("АСПЕКТЫ (мажорные, с орбисом):")
    aspects = compute_aspects(trop)
    if aspects:
        for first, second, aspect, delta in aspects:
            lines.append(f"  {first} — {second}: {aspect} (отклонение {delta:.2f}°)")
    else:
        lines.append("  нет аспектов в заданных орбисах")
    try:
        chiron_r = swe.calc_ut(jd, swe.CHIRON, swe.FLG_SWIEPH)[0]
        ch_lon = chiron_r[0]
        ch_sign, ch_d, ch_m, _ = deg_to_sign(ch_lon)
        ch_house = get_house_num(ch_lon, cusps)
        lines.append(f"{'Хирон':<12} {ch_sign:<13} {ch_d:2d}°{ch_m:02d}'     {ch_house:<5} ")
    except Exception:
        pass
    # Куспиды ключевых домов (отношения + призвание)
    lines.append("")
    lines.append("ДОМА (Плацидус, все 12):")
    for h_num in range(1, 13):
        sign, d, m, _ = deg_to_sign(cusps[h_num - 1])
        sign_idx = int((cusps[h_num - 1] % 360) // 30)
        ruler = TRADITIONAL_RULERS[sign_idx]
        # Планеты в этом доме
        planets_in = [pn for pn, pd in trop.items() if get_house_num(pd["lon"], cusps) == h_num]
        extra = f" (планеты: {', '.join(planets_in)})" if planets_in else ""
        lines.append(f"  {h_num}-й дом: {sign} {d}°{m:02d}' | традиционный управитель: {ruler}{extra}")

    lines.append("")
    lines.append(f"── ДЖЙОТИШ (Сидерический, Лахири, айанамша {ayanamsha:.2f}°) ──")
    sid_asc = (asc - ayanamsha) % 360
    lag_sign, ld, lm, _ = deg_to_sign(sid_asc)
    lag_nak, lag_ruler, lag_pada = get_nakshatra(sid_asc)
    lines.append(f"ЛАГНА: {lag_sign} {ld}°{lm:02d}' | {lag_nak} пада {lag_pada} (упр. {lag_ruler})")
    lines.append(f"{'Планета':<12} {'Знак':<13} {'Градус':<10} {'Накшатра':<18} Пада  R")
    lines.append("─"*62)
    for pname, pdata in sid.items():
        sign, d, m, _ = deg_to_sign(pdata["lon"])
        nak, ruler, pada = get_nakshatra(pdata["lon"])
        r = "℞" if pdata["retro"] else ""
        lines.append(f"{pname:<12} {sign:<13} {d:2d}°{m:02d}'     {nak:<18} {pada}     {r}")

    return "\n".join(lines)


def tool_human_design(args):
    """Дизайн Человека: Тип, Авторитет, Профиль, Центры, Ворота, Каналы"""
    year   = int(args["year"])
    month  = int(args["month"])
    day    = int(args["day"])
    hour   = int(args["hour"])
    minute = int(args.get("minute", 0))
    tz     = float(args["timezone"])
    lat    = float(args.get("lat", 55.75))
    lon    = float(args.get("lon", 37.58))
    name   = args.get("name", "")

    swe.set_sid_mode(swe.SIDM_LAHIRI)
    jd_conscious = birth_to_jd(year, month, day, hour, minute, tz)

    # Бессознательная точка: Солнце на 88° раньше
    # Ищем JD когда Солнце было на 88° меньше
    sun_now = swe.calc_ut(jd_conscious, swe.SUN, swe.FLG_SWIEPH)[0][0]
    sun_target = (sun_now - 88.0) % 360
    # Приблизительно: 88° ~ 88 дней
    jd_unconscious = jd_conscious - 88.0

    # Точный JD для бессознательного: Солнце было ровно на 88° раньше
    sun_con = swe.calc_ut(jd_conscious, swe.SUN, swe.FLG_SWIEPH)[0][0]
    sun_target = (sun_con - 88.0) % 360
    jd_unconscious = jd_conscious - 88.0
    for _ in range(10):
        s = swe.calc_ut(jd_unconscious, swe.SUN, swe.FLG_SWIEPH)[0][0]
        jd_unconscious -= (s - sun_target + 180) % 360 - 180

    # Сознательные позиции (рождение)
    con_planets = calc_planets(jd_conscious, sidereal=False)
    # Бессознательные позиции (Дизайн)
    unc_planets = calc_planets(jd_unconscious, sidereal=False)

    # Вычислить ворота и линии
    # Включаем Землю (оппозиция Солнцу) и Южный Узел (оппозиция С.Узлу)
    def planets_to_gates(planet_dict):
        result = {}
        for pname, pdata in planet_dict.items():
            gate, line, color, tone, base = deg_to_gate_substructure(pdata["lon"])
            result[pname] = {
                "gate": gate, "line": line, "color": color,
                "tone": tone, "base": base, "lon": pdata["lon"]
            }
            if pname == "Солнце":
                earth_lon = (pdata["lon"] + 180) % 360
                eg, el, ec, et, eb = deg_to_gate_substructure(earth_lon)
                result["Земля"] = {
                    "gate": eg, "line": el, "color": ec,
                    "tone": et, "base": eb, "lon": earth_lon
                }
            if pname == "С.Узел":
                sn_lon = (pdata["lon"] + 180) % 360
                sg, sl, sc, st, sb = deg_to_gate_substructure(sn_lon)
                result["Ю.Узел"] = {
                    "gate": sg, "line": sl, "color": sc,
                    "tone": st, "base": sb, "lon": sn_lon
                }
        return result

    con_gates = planets_to_gates(con_planets)
    unc_gates = planets_to_gates(unc_planets)

    # Профиль: линия Солнца сознательного + линия Солнца бессознательного
    con_sun_line = con_gates["Солнце"]["line"]
    unc_sun_line = unc_gates["Солнце"]["line"]
    profile = f"{con_sun_line}/{unc_sun_line}"

    PROFILE_NAMES = {
        "1/3":"Следователь/Мученик", "1/4":"Следователь/Оппортунист",
        "2/4":"Отшельник/Оппортунист", "2/5":"Отшельник/Еретик",
        "3/5":"Мученик/Еретик", "3/6":"Мученик/Образец для подражания",
        "4/6":"Оппортунист/Образец", "4/1":"Оппортунист/Следователь",
        "5/1":"Еретик/Следователь", "5/2":"Еретик/Отшельник",
        "6/2":"Образец/Отшельник", "6/3":"Образец/Мученик",
    }
    profile_name = PROFILE_NAMES.get(profile, profile)

    # Все активные ворота
    all_gates = set()
    for pg in con_gates.values():
        all_gates.add(pg["gate"])
    for pg in unc_gates.values():
        all_gates.add(pg["gate"])

    # Определённые каналы
    defined_channels = []
    for (g1, g2) in CHANNELS:
        if g1 in all_gates and g2 in all_gates:
            if (g1, g2) not in defined_channels and (g2, g1) not in defined_channels:
                defined_channels.append((g1, g2))

    # Определённые центры: каждый канал соединяет ДВА разных центра
    gate_to_center = {}
    for center, gates in CENTERS.items():
        for g in gates:
            gate_to_center[g] = center

    defined_centers = []
    for (g1, g2) in defined_channels:
        for g in (g1, g2):
            c = gate_to_center.get(g)
            if c and c not in defined_centers:
                defined_centers.append(c)

    hd_type = get_type(defined_centers, defined_channels)
    authority = get_authority(defined_centers, defined_channels, hd_type)

    # Стратегия по типу
    STRATEGY = {
        "Генератор": "Ждать и отвечать (Сакральный да/нет)",
        "Манифестирующий Генератор": "Ждать и отвечать, затем информировать",
        "Манифестор": "Информировать перед действием",
        "Проектор": "Ждать приглашения",
        "Рефлектор": "Ждать лунный цикл (28 дней)",
    }
    NOT_SELF = {
        "Генератор": "Фрустрация",
        "Манифестирующий Генератор": "Фрустрация и злость",
        "Манифестор": "Злость",
        "Проектор": "Горечь",
        "Рефлектор": "Разочарование",
    }

    lines = []
    if name:
        lines.append(f"═══ ДИЗАЙН ЧЕЛОВЕКА: {name} ═══")
    lines.append(f"Дата: {day:02d}.{month:02d}.{year}  {hour:02d}:{minute:02d}  UTC{tz:+.0f}")
    lines.append("")
    lines.append(f"ТИП:         {hd_type}")
    lines.append(f"СТРАТЕГИЯ:   {STRATEGY.get(hd_type,'—')}")
    lines.append(f"АВТОРИТЕТ:   {authority}")
    lines.append(f"НЕ-Я ТЕМА:  {NOT_SELF.get(hd_type,'—')}")
    lines.append(f"ПРОФИЛЬ:     {profile} — {profile_name}")
    lines.append("")

    # Линия и субструктура уже выведены раздельно в формате
    # Gate.Line.Color.Tone.Base. Сводка четырёх трансформаций строится в
    # hd_library.py, чтобы не смешивать её с базовым расчётом карты.

    lines.append(f"ОПРЕДЕЛЁННЫЕ ЦЕНТРЫ ({len(defined_centers)}):")
    lines.append("  " + ", ".join(defined_centers) if defined_centers else "  нет")
    lines.append("")
    # В HD «есть активация, но нет полного канала» и «нет активаций вообще» —
    # разные состояния. Не смешиваем их: это меняет смысл интерпретации.
    undefined = [c for c, gates in CENTERS.items()
                 if c not in defined_centers and any(g in all_gates for g in gates)]
    open_centers = [c for c in CENTERS if c not in defined_centers and c not in undefined]
    lines.append("НЕОПРЕДЕЛЁННЫЕ ЦЕНТРЫ (есть отдельные активации, но нет полного канала):")
    lines.append("  " + ", ".join(undefined) if undefined else "  нет")
    lines.append("")
    lines.append("ОТКРЫТЫЕ ЦЕНТРЫ (нет активированных ворот):")
    lines.append("  " + ", ".join(open_centers) if open_centers else "  нет")
    lines.append("")
    lines.append(f"КАНАЛЫ ({len(defined_channels)}):")
    for ch in defined_channels:
        lines.append(f"  {ch[0]}-{ch[1]}")
    lines.append("")
    # Крест воплощения (4 оси)
    ps_gate = con_gates.get("Солнце", {})
    pe_gate = con_gates.get("Земля", {})
    ds_gate = unc_gates.get("Солнце", {})
    de_gate = unc_gates.get("Земля", {})
    if ps_gate and pe_gate and ds_gate and de_gate:
        lines.append("КРЕСТ ВОПЛОЩЕНИЯ (тема жизни):")
        lines.append(f"  Ось Личности:  Солнце {ps_gate['gate']}.{ps_gate['line']} ↔ Земля {pe_gate['gate']}.{pe_gate['line']}")
        lines.append(f"  Ось Дизайна:   Солнце {ds_gate['gate']}.{ds_gate['line']} ↔ Земля {de_gate['gate']}.{de_gate['line']}")
        # Определяем четверть (Initiation/Civilization/Duality/Mutation)
        ps_num = ps_gate['gate']
        quarters = {
            "Инициации":     [13,49,30,55,37,63,22,36,25,17,21,51,42,3,27,24],
            "Цивилизации":   [2,23,8,20,16,35,45,12,15,52,39,53,62,56,31,33],
            "Двойственности":[7,4,29,59,40,64,47,6,46,18,48,57,32,50,28,44],
            "Мутации":       [1,43,14,34,9,5,26,11,10,58,38,54,61,60,41,19],
        }
        cross_quarter = "—"
        for qname, gates_list in quarters.items():
            if ps_num in gates_list:
                cross_quarter = qname
                break
        lines.append(f"  Четверть:      {cross_quarter}")
    lines.append("")
    lines.append("СОЗНАТЕЛЬНЫЕ ВОРОТА (личность — чёрный):")
    for pname, pg in con_gates.items():
        sign, d, m, _ = deg_to_sign(pg["lon"])
        lines.append(
            f"  {pname:<12} Ворота {pg['gate']:2d}.{pg['line']}.{pg['color']}.{pg['tone']}.{pg['base']}   "
            f"{sign} {d}°{m:02d}'"
        )
    lines.append("")
    lines.append("БЕССОЗНАТЕЛЬНЫЕ ВОРОТА (дизайн — красный, ~88° до рождения):")
    for pname, pg in unc_gates.items():
        sign, d, m, _ = deg_to_sign(pg["lon"])
        lines.append(
            f"  {pname:<12} Ворота {pg['gate']:2d}.{pg['line']}.{pg['color']}.{pg['tone']}.{pg['base']}   "
            f"{sign} {d}°{m:02d}'"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  СОВМЕСТИМОСТЬ HD
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_hd_raw(raw: str) -> dict:
    """Извлечь из текста HD только расчётные факты для составной карты.

    Важно: совместимость нельзя поручать модели, передавая ей два сырых текста
    и ожидая, что она сама не перепутает каналы и направления. Здесь сначала
    строится составная карта по множествам ворот, а уже затем результат можно
    переводить в человеческий язык.
    """
    raw = raw or ""

    def one(pattern, default="—"):
        match = re.search(pattern, raw, re.IGNORECASE)
        return match.group(1).strip() if match else default

    def section(start, end=None):
        pattern = rf"{start}.*?:\n(.*?)(?={end}|$)" if end else rf"{start}.*?:\n(.*)$"
        match = re.search(pattern, raw, re.IGNORECASE | re.DOTALL)
        return match.group(1) if match else ""

    conscious = section("СОЗНАТЕЛЬНЫЕ ВОРОТА", "БЕССОЗНАТЕЛЬНЫЕ ВОРОТА")
    design = section("БЕССОЗНАТЕЛЬНЫЕ ВОРОТА")
    gate_pattern = re.compile(
        r"(Солнце|Земля|Луна|Меркурий|Венера|Марс|Юпитер|Сатурн|Уран|Нептун|Плутон|С\.Узел|Ю\.Узел)"
        r"\s+Ворота\s+(\d+)\.(\d+)(?:\.(\d+))?",
        re.IGNORECASE,
    )

    activations = []
    for side, text in (("личность", conscious), ("дизайн тела", design)):
        for match in gate_pattern.finditer(text):
            activations.append({
                "planet": match.group(1),
                "gate": int(match.group(2)),
                "line": int(match.group(3)),
                "side": side,
            })

    gates = {item["gate"] for item in activations}
    channel_pattern = re.compile(r"^\s*(\d+)\s*-\s*(\d+)", re.MULTILINE)
    channel_pairs = set()
    channels_section = section("КАНАЛЫ", "КРЕСТ ВОПЛОЩЕНИЯ")
    if not channels_section:
        channels_section = section("КАНАЛЫ", "СОЗНАТЕЛЬНЫЕ ВОРОТА")
    for match in channel_pattern.finditer(channels_section):
        channel_pairs.add(frozenset((int(match.group(1)), int(match.group(2)))))

    return {
        "type": one(r"ТИП:\s*(.+)"),
        "authority": one(r"АВТОРИТЕТ:\s*(.+)"),
        "profile": one(r"ПРОФИЛЬ:\s*(.+)"),
        "gates": gates,
        "channels": channel_pairs,
        "activations": activations,
    }


def _channel_label(channel: frozenset) -> str:
    """Каналы выводим в одном порядке, независимо от стороны в исходном тексте."""
    a, b = sorted(channel)
    return f"{a}-{b}"


def _channels_from_gate_set(gates: set[int]) -> set[frozenset]:
    unique = {frozenset(pair) for pair in CHANNELS if len(set(pair)) == 2}
    return {channel for channel in unique if channel.issubset(gates)}


def _center_map() -> dict[int, str]:
    return {gate: center for center, gates in CENTERS.items() for gate in gates}


def _centers_from_channels(channels: set[frozenset]) -> set[str]:
    gate_to_center = _center_map()
    centers = set()
    for channel in channels:
        for gate in channel:
            if gate in gate_to_center:
                centers.add(gate_to_center[gate])
    return centers


def build_hd_compatibility(hd_a_raw: str, hd_b_raw: str,
                           name_a: str = "Человек 1", name_b: str = "Человек 2") -> str:
    """Рассчитать составную HD-карту двух людей.

    Категории следуют механике connection chart: общие каналы, электромагнитное
    соединение, компромисс и доминирование. Функция ничего не интерпретирует
    психологически и не достраивает отсутствующие ворота.
    """
    a = _parse_hd_raw(hd_a_raw)
    b = _parse_hd_raw(hd_b_raw)
    channels_a = a["channels"] or _channels_from_gate_set(a["gates"])
    channels_b = b["channels"] or _channels_from_gate_set(b["gates"])
    all_channels = {frozenset(pair) for pair in CHANNELS if len(set(pair)) == 2}

    companionship = sorted(channels_a & channels_b, key=lambda c: tuple(sorted(c)))
    electromagnetic = []
    compromise_a = []
    compromise_b = []
    dominance_a = []
    dominance_b = []

    for channel in all_channels:
        endpoints = set(channel)
        a_has = len(a["gates"] & endpoints)
        b_has = len(b["gates"] & endpoints)
        a_full = channel in channels_a
        b_full = channel in channels_b

        if not a_full and not b_full and a_has == 1 and b_has == 1 and a["gates"] != b["gates"]:
            # Проверяем именно разделение концов, а не ситуацию, когда оба
            # человека принесли один и тот же конец канала.
            if len((a["gates"] & endpoints) | (b["gates"] & endpoints)) == 2:
                electromagnetic.append(channel)
        elif a_full and not b_full and b_has == 1:
            compromise_a.append(channel)
        elif b_full and not a_full and a_has == 1:
            compromise_b.append(channel)
        elif a_full and not b_full and b_has == 0:
            dominance_a.append(channel)
        elif b_full and not a_full and a_has == 0:
            dominance_b.append(channel)

    combined_gates = a["gates"] | b["gates"]
    combined_channels = _channels_from_gate_set(combined_gates)
    combined_centers = _centers_from_channels(combined_channels)
    all_centers = set(CENTERS)

    def labels(items):
        return ", ".join(_channel_label(item) for item in items) if items else "нет"

    def activation_label(item):
        return f"{item['planet']} {item['gate']}.{item['line']} ({item['side']})"

    lines = [
        "=== СОСТАВНАЯ КАРТА СОВМЕСТИМОСТИ HD (РАСЧЁТ) ===",
        "Правило: канал учитывается только если оба его конца реально активированы;"
        " психологический смысл не вычисляется автоматически.",
        "",
        f"{name_a}: тип — {a['type']}; авторитет — {a['authority']}; профиль — {a['profile']}",
        f"{name_b}: тип — {b['type']}; авторитет — {b['authority']}; профиль — {b['profile']}",
        "",
        "ОБЩИЕ КАНАЛЫ (у обоих уже собран целиком):",
        f"  {labels(companionship)}",
        "ЭЛЕКТРОМАГНИТНЫЕ СОЕДИНЕНИЯ (по одному концу у каждого):",
        f"  {labels(sorted(electromagnetic, key=lambda c: tuple(sorted(c))))}",
        f"КОМПРОМИССЫ — канал собран у {name_a}, второй человек приносит один конец:",
        f"  {labels(sorted(compromise_a, key=lambda c: tuple(sorted(c))))}",
        f"КОМПРОМИССЫ — канал собран у {name_b}, первый человек приносит один конец:",
        f"  {labels(sorted(compromise_b, key=lambda c: tuple(sorted(c))))}",
        f"ДОМИНИРОВАНИЕ — канал собран у {name_a}, у второго нет его концов:",
        f"  {labels(sorted(dominance_a, key=lambda c: tuple(sorted(c))))}",
        f"ДОМИНИРОВАНИЕ — канал собран у {name_b}, у первого нет его концов:",
        f"  {labels(sorted(dominance_b, key=lambda c: tuple(sorted(c))))}",
        "",
        "СОСТАВНЫЕ КАНАЛЫ (все каналы, которые образуются в поле пары):",
        f"  {labels(sorted(combined_channels, key=lambda c: tuple(sorted(c))))}",
        "ОПРЕДЕЛЁННЫЕ ЦЕНТРЫ СОСТАВНОЙ КАРТЫ:",
        "  " + ", ".join(sorted(combined_centers)) if combined_centers else "  нет",
        "ЦЕНТРЫ, КОТОРЫЕ В СОСТАВНОЙ КАРТЕ НЕ СОБРАНЫ:",
        "  " + ", ".join(sorted(all_centers - combined_centers)) if all_centers - combined_centers else "  нет",
        "",
        "АКТИВАЦИИ, УЧАСТВУЮЩИЕ В СОЕДИНЕНИЯХ:",
    ]

    for channel in sorted(set(electromagnetic + compromise_a + compromise_b), key=lambda c: tuple(sorted(c))):
        lines.append(f"  Канал {_channel_label(channel)}:")
        for person, parsed in ((name_a, a), (name_b, b)):
            endpoint_items = [item for item in parsed["activations"] if item["gate"] in channel]
            if endpoint_items:
                lines.append(f"    {person}: " + "; ".join(activation_label(item) for item in endpoint_items))

    lines.extend([
        "",
        "ОГРАНИЧЕНИЯ РАСЧЁТА:",
        "  Это механический слой connection chart. Смысл каналов, ворот, линий,"
        " профилей и типов нужно брать из проверенной библиотеки отдельно для каждого человека.",
        "  Если время рождения неизвестно, линии и составные связи могут измениться;"
        " не выдавать такой результат как точный.",
    ])
    return "\n".join(lines)


def tool_solar_return(args):
    """Соляр: карта момента, когда Солнце возвращается в натальную позицию"""
    year   = int(args["birth_year"])
    month  = int(args["birth_month"])
    day    = int(args["birth_day"])
    hour   = int(args["birth_hour"])
    minute = int(args.get("birth_minute", 0))
    tz     = float(args["birth_timezone"])
    lat    = float(args["lat"])
    lon    = float(args["lon"])
    return_lat = float(args.get("return_lat", lat))
    return_lon = float(args.get("return_lon", lon))
    sr_year = int(args.get("return_year", __import__("datetime").datetime.utcnow().year))

    swe.set_sid_mode(swe.SIDM_LAHIRI)
    jd_natal = birth_to_jd(year, month, day, hour, minute, tz)

    # Натальная позиция Солнца
    natal_sun = swe.calc_ut(jd_natal, swe.SUN, swe.FLG_SWIEPH)[0][0]

    # Начало поиска — примерно день рождения в году соляра
    import datetime as dt
    jd_start = swe.julday(sr_year, month, day, 12.0)

    # Точный поиск: когда Солнце вернётся в натальную точку.
    jd_sr = find_longitude_return(swe.SUN, natal_sun, jd_start)

    # Планеты в момент соляра
    sr_planets = calc_planets(jd_sr, sidereal=False)
    # Дома соляра зависят от места, где человек находится в момент возврата.
    # Если место не передано отдельно, сохраняем совместимость и используем
    # место рождения.
    sr_cusps, sr_asc, sr_mc = calc_houses(jd_sr, return_lat, return_lon)

    # Конвертация JD обратно в дату
    sr_date = swe.revjul(jd_sr)
    sr_dt = f"{int(sr_date[2]):02d}.{int(sr_date[1]):02d}.{int(sr_date[0])}  {int(sr_date[3]):02d}:{int((sr_date[3]%1)*60):02d} UTC"

    lines = [f"═══ СОЛЯР {sr_year}: возвращение Солнца ═══"]
    lines.append(f"Точный момент: {sr_dt}")
    lines.append(f"Место соляра: {return_lat:.4f}°N  {return_lon:.4f}°E")
    lines.append(f"Место рождения: {lat:.4f}°N  {lon:.4f}°E")
    lines.append("")
    asc_sign, ad, am, _ = deg_to_sign(sr_asc)
    mc_sign, md, mm, _  = deg_to_sign(sr_mc)
    lines.append(f"АСЦ соляра: {asc_sign} {ad}°{am:02d}'   МС соляра: {mc_sign} {md}°{mm:02d}'")
    lines.append("")
    lines.append(f"{'Планета':<12} {'Знак':<13} {'Градус':<10} R")
    lines.append("─"*42)
    for pname, pdata in sr_planets.items():
        sign, d, m, _ = deg_to_sign(pdata["lon"])
        r = "℞" if pdata["retro"] else ""
        lines.append(f"{pname:<12} {sign:<13} {d:2d}°{m:02d}'      {r}")

    lines.append("")
    lines.append("── НАЛОЖЕНИЕ НА НАТАЛЬНУЮ КАРТУ ──")
    lines.append(f"Натальное Солнце: {deg_to_sign(natal_sun)[0]} {deg_to_sign(natal_sun)[1]}°{deg_to_sign(natal_sun)[2]:02d}'")
    natal_planets = calc_planets(jd_natal, sidereal=False)
    ASPECTS = [(0,"соединение",8),(60,"секстиль",5),(90,"квадрат",6),(120,"трин",7),(180,"оппозиция",7)]
    lines.append(f"{'Соляр-планета':<14} {'→ Натальная':<14} {'Аспект'}")
    lines.append("─"*50)
    for spname, spdata in sr_planets.items():
        for npname, npdata in natal_planets.items():
            diff = abs(spdata["lon"] - npdata["lon"]) % 360
            if diff > 180: diff = 360 - diff
            for asp_deg, asp_name, orb in ASPECTS:
                if abs(diff - asp_deg) <= orb:
                    lines.append(f"{spname:<14} {npname:<14} {asp_name} (орб {abs(diff - asp_deg):.2f}°)")
                    break

    return "\n".join(lines)


def tool_lunar_return(args):
    """Лунар: карта момента, когда Луна возвращается в натальную позицию (~каждые 27.3 дня)"""
    import datetime as dt
    year   = int(args["birth_year"])
    month  = int(args["birth_month"])
    day    = int(args["birth_day"])
    hour   = int(args["birth_hour"])
    minute = int(args.get("birth_minute", 0))
    tz     = float(args["birth_timezone"])
    lat    = float(args["lat"])
    lon    = float(args["lon"])
    return_lat = float(args.get("return_lat", lat))
    return_lon = float(args.get("return_lon", lon))

    # Дата поиска: следующий лунар от указанной даты (по умолчанию — сегодня)
    now = dt.datetime.utcnow()
    from_year  = int(args.get("from_year",  now.year))
    from_month = int(args.get("from_month", now.month))
    from_day   = int(args.get("from_day",   now.day))

    jd_natal = birth_to_jd(year, month, day, hour, minute, tz)
    natal_moon = swe.calc_ut(jd_natal, swe.MOON, swe.FLG_SWIEPH)[0][0]

    # Ищем следующий возврат Луны от заданной даты
    jd_start = swe.julday(from_year, from_month, from_day, 0.0)
    jd_lr = find_longitude_return(swe.MOON, natal_moon, jd_start, tolerance=1e-7, max_iter=120)

    lr_planets = calc_planets(jd_lr, sidereal=False)
    lr_cusps, lr_asc, lr_mc = calc_houses(jd_lr, return_lat, return_lon)

    lr_date = swe.revjul(jd_lr)
    lr_dt = f"{int(lr_date[2]):02d}.{int(lr_date[1]):02d}.{int(lr_date[0])}  {int(lr_date[3]):02d}:{int((lr_date[3]%1)*60):02d} UTC"

    lines = ["═══ ЛУНАР: возвращение Луны ═══"]
    lines.append(f"Точный момент: {lr_dt}")
    lines.append(f"Место лунара: {return_lat:.4f}°N  {return_lon:.4f}°E")
    lines.append(f"Место рождения: {lat:.4f}°N  {lon:.4f}°E")
    lines.append(f"Натальная Луна: {deg_to_sign(natal_moon)[0]} {deg_to_sign(natal_moon)[1]}°{deg_to_sign(natal_moon)[2]:02d}'")
    lines.append("")
    asc_sign, ad, am, _ = deg_to_sign(lr_asc)
    mc_sign, md, mm, _  = deg_to_sign(lr_mc)
    lines.append(f"АСЦ лунара: {asc_sign} {ad}°{am:02d}'   МС лунара: {mc_sign} {md}°{mm:02d}'")
    lines.append("")
    lines.append(f"{'Планета':<12} {'Знак':<13} {'Градус':<10} R")
    lines.append("─"*42)
    for pname, pdata in lr_planets.items():
        sign, d, m, _ = deg_to_sign(pdata["lon"])
        r = "℞" if pdata["retro"] else ""
        lines.append(f"{pname:<12} {sign:<13} {d:2d}°{m:02d}'      {r}")

    lines.append("")
    lines.append("── НАЛОЖЕНИЕ НА НАТАЛЬНУЮ КАРТУ ──")
    natal_planets = calc_planets(jd_natal, sidereal=False)
    ASPECTS = [(0,"соединение",8),(60,"секстиль",5),(90,"квадрат",6),(120,"трин",7),(180,"оппозиция",7)]
    lines.append(f"{'Лунар-планета':<14} {'→ Натальная':<14} {'Аспект'}")
    lines.append("─"*50)
    for spname, spdata in lr_planets.items():
        for npname, npdata in natal_planets.items():
            diff = abs(spdata["lon"] - npdata["lon"]) % 360
            if diff > 180: diff = 360 - diff
            for asp_deg, asp_name, orb in ASPECTS:
                if abs(diff - asp_deg) <= orb:
                    lines.append(f"{spname:<14} {npname:<14} {asp_name} (орб {abs(diff - asp_deg):.2f}°)")
                    break

    return "\n".join(lines)


def tool_hd_cycles(args):
    """HD-циклы: возвраты/оппозиции и годовой календарь транзитов.

    Официальная циклология Human Design выделяет Solar/Rave Return, Saturn
    Return, Uranus Opposition и Chiron Return. Календарь Солнца по 64 воротам
    идёт отдельным слоем и не подменяет эти возрастные циклы.
    """
    import datetime as dt
    year   = int(args["birth_year"])
    month  = int(args["birth_month"])
    day    = int(args["birth_day"])
    hour   = int(args["birth_hour"])
    minute = int(args.get("birth_minute", 0))
    tz     = float(args["birth_timezone"])

    now = dt.datetime.utcnow()
    cycle_year = int(args.get("cycle_year", now.year))

    jd_natal  = birth_to_jd(year, month, day, hour, minute, tz)
    natal_planets = calc_planets(jd_natal, sidereal=False)

    def cycle_date(body_id, target_lon, approximate_years):
        start = jd_natal + approximate_years * 365.2425
        try:
            return find_longitude_return(body_id, target_lon, start, max_iter=120)
        except Exception:
            return None

    def utc_date(jd_value):
        if jd_value is None:
            return 'не рассчитан'
        value = swe.revjul(jd_value)
        return (
            f"{int(value[2]):02d}.{int(value[1]):02d}.{int(value[0])} "
            f"{int(value[3]):02d}:{int((value[3] % 1) * 60):02d} UTC"
        )

    solar_return_jd = find_longitude_return(
        swe.SUN,
        natal_planets['Солнце']['lon'],
        swe.julday(cycle_year, month, day, 12.0),
    )
    saturn_return_jd = cycle_date(swe.SATURN, natal_planets['Сатурн']['lon'], 29.5)
    uranus_opposition_jd = cycle_date(
        swe.URANUS, (natal_planets['Уран']['lon'] + 180.0) % 360.0, 42.0
    )
    try:
        chiron_lon = swe.calc_ut(jd_natal, swe.CHIRON, swe.FLG_SWIEPH)[0][0]
        chiron_return_jd = cycle_date(swe.CHIRON, chiron_lon, 50.7)
    except Exception:
        chiron_return_jd = None

    lines = [f"═══ HD-ЦИКЛЫ {cycle_year} ═══", ""]
    lines.append("── КЛЮЧЕВЫЕ ЖИЗНЕННЫЕ ЦИКЛЫ ──")
    lines.append(f"Solar/Rave Return (возврат Солнца): {utc_date(solar_return_jd)}")
    lines.append(f"Saturn Return: {utc_date(saturn_return_jd)} (примерно 29–30 лет)")
    lines.append(f"Uranus Opposition: {utc_date(uranus_opposition_jd)} (примерно 42 года)")
    lines.append(f"Chiron Return: {utc_date(chiron_return_jd)} (примерно 50–51 год)")
    lines.append("")
    # Дизайнное Солнце: точно 88° до натального Солнца по эклиптике
    natal_sun_lon = swe.calc_ut(jd_natal, swe.SUN, swe.FLG_SWIEPH)[0][0]
    design_sun_lon = (natal_sun_lon - 88.0) % 360

    # Натальные ворота (Личность + Дизайн), для поиска активируемых каналов
    # Дизайнная точка: не просто «минус 88 дней», а точный момент, когда
    # Солнце было на 88° раньше. Это важно для линии ворот на границе.
    sun_target = (natal_sun_lon - 88.0) % 360
    jd_design = jd_natal - 88.0
    for _ in range(10):
        current = swe.calc_ut(jd_design, swe.SUN, swe.FLG_SWIEPH)[0][0]
        diff = (current - sun_target + 180) % 360 - 180
        jd_design -= diff / 0.9856
    natal_gates = set()
    for planet_id, _ in PLANETS:
        for jd in [jd_natal, jd_design]:
            lon = swe.calc_ut(jd, planet_id, swe.FLG_SWIEPH)[0][0]
            gate, _line = deg_to_gate_line(lon)
            natal_gates.add(gate)

    # ── Личный HD Новый год: когда транзитное Солнце возвращается в позицию Дизайнного Солнца ──
    # Ищем в диапазоне ±180 дней от 1 января cycle_year
    jd_search = swe.julday(cycle_year, 1, 1, 0.0)
    jd_hd_ny = find_longitude_return(swe.SUN, design_sun_lon, jd_search)

    hd_ny_date = swe.revjul(jd_hd_ny)
    hd_ny_str = f"{int(hd_ny_date[2]):02d}.{int(hd_ny_date[1]):02d}.{int(hd_ny_date[0])}  {int(hd_ny_date[3]):02d}:{int((hd_ny_date[3]%1)*60):02d} UTC"
    design_sign, design_d, design_m, _ = deg_to_sign(design_sun_lon)
    design_gate, _design_line = deg_to_gate_line(design_sun_lon)

    lines.append("── ВОЗВРАТ ДИЗАЙННОГО СОЛНЦА ──")
    lines.append(f"Дизайнное Солнце: {design_sign} {design_d}°{design_m:02d}'  (Ворота {design_gate})")
    lines.append(f"Транзит наступает: {hd_ny_str}")
    lines.append("Это точка где начинается твой личный HD-год — новая тема, новый импульс.")
    lines.append("")

    # ── Прохождение Солнца через 64 ворота ──
    lines.append("── СОЛНЦЕ ЧЕРЕЗ 64 ВОРОТА ──")
    lines.append("★ = Солнце замыкает канал с натальными воротами (энергетический пик)")
    lines.append("")

    jd_start = swe.julday(cycle_year, 1, 1, 0.0)
    jd_end   = swe.julday(cycle_year, 12, 31, 23.0)
    prev_gate = None
    jd = jd_start
    while jd <= jd_end:
        lon = swe.calc_ut(jd, swe.SUN, swe.FLG_SWIEPH)[0][0]
        gate, _line = deg_to_gate_line(lon)
        if gate != prev_gate:
            d = swe.revjul(jd)
            date_str = f"{int(d[2]):02d}.{int(d[1]):02d}"
            channel_note = ""
            for ga, gb in CHANNELS:
                if ga == gate and gb in natal_gates:
                    channel_note = f"  ★ канал {gate}-{gb}"
                    break
                elif gb == gate and ga in natal_gates:
                    channel_note = f"  ★ канал {ga}-{gate}"
                    break
            lines.append(f"{date_str}  Ворота {gate:>2}{channel_note}")
            prev_gate = gate
        jd += 0.25

    lines.append("")
    lines.append(f"Натальные ворота: {sorted(natal_gates)}")
    return "\n".join(lines)


def tool_transits(args):
    """Текущие транзиты относительно натальной карты"""
    # Натальная карта
    nyear  = int(args["birth_year"])
    nmonth = int(args["birth_month"])
    nday   = int(args["birth_day"])
    nhour  = int(args["birth_hour"])
    nminute= int(args.get("birth_minute", 0))
    ntz    = float(args["birth_timezone"])
    lat    = float(args["lat"])
    lon    = float(args["lon"])

    # Дата транзитов (по умолчанию сейчас)
    import datetime
    now = datetime.datetime.utcnow()
    tyear  = int(args.get("transit_year",  now.year))
    tmonth = int(args.get("transit_month", now.month))
    tday   = int(args.get("transit_day",   now.day))
    transit_hour_utc = int(args.get("transit_hour_utc", 12))
    transit_minute_utc = int(args.get("transit_minute_utc", 0))

    swe.set_sid_mode(swe.SIDM_LAHIRI)
    jd_natal   = birth_to_jd(nyear, nmonth, nday, nhour, nminute, ntz)
    jd_transit = swe.julday(tyear, tmonth, tday,
                            transit_hour_utc + transit_minute_utc / 60.0)

    natal   = calc_planets(jd_natal,   sidereal=False)
    transit = calc_planets(jd_transit, sidereal=False)
    natal_cusps, _natal_asc, _natal_mc = calc_houses(jd_natal, lat, lon)

    lines = [
        f"── ТРАНЗИТЫ на {tday:02d}.{tmonth:02d}.{tyear} "
        f"{transit_hour_utc:02d}:{transit_minute_utc:02d} UTC ──", ""
    ]
    lines.append(f"{'Транзит':<12} {'Знак':<13} {'Градус':<10} {'Фаза':<12} {'Нат.дом':<8} {'→ Нат.планета':<18} {'Аспект'}")
    lines.append("─"*82)

    ASPECTS = [(0,"соединение",8),(60,"секстиль",6),(90,"квадрат",7),
               (120,"трин",8),(150,"квинконс",3),(180,"оппозиция",8)]

    for tpname, tpdata in transit.items():
        t_lon = tpdata["lon"]
        t_sign, td, tm, _ = deg_to_sign(t_lon)
        phase = "ретроградная" if tpdata["retro"] else "прямая"
        transit_house = get_house_num(t_lon, natal_cusps)

        best_asp = []
        for npname, npdata in natal.items():
            diff = abs(t_lon - npdata["lon"]) % 360
            if diff > 180: diff = 360 - diff
            for asp_deg, asp_name, orb in ASPECTS:
                if abs(diff - asp_deg) <= orb:
                    delta = abs(diff - asp_deg)
                    best_asp.append(f"{asp_name} {npname} (орб {delta:.2f}°)")

        asp_str = " | ".join(best_asp[:2]) if best_asp else ""
        lines.append(f"{tpname:<12} {t_sign:<13} {td:2d}°{tm:02d}'  {phase:<12} {transit_house:<8} {asp_str}")

    # Межпланетный слой: именно его не хватало для ясного описания больших
    # конфигураций вроде замкнутой четырёхпланетной связки. Натальные аспекты
    # выше отвечают на вопрос «что задевает карту человека», этот блок —
    # «как планеты взаимодействуют между собой прямо на небе».
    outer_names = ["Юпитер", "Сатурн", "Уран", "Нептун", "Плутон"]
    outer_names = [name for name in outer_names if name in transit]
    global_aspects = {}
    for first, second in itertools.combinations(outer_names, 2):
        diff = abs(transit[first]["lon"] - transit[second]["lon"]) % 360
        diff = min(diff, 360 - diff)
        matches = []
        for degree, aspect_name, orb in ((0, "соединение", 6), (60, "секстиль", 5),
                                         (90, "квадрат", 5), (120, "трин", 5),
                                         (180, "оппозиция", 6)):
            delta = abs(diff - degree)
            if delta <= orb:
                matches.append((delta, aspect_name, degree))
        if matches:
            delta, aspect_name, degree = min(matches)
            global_aspects[frozenset((first, second))] = (aspect_name, delta, degree)

    lines.append("")
    lines.append("── АСПЕКТЫ МЕЖДУ ТРАНЗИТНЫМИ ПЛАНЕТАМИ ──")
    if global_aspects:
        for pair, (aspect_name, orb, _degree) in sorted(global_aspects.items(), key=lambda item: tuple(sorted(item[0]))):
            first, second = sorted(pair)
            lines.append(f"  {first} — {second}: {aspect_name} (орб {orb:.2f}°)")
    else:
        lines.append("  значимых связок между медленными планетами в этом срезе нет")

    # Строгий шаблон для трапециевидной связки: одна оппозиция как основание,
    # две вершины в секстиле друг к другу, а к основанию каждая вершина даёт
    # по одному трину и секстилю. Если хотя бы одного звена нет, фигуру не
    # называем — модель получит только фактически найденные аспекты.
    configurations = []
    for base_a, base_b in itertools.combinations(outer_names, 2):
        base_aspect = global_aspects.get(frozenset((base_a, base_b)))
        if not base_aspect or base_aspect[2] != 180:
            continue
        apexes = [name for name in outer_names if name not in (base_a, base_b)]
        for apex_a, apex_b in itertools.combinations(apexes, 2):
            apex_aspect = global_aspects.get(frozenset((apex_a, apex_b)))
            if not apex_aspect or apex_aspect[2] != 60:
                continue
            support_ok = True
            support = []
            for apex in (apex_a, apex_b):
                pair_aspects = []
                for base in (base_a, base_b):
                    item = global_aspects.get(frozenset((apex, base)))
                    if not item:
                        support_ok = False
                        break
                    pair_aspects.append(item[2])
                    support.append((apex, base, item[0], item[1]))
                if not support_ok or sorted(pair_aspects) != [60, 120]:
                    support_ok = False
                    break
            if support_ok:
                configurations.append((base_a, base_b, apex_a, apex_b, support))

    lines.append("ГЕОМЕТРИЧЕСКИЕ КОНФИГУРАЦИИ (только при полном расчёте):")
    if configurations:
        for base_a, base_b, apex_a, apex_b, support in configurations:
            lines.append(f"  Трапеция: основание {base_a} — {base_b} (оппозиция); вершины {apex_a} и {apex_b} (секстиль)")
            lines.extend(f"    {apex} — {base}: {aspect_name} (орб {orb:.2f}°)" for apex, base, aspect_name, orb in support)
    else:
        lines.append("  нет полностью рассчитанной трапециевидной конфигурации")

    # HD-транзитный слой: какие ворота и линии активны в поле и какие
    # временные каналы они достраивают с натальными активациями. Это не
    # «событие», а механический срез состояния поля на выбранную дату.
    natal_sun_lon = swe.calc_ut(jd_natal, swe.SUN, swe.FLG_SWIEPH)[0][0]
    design_target = (natal_sun_lon - 88.0) % 360
    jd_design = jd_natal - 88.0
    for _ in range(10):
        current = swe.calc_ut(jd_design, swe.SUN, swe.FLG_SWIEPH)[0][0]
        diff = (current - design_target + 180) % 360 - 180
        jd_design -= diff / 0.9856

    natal_gates = set()
    for planet_id, _planet_name in PLANETS:
        for chart_jd in (jd_natal, jd_design):
            natal_gates.add(deg_to_gate_substructure(
                swe.calc_ut(chart_jd, planet_id, swe.FLG_SWIEPH)[0][0]
            )[0])

    transit_gate_data = {
        pname: deg_to_gate_substructure(pdata["lon"])
        for pname, pdata in transit.items()
    }
    channel_pairs = {frozenset((a, b)) for a, b in CHANNELS}
    temporary = []
    seen_pairs = set()
    for pname, detail in transit_gate_data.items():
        gate = detail[0]
        for natal_gate in natal_gates:
            pair = frozenset((gate, natal_gate))
            if len(pair) == 2 and pair in channel_pairs and pair not in seen_pairs:
                seen_pairs.add(pair)
                temporary.append(f"{min(pair)}-{max(pair)} (транзит {pname} + натальная активация)")
    transit_names = list(transit_gate_data)
    for i, first in enumerate(transit_names):
        for second in transit_names[i + 1:]:
            pair = frozenset((transit_gate_data[first][0], transit_gate_data[second][0]))
            if len(pair) == 2 and pair in channel_pairs and pair not in seen_pairs:
                seen_pairs.add(pair)
                temporary.append(f"{min(pair)}-{max(pair)} (два транзитных тела)")

    lines.append("")
    lines.append("── HD-СРЕЗ НА ЭТУ ДАТУ ──")
    for pname, (gate, line, color, tone, base) in transit_gate_data.items():
        lines.append(f"  {pname}: ворота {gate}.{line}.{color}.{tone}.{base}")
    if temporary:
        lines.append("ВРЕМЕННЫЕ КАНАЛЫ:")
        lines.extend(f"  {item}" for item in temporary)
    else:
        lines.append("ВРЕМЕННЫЕ КАНАЛЫ: нет полного соединения в этом срезе")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  MCP JSON-RPC СЕРВЕР (stdio)
# ═══════════════════════════════════════════════════════════════════════════════

TOOLS_SCHEMA = [
    {
        "name": "natal_chart",
        "description": "Вычислить полную натальную карту: Западная астрология (тропик, Плацидус) + Джйотиш (сидерик, Лахири, накшатры). Используй для любого человека по дате рождения.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "year":     {"type":"integer","description":"Год рождения"},
                "month":    {"type":"integer","description":"Месяц рождения (1-12)"},
                "day":      {"type":"integer","description":"День рождения"},
                "hour":     {"type":"integer","description":"Час рождения по местному времени"},
                "minute":   {"type":"integer","description":"Минута рождения","default":0},
                "timezone": {"type":"number","description":"Часовой пояс UTC+X (напр. 3 для Москвы, 1 для Польши)"},
                "lat":      {"type":"number","description":"Широта места рождения (напр. 52.44)"},
                "lon":      {"type":"number","description":"Долгота места рождения (напр. 15.12)"},
                "name":     {"type":"string","description":"Имя человека (необязательно)"},
            },
            "required":["year","month","day","hour","timezone","lat","lon"]
        }
    },
    {
        "name": "human_design",
        "description": "Вычислить карту Дизайна Человека: Тип, Авторитет, Стратегия, Профиль, определённые/неопределённые Центры, Каналы, Ворота (сознательные и бессознательные).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "year":     {"type":"integer"},
                "month":    {"type":"integer"},
                "day":      {"type":"integer"},
                "hour":     {"type":"integer"},
                "minute":   {"type":"integer","default":0},
                "timezone": {"type":"number","description":"UTC+X"},
                "lat":      {"type":"number","description":"Широта (необязательно)","default":55.75},
                "lon":      {"type":"number","description":"Долгота (необязательно)","default":37.58},
                "name":     {"type":"string"},
            },
            "required":["year","month","day","hour","timezone"]
        }
    },
    {
        "name": "solar_return",
        "description": "Соляр — точная карта момента возвращения Солнца в натальную позицию; дома считаются по месту возврата (или месту рождения по умолчанию). Показывает планеты соляра и их аспекты к натальным.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "birth_year":    {"type":"integer"},
                "birth_month":   {"type":"integer"},
                "birth_day":     {"type":"integer"},
                "birth_hour":    {"type":"integer"},
                "birth_minute":  {"type":"integer","default":0},
                "birth_timezone":{"type":"number"},
                "lat":           {"type":"number"},
                "lon":           {"type":"number"},
                "return_lat":   {"type":"number","description":"Широта места нахождения в момент соляра/лунара; по умолчанию место рождения"},
                "return_lon":   {"type":"number","description":"Долгота места нахождения в момент соляра/лунара; по умолчанию место рождения"},
                "return_year":   {"type":"integer","description":"Год соляра (по умолчанию текущий)"},
            },
            "required":["birth_year","birth_month","birth_day","birth_hour","birth_timezone","lat","lon"]
        }
    },
    {
        "name": "lunar_return",
        "description": "Лунар — точная карта момента возвращения Луны в натальную позицию (~каждые 27.3 дня); дома считаются по месту возврата или месту рождения по умолчанию.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "birth_year":    {"type":"integer"},
                "birth_month":   {"type":"integer"},
                "birth_day":     {"type":"integer"},
                "birth_hour":    {"type":"integer"},
                "birth_minute":  {"type":"integer","default":0},
                "birth_timezone":{"type":"number"},
                "lat":           {"type":"number"},
                "lon":           {"type":"number"},
                "return_lat":   {"type":"number","description":"Широта места лунара; по умолчанию место рождения"},
                "return_lon":   {"type":"number","description":"Долгота места лунара; по умолчанию место рождения"},
                "from_year":     {"type":"integer","description":"Искать лунар начиная с этого года (по умолчанию текущий)"},
                "from_month":    {"type":"integer","description":"Месяц начала поиска"},
                "from_day":      {"type":"integer","description":"День начала поиска"},
            },
            "required":["birth_year","birth_month","birth_day","birth_hour","birth_timezone","lat","lon"]
        }
    },
    {
        "name": "hd_cycles",
        "description": "HD-циклы: Solar/Rave Return, Saturn Return, Uranus Opposition, Chiron Return при доступной эфемериде и отдельный календарь прохождения Солнца через 64 ворот.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "birth_year":    {"type":"integer"},
                "birth_month":   {"type":"integer"},
                "birth_day":     {"type":"integer"},
                "birth_hour":    {"type":"integer"},
                "birth_minute":  {"type":"integer","default":0},
                "birth_timezone":{"type":"number"},
                "cycle_year":    {"type":"integer","description":"Год циклов (по умолчанию текущий)"},
            },
            "required":["birth_year","birth_month","birth_day","birth_hour","birth_timezone"]
        }
    },
    {
        "name": "transits",
        "description": "Посмотреть текущие транзиты планет относительно натальной карты — аспекты транзитных планет к натальным.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "birth_year":     {"type":"integer"},
                "birth_month":    {"type":"integer"},
                "birth_day":      {"type":"integer"},
                "birth_hour":     {"type":"integer"},
                "birth_minute":   {"type":"integer","default":0},
                "birth_timezone": {"type":"number"},
                "lat":            {"type":"number"},
                "lon":            {"type":"number"},
                "transit_year":   {"type":"integer","description":"Год транзита (по умолчанию сегодня)"},
                "transit_month":  {"type":"integer"},
                "transit_day":    {"type":"integer"},
                "transit_hour_utc": {"type":"integer","description":"Час среза UTC; по умолчанию 12"},
                "transit_minute_utc": {"type":"integer","description":"Минута среза UTC; по умолчанию 00"},
            },
            "required":["birth_year","birth_month","birth_day","birth_hour","birth_timezone","lat","lon"]
        }
    },
]

TOOL_HANDLERS = {
    "natal_chart":   tool_natal_chart,
    "human_design":  tool_human_design,
    "solar_return":  tool_solar_return,
    "lunar_return":  tool_lunar_return,
    "hd_cycles":     tool_hd_cycles,
    "transits":      tool_transits,
}

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def handle(req):
    method = req.get("method","")
    rid    = req.get("id")

    if method == "initialize":
        send({"jsonrpc":"2.0","id":rid,"result":{
            "protocolVersion":"2024-11-05",
            "capabilities":{"tools":{}},
            "serverInfo":{"name":"astro-hd-server","version":"1.0"}
        }})

    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":rid,"result":{"tools": TOOLS_SCHEMA}})

    elif method == "tools/call":
        params   = req.get("params",{})
        tname    = params.get("name","")
        targs    = params.get("arguments",{})
        handler  = TOOL_HANDLERS.get(tname)
        if handler:
            try:
                result = handler(targs)
                send({"jsonrpc":"2.0","id":rid,"result":{
                    "content":[{"type":"text","text":result}]
                }})
            except Exception as e:
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32000,"message":str(e)}})
        else:
            send({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"Unknown tool: {tname}"}})

    elif method == "notifications/initialized":
        pass  # no response needed

    else:
        if rid is not None:
            send({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"Unknown method: {method}"}})

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handle(req)
        except json.JSONDecodeError:
            pass

if __name__ == "__main__":
    main()
