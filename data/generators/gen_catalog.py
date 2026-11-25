#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BD516 - Generateur du jeu de donnees metier (catalogue produits + commandes).

Produit des fichiers NDJSON au format de l'API _bulk d'Elasticsearch :
  data/products/products.ndjson         (catalogue, ~500 produits)
  data/orders/orders-001.ndjson ...     (commandes, 1 000 par fichier)
  data/expected-results.txt             (resultats attendus pour les TP)

Le generateur est deterministe (graine fixe) : deux executions produisent
les memes documents ; seules les dates suivent la date du jour (les commandes
couvrent les 365 derniers jours) afin que les requetes "now-30d" aient du sens.

Usage :
  python3 data/generators/gen_catalog.py              # valeurs par defaut
  python3 data/generators/gen_catalog.py --orders 20000
Uniquement la bibliotheque standard Python 3 (aucun pip install).
"""
import argparse
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 516

CATEGORIES = {
    "Randonnée": {
        "code": "RAN",
        "types": [("Chaussures de randonnée", 59, 189), ("Sac à dos", 29, 159),
                  ("Bâtons de marche", 19, 89), ("Veste imperméable", 49, 249),
                  ("Pantalon de randonnée", 29, 99), ("Lampe frontale", 15, 79)],
        "usages": ["la randonnée en montagne", "les treks de plusieurs jours",
                   "les balades en forêt", "la randonnée sous la pluie"],
        "center": [0.9, 0.2, 0.1, 0.1],
    },
    "Running": {
        "code": "RUN",
        "types": [("Chaussures de running", 49, 179), ("Chaussures de trail", 69, 199),
                  ("Montre GPS", 99, 449), ("Tee-shirt technique", 12, 49),
                  ("Ceinture porte-bidon", 9, 39)],
        "usages": ["le running sur route", "le trail sur sentier",
                   "la préparation au marathon", "les sorties fractionnées"],
        "center": [0.6, 0.8, 0.1, 0.1],
    },
    "Vélo": {
        "code": "VEL",
        "types": [("Casque de vélo", 29, 149), ("Vélo tout chemin", 399, 1499),
                  ("Éclairage vélo", 15, 89), ("Cuissard", 29, 119),
                  ("Antivol en U", 19, 79)],
        "usages": ["le vélotaf en ville", "le cyclotourisme",
                   "les sorties sur route", "le VTT en montagne"],
        "center": [0.1, 0.8, 0.6, 0.1],
    },
    "Camping": {
        "code": "CAM",
        "types": [("Tente 2 places", 69, 399), ("Sac de couchage", 39, 299),
                  ("Matelas autogonflant", 29, 129), ("Réchaud à gaz", 19, 99),
                  ("Glacière rigide", 29, 149)],
        "usages": ["le camping en famille", "le bivouac en montagne",
                   "les week-ends en plein air", "le camping itinérant"],
        "center": [0.7, 0.1, 0.1, 0.7],
    },
    "Ski": {
        "code": "SKI",
        "types": [("Skis de randonnée", 299, 899), ("Masque de ski", 29, 189),
                  ("Veste de ski", 99, 449), ("Gants de ski", 19, 99),
                  ("Casque de ski", 49, 199)],
        "usages": ["le ski de piste", "le ski de randonnée",
                   "le freeride hors-piste", "les vacances à la montagne"],
        "center": [0.3, 0.1, 0.9, 0.4],
    },
    "Natation": {
        "code": "NAT",
        "types": [("Lunettes de natation", 9, 49), ("Combinaison néoprène", 99, 399),
                  ("Maillot de bain", 19, 69), ("Bonnet de bain", 5, 19)],
        "usages": ["la natation en piscine", "la natation en eau libre",
                   "le triathlon", "l'aquagym"],
        "center": [0.1, 0.3, 0.2, 0.9],
    },
    "Fitness": {
        "code": "FIT",
        "types": [("Tapis de yoga", 15, 79), ("Haltères réglables", 49, 299),
                  ("Élastiques de musculation", 9, 39), ("Corde à sauter", 7, 29)],
        "usages": ["le fitness à la maison", "le yoga", "la musculation",
                   "le cross-training"],
        "center": [0.2, 0.6, 0.2, 0.6],
    },
    "Escalade": {
        "code": "ESC",
        "types": [("Chaussons d'escalade", 49, 159), ("Baudrier", 39, 119),
                  ("Corde dynamique", 99, 279), ("Casque d'escalade", 39, 119),
                  ("Sac à magnésie", 9, 35)],
        "usages": ["l'escalade en salle", "la grande voie",
                   "le bloc en extérieur", "l'alpinisme"],
        "center": [0.8, 0.3, 0.7, 0.2],
    },
}

BRANDS = ["Altitude", "Azimut", "Boréal", "Iroise", "Kaïros", "Mistral",
          "Nordika", "Ventoux", "Sirocco", "Vercors"]
MODELS = ["Trek", "Alpine", "Evasion", "Horizon", "Summit", "Explorer",
          "Light", "Pro", "Confort", "Expert", "Fusion", "Aventure"]
FEATURES = [
    "Membrane imperméable et respirante",
    "Coutures étanchées pour rester au sec",
    "Semelle crantée pour une excellente adhérence",
    "Tissu stretch déperlant",
    "Fabrication en matériaux recyclés",
    "Ultra léger et compact une fois replié",
    "Réflecteurs pour une meilleure visibilité de nuit",
    "Rembourrage en duvet certifié",
    "Fermetures éclair résistantes à l'eau",
    "Maintien renforcé de la cheville",
    "Séchage rapide après effort",
    "Garantie fabricant de 2 ans",
    "Isolation thermique pour le froid extrême",
    "Réglages multiples pour un ajustement précis",
]
COLORS = ["noir", "bleu", "rouge", "vert", "gris", "jaune", "orange", "blanc"]

CITIES = [  # ville, pays, lat, lon, poids
    ("Paris", "FR", 48.8566, 2.3522, 18), ("Lyon", "FR", 45.7640, 4.8357, 8),
    ("Marseille", "FR", 43.2965, 5.3698, 7), ("Toulouse", "FR", 43.6047, 1.4442, 6),
    ("Nice", "FR", 43.7102, 7.2620, 4), ("Nantes", "FR", 47.2184, -1.5536, 5),
    ("Strasbourg", "FR", 48.5734, 7.7521, 4), ("Montpellier", "FR", 43.6108, 3.8767, 4),
    ("Bordeaux", "FR", 44.8378, -0.5792, 5), ("Lille", "FR", 50.6292, 3.0573, 5),
    ("Rennes", "FR", 48.1173, -1.6778, 4), ("Grenoble", "FR", 45.1885, 5.7245, 5),
    ("Annecy", "FR", 45.8992, 6.1294, 3), ("Chamonix-Mont-Blanc", "FR", 45.9237, 6.8694, 2),
    ("Clermont-Ferrand", "FR", 45.7772, 3.0870, 2), ("Brest", "FR", 48.3904, -4.4861, 2),
    ("Dijon", "FR", 47.3220, 5.0415, 2), ("Tours", "FR", 47.3941, 0.6848, 2),
    ("Bruxelles", "BE", 50.8503, 4.3517, 3), ("Liège", "BE", 50.6326, 5.5797, 1),
    ("Genève", "CH", 46.2044, 6.1432, 2), ("Lausanne", "CH", 46.5197, 6.6323, 1),
    ("Luxembourg", "LU", 49.6116, 6.1319, 1), ("Montréal", "CA", 45.5019, -73.5674, 2),
    ("Québec", "CA", 46.8139, -71.2080, 1),
]
FIRST_NAMES = ["Camille", "Léa", "Manon", "Chloé", "Emma", "Inès", "Sarah", "Julie",
               "Lucas", "Hugo", "Louis", "Nathan", "Gabriel", "Arthur", "Jules", "Thomas",
               "Nicolas", "Julien", "Sophie", "Claire", "Antoine", "Marie", "Paul", "Alice"]
LAST_NAMES = ["Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit",
              "Durand", "Leroy", "Moreau", "Simon", "Laurent", "Lefebvre", "Michel",
              "Garcia", "David", "Bertrand", "Roux", "Vincent", "Fournier", "Morel",
              "Girard", "André", "Mercier", "Dupont", "Lambert", "Bonnet", "Fontaine"]

STATUSES = [("delivered", 62), ("shipped", 12), ("paid", 8), ("new", 4),
            ("cancelled", 9), ("returned", 5)]
CHANNELS = [("web", 55), ("mobile", 30), ("store", 10), ("marketplace", 5)]
PAYMENTS = [("card", 60), ("paypal", 20), ("apple_pay", 10), ("transfer", 5), ("gift_card", 5)]
SEGMENTS = [("particulier", 80), ("pro", 15), ("vip", 5)]
SHIPPING = [("domicile", 50, 4.99, 3), ("relais", 35, 3.49, 4), ("express", 10, 9.90, 1),
            ("retrait_magasin", 5, 0.0, 2)]

POSITIVE = ["Livraison rapide, produit conforme à la description.",
            "Très bonne qualité, je recommande !",
            "Excellent rapport qualité prix, parfait pour mes sorties.",
            "Service client réactif et agréable.",
            "Produit léger et confortable, idéal en montagne.",
            "Colis bien emballé, livraison en avance."]
NEGATIVE = ["Colis arrivé endommagé, emballage insuffisant.",
            "Délai de livraison trop long par rapport à l'annonce.",
            "Taille trop petite, retour effectué sans difficulté.",
            "Produit défectueux à la réception, remboursement demandé.",
            "La couleur ne correspond pas à la photo."]
NEUTRAL = ["Produit correct mais sans plus.",
           "Livraison dans les temps, rien à signaler.",
           "Conforme, mais la notice est uniquement en anglais."]


def wchoice(rng, pairs, w=1):
    """Choix pondere dans une liste de tuples ; le poids est a l'index w."""
    total = sum(p[w] for p in pairs)
    r = rng.random() * total
    acc = 0.0
    for p in pairs:
        acc += p[w]
        if r < acc:
            return p
    return pairs[-1]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize(v):
    n = math.sqrt(sum(x * x for x in v))
    return [round(x / n, 4) for x in v]


