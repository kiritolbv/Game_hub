"""
🎲 Hub de jeux à deux — Petit Bac + UNO
Multijoueur en réseau via un état partagé côté serveur (st.cache_resource).
Lancer :  streamlit run app.py
"""
import copy
import html
import random
import re
import string
import threading
import time
import unicodedata
import uuid

import streamlit as st
import streamlit.components.v1 as components

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
    """Retour au menu principal (choix du jeu)."""
    with LOCK:
        room = ROOMS.get(code)
        if room:
            room.update(status="lobby", game=None, bac=None, uno=None)
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
    b["stop_at"] = None  # moment où le 1er joueur a cliqué sur « Terminé »
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


AUTO_STOP_JS = """<script>
(function () {
  var tries = 0;
  function go() {
    var btns = window.parent.document.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
      if (btns[i].innerText && btns[i].innerText.indexOf('Terminé') > -1 && !btns[i].disabled) {
        btns[i].click();
        return;
      }
    }
    if (tries++ < 12) setTimeout(go, 250);
  }
  setTimeout(go, 150);
})();
</script>"""


def bac_confirm_contest(code, me, cats, bid, rnd):
    with LOCK:
        room = ROOMS.get(code)
        if not room or not room["bac"]:
            return
        room["bac"]["rejects"][me] = {c for c in cats if ss.get(f"rej_{bid}_{rnd}_{c}")}
        touch(room)


def bac_finalize_if_due(code):
    """Si un joueur a fini et que l'autre ne répond pas (onglet fermé…), on termine la manche."""
    with LOCK:
        room = ROOMS.get(code)
        if not room or room["status"] != "playing" or room["game"] != "Petit Bac" or not room["bac"]:
            return
        b = room["bac"]
        if b["phase"] == "playing" and b["stop_at"] and time.time() - b["stop_at"] > 5:
            b["submitted"] = [True, True]
            b["phase"] = "review"
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


def review_table(b, rows, me, opp, names):
    """Tableau HTML des résultats (lisible aussi sur téléphone)."""

    def cell(cat, ans, ok, pts, p):
        a = html.escape(ans[p]) if ans[p].strip() else "—"
        if ans[p].strip() and cat in b["rejects"][1 - p]:
            return f"🚫 <s>{a}</s> <i>contesté</i> <b>+0</b>"
        return f"{'✅' if ok[p] else '❌'} {a} <b>+{pts[p]}</b>"

    out = [
        "<table style='width:100%;border-collapse:collapse;font-size:15px'>",
        f"<tr style='text-align:left;opacity:.7'><th style='padding:6px 4px'>Catégorie</th><th>Toi</th>"
        f"<th>{html.escape(names[opp])}</th></tr>",
    ]
    for cat, ans, ok, pts in rows:
        out.append(
            f"<tr style='border-top:1px solid rgba(128,128,128,.3)'>"
            f"<td style='padding:6px 4px;font-weight:600'>{html.escape(cat)}</td>"
            f"<td>{cell(cat, ans, ok, pts, me)}</td><td>{cell(cat, ans, ok, pts, opp)}</td></tr>"
        )
    out.append("</table>")
    return "".join(out)


