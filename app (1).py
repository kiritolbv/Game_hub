"""
🎲 Hub de jeux à deux — Petit Bac + UNO
Multijoueur en réseau via un état partagé côté serveur (st.cache_resource).
Lancer :  streamlit run app.py
"""
import html
import random
import re
import string
import threading
import time
import unicodedata
import uuid

import streamlit as st

st.set_page_config(page_title="Hub de jeux à 2", page_icon="🎲", layout="centered")

ss = st.session_state

# ════════════════════════════════════════════════════════════════
#  STOCKAGE PARTAGÉ ENTRE TOUTES LES SESSIONS
# ════════════════════════════════════════════════════════════════


@st.cache_resource
def get_store():
    return {"rooms": {}, "lock": threading.RLock()}


STORE = get_store()
ROOMS = STORE["rooms"]
LOCK = STORE["lock"]

GAMES = ["Petit Bac", "UNO"]


def touch(room):
    room["version"] += 1
    room["updated"] = time.time()


def new_code():
    while True:
        c = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=4))
        if c not in ROOMS:
            return c


def create_room(name, token):
    with LOCK:
        now = time.time()
        for k in [k for k, r in ROOMS.items() if now - r["updated"] > 6 * 3600]:
            del ROOMS[k]
        code = new_code()
        ROOMS[code] = {
            "code": code,
            "players": [name],
            "tokens": [token],
            "version": 0,
            "updated": now,
            "status": "lobby",  # lobby | playing
            "game": None,
            "wins": [0, 0],
            "bac_settings": {"cats": BAC_POOL[:6], "rounds": 3, "excluded": list("KQWXYZ")},
            "uno_rules": default_uno_rules(),
            "bac": None,
            "uno": None,
        }
        return code


def join_room(code, name, token):
    with LOCK:
        room = ROOMS.get(code)
        if not room:
            return None, "Salle introuvable."
        if token in room["tokens"]:
            return room["tokens"].index(token), None
        if len(room["players"]) >= 2:
            return None, "Cette salle est déjà complète."
        if name.casefold() == room["players"][0].casefold():
            return None, "Ce pseudo est déjà pris dans la salle."
        room["players"].append(name)
        room["tokens"].append(token)
        touch(room)
        return 1, None


def leave_room():
    code, seat = ss.room, ss.seat
    with LOCK:
        room = ROOMS.get(code)
        if room:
            if seat == 0:
                del ROOMS[code]
            else:
                room["players"].pop(1)
                room["tokens"].pop(1)
                room.update(status="lobby", bac=None, uno=None)
                touch(room)
    ss.room = None
    ss.seat = None
    if "r" in st.query_params:
        del st.query_params["r"]


def back_to_lobby(code):
    with LOCK:
        room = ROOMS.get(code)
        if room:
            room.update(status="lobby", bac=None, uno=None)
            touch(room)


def start_game(code):
    with LOCK:
        room = ROOMS.get(code)
        if not room or len(room["players"]) < 2 or not room["game"]:
            return
        if room["game"] == "Petit Bac":
            s = room["bac_settings"]
            if not 5 <= len(s["cats"]) <= 10 or len(s["excluded"]) >= 26:
                return
            room["bac"] = {"id": uuid.uuid4().hex[:6], "round": 0, "used": [], "scores": [0, 0]}
            bac_new_round(room)
        else:
            start_uno(room)
        room["status"] = "playing"
        touch(room)


# ════════════════════════════════════════════════════════════════
#  PETIT BAC
# ════════════════════════════════════════════════════════════════

BAC_POOL = [
    "Prénom", "Pays", "Ville", "Animal", "Fruit", "Légume", "Métier", "Objet",
    "Marque", "Couleur", "Film / Série", "Chanteur / Groupe", "Sport", "Vêtement",
    "Plat / Cuisine", "Fleur / Plante", "Partie du corps", "Célébrité",
    "Instrument de musique", "Boisson", "Personnage de fiction", "Fleuve / Montagne",
]


def norm(t):
    t = unicodedata.normalize("NFD", t or "")
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return t.strip().casefold()


def esc(t):
    return re.sub(r"([\\`*_{}\[\]()#+\-.!~<>|$])", r"\\\1", t or "")


def bac_new_round(room):
    b, s = room["bac"], room["bac_settings"]
    pool = [l for l in string.ascii_uppercase if l not in s["excluded"]]
    avail = [l for l in pool if l not in b["used"]] or pool
    b["letter"] = random.choice(avail)
    b["used"].append(b["letter"])
    b["round"] += 1
    b["answers"] = [{}, {}]
    b["submitted"] = [False, False]
    b["first"] = None
    b["rejects"] = [set(), set()]  # rejects[i] = catégories refusées par i chez l'adversaire
    b["ready"] = [False, False]
    b["phase"] = "playing"  # playing | review | finished


