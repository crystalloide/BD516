#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BD516 - Generateur de logs pour les TP Logstash / Kibana.

Deux sources simulees d'une boutique en ligne "BD516 Shop" :
  * logs applicatifs Java de l'API "shop-api" (multi-lignes pour les stack traces)
      data/logs/app/shop-api.log
  * logs d'acces du serveur web nginx (format "combined")
      data/logs/web/access.log

Commandes :
  python3 data/generators/gen_logs.py sample   # petit echantillon pour mettre au point grok
  python3 data/generators/gen_logs.py batch    # historique des 7 derniers jours
  python3 data/generators/gen_logs.py follow   # ajoute des evenements "en direct" (Ctrl+C pour arreter)

Les horodatages sont exprimes dans le fuseau Europe/Paris :
  * logs applicatifs : "2026-11-25 14:03:12.345" (SANS decalage horaire -> filtre date + timezone)
  * logs nginx       : "25/Nov/2026:14:03:12 +0100" (AVEC decalage horaire)
Uniquement la bibliotheque standard Python 3 (aucun pip install).
"""
import argparse
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
SEED = 516
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HOUR_WEIGHT = [0.20, 0.15, 0.10, 0.08, 0.08, 0.12, 0.30, 0.60, 0.90, 1.00, 1.00, 1.05,
               1.15, 1.20, 1.10, 1.00, 1.00, 1.10, 1.30, 1.40, 1.30, 1.00, 0.60, 0.35]

# Plages d'adresses IP publiques par pays (bloc /16, partie hote aleatoire)
IP_POOLS = [
    ("FR", 70, ["90.84", "90.12", "90.110", "82.64", "82.65", "88.160", "88.170", "2.10", "176.140"]),
    ("BE", 5, ["81.240", "81.241", "81.244"]),
    ("CH", 4, ["85.0", "85.1", "85.4"]),
    ("DE", 4, ["79.200", "87.150", "91.10", "93.200"]),
    ("GB", 3, ["86.140", "86.150", "81.2"]),
    ("ES", 3, ["83.40", "83.50", "88.10"]),
    ("CA", 3, ["99.230", "99.240"]),
    ("US", 4, ["73.10", "73.120", "73.200"]),
    ("JP", 1, ["126.10", "126.100"]),
    ("BR", 1, ["177.50", "187.10"]),
]
INTERNAL_IPS = ["10.0.12.5", "10.0.12.6", "10.0.14.21"]

SEARCH_TERMS = ["chaussures randonnée", "tente", "veste ski", "sac à dos", "montre gps",
                "casque vélo", "lunettes natation", "tapis yoga", "baudrier", "lampe frontale",
                "gourde", "trail", "doudoune", "gants", "corde escalade"]
CATS = ["RAN", "RUN", "VEL", "CAM", "SKI", "NAT", "FIT", "ESC"]
CAT_SLUGS = ["randonnee", "running", "velo", "camping", "ski", "natation", "fitness", "escalade"]

UAS = [
    (30, "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    (12, "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    (15, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"),
    (18, "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
    (8, "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15"),
    (4, "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    (5, "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    (2, "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"),
    (1, "curl/8.5.0"),
]
REFERRERS = [(50, "-"), (25, "https://www.google.com/"), (15, "https://shop.bd516.example/"),
             (5, "https://www.bing.com/"), (5, "https://www.facebook.com/")]

# method, chemin, logger, latence de base (ms), poids, statuts possibles (code, poids)
ENDPOINTS = [
    ("GET", "/api/v1/products", "ProductController", 40, 30, [(200, 97), (400, 2), (500, 1)]),
    ("GET", "/api/v1/products/{sku}", "ProductController", 25, 25, [(200, 92), (404, 7), (500, 1)]),
    ("GET", "/api/v1/recommendations", "RecommendationController", 120, 8, [(200, 98), (503, 2)]),
    ("GET", "/api/v1/cart", "CartController", 20, 10, [(200, 99), (500, 1)]),
    ("POST", "/api/v1/cart/items", "CartController", 35, 8, [(201, 94), (409, 3), (400, 3)]),
    ("POST", "/api/v1/orders", "OrderController", 150, 4, [(201, 93), (400, 4), (409, 2), (500, 1)]),
    ("GET", "/api/v1/orders/{order}", "OrderController", 30, 3, [(200, 97), (404, 3)]),
    ("POST", "/api/v1/payments", "PaymentController", 600, 3.5, [(200, 95), (402, 3), (502, 1), (504, 1)]),
    ("POST", "/api/v1/auth/login", "AuthController", 90, 5, [(200, 85), (401, 14), (429, 1)]),
    ("GET", "/api/v1/health", "HealthController", 2, 3, [(200, 100)]),
]

STACK_GATEWAY = """fr.bd516.shop.payment.GatewayTimeoutException: Payment gateway did not answer within 3000 ms
\tat fr.bd516.shop.payment.PspClient.authorize(PspClient.java:87)
\tat fr.bd516.shop.service.PaymentService.pay(PaymentService.java:42)
\tat fr.bd516.shop.api.PaymentController.create(PaymentController.java:31)
\t... 54 common frames omitted"""
STACK_NPE = """java.lang.NullPointerException: Cannot invoke "java.util.Map.get(Object)" because "context" is null
\tat fr.bd516.shop.web.RequestContext.attribute(RequestContext.java:118)
\tat fr.bd516.shop.api.{ctrl}.handle({ctrl}.java:64)
\t... 48 common frames omitted"""
STACK_SQL = """java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:686)
\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:179)
\tat fr.bd516.shop.repository.OrderRepository.save(OrderRepository.java:52)
\t... 61 common frames omitted"""


def wpick(rng, pairs):
    total = sum(p[0] for p in pairs)
    r = rng.random() * total
    acc = 0.0
    for w, v in pairs:
        acc += w
        if r < acc:
            return v
    return pairs[-1][1]


class World:
    """Etat partage (utilisateurs, IP, fenetre d'incident) pour des logs coherents."""

    def __init__(self, rng, now):
        self.rng = rng
        self.users = {}
        self.incident_start = (now - timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        self.incident_end = self.incident_start + timedelta(minutes=90)
        self.pools = [(w, (c, prefixes)) for c, w, prefixes in IP_POOLS]

    def random_ip(self):
        _, prefixes = wpick(self.rng, self.pools)
        return f"{self.rng.choice(prefixes)}.{self.rng.randint(1, 254)}.{self.rng.randint(1, 254)}"

    def user(self):
        if self.rng.random() < 0.30:
            return "-", self.random_ip()
        uid = f"u{self.rng.randint(1000, 2999)}"
        if uid not in self.users:
            self.users[uid] = self.random_ip()
        return uid, self.users[uid]

    def in_incident(self, ts):
        return self.incident_start <= ts < self.incident_end


def fmt_app_ts(ts):
    return ts.strftime("%Y-%m-%d %H:%M:%S.") + f"{ts.microsecond // 1000:03d}"


def fmt_web_ts(ts):
    off = ts.strftime("%z")
    return f"{ts.day:02d}/{MONTHS[ts.month - 1]}/{ts.year}:{ts.strftime('%H:%M:%S')} {off}"


def app_event(w, ts):
    rng = w.rng
    incident = w.in_incident(ts)
    if rng.random() < 0.02:  # lignes "systeme" sans requete HTTP
        kind = rng.random()
        if incident and kind < 0.5:
            return (f"{fmt_app_ts(ts)} ERROR [http-nio-8080-exec-{rng.randint(1, 50)}] "
                    f"fr.bd516.shop.repository.OrderRepository - Impossible d'obtenir une connexion SQL\n{STACK_SQL}")
        if incident and kind < 0.8:
            return (f"{fmt_app_ts(ts)} WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.HikariPool - "
                    f"HikariPool-1 - Pool stats (total=20, active=20, idle=0, waiting={rng.randint(5, 60)})")
        if kind < 0.6:
            return (f"{fmt_app_ts(ts)} INFO  [scheduling-1] fr.bd516.shop.stock.StockSyncJob - "
                    f"Synchronisation des stocks terminée : {rng.randint(20, 400)} références mises à jour "
                    f"en {rng.randint(300, 2500)} ms")
        return (f"{fmt_app_ts(ts)} INFO  [scheduling-2] fr.bd516.shop.cache.CacheManager - "
                f"Cache 'catalogue' rafraîchi ({rng.randint(450, 520)} entrées)")

    method, path, ctrl, base, _, statuses = wpick(rng, [(e[4], e) for e in ENDPOINTS])
    user, ip = w.user()
    sku = f"{rng.choice(CATS)}-{rng.randint(1, 520):04d}"
    order = f"CMD-{rng.randint(1, 99999):06d}"
    query = ""
    if path == "/api/v1/products":
        query = "?q=" + quote_plus(rng.choice(SEARCH_TERMS)) + f"&page={rng.randint(1, 3)}"
    if ctrl == "HealthController":
        user, ip = "-", rng.choice(INTERNAL_IPS)
    real_path = path.replace("{sku}", sku).replace("{order}", order)
    status = wpick(rng, [(p, c) for c, p in statuses])
    latency = base * math.exp(rng.gauss(0, 0.5))
    if incident and ctrl in ("PaymentController", "OrderController"):
        latency *= 8
        if rng.random() < 0.45:
            status = rng.choice([502, 504]) if ctrl == "PaymentController" else 500
    duration = max(1, int(latency))
    if status in (502, 504):
        duration = max(duration, 3000 + rng.randint(0, 40))
    size = 0 if status >= 500 else rng.randint(120, 24000)
    if ctrl == "HealthController":
        size = 15

    msg, stack = "", None
    if ctrl == "HealthController":
        msg = "Health check OK"
    elif status >= 500:
        if ctrl == "PaymentController":
            msg, stack = f"Échec du paiement {order} : passerelle de paiement indisponible", STACK_GATEWAY
        elif incident and ctrl == "OrderController":
            msg, stack = f"Échec de création de la commande {order}", STACK_SQL
        else:
            msg, stack = "Erreur interne inattendue", STACK_NPE.replace("{ctrl}", ctrl)
    elif ctrl == "ProductController" and path.endswith("{sku}"):
        msg = f"Produit {sku} introuvable" if status == 404 else f"Fiche produit {sku} consultée"
    elif ctrl == "ProductController":
        msg = "Paramètre de recherche invalide" if status == 400 else f"Recherche catalogue : {rng.randint(0, 60)} résultat(s)"
    elif ctrl == "RecommendationController":
        msg = f"{rng.randint(4, 12)} recommandations calculées"
    elif ctrl == "CartController" and method == "GET":
        msg = f"Panier consulté ({rng.randint(0, 6)} article(s))"
    elif ctrl == "CartController":
        msg = {201: f"Article {sku} ajouté au panier", 409: "Conflit de version du panier, nouvelle tentative requise",
               400: "Quantité demandée invalide"}[status]
    elif ctrl == "OrderController" and method == "POST":
        msg = {201: f"Commande {order} créée ({rng.randint(15, 900)}.{rng.randint(0, 99):02d} EUR)",
               400: "Requête invalide : adresse de livraison manquante",
               409: f"Commande {order} déjà soumise"}[status]
    elif ctrl == "OrderController":
        msg = f"Commande {order} introuvable" if status == 404 else f"Commande {order} consultée"
    elif ctrl == "PaymentController":
        msg = f"Paiement refusé par la banque pour {order}" if status == 402 else f"Paiement accepté pour {order}"
    elif ctrl == "AuthController":
        msg = {200: "Authentification réussie", 401: "Échec d'authentification : identifiants invalides",
               429: "Trop de tentatives de connexion, compte temporairement bloqué"}[status]

    level = "ERROR" if status >= 500 else ("WARN " if status >= 400 else "INFO ")
    line = (f"{fmt_app_ts(ts)} {level} [http-nio-8080-exec-{rng.randint(1, 50)}] "
            f"fr.bd516.shop.api.{ctrl} - {ip} {user} \"{method} {real_path}{query}\" "
            f"{status} {duration}ms {size} - {msg}")
    return line + ("\n" + stack if stack else "")


def restart_events(ts):
    """Redemarrage applicatif : les 3 premieres lignes ne respectent pas le format (echec grok)."""
    t = ts
    out = []
    for txt in ["[main] Starting ShopApplication v2.3.1 using Java 21.0.5 with PID 1",
                "[main] The following 1 profile is active: \"prod\"",
                "[main] Tomcat initialized with port 8080 (http)"]:
        out.append(f"{fmt_app_ts(t)} {txt}")
        t += timedelta(milliseconds=731)
    out.append(f"{fmt_app_ts(t + timedelta(seconds=6))} INFO  [main] fr.bd516.shop.ShopApplication - "
               f"Started ShopApplication in 7.42 seconds (process running for 8.1)")
    return out


def web_event(w, ts):
    rng = w.rng
    incident = w.in_incident(ts)
    user, ip = w.user()
    r = rng.random()
    cat = rng.randrange(len(CATS))
    status, size = 200, rng.randint(8000, 60000)
    if r < 0.10:
        url = "/"
    elif r < 0.30:
        url = f"/produits/{CAT_SLUGS[cat]}"
    elif r < 0.58:
        url = f"/produits/{CAT_SLUGS[cat]}/{CATS[cat].lower()}-{rng.randint(1, 520):04d}"
        if rng.random() < 0.03:
            status, size = 404, 1534
    elif r < 0.66:
        url = "/recherche?q=" + quote_plus(rng.choice(SEARCH_TERMS))
    elif r < 0.72:
        url = "/panier"
    elif r < 0.75:
        url = "/commande"
    elif r < 0.78:
        url = "/compte/connexion"
    elif r < 0.93:
        url = rng.choice(["/static/js/app.3f9a1c.js", "/static/css/main.8b2e4d.css",
                          f"/images/produits/{CATS[cat].lower()}-{rng.randint(1, 520):04d}.webp"])
        size = rng.randint(20000, 220000)
        if rng.random() < 0.25:
            status, size = 304, 0
    elif r < 0.95:
        url = rng.choice(["/robots.txt", "/sitemap.xml", "/favicon.ico"])
        size = rng.randint(200, 4000)
    elif r < 0.96:
        url = rng.choice(["/wp-login.php", "/.env", "/phpmyadmin/index.php", "/admin/config.php"])
        status, size, user = 404, 1534, "-"
        ip = f"112.{rng.randint(80, 120)}.{rng.randint(1, 254)}.{rng.randint(1, 254)}"
    else:
        url = rng.choice(["/api/v1/products?q=" + quote_plus(rng.choice(SEARCH_TERMS)),
                          f"/api/v1/products/{CATS[cat]}-{rng.randint(1, 520):04d}"])
        size = rng.randint(300, 12000)
    method = "POST" if url in ("/commande", "/compte/connexion") and rng.random() < 0.4 else "GET"
    if incident and url in ("/commande", "/panier") and rng.random() < 0.4:
        status, size = rng.choice([500, 502, 503]), 0
    if method == "POST" and status == 200:
        status = 302
    ua = wpick(rng, UAS)
    ref = wpick(rng, REFERRERS)
    remote_user = "-" if user == "-" or rng.random() < 0.9 else user
    return (f"{ip} - {remote_user} [{fmt_web_ts(ts)}] \"{method} {url} HTTP/1.1\" {status} {size} "
            f"\"{ref}\" \"{ua}\"")


def timeline(rng, start, end, n):
    """n horodatages entre start et end, ponderes par l'heure de la journee."""
    span = (end - start).total_seconds()
    out = []
    maxw = max(HOUR_WEIGHT)
    while len(out) < n:
        ts = start + timedelta(seconds=rng.random() * span)
        if rng.random() < HOUR_WEIGHT[ts.hour] / maxw:
            out.append(ts)
    out.sort()
    return out


def write_atomic(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    os.replace(tmp, path)


def cmd_batch(args, root):
    rng = random.Random(SEED)
    now = datetime.now(TZ).replace(microsecond=0)
    start = now - timedelta(days=args.days)
    w = World(rng, now)
    app = []
    restarted = False
    for ts in timeline(rng, start, now, args.app_events):
        if not restarted and ts >= w.incident_end:
            app.extend(restart_events(w.incident_end + timedelta(seconds=30)))
            restarted = True
        app.append(app_event(w, ts))
    web = [web_event(w, ts) for ts in timeline(rng, start, now, args.web_events)]
    write_atomic(root / "logs" / "app" / "shop-api.log", app)
    write_atomic(root / "logs" / "web" / "access.log", web)
    n_lines = sum(e.count("\n") + 1 for e in app)
    print(f"Logs applicatifs : {len(app)} evenements ({n_lines} lignes) -> data/logs/app/shop-api.log")
    print(f"Logs nginx       : {len(web)} lignes -> data/logs/web/access.log")
    print(f"Periode          : {start:%Y-%m-%d %H:%M} -> {now:%Y-%m-%d %H:%M} (Europe/Paris)")
    print(f"Incident simule  : {w.incident_start:%Y-%m-%d %H:%M} -> {w.incident_end:%H:%M} "
          f"(erreurs 5xx et latence sur /api/v1/payments et /api/v1/orders)")


def cmd_sample(args, root):
    rng = random.Random(SEED + 1)
    now = datetime.now(TZ).replace(microsecond=0)
    w = World(rng, now)
    w.incident_start = now - timedelta(minutes=30)  # l'echantillon contient des erreurs
    w.incident_end = now + timedelta(minutes=30)
    lines = restart_events(now - timedelta(minutes=10))
    ts = now - timedelta(minutes=9)
    for _ in range(25):
        ts += timedelta(seconds=rng.randint(1, 20), milliseconds=rng.randint(0, 999))
        lines.append(app_event(w, ts))
    write_atomic(root / "logs" / "sample" / "shop-api-sample.log", lines)
    print(f"Echantillon : {len(lines)} evenements -> data/logs/sample/shop-api-sample.log")


def cmd_follow(args, root):
    rng = random.Random()
    now = datetime.now(TZ)
    w = World(rng, now)
    w.incident_start = w.incident_end = now - timedelta(days=30)  # pas d'incident en direct
    app_path = root / "logs" / "app" / "shop-api.log"
    web_path = root / "logs" / "web" / "access.log"
    app_path.parent.mkdir(parents=True, exist_ok=True)
    web_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Ajout d'evenements en direct ({args.rate}/s) dans {app_path} et {web_path} - Ctrl+C pour arreter")
    t_end = time.time() + args.duration if args.duration > 0 else None
    count = 0
    try:
        with open(app_path, "a", encoding="utf-8") as fa, open(web_path, "a", encoding="utf-8") as fw:
            while t_end is None or time.time() < t_end:
                ts = datetime.now(TZ)
                fa.write(app_event(w, ts) + "\n")
                fa.flush()
                fw.write(web_event(w, ts) + "\n")
                fw.flush()
                count += 1
                if count % 50 == 0:
                    print(f"  {count} evenements ajoutes", flush=True)
                time.sleep(rng.expovariate(args.rate))
    except KeyboardInterrupt:
        pass
    print(f"\nTermine : {count} evenements ajoutes dans chaque fichier.")


def main():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Generateur de logs BD516")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch", help="historique de N jours")
    b.add_argument("--days", type=int, default=7)
    b.add_argument("--app-events", type=int, default=50000)
    b.add_argument("--web-events", type=int, default=40000)
    sub.add_parser("sample", help="petit echantillon pour la mise au point")
    f = sub.add_parser("follow", help="evenements en temps reel")
    f.add_argument("--rate", type=float, default=3.0, help="evenements par seconde (defaut 3)")
    f.add_argument("--duration", type=int, default=0, help="duree en secondes (0 = infini)")
    args = p.parse_args()
    {"batch": cmd_batch, "sample": cmd_sample, "follow": cmd_follow}[args.cmd](args, root)


if __name__ == "__main__":
    sys.exit(main())