def bac_screen(room):
    b, s = room["bac"], room["bac_settings"]
    me, opp = ss.seat, 1 - ss.seat
    names = room["players"]
    host = me == 0

    st.subheader(f"Petit Bac — manche {b['round']}/{s['rounds']}")
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
            st.success(f"Manche terminée — résultats dans un instant…")
        else:
            if b["stop_at"] is not None:
                st.warning(f"⏱️ {esc(names[opp])} a terminé : la manche s'arrête, envoi de tes réponses…")
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
                    if b["stop_at"] is None:
                        b["stop_at"] = time.time()
                    if all(b["submitted"]):
                        b["phase"] = "review"
                    touch(room)
                st.rerun()
            elif b["stop_at"] is not None:
                # l'adversaire a fini : on envoie automatiquement nos réponses (ce qui est déjà tapé)
                components.html(AUTO_STOP_JS, height=0)
        st.caption("Barème : 2 pts si ta réponse est différente de celle de l'autre, 1 pt si vous avez la même, 0 si vide ou mauvaise lettre.")

    elif b["phase"] == "review":
        st.markdown(f"### Lettre **{b['letter']}** — résultats")
        rows, tot = score_round(b, s)
        lost = [c for c in s["cats"] if c in b["rejects"][opp] and b["answers"][me].get(c, "").strip()]
        if lost:
            st.warning(f"⚖️ {esc(names[opp])} a contesté : {esc(', '.join(lost))} (0 point pour ces réponses)")
        st.markdown(review_table(b, rows, me, opp, names), unsafe_allow_html=True)
        st.markdown(f"**Points de la manche :** toi {tot[me]} · {esc(names[opp])} {tot[opp]}")

        opp_answers = [(c, b["answers"][opp].get(c, "")) for c in s["cats"] if b["answers"][opp].get(c, "").strip()]
        if opp_answers:
            with st.expander(f"⚖️ Contester les réponses de {names[opp]}"):
                st.caption("Coche la ou les réponses que tu juges fausses, puis confirme : elles passeront à 0 point.")
                cats_c = []
                for cat, a in opp_answers:
                    key = f"rej_{b['id']}_{b['round']}_{cat}"
                    if key not in ss:
                        ss[key] = cat in b["rejects"][me]
                    st.checkbox(f"{esc(cat)} : {esc(a)}", key=key)
                    cats_c.append(cat)
                st.button(
                    "✅ Confirmer la contestation",
                    key=f"conf_{b['id']}_{b['round']}",
                    type="primary",
                    on_click=bac_confirm_contest,
                    args=(room["code"], me, cats_c, b["id"], b["round"]),
                    use_container_width=True,
                )
                n_c = len(b["rejects"][me])
                if n_c:
                    st.caption(f"Contestation enregistrée : {n_c} réponse{'s' if n_c > 1 else ''} à 0 point.")

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
            c2.button("🏠 Menu principal", on_click=back_to_lobby, args=(room["code"],), use_container_width=True)
        else:
            st.caption("L'hôte peut relancer une partie ou revenir au menu.")


# ════════════════════════════════════════════════════════════════
#  UNO
# ════════════════════════════════════════════════════════════════

COLOR_NAMES = {"R": "Rouge", "Y": "Jaune", "G": "Vert", "B": "Bleu", "K": "Noir"}
CSS_COLORS = {"R": "#d72600", "Y": "#e6c700", "G": "#2e8b17", "B": "#0063b3", "K": "#222222"}
GRAD = "linear-gradient(135deg,#d72600 25%,#e6c700 25% 50%,#2e8b17 50% 75%,#0063b3 75%)"
# symboles texte (pas d'emoji) pour les cartes spéciales
BIG = {"skip": "⊘", "reverse": "⇄", "+2": "+2", "+4": "+4", "wild": "★"}
SMALL = {"skip": "Passe", "reverse": "Inversion", "+2": "+2", "+4": "+4", "wild": "Joker"}

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
    return SMALL[v] if c == "K" else f"{COLOR_NAMES[c]} {SMALL.get(v, v)}"


def card_bg(card):
    return GRAD if card == "K:wild" else CSS_COLORS[card.split(":")[0]]


def card_html(card, cur_color):
    """Carte de la défausse. La bordure indique la couleur en cours (utile après un joker)."""
    c, v = card.split(":")
    fg = "#000" if c == "Y" else "#fff"
    return (
        f"<div style='width:84px;height:122px;border-radius:12px;background:{card_bg(card)};color:{fg};"
        f"font-size:36px;font-weight:800;display:flex;align-items:center;justify-content:center;"
        f"border:5px solid {CSS_COLORS[cur_color]};box-shadow:0 3px 10px rgba(0,0,0,.35);flex:0 0 auto'>"
        f"{BIG.get(v, v)}</div>"
    )