def bac_valid(letter, ans):
    n = norm(ans)
    return bool(n) and n.startswith(letter.casefold())


def score_round(b, s):
    rows, tot = [], [0, 0]
    for cat in s["cats"]:
        ans = [b["answers"][p].get(cat, "") for p in (0, 1)]
        ok = [bac_valid(b["letter"], ans[p]) and cat not in b["rejects"][1 - p] for p in (0, 1)]
        pts = [0, 0]
        for p in (0, 1):
            if ok[p]:
                same = ok[1 - p] and norm(ans[p]) == norm(ans[1 - p])
                pts[p] = 1 if same else 2
        rows.append((cat, ans, ok, pts))
        tot[0] += pts[0]
        tot[1] += pts[1]
    return rows, tot


def bac_advance(room):
    b, s = room["bac"], room["bac_settings"]
    _, tot = score_round(b, s)
    b["scores"][0] += tot[0]
    b["scores"][1] += tot[1]
    if b["round"] >= s["rounds"]:
        b["phase"] = "finished"
        if b["scores"][0] != b["scores"][1]:
            room["wins"][0 if b["scores"][0] > b["scores"][1] else 1] += 1
    else:
        bac_new_round(room)


def bac_ready(code, me):
    with LOCK:
        room = ROOMS.get(code)
        if not room or not room["bac"]:
            return
        b = room["bac"]
        b["ready"][me] = True
        if all(b["ready"]):
            bac_advance(room)
        touch(room)


def bac_toggle_reject(code, me, cat, key):
    with LOCK:
        room = ROOMS.get(code)
        if not room or not room["bac"]:
            return
        rej = room["bac"]["rejects"][me]
        if ss.get(key):
            rej.add(cat)
        else:
            rej.discard(cat)
        touch(room)


def add_custom_category():
    v = (ss.get("bac_new") or "").strip()
    if v and v not in BAC_POOL + ss.bac_custom:
        ss.bac_custom.append(v)
        if len(ss.bac_cats) < 10:
            ss.bac_cats = ss.bac_cats + [v]
    ss.bac_new = ""


def bac_settings_ui(room, host):
    s = room["bac_settings"]
    if not host:
        st.markdown("**Catégories choisies par l'hôte :** " + " · ".join(s["cats"]))
        st.markdown(f"**Manches :** {s['rounds']}  \n**Lettres exclues :** {', '.join(s['excluded']) or 'aucune'}")
        return
    if "bac_custom" not in ss:
        ss.bac_custom = []
    if "bac_cats" not in ss:
        ss.bac_cats = list(s["cats"])
    options = BAC_POOL + ss.bac_custom
    cats = st.multiselect("Catégories (de 5 à 10)", options, key="bac_cats", max_selections=10)
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    c1.text_input("Ajouter une catégorie perso", key="bac_new", placeholder="Ex : Super-héros")
    c2.button("➕ Ajouter", on_click=add_custom_category, use_container_width=True)
    rounds = st.slider("Nombre de manches", 1, 10, s["rounds"], key="bac_rounds")
    if "bac_excl" not in ss:
        ss.bac_excl = list(s["excluded"])
    excluded = st.multiselect(
        "Lettres à exclure (tirage au sort)", list(string.ascii_uppercase), key="bac_excl",
        help="Ces lettres ne seront jamais tirées.",
    )
    new = {"cats": list(cats), "rounds": rounds, "excluded": list(excluded)}
    if new != s:
        with LOCK:
            room["bac_settings"] = new
            touch(room)
        ss.seen_version = room["version"]
    n = len(cats)
    if len(excluded) >= 26:
        st.error("Tu ne peux pas exclure toutes les lettres !")
    if n < 5:
        st.warning(f"Il faut au moins 5 catégories (actuellement {n}).")
    else:
        st.caption(f"{n} catégories sélectionnées.")