def gen_products(rng, n, anchor):
    products = []
    cats = list(CATEGORIES.items())
    for i in range(n):
        cat_name, cat = cats[i % len(cats)]
        ptype, pmin, pmax = rng.choice(cat["types"])
        brand = rng.choice(BRANDS)
        model = f"{rng.choice(MODELS)} {rng.choice([100, 300, 500, 700, 900])}"
        sku = f"{cat['code']}-{i + 1:04d}"
        price = round(rng.uniform(pmin, pmax), 0) - 0.01
        feats = rng.sample(FEATURES, 3)
        created = anchor - timedelta(days=rng.randint(1, 4 * 365), seconds=rng.randint(0, 86399))
        rating = round(min(5.0, max(1.5, rng.gauss(4.1, 0.6))), 1)
        reviews = int(rng.expovariate(1 / 80))
        stock = 0 if rng.random() < 0.06 else rng.randint(1, 500)
        tags = []
        if (anchor - created).days < 90:
            tags.append("nouveauté")
        if rng.random() < 0.15:
            tags.append("promo")
        if "Fabrication en matériaux recyclés" in feats:
            tags.append("éco-responsable")
        if rating >= 4.6 and reviews >= 100:
            tags.append("best-seller")
        doc = {
            "sku": sku,
            "name": f"{ptype} {model} {brand}",
            "category": cat_name,
            "brand": brand,
            "price": price,
            "stock": stock,
            "rating": rating,
            "reviews_count": reviews,
            "created_at": iso(created),
            "tags": tags,
            "colors": sorted(rng.sample(COLORS, rng.randint(1, 3))),
            "embedding": normalize([c + rng.uniform(-0.15, 0.15) for c in cat["center"]]),
        }
        if rng.random() >= 0.03:  # ~3 % des produits n'ont pas de description
            doc["description"] = (f"{ptype} {brand}, idéal pour {rng.choice(cat['usages'])}. "
                                  f"{feats[0]}. {feats[1]}. {feats[2]}. "
                                  f"Poids : {rng.randint(80, 2500)} g.")
        products.append(doc)
    return products