def backs_html(n):
    n = min(n, 24)
    cards = "".join(
        f"<div style='width:26px;height:38px;border-radius:5px;background:linear-gradient(135deg,#5a1208,#b32410);"
        f"margin-left:{0 if i == 0 else -15}px;box-shadow:-2px 0 4px rgba(0,0,0,.4)'></div>"
        for i in range(n)
    )
    return f"<div style='display:flex;justify-content:center;padding:2px 0 8px'>{cards}</div>"


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
        "uno_said": [False, False],
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


def apply_card(u, r, p, card, chosen, last):
    """Pose une carte sur l'état u (modifié en place). `last` = dernière carte du groupe joué."""
    c, v = card.split(":")
    u["uno_vuln"] = None
    u["hands"][p].remove(card)
    u["discard"].append(card)
    u["color"] = chosen if c == "K" else c
    u["drawn"] = False
    if not u["hands"][p]:
        u["winner"] = p
        return
    nxt = 1 - p
    if v in ("skip", "reverse"):
        nxt = p
    elif v == "+2":
        u["pending"] += 2
    elif v == "+4":
        u["pending"] += 4
    elif v in ("7", "0") and r["seven_zero"] and last:
        u["hands"][0], u["hands"][1] = u["hands"][1], u["hands"][0]
    u["turn"] = nxt
    if last:
        if r["uno_call"] and len(u["hands"][p]) == 1 and not u["uno_said"][p]:
            u["uno_vuln"] = p
        u["uno_said"][p] = False


def _simulate(u, r, p, seq, chosen):
    """Joue la séquence sur une copie. Renvoie la copie, ou None si un coup est illégal."""
    w = copy.deepcopy(u)
    for k, card in enumerate(seq):
        if k:
            w["turn"] = p  # on enchaîne : c'est toujours à nous
        if card not in w["hands"][p] or not can_play(w, r, p, card):
            return None
        if card.startswith("K:") and chosen not in ("R", "Y", "G", "B"):
            return None
        apply_card(w, r, p, card, chosen, k == len(seq) - 1)
    return w


def plan_group(u, r, p, cards, chosen=None):
    """Plusieurs cartes de MÊME VALEUR (couleurs libres). Renvoie (ordre, état_final) ou None."""
    if not cards or len({c.split(":")[1] for c in cards}) != 1:
        return None
    idx = list(range(len(cards)))
    orders = [idx] + [[i] + [j for j in idx if j != i] for i in idx if can_play(u, r, p, cards[i])]
    for o in orders:
        seq = [cards[i] for i in o]
        w = _simulate(u, r, p, seq, chosen)
        if w is not None:
            return seq, w
    return None


def uno_play_group(code, p, cards, chosen):
    with LOCK:
        room, u, r = _uno_ctx(code)
        if not room or u["winner"] is not None:
            return
        res = plan_group(u, r, p, cards, chosen)
        if res is None:
            return
        seq, w = res
        name = room["players"][p]
        jump = u["turn"] != p
        w["log"].append(
            f"{'⚡ ' if jump else ''}{name} joue {', '.join(card_label(c) for c in seq)}"
            + (f" → {COLOR_NAMES[w['color']]}" if seq[0].startswith("K:") else "")
        )
        if w["winner"] is not None:
            room["wins"][p] += 1
            w["log"].append(f"🏆 {name} a gagné !")
        room["uno"] = w
        touch(room)


def uno_say(code, p):
    with LOCK:
        room, u, r = _uno_ctx(code)
        if not room or u["winner"] is not None or not r["uno_call"] or len(u["hands"][p]) > 2:
            return
        u["uno_said"][p] = True
        if u["uno_vuln"] == p:
            u["uno_vuln"] = None
        u["log"].append(f"📣 {room['players'][p]} annonce UNO !")
        touch(room)


def clear_sel():
    ss.uno_sel = None