def bac_screen(room):
    b, s = room["bac"], room["bac_settings"]
    me, opp = ss.seat, 1 - ss.seat
    names = room["players"]
    host = me == 0

    st.subheader(f"🔤 Petit Bac — manche {b['round']}/{s['rounds']}")
    st.markdown(
        f"**{esc(names[0])}** : {b['scores'][0]} pts &nbsp;|&nbsp; **{esc(names[1])}** : {b['scores'][1]} pts"
    )

    if b["phase"] == "playing":
        st.markdown(
            f"<div style='text-align:center;font-size:90px;font-weight:900;line-height:1.1;"
            f"padding:8px 0'>{b['letter']}</div>",
            unsafe_allow_html=True,
        )
        if b["submitted"][me]:
            st.success(f"Tes réponses sont envoyées. En attente de {names[opp]}…")
        else:
            if b["submitted"][opp]:
                st.warning(f"⏱️ {names[opp]} a terminé ! Envoie vite tes réponses.")
            with st.form(f"bac_form_{b['id']}_{b['round']}"):
                vals = {}
                for cat in s["cats"]:
                    vals[cat] = st.text_input(
                        cat, key=f"bac_{b['id']}_{b['round']}_{cat}", placeholder=f"Commence par {b['letter']}"
                    )
                go = st.form_submit_button("🛑 Terminé !", type="primary", use_container_width=True)
            if go:
                with LOCK:
                    b["answers"][me] = dict(vals)
                    b["submitted"][me] = True
                    if b["first"] is None:
                        b["first"] = me
                    if all(b["submitted"]):
                        b["phase"] = "review"
                    touch(room)
                st.rerun()
        st.caption("Barème : 2 pts si ta réponse est différente de celle de l'autre, 1 pt si vous avez la même, 0 si vide ou mauvaise lettre.")

    elif b["phase"] == "review":
        st.markdown(f"### Lettre **{b['letter']}** — vérification")
        st.caption("Tu peux refuser une réponse de l'adversaire (case « Refuser ») si elle te semble fausse.")
        rows, tot = score_round(b, s)
        h = st.columns([2, 3, 3])
        h[0].markdown("**Catégorie**")
        h[1].markdown(f"**{esc(names[me])} (toi)**")
        h[2].markdown(f"**{esc(names[opp])}**")
        for cat, ans, ok, pts in rows:
            c = st.columns([2, 3, 3])
            c[0].markdown(f"**{esc(cat)}**")
            c[1].markdown(f"{'✅' if ok[me] else '❌'} {esc(ans[me]) or '—'} **(+{pts[me]})**")
            c[2].markdown(f"{'✅' if ok[opp] else '❌'} {esc(ans[opp]) or '—'} **(+{pts[opp]})**")
            if ans[opp].strip():
                key = f"rej_{b['id']}_{b['round']}_{cat}"
                c[2].checkbox(
                    "Refuser",
                    value=cat in b["rejects"][me],
                    key=key,
                    on_change=bac_toggle_reject,
                    args=(room["code"], me, cat, key),
                )
        st.markdown(f"**Points de la manche :** toi {tot[me]} — {esc(names[opp])} {tot[opp]}")
        last = b["round"] >= s["rounds"]
        if b["ready"][me]:
            st.info(f"En attente de {names[opp]}…")
        else:
            st.button(
                "🏁 Voir le résultat final" if last else "➡️ Manche suivante",
                type="primary",
                on_click=bac_ready,
                args=(room["code"], me),
                use_container_width=True,
            )

    else:  # finished
        a, c = b["scores"]
        st.markdown("### 🏆 Résultat final")
        if a == c:
            st.info("Égalité parfaite !")
        else:
            w = 0 if a > c else 1
            (st.success if w == me else st.error)(
                f"{'Tu as gagné' if w == me else names[w] + ' a gagné'} ! ({max(a, c)} – {min(a, c)})"
            )
        if host:
            c1, c2 = st.columns(2)
            c1.button("🔁 Rejouer", on_click=start_game, args=(room["code"],), type="primary", use_container_width=True)
            c2.button("🏠 Retour au salon", on_click=back_to_lobby, args=(room["code"],), use_container_width=True)
        else:
            st.caption("L'hôte peut relancer une partie ou revenir au salon.")


# ════════════════════════════════════════════════════════════════
#  UNO
# ════════════════════════════════════════════════════════════════

COLORS = {"R": "🟥", "Y": "🟨", "G": "🟩", "B": "🟦", "K": "⬛"}
COLOR_NAMES = {"R": "Rouge", "Y": "Jaune", "G": "Vert", "B": "Bleu"}
CSS_COLORS = {"R": "#d72600", "Y": "#e6c700", "G": "#2e8b17", "B": "#0063b3", "K": "#222222"}
BIG = {"skip": "⛔", "reverse": "🔄", "+2": "+2", "+4": "+4", "wild": "🌈"}
SMALL = {"skip": "⛔ Passe", "reverse": "🔄 Sens", "+2": "+2", "+4": "+4", "wild": "Joker"}