def gen_customers(rng, n):
    customers = []
    for i in range(n):
        city, country, lat, lon, _ = wchoice(rng, CITIES, w=4)
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        customers.append({
            "id": f"C{i + 1:05d}",
            "name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}{i + 1}@example.fr"
                     .replace("é", "e").replace("è", "e").replace("ë", "e"),
            "segment": wchoice(rng, SEGMENTS)[0],
            "city": city,
            "country": country,
            "location": {"lat": round(lat + rng.uniform(-0.05, 0.05), 5),
                         "lon": round(lon + rng.uniform(-0.05, 0.05), 5)},
        })
    return customers


def gen_orders(rng, n, products, customers, anchor):
    orders = []
    weights = [1.0 / (k + 1) ** 0.8 for k in range(len(customers))]  # clients fideles
    cum = []
    acc = 0.0
    for w in weights:
        acc += w
        cum.append(acc)
    pop = [max(1, p["reviews_count"]) for p in products]
    # Generateur dedie aux dates : les autres valeurs ne dependent pas du jour de generation
    rng_dates = random.Random(SEED + 7)
    year = 365 * 86400
    for i in range(n):
        # date : 365 derniers jours, activite en croissance (plus de commandes recentes)
        while True:
            offset = rng_dates.randint(0, year - 1)
            if rng_dates.random() < 0.55 + 0.45 * (1 - offset / year):
                break
        d = anchor - timedelta(seconds=offset)
        r = rng.random() * acc
        lo, hi = 0, len(cum) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cum[mid] < r:
                lo = mid + 1
            else:
                hi = mid
        cust = customers[lo]
        n_items = wchoice(rng, [(1, 50), (2, 30), (3, 15), (4, 5)])[0]
        chosen = rng.choices(products, weights=pop, k=n_items)
        items, seen = [], set()
        for p in chosen:
            if p["sku"] in seen:
                continue
            seen.add(p["sku"])
            q = wchoice(rng, [(1, 75), (2, 18), (3, 7)])[0]
            items.append({"sku": p["sku"], "product_name": p["name"],
                          "category": p["category"], "brand": p["brand"],
                          "quantity": q, "unit_price": p["price"]})
        items_total = round(sum(it["quantity"] * it["unit_price"] for it in items), 2)
        ship_method, _, ship_cost, days = wchoice(rng, SHIPPING)
        if items_total >= 50 and ship_method != "express":
            ship_cost = 0.0
        age_days = (anchor - d).days
        if age_days <= 2:
            status = wchoice(rng, [("new", 40), ("paid", 45), ("cancelled", 15)])[0]
        elif age_days <= 7:
            status = wchoice(rng, [("paid", 20), ("shipped", 60), ("cancelled", 10), ("delivered", 10)])[0]
        else:
            status = wchoice(rng, STATUSES)[0]
        doc = {
            "order_id": f"CMD-{i + 1:06d}",
            "order_date": iso(d),
            "status": status,
            "channel": wchoice(rng, CHANNELS)[0],
            "payment_method": wchoice(rng, PAYMENTS)[0],
            "customer": cust,
            "items": items,
            "items_count": sum(it["quantity"] for it in items),
            "total_amount": round(items_total + ship_cost, 2),
            "shipping": {"method": ship_method, "cost": ship_cost,
                         "delivery_days": days + rng.randint(0, 3)},
            "tags": [],
        }
        if d.month == 11 and d.day >= 20:
            doc["tags"].append("black_friday")
        if d.month == 12 and d.day <= 24:
            doc["tags"].append("noel")
        if status in ("delivered", "returned") and rng.random() < 0.4:
            if status == "returned":
                doc["comment"], doc["rating"] = rng.choice(NEGATIVE), rng.randint(1, 2)
            else:
                kind = rng.random()
                if kind < 0.7:
                    doc["comment"], doc["rating"] = rng.choice(POSITIVE), rng.randint(4, 5)
                elif kind < 0.9:
                    doc["comment"], doc["rating"] = rng.choice(NEUTRAL), 3
                else:
                    doc["comment"], doc["rating"] = rng.choice(NEGATIVE), rng.randint(1, 2)
        orders.append(doc)
    orders.sort(key=lambda o: o["order_date"])
    return orders