def on_card_click(code, p, idx, card, u_id):
    """Clic sur une carte : sélection (elle sort de l'éventail), ajout d'une carte de même valeur,
    ou, si elle est déjà sélectionnée, on joue tout le groupe."""
    sel = ss.get("uno_sel")
    items = list(sel["items"]) if sel and sel["id"] == u_id else []
    it = (idx, card)
    val = card.split(":")[1]
    if it in items:
        if card.startswith("K:"):  # jokers : on joue via le choix de couleur
            items.remove(it)
            ss.uno_sel = {"id": u_id, "items": items} if items else None
        else:
            ss.uno_sel = None
            uno_play_group(code, p, [c for _, c in items], None)
    else:
        if items and items[0][1].split(":")[1] == val:
            items.append(it)
        else:
            items = [it]
        ss.uno_sel = {"id": u_id, "items": items}


def on_pick_color(code, p, color):
    sel = ss.get("uno_sel")
    ss.uno_sel = None
    if sel:
        uno_play_group(code, p, [c for _, c in sel["items"]], color)


def uno_draw(code, p):
    with LOCK:
        room, u, r = _uno_ctx(code)
        if not room or u["winner"] is not None or u["turn"] != p:
            return
        name = room["players"][p]
        u["uno_vuln"] = None
        u["uno_said"][p] = False
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


ROW_MAX = 8  # cartes max par rangée de l'éventail