# clé : (libellé, valeur par défaut, aide)
UNO_RULE_DEFS = {
    "stack_same": ("Cumuler les pénalités identiques (+2 sur +2, +4 sur +4)", True,
                   "L'adversaire peut répondre par le même type de carte au lieu de piocher ; la pénalité s'additionne."),
    "stack_4_on_2": ("Autoriser un +4 sur un +2", False, "Le +4 peut être posé en réponse à un +2."),
    "stack_2_on_4": ("Autoriser un +2 sur un +4", False, "Le +2 doit être de la couleur choisie par le +4."),
    "jump_in": ("Interruption : jouer une carte identique hors de son tour", False,
                "Une carte strictement identique (même couleur et même valeur) à la dernière posée peut être jouée à tout moment."),
    "seven_zero": ("Règle 7-0 : le 7 et le 0 échangent les mains", False,
                   "À deux joueurs, jouer un 7 ou un 0 échange vos deux mains."),
    "draw_until": ("Piocher jusqu'à obtenir une carte jouable", False,
                   "Sinon : on pioche une seule carte, puis on joue ou on passe."),
    "strict_4": ("+4 strict : seulement sans carte de la couleur en cours", False,
                 "Règle officielle du +4 (hors empilement)."),
    "no_finish_special": ("Interdit de finir sur une carte spéciale", False,
                          "Impossible de gagner avec un joker, +2, +4, passe ou inversion."),
    "uno_call": ("« UNO ! » obligatoire (sinon contre-UNO = +2 cartes)", True,
                 "Annonce UNO avant de jouer ton avant-dernière carte, sinon l'adversaire peut te faire piocher 2 cartes."),
}


def default_uno_rules():
    r = {k: v[1] for k, v in UNO_RULE_DEFS.items()}
    r["hand_size"] = 7
    return r


def build_deck():
    d = []
    for c in "RYGB":
        d.append(f"{c}:0")
        for v in list("123456789") + ["skip", "reverse", "+2"]:
            d += [f"{c}:{v}"] * 2
    d += ["K:wild"] * 4 + ["K:+4"] * 4
    random.shuffle(d)
    return d


def sort_key(card):
    c, v = card.split(":")
    order = {"skip": 10, "reverse": 11, "+2": 12, "wild": 13, "+4": 14}
    return ("RYGBK".index(c), int(v) if v.isdigit() else order[v])


def card_label(card):
    c, v = card.split(":")
    return f"{COLORS[c]} {SMALL.get(v, v)}"


def card_html(card, cur_color):
    c, v = card.split(":")
    fg = "#000" if c == "Y" else "#fff"
    return (
        f"<div style='display:inline-block;width:92px;height:132px;border-radius:14px;"
        f"background:{CSS_COLORS[c]};color:{fg};font-size:38px;font-weight:800;line-height:120px;"
        f"text-align:center;border:6px solid {CSS_COLORS[cur_color]};"
        f"box-shadow:0 3px 10px rgba(0,0,0,.35)'>{BIG.get(v, v)}</div>"
    )


def start_uno(room):
    r = room["uno_rules"]
    deck = build_deck()
    hands = [[deck.pop() for _ in range(r["hand_size"])] for _ in range(2)]
    while True:
        top = deck.pop()
        c, v = top.split(":")
        if c != "K" and v.isdigit():
            break
        deck.insert(0, top)
    first = random.choice([0, 1])
    room["uno"] = {
        "id": uuid.uuid4().hex[:6],
        "deck": deck,
        "discard": [top],
        "hands": hands,
        "color": top.split(":")[0],
        "turn": first,
        "pending": 0,
        "drawn": False,
        "winner": None,
        "uno_vuln": None,
        "log": [f"🎬 Début de partie — {room['players'][first]} commence."],
    }


def draw_cards(u, p, n):
    got = 0
    for _ in range(n):
        if not u["deck"]:
            if len(u["discard"]) <= 1:
                break
            top = u["discard"].pop()
            rest = u["discard"]
            random.shuffle(rest)
            u["deck"], u["discard"] = rest, [top]
        u["hands"][p].append(u["deck"].pop())
        got += 1
    return got


def can_play(u, r, p, card):
    c, v = card.split(":")
    tc, tv = u["discard"][-1].split(":")
    hand = u["hands"][p]
    if u["winner"] is not None:
        return False
    if r["no_finish_special"] and len(hand) == 1 and (c == "K" or not v.isdigit()):
        return False
    if u["turn"] != p:  # hors de son tour : seulement l'interruption
        return bool(r["jump_in"]) and u["pending"] == 0 and c != "K" and card == u["discard"][-1]
    if u["pending"] > 0:
        if v == "+2":
            return (tv == "+2" and r["stack_same"]) or (tv == "+4" and r["stack_2_on_4"] and c == u["color"])
        if v == "+4":
            return (tv == "+4" and r["stack_same"]) or (tv == "+2" and r["stack_4_on_2"])
        return False
    if c == "K":
        if v == "+4" and r["strict_4"]:
            return not any(h.split(":")[0] == u["color"] for h in hand)
        return True
    return c == u["color"] or v == tv


def _uno_ctx(code):
    room = ROOMS.get(code)
    if not room or room["status"] != "playing" or room["game"] != "UNO" or not room["uno"]:
        return None, None, None
    return room, room["uno"], room["uno_rules"]