def write_bulk(path, docs, id_field):
    with open(path, "w", encoding="utf-8") as f:
        for doc in docs:
            f.write(json.dumps({"index": {"_id": doc[id_field]}}, ensure_ascii=False) + "\n")
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Generateur du jeu de donnees BD516")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--out", default=str(root), help="repertoire data/ (defaut : %(default)s)")
    parser.add_argument("--products", type=int, default=500)
    parser.add_argument("--customers", type=int, default=2000)
    parser.add_argument("--orders", type=int, default=10000)
    parser.add_argument("--chunk", type=int, default=1000, help="documents par fichier bulk")
    args = parser.parse_args()

    rng = random.Random(SEED)
    anchor = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out = Path(args.out)
    (out / "products").mkdir(parents=True, exist_ok=True)
    (out / "orders").mkdir(parents=True, exist_ok=True)
    for old in (out / "orders").glob("orders-*.ndjson"):
        old.unlink()

    products = gen_products(rng, args.products, anchor)
    customers = gen_customers(rng, args.customers)
    orders = gen_orders(rng, args.orders, products, customers, anchor)

    write_bulk(out / "products" / "products.ndjson", products, "sku")
    files = 0
    for k in range(0, len(orders), args.chunk):
        files += 1
        write_bulk(out / "orders" / f"orders-{files:03d}.ndjson", orders[k:k + args.chunk], "order_id")

    # ---- resultats attendus (pour verifier les requetes des TP) ----
    st = Counter(o["status"] for o in orders)
    ch = Counter(o["channel"] for o in orders)
    seg = Counter(o["customer"]["segment"] for o in orders)
    revenue = sum(o["total_amount"] for o in orders)
    cat_rev = defaultdict(float)
    for o in orders:
        for it in o["items"]:
            cat_rev[it["category"]] += it["quantity"] * it["unit_price"]
    lines = [
        "BD516 - resultats attendus (generes le %s UTC)" % anchor.strftime("%Y-%m-%d"),
        "",
        f"Produits                 : {len(products)} (sans description : {sum('description' not in p for p in products)}, stock = 0 : {sum(p['stock'] == 0 for p in products)})",
        f"Commandes                : {len(orders)} dans {files} fichiers bulk",
        f"Clients distincts        : {len({o['customer']['id'] for o in orders})}",
        f"Commandes avec commentaire : {sum('comment' in o for o in orders)}",
        f"Chiffre d'affaires total : {revenue:.2f} EUR",
        f"Panier moyen             : {revenue / len(orders):.2f} EUR",
        f"Montant max              : {max(o['total_amount'] for o in orders):.2f} EUR",
        "",
        "Commandes par statut     : " + ", ".join(f"{k}={v}" for k, v in st.most_common()),
        "Commandes par canal      : " + ", ".join(f"{k}={v}" for k, v in ch.most_common()),
        "Commandes par segment    : " + ", ".join(f"{k}={v}" for k, v in seg.most_common()),
        "",
        "CA articles par categorie (quantite x prix unitaire) :",
    ]
    for k, v in sorted(cat_rev.items(), key=lambda kv: -kv[1]):
        lines.append(f"   {k:<12} {v:12.2f} EUR")
    (out / "expected-results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nFichiers ecrits dans {out}/products et {out}/orders")


if __name__ == "__main__":
    main()
