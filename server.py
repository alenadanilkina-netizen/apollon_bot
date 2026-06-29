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
    "Я/Самость": [1,10,25,15,7,13],
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
def get_authority(defined_centers):
    if "Солнечное сплетение" in defined_centers:
        return "Эмоциональный"
    if "Сакральный" in defined_centers:
        return "Сакральный"
    if "Селезёнка" in defined_centers:
        return "Селезёночный"
    if "Эго" in defined_centers:
        return "Эго"
    if "Я/Самость" in defined_centers:
        return "Я/Самость"
    return "Лунный / Нет авторитета"

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

def deg_to_gate_line(trop_deg):
    """Вычислить ворота и линию Дизайна Человека из тропического градуса.
    Колесо мандалы стартует в Рыбах 28°15' (офсет +1.75° от 0° Овна)."""
    HD_OFFSET = 1.75
    deg = (trop_deg + HD_OFFSET) % 360
    idx = int(deg / 5.625) % 64
    gate = HD_GATES_BY_DEGREE[idx]
    pos_in_gate = deg - idx * 5.625
    line = int(pos_in_gate / (5.625 / 6)) + 1
    line = min(line, 6)
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
        result[pname] = {"lon": r[0][0], "retro": r[0][3] < 0}
    return result

def calc_houses(jd, lat, lon):
    cusps, ascmc = swe.houses(jd, lat, lon, b'P')
    return cusps, ascmc[0], ascmc[1]  # cusps, ASC, MC

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
    lines.append(f"{'Планета':<12} {'Знак':<13} {'Градус':<10} R")
    lines.append("─"*42)
    for pname, pdata in trop.items():
        sign, d, m, _ = deg_to_sign(pdata["lon"])
        r = "℞" if pdata["retro"] else ""
        lines.append(f"{pname:<12} {sign:<13} {d:2d}°{m:02d}'      {r}")

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
            gate, line = deg_to_gate_line(pdata["lon"])
            result[pname] = {"gate": gate, "line": line, "lon": pdata["lon"]}
            if pname == "Солнце":
                earth_lon = (pdata["lon"] + 180) % 360
                eg, el = deg_to_gate_line(earth_lon)
                result["Земля"] = {"gate": eg, "line": el, "lon": earth_lon}
            if pname == "С.Узел":
                sn_lon = (pdata["lon"] + 180) % 360
                sg, sl = deg_to_gate_line(sn_lon)
                result["Ю.Узел"] = {"gate": sg, "line": sl, "lon": sn_lon}
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
    authority = get_authority(defined_centers)

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
    lines.append(f"ОПРЕДЕЛЁННЫЕ ЦЕНТРЫ ({len(defined_centers)}):")
    lines.append("  " + ", ".join(defined_centers) if defined_centers else "  нет")
    lines.append("")
    lines.append(f"НЕОПРЕДЕЛЁННЫЕ ЦЕНТРЫ:")
    undef = [c for c in CENTERS if c not in defined_centers]
    lines.append("  " + ", ".join(undef) if undef else "  нет")
    lines.append("")
    lines.append(f"КАНАЛЫ ({len(defined_channels)}):")
    for ch in defined_channels:
        lines.append(f"  {ch[0]}-{ch[1]}")
    lines.append("")
    lines.append("СОЗНАТЕЛЬНЫЕ ВОРОТА (личность — чёрный):")
    for pname, pg in con_gates.items():
        sign, d, m, _ = deg_to_sign(pg["lon"])
        lines.append(f"  {pname:<12} Ворота {pg['gate']:2d}.{pg['line']}   {sign} {d}°{m:02d}'")
    lines.append("")
    lines.append("БЕССОЗНАТЕЛЬНЫЕ ВОРОТА (дизайн — красный, ~88° до рождения):")
    for pname, pg in unc_gates.items():
        sign, d, m, _ = deg_to_sign(pg["lon"])
        lines.append(f"  {pname:<12} Ворота {pg['gate']:2d}.{pg['line']}   {sign} {d}°{m:02d}'")

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
    sr_year = int(args.get("return_year", __import__("datetime").datetime.utcnow().year))

    swe.set_sid_mode(swe.SIDM_LAHIRI)
    jd_natal = birth_to_jd(year, month, day, hour, minute, tz)

    # Натальная позиция Солнца
    natal_sun = swe.calc_ut(jd_natal, swe.SUN, swe.FLG_SWIEPH)[0][0]

    # Начало поиска — примерно день рождения в году соляра
    import datetime as dt
    jd_start = swe.julday(sr_year, month, day, 12.0)

    # Итерационный поиск: когда Солнце вернётся в натальную точку
    jd_sr = jd_start
    for _ in range(50):
        cur_sun = swe.calc_ut(jd_sr, swe.SUN, swe.FLG_SWIEPH)[0][0]
        diff = (natal_sun - cur_sun + 180) % 360 - 180
        if abs(diff) < 0.0001:
            break
        jd_sr += diff / 360  # ~1 день на 1 градус

    # Планеты в момент соляра
    sr_planets = calc_planets(jd_sr, sidereal=False)
    sr_cusps, sr_asc, sr_mc = calc_houses(jd_sr, lat, lon)

    # Конвертация JD обратно в дату
    sr_date = swe.revjul(jd_sr)
    sr_dt = f"{int(sr_date[2]):02d}.{int(sr_date[1]):02d}.{int(sr_date[0])}  {int(sr_date[3]):02d}:{int((sr_date[3]%1)*60):02d} UTC"

    lines = [f"═══ СОЛЯР {sr_year}: возвращение Солнца ═══"]
    lines.append(f"Точный момент: {sr_dt}")
    lines.append(f"Место: {lat:.4f}°N  {lon:.4f}°E")
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
                    lines.append(f"{spname:<14} {npname:<14} {asp_name}")
                    break

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

    swe.set_sid_mode(swe.SIDM_LAHIRI)
    jd_natal   = birth_to_jd(nyear, nmonth, nday, nhour, nminute, ntz)
    jd_transit = swe.julday(tyear, tmonth, tday, 12.0)

    natal   = calc_planets(jd_natal,   sidereal=False)
    transit = calc_planets(jd_transit, sidereal=False)

    lines = [f"── ТРАНЗИТЫ на {tday:02d}.{tmonth:02d}.{tyear} ──", ""]
    lines.append(f"{'Транзит':<12} {'Знак':<13} {'Градус':<10} {'→ Нат.планета':<14} {'Аспект'}")
    lines.append("─"*65)

    ASPECTS = [(0,"соединение",8),(60,"секстиль",6),(90,"квадрат",7),
               (120,"трин",8),(150,"квинконс",3),(180,"оппозиция",8)]

    for tpname, tpdata in transit.items():
        t_lon = tpdata["lon"]
        t_sign, td, tm, _ = deg_to_sign(t_lon)
        r = "℞" if tpdata["retro"] else ""

        best_asp = []
        for npname, npdata in natal.items():
            diff = abs(t_lon - npdata["lon"]) % 360
            if diff > 180: diff = 360 - diff
            for asp_deg, asp_name, orb in ASPECTS:
                if abs(diff - asp_deg) <= orb:
                    best_asp.append(f"{asp_name} {npname}")

        asp_str = " | ".join(best_asp[:2]) if best_asp else ""
        lines.append(f"{tpname:<12} {t_sign:<13} {td:2d}°{tm:02d}'  {r:<2}  {asp_str}")

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
        "description": "Соляр — карта момента возвращения Солнца в натальную позицию. Основа годового прогноза. Показывает планеты соляра и их аспекты к натальным.",
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
                "return_year":   {"type":"integer","description":"Год соляра (по умолчанию текущий)"},
            },
            "required":["birth_year","birth_month","birth_day","birth_hour","birth_timezone","lat","lon"]
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
            },
            "required":["birth_year","birth_month","birth_day","birth_hour","birth_timezone","lat","lon"]
        }
    },
]

TOOL_HANDLERS = {
    "natal_chart":   tool_natal_chart,
    "human_design":  tool_human_design,
    "solar_return":  tool_solar_return,
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