def uno_play(code, p, card, chosen, said_uno):
    with LOCK:
        room, u, r = _uno_ctx(code)
        if not room or card not in u["hands"][p] or not can_play(u, r, p, card):
            return
        name = room["players"][p]
        c, v = card.split(":")
        jump = u["turn"] != p
        u["uno_vuln"] = None
        u["hands"][p].remove(card)
        u["discard"].append(card)
        u["color"] = chosen if c == "K" else c
        u["drawn"] = False
        u["log"].append(
            f"{'⚡ ' if jump else ''}{name} joue {card_label(card)}"
            + (f" → {COLOR_NAMES[u['color']]}" if c == "K" else "")
        )
        if not u["hands"][p]:
            u["winner"] = p
            room["wins"][p] += 1
            u["log"].append(f"🏆 {name} a gagné !")
            touch(room)
            return
        nxt = 1 - p
        if v in ("skip", "reverse"):
            nxt = p
        elif v == "+2":
            u["pending"] += 2
        elif v == "+4":
            u["pending"] += 4
        elif v in ("7", "0") and r["seven_zero"]:
            u["hands"][0], u["hands"][1] = u["hands"][1], u["hands"][0]
            u["log"].append("🔀 Les mains sont échangées !")
        u["turn"] = nxt
        if r["uno_call"] and len(u["hands"][p]) == 1 and not said_uno:
            u["uno_vuln"] = p
        elif len(u["hands"][p]) == 1:
            u["log"].append(f"📣 {name} annonce UNO !")
        touch(room)


def on_play(code, p, card):
    uno_play(code, p, card, ss.get("uno_color", "R"), bool(ss.get("uno_say", False)))
    ss["uno_say"] = False


def uno_draw(code, p):
    with LOCK:
        room, u, r = _uno_ctx(code)
        if not room or u["winner"] is not None or u["turn"] != p:
            return
        name = room["players"][p]
        u["uno_vuln"] = None
        if u["pending"] > 0:
            n = draw_cards(u, p, u["pending"])
            u["log"].append(f"📥 {name} pioche {n} cartes et passe son tour.")
            u["pending"] = 0
            u["turn"] = 1 - p
            u["drawn"] = False
            touch(room)
            return
        if u["drawn"]:
            return
        cnt = 0
        if r["draw_until"]:
            while True:
                if not draw_cards(u, p, 1):
                    break
                cnt += 1
                if can_play(u, r, p, u["hands"][p][-1]):
                    break
        else:
            cnt = draw_cards(u, p, 1)
        u["log"].append(f"📥 {name} pioche {cnt} carte{'s' if cnt > 1 else ''}.")
        if any(can_play(u, r, p, h) for h in u["hands"][p]):
            u["drawn"] = True
        else:
            u["turn"] = 1 - p
            u["drawn"] = False
        touch(room)


def uno_pass(code, p):
    with LOCK:
        room, u, r = _uno_ctx(code)
        if not room or u["turn"] != p or not u["drawn"] or u["pending"] > 0:
            return
        u["log"].append(f"⏭️ {room['players'][p]} passe son tour.")
        u["turn"] = 1 - p
        u["drawn"] = False
        touch(room)


def uno_challenge(code, me):
    with LOCK:
        room, u, r = _uno_ctx(code)
        if not room or u["uno_vuln"] != 1 - me:
            return
        victim = 1 - me
        draw_cards(u, victim, 2)
        u["uno_vuln"] = None
        u["log"].append(f"🚨 Contre-UNO ! {room['players'][victim]} n'avait pas annoncé UNO : +2 cartes.")
        touch(room)


def uno_rules_ui(room, host):
    cur = room["uno_rules"]
    if not host:
        st.markdown(f"**Cartes distribuées :** {cur['hand_size']}")
        for k, (label, _, _) in UNO_RULE_DEFS.items():
            st.markdown(f"{'✅' if cur[k] else '⬜'} {label}")
        return
    new = {"hand_size": st.slider("Cartes distribuées", 3, 10, cur["hand_size"], key="uno_hand")}
    for k, (label, _, hlp) in UNO_RULE_DEFS.items():
        new[k] = st.checkbox(label, value=cur[k], key=f"rule_{k}", help=hlp)
    if new != cur:
        with LOCK:
            room["uno_rules"] = new
            touch(room)
        ss.seen_version = room["version"]