def hand_rows(n):
    if n <= ROW_MAX:
        return [(0, n)]
    k = -(-n // ROW_MAX)
    size = -(-n // k)
    return [(i, min(i + size, n)) for i in range(0, n, size)]


def fan_css(u_id, hand, enabled, selected):
    """CSS qui transforme les boutons de la main en éventail (une ou plusieurs rangées)."""
    css = []
    for a, b in hand_rows(len(hand)):
        m = b - a
        mid = (m - 1) / 2
        ratio = 0.72 if m <= 3 else 0.6 if m <= 5 else 0.52
        spread = 0 if m <= 1 else min(34, 6 * (m - 1))
        for i in range(a, b):
            c, v = hand[i].split(":")
            t = 0 if m <= 1 else (i - a - mid) / mid
            angle = t * spread / 2
            dy = (t ** 2) * 14 - (10 if enabled[i] else 0)
            bg = card_bg(hand[i])
            fg = "#000" if c == "Y" else "#fff"
            fs = 0.32 if v in ("skip", "reverse", "wild") else 0.40
            ml = "0" if i == a else f"calc(var(--cw) * -{1 - ratio:.2f})"
            k = f"uc_{u_id}_{i}"
            if i in selected:
                tr, z, sh = "translateY(-34px) rotate(0deg)", 99, "0 10px 22px rgba(0,0,0,.55)"
            else:
                tr, z, sh = f"translateY({dy:.1f}px) rotate({angle:.1f}deg)", i, "-3px 0 8px rgba(0,0,0,.35)"
            css.append(
                f".st-key-{k}{{width:var(--cw) !important;margin-left:{ml};transform:{tr};"
                f"transform-origin:50% 140%;transition:transform .12s;position:relative;z-index:{z};}}"
            )
            if c != "K":
                # retour visuel immédiat au clic (avant même la réponse du serveur)
                css.append(
                    f".st-key-{k}:has(button:focus:not(:disabled)){{transform:translateY(-34px) rotate(0deg);z-index:99;}}"
                )
            css.append(
                f".st-key-{k} button,.st-key-{k} button:hover,.st-key-{k} button:focus,.st-key-{k} button:active{{"
                f"width:var(--cw) !important;height:calc(var(--cw) * 1.46) !important;min-height:0 !important;"
                f"padding:0 !important;border-radius:calc(var(--cw) * .14) !important;background:{bg} !important;"
                f"color:{fg} !important;border:none !important;outline:none !important;box-shadow:{sh};}}"
            )
            css.append(
                f".st-key-{k} button p{{font-size:calc(var(--cw) * {fs}) !important;font-weight:800 !important;"
                f"color:{fg} !important;line-height:1 !important;}}"
            )
            css.append(f".st-key-{k} button:disabled{{opacity:.5;filter:saturate(.6);}}")
    return "".join(css)


@st.fragment
def uno_hand_zone(code, me):
    """Zone de jeu (actions + main). C'est un fragment : un clic sur une carte ne relance
    que cette zone, pas toute la page → plus de latence à la sélection."""
    room = ROOMS.get(code)
    if (not room or room["status"] != "playing" or room["game"] != "UNO" or not room["uno"]
            or room["version"] != ss.get("seen_version")):
        st.rerun()  # l'état a changé : on relance toute la page
        return
    u, r = room["uno"], room["uno_rules"]
    opp = 1 - me
    my_turn = u["turn"] == me and u["winner"] is None

    hand = sorted(u["hands"][me], key=sort_key)
    playable = [can_play(u, r, me, card) for card in hand]

    # sélection courante (une ou plusieurs cartes de même valeur)
    sel = ss.get("uno_sel")
    items = []
    if sel and sel["id"] == u["id"] and all(i < len(hand) and hand[i] == c for i, c in sel["items"]):
        items = sel["items"]
    else:
        ss.uno_sel = None
    sel_idx = {i for i, _ in items}
    sel_val = items[0][1].split(":")[1] if items else None
    enabled = [
        (i in sel_idx) or playable[i] or (sel_val is not None and hand[i].split(":")[1] == sel_val)
        for i in range(len(hand))
    ]

    # actions
    show_uno = r["uno_call"] and len(hand) <= 2 and (len(hand) == 2 or u["uno_vuln"] == me)
    with st.container(key="uact"):
        st.button(
            f"Piocher {u['pending']}" if u["pending"] else "Piocher",
            key="act_draw", on_click=uno_draw, args=(code, me),
            disabled=not my_turn or (u["drawn"] and u["pending"] == 0),
        )
        if my_turn and u["drawn"] and u["pending"] == 0:
            st.button("Passer", key="act_pass", on_click=uno_pass, args=(code, me))
        if show_uno and not u["uno_said"][me]:
            st.button("UNO !", key="act_uno", on_click=uno_say, args=(code, me))
        if r["uno_call"] and u["uno_vuln"] == opp:
            st.button("Contre-UNO !", key="act_counter", on_click=uno_challenge, args=(code, me), type="primary")
    if r["uno_call"] and u["uno_said"][me]:
        st.caption("📣 UNO annoncé : joue ta carte.")

    # aide / choix de couleur
    if items:
        cards = [c for _, c in items]
        black = cards[0].startswith("K:")
        ok = plan_group(u, r, me, cards, "R" if black else None) is not None
        if not ok:
            st.warning("Cette combinaison ne peut pas être jouée.")
        elif black:
            extra = f" — l'adversaire piochera {4 * len(cards)} cartes" if cards[0] == "K:+4" else ""
            st.markdown(f"**Choisis la couleur{extra}**")
            with st.container(key="cpick"):
                for col in "RYGB":
                    st.button(COLOR_NAMES[col], key=f"cp_{col}", on_click=on_pick_color, args=(code, me, col))
        else:
            n = len(cards)
            st.caption(
                f"{n} carte{'s' if n > 1 else ''} sélectionnée{'s' if n > 1 else ''} — re-clique sur l'une d'elles "
                "pour jouer. Tu peux ajouter d'autres cartes de même valeur."
            )
        st.button("Annuler la sélection", key="act_cancel", on_click=clear_sel)
    elif my_turn and any(playable):
        st.caption("Clique une carte pour la sélectionner (et d'autres de même valeur), re-clique pour jouer.")
    elif my_turn and not u["drawn"]:
        st.caption("Aucune carte jouable : pioche !")

    # main en éventail
    st.markdown(f"**Ta main ({len(hand)})**")
    st.markdown(f"<style>{fan_css(u['id'], hand, enabled, sel_idx)}</style>", unsafe_allow_html=True)
    for ri, (a, b) in enumerate(hand_rows(len(hand))):
        with st.container(key=f"uhand_{ri}"):
            for i in range(a, b):
                val = hand[i].split(":")[1]
                st.button(
                    BIG.get(val, val),
                    key=f"uc_{u['id']}_{i}",
                    disabled=not enabled[i],
                    on_click=on_card_click,
                    args=(code, me, i, hand[i], u["id"]),
                )


def uno_screen(room):
    u, r = room["uno"], room["uno_rules"]
    me, opp = ss.seat, 1 - ss.seat
    names = room["players"]
    code = room["code"]
    host = me == 0
    my_turn = u["turn"] == me and u["winner"] is None
    cur = u["color"]

    st.subheader("UNO")

    n_opp = len(u["hands"][opp])
    st.markdown(f"**{esc(names[opp])}** — {n_opp} carte{'s' if n_opp > 1 else ''}")
    st.markdown(backs_html(n_opp), unsafe_allow_html=True)

    dot = (f"<span style='display:inline-block;width:14px;height:14px;border-radius:50%;"
           f"background:{CSS_COLORS[cur]};vertical-align:middle;margin-right:6px'></span>")
    pend = f"<br>⚠️ <b>Pénalité en cours : +{u['pending']}</b>" if u["pending"] else ""
    st.markdown(
        f"<div style='display:flex;align-items:center;justify-content:center;gap:18px;margin:4px 0 8px'>"
        f"{card_html(u['discard'][-1], cur)}"
        f"<div style='line-height:1.8'>{dot}Couleur : <b>{COLOR_NAMES[cur]}</b><br>"
        f"Pioche : {len(u['deck'])} cartes{pend}</div></div>",
        unsafe_allow_html=True,
    )

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
            c2.button("🏠 Menu principal", on_click=back_to_lobby, args=(code,), use_container_width=True)
        else:
            st.caption("L'hôte peut relancer une partie ou revenir au menu.")
        return

    if my_turn:
        if u["pending"] > 0:
            st.warning(f"⚠️ Réponds par une carte autorisée ou pioche {u['pending']} cartes.")
        else:
            st.success("🟢 À toi de jouer !")
    else:
        st.info(f"⏳ Au tour de {names[opp]}" + (" — tu peux interrompre avec une carte identique." if r["jump_in"] else ""))

    uno_hand_zone(code, me)


# ════════════════════════════════════════════════════════════════
#  SALON / ACCUEIL
# ════════════════════════════════════════════════════════════════


@st.fragment(run_every=2)
def watcher(code):
    """Vérifie toutes les 2 s si l'état de la salle a changé et relance la page si oui."""
    bac_finalize_if_due(code)
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


GLOBAL_CSS = "".join([
    ":root{--cw:clamp(50px,13.5vw,80px);}",
    # --- menu principal : 2 grandes cartes
    ".st-key-pick_uno button,.st-key-pick_bac button{height:170px !important;border-radius:22px !important;"
    "border:none !important;color:#fff !important;box-shadow:0 8px 20px rgba(0,0,0,.3);}",
    ".st-key-pick_uno button,.st-key-pick_uno button:hover{background:linear-gradient(135deg,#e0361a,#8e1600) !important;}",
    ".st-key-pick_bac button,.st-key-pick_bac button:hover{background:linear-gradient(135deg,#0a74d1,#2e9b1f) !important;}",
    ".st-key-pick_uno button p,.st-key-pick_bac button p{font-size:34px !important;font-weight:900 !important;color:#fff !important;}",
    ".st-key-pick_uno button:disabled,.st-key-pick_bac button:disabled{opacity:1 !important;cursor:default;}",
    # --- UNO : rangées de boutons, éventail, choix de couleur
    ".st-key-uact,.st-key-cpick{display:flex !important;flex-direction:row !important;flex-wrap:nowrap !important;gap:.5rem !important;}",
    ".st-key-uact > div,.st-key-cpick > div{flex:1 1 0 !important;min-width:0 !important;width:auto !important;}",
    ".st-key-uact button,.st-key-cpick button{width:100% !important;}",
    ".st-key-act_uno button,.st-key-act_uno button:hover{background:#d72600 !important;color:#fff !important;border:none !important;}",
    ".st-key-act_uno button p{color:#fff !important;font-weight:900 !important;}",
    ".st-key-cp_R button,.st-key-cp_Y button,.st-key-cp_G button,.st-key-cp_B button{height:52px !important;border:none !important;}",
    ".st-key-cp_R button,.st-key-cp_R button:hover{background:#d72600 !important;color:#fff !important;}",
    ".st-key-cp_Y button,.st-key-cp_Y button:hover{background:#e6c700 !important;color:#000 !important;}",
    ".st-key-cp_G button,.st-key-cp_G button:hover{background:#2e8b17 !important;color:#fff !important;}",
    ".st-key-cp_B button,.st-key-cp_B button:hover{background:#0063b3 !important;color:#fff !important;}",
    ".st-key-cp_R button p,.st-key-cp_G button p,.st-key-cp_B button p{color:#fff !important;font-weight:800 !important;}",
    ".st-key-cp_Y button p{color:#000 !important;font-weight:800 !important;}",
    "[class*='st-key-uhand_']{display:flex !important;flex-direction:row !important;flex-wrap:nowrap !important;"
    "justify-content:center;align-items:flex-end;gap:0 !important;padding:30px 6px 4px;}",
    "[class*='st-key-uhand_'] > div{width:auto !important;flex:0 0 auto !important;}",
    # --- barre du bas (téléphone uniquement)
    ".st-key-bbar{display:none !important;}",
    "@media (max-width:768px){",
    ".st-key-pick_uno button,.st-key-pick_bac button{height:120px !important;}",
    ".st-key-bbar{display:flex !important;flex-direction:row !important;align-items:center;gap:10px !important;"
    "position:fixed;bottom:0;left:0;right:0;z-index:1000;padding:8px 12px calc(8px + env(safe-area-inset-bottom,0px));"
    "background:rgba(24,26,38,.97);border-top:1px solid rgba(255,255,255,.18);}",
    ".st-key-bbar > div{width:auto !important;flex:0 0 auto !important;}",
    ".st-key-bbar > div:last-child{flex:1 1 auto !important;min-width:0 !important;}",
    ".st-key-bbar button,.st-key-bbar button:hover{background:#3b3f5c !important;color:#fff !important;border:none !important;"
    "min-height:0 !important;padding:.45rem .9rem !important;}",
    ".st-key-bbar button p{color:#fff !important;font-weight:700;}",
    ".st-key-bbar button:disabled{opacity:.45;}",
    "[data-testid='stMainBlockContainer'],.block-container{padding:1rem .8rem 6.5rem !important;}",
    "h1{font-size:1.7rem !important;} h2,h3{font-size:1.25rem !important;}",
    "}",
])


def inject_css():
    st.markdown(f"<style>{GLOBAL_CSS}</style>", unsafe_allow_html=True)


def pick_game(code, game):
    with LOCK:
        room = ROOMS.get(code)
        if room and room["status"] == "lobby":
            room["game"] = game
            touch(room)


def reset_scores(code):
    with LOCK:
        room = ROOMS.get(code)
        if room:
            room["wins"] = [0, 0]
            touch(room)


def _live_line(room):
    if room["status"] != "playing":
        return ""
    if room["game"] == "Petit Bac" and room["bac"]:
        b = room["bac"]
        return f"Manche {b['round']}/{room['bac_settings']['rounds']} · {b['scores'][0]} – {b['scores'][1]} pts"
    return ""


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
    live = _live_line(room)
    if live and len(names) == 2:
        rows += f"<div style='font-size:13px;opacity:.8;margin-top:2px'>{live}</div>"
    return rows


def scorebar_html(room):
    names, wins = room["players"], room["wins"]
    if len(names) < 2:
        return "<div style='color:#fff;font-size:14px'>⏳ En attente d'un joueur…</div>"
    tie = wins[0] == wins[1]
    nm = "display:inline-block;max-width:70px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom"
    crown = lambda i: "👑 " if (not tie and wins[i] == max(wins)) else ""
    return (
        f"<div style='color:#fff;display:flex;justify-content:center;align-items:center;gap:8px;font-size:14px'>"
        f"<span>{crown(0)}<span style='{nm}'>{html.escape(names[0])}</span> <b style='font-size:20px'>{wins[0]}</b></span>"
        f"<span style='opacity:.6'>–</span>"
        f"<span><b style='font-size:20px'>{wins[1]}</b> <span style='{nm}'>{html.escape(names[1])}</span>{crown(1).replace('👑 ', ' 👑')}</span>"
        f"</div>"
    )


def bottom_bar(room):
    host = ss.seat == 0
    disabled = room["status"] == "lobby" and (room["game"] is None or not host)
    with st.container(key="bbar"):
        st.button("🏠 Menu", key="bar_menu", on_click=back_to_lobby, args=(room["code"],), disabled=disabled)
        st.markdown(scorebar_html(room), unsafe_allow_html=True)


def room_lobby(room):
    host = ss.seat == 0
    code = room["code"]
    game = room["game"]

    # ── MENU PRINCIPAL : seulement les deux grandes cartes
    if not game:
        st.subheader("Choisis un jeu" if host else "L'hôte choisit le jeu…")
        c1, c2 = st.columns(2)
        c1.button("UNO", key="pick_uno", on_click=pick_game, args=(code, "UNO"),
                  disabled=not host, use_container_width=True)
        c2.button("Petit Bac", key="pick_bac", on_click=pick_game, args=(code, "Petit Bac"),
                  disabled=not host, use_container_width=True)
        if not host:
            st.caption("⏳ En attente du choix de l'hôte…")
        return

    # ── MENU DU JEU : réglages (les cartes ont disparu)
    if host:
        st.button("← Menu principal", key="back_main", on_click=back_to_lobby, args=(code,))
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
        st.button(f"▶️ Lancer {game}", type="primary", on_click=start_game, args=(code,),
                  disabled=not can_start, use_container_width=True)
    else:
        st.caption("⏳ En attente que l'hôte lance la partie…")


def room_screen(room):
    ss.seen_version = room["version"]
    watcher(room["code"])
    me = ss.seat
    names = room["players"]
    code = room["code"]

    with st.sidebar:
        st.markdown("### 🏆 Tableau des scores")
        st.markdown(scoreboard_html(room), unsafe_allow_html=True)
        st.caption(f"Salle **{code}** · victoires de la session")
        if len(names) < 2:
            st.markdown("⏳ _en attente d'un joueur_")
        else:
            st.button("↺ Remettre les scores à zéro", key="side_reset", on_click=reset_scores, args=(code,),
                      use_container_width=True)
        menu_off = room["status"] == "lobby" and (room["game"] is None or me != 0)
        st.button("🏠 Menu principal", key="side_menu", on_click=back_to_lobby, args=(code,),
                  disabled=menu_off, use_container_width=True)
        st.button("🚪 Quitter la salle", key="side_leave", on_click=leave_room, use_container_width=True)

    st.title(f"🎲 Salle {code}")

    if len(names) < 2:
        st.info(f"Partage ce code avec ton adversaire : **{code}**\n\nLa page se mettra à jour toute seule.")
    elif room["status"] == "lobby":
        room_lobby(room)
    elif room["game"] == "Petit Bac":
        bac_screen(room)
    else:
        uno_screen(room)

    bottom_bar(room)


def main():
    inject_css()
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