def fan_css(u_id, hand, playable):
    """CSS qui transforme les boutons de la main en éventail de cartes."""
    n = len(hand)
    W, H = 78, 114
    step = 0 if n <= 1 else max(24, min(56, (640 - W) / (n - 1)))
    spread = 0 if n <= 1 else min(48, 6 * (n - 1))
    mid = (n - 1) / 2
    css = [
        ".st-key-uhand{display:flex !important;flex-direction:row !important;flex-wrap:nowrap !important;"
        "justify-content:safe center;align-items:flex-end;gap:0 !important;padding:46px 28px 30px;"
        "overflow-x:auto;overflow-y:hidden;}",
        ".st-key-uhand > div{width:auto !important;flex:0 0 auto !important;}",
    ]
    for i, card in enumerate(hand):
        c, v = card.split(":")
        t = 0 if n <= 1 else (i - mid) / mid
        angle = t * spread / 2
        dy = (t ** 2) * 18 - (12 if playable[i] else 0)
        ml = 0 if i == 0 else -(W - step)
        if card == "K:wild":
            bg = "linear-gradient(135deg,#d72600 25%,#e6c700 25% 50%,#2e8b17 50% 75%,#0063b3 75%)"
        else:
            bg = CSS_COLORS[c]
        fg = "#000" if c == "Y" else "#fff"
        fs = 20 if v in ("skip", "reverse", "wild") else 30
        k = f"uc_{u_id}_{i}"
        css.append(
            f".st-key-{k}{{width:{W}px !important;margin-left:{ml:.1f}px;"
            f"transform:translateY({dy:.1f}px) rotate({angle:.1f}deg);transform-origin:50% 140%;"
            f"transition:transform .15s;position:relative;z-index:{i};}}"
        )
        css.append(
            f".st-key-{k}:has(button:not(:disabled)):hover{{transform:translateY(-34px) rotate(0deg);z-index:99;}}"
        )
        css.append(
            f".st-key-{k} button{{width:{W}px !important;height:{H}px !important;min-height:0 !important;"
            f"padding:0 !important;border-radius:12px !important;background:{bg} !important;color:{fg} !important;"
            f"border:3px solid #fff !important;box-shadow:0 4px 12px rgba(0,0,0,.45);}}"
        )
        css.append(
            f".st-key-{k} button p{{font-size:{fs}px !important;font-weight:800 !important;color:{fg} !important;"
            f"line-height:1 !important;text-shadow:0 1px 3px rgba(0,0,0,.5);}}"
        )
        css.append(f".st-key-{k} button:disabled{{opacity:.55;filter:saturate(.6);}}")
    return "".join(css)


def uno_screen(room):
    u, r = room["uno"], room["uno_rules"]
    me, opp = ss.seat, 1 - ss.seat
    names = room["players"]
    code = room["code"]
    host = me == 0
    my_turn = u["turn"] == me and u["winner"] is None

    st.subheader("🃏 UNO")

    n_opp = len(u["hands"][opp])
    st.markdown(f"**{esc(names[opp])}** — {n_opp} carte{'s' if n_opp > 1 else ''} &nbsp; " + "🂠" * min(n_opp, 20))

    col_a, col_b = st.columns([1, 2], vertical_alignment="center")
    col_a.markdown(card_html(u["discard"][-1], u["color"]), unsafe_allow_html=True)
    with col_b:
        st.markdown(f"Couleur en cours : **{COLORS[u['color']]} {COLOR_NAMES[u['color']]}**")
        st.markdown(f"Pioche : {len(u['deck'])} cartes")
        if u["pending"] > 0:
            st.markdown(f"⚠️ **Pénalité en cours : +{u['pending']}**")

    with st.expander("Historique", expanded=False):
        for line in reversed(u["log"][-8:]):
            st.write(line)

    # fin de partie
    if u["winner"] is not None:
        w = u["winner"]
        (st.success if w == me else st.error)("🏆 Tu as gagné !" if w == me else f"{names[w]} a gagné…")
        if host:
            c1, c2 = st.columns(2)
            c1.button("🔁 Rejouer", on_click=start_game, args=(code,), type="primary", use_container_width=True)
            c2.button("🏠 Retour au salon", on_click=back_to_lobby, args=(code,), use_container_width=True)
        else:
            st.caption("L'hôte peut relancer une partie ou revenir au salon.")
        return

    # statut
    if my_turn:
        if u["pending"] > 0:
            st.warning(f"⚠️ Réponds par une carte autorisée ou pioche {u['pending']} cartes.")
        else:
            st.success("🟢 À toi de jouer !")
    else:
        st.info(f"⏳ Au tour de {names[opp]}" + (" — tu peux interrompre avec une carte identique." if r["jump_in"] else ""))

    hand = sorted(u["hands"][me], key=sort_key)

    # actions
    a1, a2, a3 = st.columns(3)
    a1.button(
        f"📥 Piocher {u['pending']}" if u["pending"] else "📥 Piocher",
        on_click=uno_draw,
        args=(code, me),
        disabled=not my_turn or (u["drawn"] and u["pending"] == 0),
        use_container_width=True,
    )
    if my_turn and u["drawn"] and u["pending"] == 0:
        a2.button("⏭️ Passer", on_click=uno_pass, args=(code, me), use_container_width=True)
    if r["uno_call"] and u["uno_vuln"] == opp:
        a3.button("🚨 Contre-UNO !", on_click=uno_challenge, args=(code, me), type="primary", use_container_width=True)

    # options de jeu
    o1, o2 = st.columns(2)
    if any(h.startswith("K:") for h in hand):
        ss.setdefault("uno_color", "R")
        o1.radio(
            "Couleur choisie (jokers)",
            list(COLOR_NAMES),
            format_func=lambda k: f"{COLORS[k]} {COLOR_NAMES[k]}",
            key="uno_color",
            horizontal=True,
        )
    if r["uno_call"] and len(hand) == 2:
        o2.checkbox("📣 Annoncer UNO avec la prochaine carte", key="uno_say")

    # main en éventail devant nous
    st.markdown(f"**Ta main ({len(hand)})**")
    playable = [can_play(u, r, me, card) for card in hand]
    st.markdown(f"<style>{fan_css(u['id'], hand, playable)}</style>", unsafe_allow_html=True)
    with st.container(key="uhand"):
        for i, card in enumerate(hand):
            val = card.split(":")[1]
            st.button(
                BIG.get(val, val),
                key=f"uc_{u['id']}_{i}",
                disabled=not playable[i],
                on_click=on_play,
                args=(code, me, card),
            )
    if my_turn and not any(playable) and not u["drawn"]:
        st.caption("Aucune carte jouable : pioche !")



# ════════════════════════════════════════════════════════════════
#  SALON / ACCUEIL
# ════════════════════════════════════════════════════════════════


@st.fragment(run_every=2)
def watcher(code):
    """Vérifie toutes les 2 s si l'état de la salle a changé et relance la page si oui."""
    room = ROOMS.get(code)
    if room is None or room["version"] != ss.get("seen_version"):
        st.rerun()


def init_session():
    if "token" not in ss:
        ss.token = st.query_params.get("t") or uuid.uuid4().hex[:10]
    if "room" not in ss:
        ss.room, ss.seat = None, None
        code = st.query_params.get("r")
        if code:  # reconnexion après un rafraîchissement de la page
            with LOCK:
                room = ROOMS.get(code)
                if room and ss.token in room["tokens"]:
                    ss.room = code
                    ss.seat = room["tokens"].index(ss.token)


def enter_room(code, seat):
    ss.room, ss.seat = code, seat
    st.query_params["r"] = code
    st.query_params["t"] = ss.token
    st.rerun()


def home_screen():
    st.title("🎲 Hub de jeux à deux")
    st.caption("Petit Bac • UNO — crée une salle et envoie le code à ton adversaire.")
    name = st.text_input("Ton pseudo", key="name_input", max_chars=20).strip()
    t1, t2 = st.tabs(["Créer une salle", "Rejoindre une salle"])
    with t1:
        if st.button("Créer une salle", type="primary", disabled=not name):
            enter_room(create_room(name, ss.token), 0)
    with t2:
        code_in = st.text_input("Code de la salle", max_chars=4, key="code_input").strip().upper()
        if st.button("Rejoindre", disabled=not (name and code_in)):
            seat, err = join_room(code_in, name, ss.token)
            if err:
                st.error(err)
            else:
                enter_room(code_in, seat)


def pick_game(code, game):
    with LOCK:
        room = ROOMS.get(code)
        if not room or room["status"] != "lobby":
            return
        room["game"] = None if room["game"] == game else game  # re-cliquer = revenir au choix
        touch(room)


def reset_scores(code):
    with LOCK:
        room = ROOMS.get(code)
        if room:
            room["wins"] = [0, 0]
            touch(room)


def lobby_css(selected):
    def extra(g):
        if selected == g:
            return ("border:4px solid #ffd43b !important;opacity:1 !important;"
                    "box-shadow:0 0 0 5px rgba(255,212,59,.35),0 10px 24px rgba(0,0,0,.4);")
        return f"opacity:{0.55 if selected else 1} !important;"

    out = []
    for key, bg, g in (
        ("pick_uno", "linear-gradient(135deg,#e0361a,#8e1600)", "UNO"),
        ("pick_bac", "linear-gradient(135deg,#0a74d1,#2e9b1f)", "Petit Bac"),
    ):
        out.append(
            f".st-key-{key} button{{height:170px !important;border-radius:22px !important;"
            f"background:{bg} !important;color:#fff !important;border:4px solid transparent;{extra(g)}}}"
        )
        out.append(f".st-key-{key} button p{{font-size:34px !important;font-weight:900 !important;color:#fff !important;}}")
        out.append(f".st-key-{key} button:disabled{{{extra(g)}}}")
    return "".join(out)


def scoreboard_html(room):
    names, wins = room["players"], room["wins"]
    tie = len(names) < 2 or wins[0] == wins[1]
    top = max(wins[: len(names)])
    rows = ""
    for i, n in enumerate(names):
        leader = (not tie) and wins[i] == top
        bg = "rgba(255,193,7,.25)" if leader else "rgba(128,128,128,.14)"
        you = " <span style='opacity:.6;font-weight:400'>(toi)</span>" if i == ss.seat else ""
        crown = "👑 " if leader else ""
        rows += (
            f"<div style='display:flex;justify-content:space-between;align-items:center;background:{bg};"
            f"border-radius:10px;padding:8px 12px;margin-bottom:6px'>"
            f"<span style='font-weight:700'>{crown}{html.escape(n)}{you}</span>"
            f"<span style='font-size:26px;font-weight:900'>{wins[i]}</span></div>"
        )
    live = ""
    if room["status"] == "playing" and len(names) == 2:
        if room["game"] == "Petit Bac" and room["bac"]:
            b = room["bac"]
            live = f"✏️ Manche {b['round']}/{room['bac_settings']['rounds']} · {b['scores'][0]} – {b['scores'][1]} pts"
        elif room["game"] == "UNO" and room["uno"]:
            h = room["uno"]["hands"]
            live = f"🃏 Cartes en main · {len(h[0])} – {len(h[1])}"
    if live:
        rows += f"<div style='font-size:13px;opacity:.8;margin-top:2px'>{live}</div>"
    return rows


def room_lobby(room):
    host = ss.seat == 0
    code = room["code"]
    game = room["game"]

    st.markdown(f"<style>{lobby_css(game)}</style>", unsafe_allow_html=True)
    st.subheader("Choisis un jeu" if host else "L'hôte choisit le jeu…")
    c1, c2 = st.columns(2)
    c1.button("🃏 UNO", key="pick_uno", on_click=pick_game, args=(code, "UNO"), disabled=not host, use_container_width=True)
    c2.button("✏️ Petit Bac", key="pick_bac", on_click=pick_game, args=(code, "Petit Bac"),
              disabled=not host, use_container_width=True)

    if not game:
        st.caption("Clique sur un jeu pour afficher ses réglages." if host else "⏳ En attente du choix de l'hôte…")
        return

    st.markdown("---")
    st.subheader(f"⚙️ Réglages — {game}")
    if game == "Petit Bac":
        bac_settings_ui(room, host)
        s = room["bac_settings"]
        can_start = 5 <= len(s["cats"]) <= 10 and len(s["excluded"]) < 26
    else:
        uno_rules_ui(room, host)
        can_start = True

    st.markdown("---")
    if host:
        b1, b2 = st.columns([2, 1])
        b1.button(f"▶️ Lancer {game}", type="primary", on_click=start_game, args=(code,),
                  disabled=not can_start, use_container_width=True)
        b2.button("↩️ Changer de jeu", on_click=pick_game, args=(code, game), use_container_width=True)
    else:
        st.caption("⏳ En attente que l'hôte lance la partie…")


def room_screen(room):
    ss.seen_version = room["version"]
    watcher(room["code"])
    me = ss.seat
    names = room["players"]

    with st.sidebar:
        st.markdown("### 🏆 Tableau des scores")
        st.markdown(scoreboard_html(room), unsafe_allow_html=True)
        st.caption(f"Salle **{room['code']}** · victoires de la session")
        if len(names) < 2:
            st.markdown("⏳ _en attente d'un joueur_")
        else:
            st.button("↺ Remettre les scores à zéro", on_click=reset_scores, args=(room["code"],),
                      use_container_width=True)
        if me == 0 and room["status"] == "playing":
            st.button("🏠 Retour au salon", on_click=back_to_lobby, args=(room["code"],), use_container_width=True)
        st.button("🚪 Quitter la salle", on_click=leave_room, use_container_width=True)

    st.title(f"🎲 Salle {room['code']}")

    if len(names) < 2:
        st.info(f"Partage ce code avec ton adversaire : **{room['code']}**\n\nLa page se mettra à jour toute seule.")
        return

    if room["status"] == "lobby":
        room_lobby(room)
    elif room["game"] == "Petit Bac":
        bac_screen(room)
    else:
        uno_screen(room)


def main():
    init_session()
    if ss.room and ss.room not in ROOMS:
        ss.room, ss.seat = None, None
        if "r" in st.query_params:
            del st.query_params["r"]
        st.warning("La salle a été fermée.")
    if not ss.room:
        home_screen()
    else:
        room_screen(ROOMS[ss.room])


main()
