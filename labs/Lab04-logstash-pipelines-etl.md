# BD516 – Lab 04 : Logstash, architecture et pipelines ETL

> **Chapitre 4 – Logstash : architecture et pipelines ETL**
> Prérequis : Lab 01 terminé (cluster green) ; Labs 02-03 conseillés – Durée : 2 h 30

## Objectifs

| Travaux pratiques du programme | Étapes |
|---|---|
| **Configurer un input file pour lire un fichier de logs applicatifs** | 1, 5 |
| **Parser les lignes de logs avec le filtre grok et extraire des champs structurés** | 4.1 |
| **Enrichir les événements (filtres date, geoip, mutate) et convertir les types** | 4.2, 4.3 |
| **Configurer un output elasticsearch pour indexer les événements transformés** | 2, 5, 6 |
| **Vérifier l'ingestion via Kibana Discover** | 7, 8 |
| *Multi-pipelines (`pipelines.yml`), files d'attente mémoire / persistante* | 3, 9 |
| *Supervision : API `_node/stats`, intégration Metricbeat (bonus)* | 9, 11 |
| *Alternatives : Beats, Elastic Agent / Fleet, ingest pipelines* | 10 |

## Architecture cible

```text
 data/logs/app/shop-api.log ──► pipeline "bd516-app" (2 workers, file d'attente PERSISTANTE)
   (Java, multi-lignes)          file+multiline ─► fingerprint ─► grok ─► date ─► mutate ─► cidr/geoip ─► elasticsearch
                                                                                                     │
 data/logs/web/access.log ───► pipeline "bd516-web" (1 worker, file d'attente mémoire)               ▼
   (nginx combined)              file ─► fingerprint ─► grok ─► date ─► geoip ─► useragent ─► elasticsearch
                                                                                                     │
                                  Elasticsearch : data streams logs-bd516.app-default / logs-bd516.web-default
                                  modèle d'index "bd516-logs" + politique ILM "bd516-logs-ilm"
```

| Bloc | Plugins vus dans ce lab | Autres plugins courants |
|---|---|---|
| **input** | `file` (+ codec `multiline`), `stdin` | `beats`, `kafka`, `jdbc`, `http`, `syslog`, `s3` |
| **filter** | `grok`, `date`, `mutate`, `geoip`, `cidr`, `useragent`, `fingerprint` | `json`, `csv`, `kv`, `dissect`, `translate`, `ruby` |
| **output** | `elasticsearch`, `stdout` | `file`, `kafka`, `s3`, `http` |

---

## Étape 1 – Générer les fichiers de logs

```bash
cat > ~/BD516/data/generators/gen_logs.py <<'BD516_EOF'
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
BD516_EOF
chmod +x ~/BD516/data/generators/gen_logs.py
cd ~/BD516
python3 data/generators/gen_logs.py sample
python3 data/generators/gen_logs.py batch
```

Résultat attendu :

```text
Echantillon : 29 evenements -> data/logs/sample/shop-api-sample.log
Logs applicatifs : 50004 evenements (~51900 lignes) -> data/logs/app/shop-api.log
Logs nginx       : 40000 lignes -> data/logs/web/access.log
Periode          : <J-7> -> <maintenant> (Europe/Paris)
Incident simule  : <hier> 14:00 -> 15:30 (erreurs 5xx et latence sur /api/v1/payments et /api/v1/orders)
```

Observez les deux formats :

```bash
head -3 data/logs/sample/shop-api-sample.log     # 3 lignes de démarrage "non conformes"
grep -m1 -A4 ' ERROR ' data/logs/app/shop-api.log # événement multi-lignes (stack trace Java)
head -2 data/logs/web/access.log                  # format nginx "combined"
```

> Ne régénérez pas les logs après les avoir ingérés (de nouveaux horodatages créeraient de nouveaux événements).

---

## Étape 2 – Préparer Elasticsearch : ILM, modèle d'index, utilisateur dédié

### 2.1 Politique de cycle de vie des logs (Dev Tools)

```console
PUT _ilm/policy/bd516-logs-ilm
{
  "policy": {
    "_meta": { "description": "BD516 - logs : rollover quotidien, warm a 3 jours, suppression a 30 jours" },
    "phases": {
      "hot": {
        "actions": {
          "rollover": { "max_primary_shard_size": "10gb", "max_age": "1d" },
          "set_priority": { "priority": 100 }
        }
      },
      "warm": {
        "min_age": "3d",
        "actions": {
          "allocate": { "number_of_replicas": 0 },
          "forcemerge": { "max_num_segments": 1 },
          "set_priority": { "priority": 50 }
        }
      },
      "delete": {
        "min_age": "30d",
        "actions": { "delete": {} }
      }
    }
  }
}
```

### 2.2 Composants et modèle d'index (noms de champs ECS)

```console
PUT _component_template/bd516-logs@settings
{
  "template": {
    "settings": {
      "index.number_of_shards": 1,
      "index.number_of_replicas": 1,
      "index.lifecycle.name": "bd516-logs-ilm",
      "index.codec": "best_compression",
      "index.refresh_interval": "5s"
    }
  },
  "_meta": { "description": "BD516 - parametres des data streams de logs" }
}

PUT _component_template/bd516-logs@mappings
{
  "template": {
    "mappings": {
      "dynamic_templates": [
        {
          "chaines_en_keyword": {
            "match_mapping_type": "string",
            "mapping": { "type": "keyword", "ignore_above": 1024 }
          }
        }
      ],
      "properties": {
        "@timestamp": { "type": "date" },
        "message":    { "type": "match_only_text" },
        "tags":       { "type": "keyword" },
        "data_stream": {
          "properties": {
            "type":      { "type": "constant_keyword" },
            "dataset":   { "type": "constant_keyword" },
            "namespace": { "type": "constant_keyword" }
          }
        },
        "event": {
          "properties": {
            "original": { "type": "keyword", "index": false, "doc_values": false },
            "dataset":  { "type": "keyword" },
            "outcome":  { "type": "keyword" }
          }
        },
        "log": {
          "properties": {
            "level":  { "type": "keyword" },
            "logger": { "type": "keyword" },
            "file":   { "properties": { "path": { "type": "keyword" } } }
          }
        },
        "service": {
          "properties": {
            "name":        { "type": "keyword" },
            "environment": { "type": "keyword" }
          }
        },
        "source": {
          "properties": {
            "ip":      { "type": "ip" },
            "address": { "type": "keyword" },
            "geo": {
              "properties": {
                "location":         { "type": "geo_point" },
                "country_iso_code": { "type": "keyword" },
                "country_name":     { "type": "keyword" },
                "city_name":        { "type": "keyword" },
                "region_name":      { "type": "keyword" },
                "continent_code":   { "type": "keyword" }
              }
            }
          }
        },
        "user": {
          "properties": {
            "id":   { "type": "keyword" },
            "name": { "type": "keyword" }
          }
        },
        "http": {
          "properties": {
            "version": { "type": "keyword" },
            "request": {
              "properties": {
                "method":   { "type": "keyword" },
                "referrer": { "type": "keyword" }
              }
            },
            "response": {
              "properties": {
                "status_code": { "type": "long" },
                "body": { "properties": { "bytes": { "type": "long" } } }
              }
            }
          }
        },
        "url": {
          "properties": {
            "path":     { "type": "keyword" },
            "query":    { "type": "keyword" },
            "original": { "type": "keyword" }
          }
        },
        "user_agent": {
          "properties": {
            "original": { "type": "keyword" },
            "name":     { "type": "keyword" },
            "os":     { "properties": { "name": { "type": "keyword" }, "full": { "type": "keyword" } } },
            "device": { "properties": { "name": { "type": "keyword" } } }
          }
        },
        "error": {
          "properties": {
            "type":        { "type": "keyword" },
            "message":     { "type": "match_only_text" },
            "stack_trace": { "type": "wildcard" }
          }
        },
        "shop": {
          "properties": {
            "duration_ms": { "type": "long" }
          }
        }
      }
    }
  },
  "_meta": { "description": "BD516 - mapping ECS des logs applicatifs et web" }
}

PUT _index_template/bd516-logs
{
  "index_patterns": ["logs-bd516.*-*"],
  "data_stream": {},
  "priority": 200,
  "composed_of": ["bd516-logs@settings", "bd516-logs@mappings"],
  "_meta": { "description": "BD516 - data streams de logs alimentes par Logstash" }
}

# Vérification : paramètres et mapping effectifs d'un futur data stream
POST _index_template/_simulate_index/logs-bd516.app-default?filter_path=template.settings.index.lifecycle,template.mappings.properties.source
```

> Le modèle intégré `logs` (motif `logs-*-*`, priorité 100) existe déjà : notre modèle, de priorité **200**, est prioritaire pour `logs-bd516.*-*`. Elasticsearch peut signaler ce recouvrement par un avertissement, sans conséquence.

### 2.3 Rôle et utilisateur `logstash_writer` (principe du moindre privilège)

Le mot de passe est lu dans `docker/.env` (variable `LOGSTASH_WRITER_PASSWORD`) : on utilise donc le terminal.

```bash
source ~/BD516/scripts/bd516-env.sh
es PUT /_security/role/bd516_logstash_writer -d '{
  "cluster": ["monitor"],
  "indices": [
    { "names": ["logs-bd516.*"], "privileges": ["create_doc", "auto_configure"] }
  ]
}'
es POST /_security/user/logstash_writer -d "{
  \"password\": \"${LOGSTASH_WRITER_PASSWORD}\",
  \"roles\": [\"bd516_logstash_writer\"],
  \"full_name\": \"Logstash BD516\"
}"
```

Résultat attendu : `{"role":{"created":true}}` puis `{"created":true}`. Testez le compte (écriture autorisée, lecture refusée), puis supprimez le data stream de test :

```bash
curl -s --cacert "$ES_CA" -u "logstash_writer:${LOGSTASH_WRITER_PASSWORD}" -H 'Content-Type: application/json' \
  -X POST "$ES_URL/logs-bd516.test-default/_doc" \
  -d "{\"@timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"message\":\"test logstash_writer\"}" | jq .result
curl -s --cacert "$ES_CA" -u "logstash_writer:${LOGSTASH_WRITER_PASSWORD}" \
  "$ES_URL/logs-bd516.test-default/_search" | jq .error.type
es DELETE /_data_stream/logs-bd516.test-default
```

Attendu : `"created"`, puis `"security_exception"`, puis `{"acknowledged":true}`.

---

## Étape 3 – Configuration de l'instance Logstash

### 3.1 `logstash.yml` (paramètres globaux)

```bash
cat > ~/BD516/docker/logstash/config/logstash.yml <<'BD516_EOF'
# =============================================================================
# BD516 - logstash.yml (parametres globaux de l'instance)
# Monte en lecture seule : ne definissez PAS de variables d'environnement
# portant le nom d'un parametre Logstash (ex. PIPELINE_WORKERS) dans docker-compose.
# =============================================================================
node.name: logstash-bd516

# API de supervision (_node/stats, _node/pipelines, _health_report...)
api.http.host: "0.0.0.0"
api.http.port: 9600

# Schema ECS (Elastic Common Schema) pour tous les plugins
pipeline.ecs_compatibility: v8

# Rechargement automatique des pipelines modifies (sans redemarrage).
# Valeur surchargeable par la variable LS_CONFIG_RELOAD (false pour les tests ponctuels
# avec "docker compose run" : sinon Logstash ne s'arrete pas a la fin de l'entree stdin).
config.reload.automatic: "${LS_CONFIG_RELOAD:true}"
config.reload.interval: 5s

# Arret propre : Logstash vide ses files d'attente avant de s'arreter
pipeline.unsafe_shutdown: false

# Le monitoring "legacy" interne est desactive : la supervision de production
# se fait avec Metricbeat (module logstash) ou Elastic Agent.
xpack.monitoring.enabled: false

log.level: info
BD516_EOF
```

### 3.2 `pipelines.yml` (deux pipelines isolés)

```bash
cat > ~/BD516/docker/logstash/config/pipelines.yml <<'BD516_EOF'
# =============================================================================
# BD516 - pipelines.yml : une instance Logstash, plusieurs pipelines isoles
# Chaque pipeline a ses propres workers, sa propre file d'attente et
# peut etre recharge independamment des autres.
# =============================================================================

# Logs applicatifs Java (API shop-api) : file d'attente PERSISTANTE sur disque
- pipeline.id: bd516-app
  path.config: "/usr/share/logstash/pipeline/bd516-app/*.conf"
  pipeline.workers: 2
  pipeline.batch.size: 250
  queue.type: persisted
  queue.max_bytes: 512mb
  dead_letter_queue.enable: true

# Logs d'acces nginx : file d'attente en memoire (par defaut)
- pipeline.id: bd516-web
  path.config: "/usr/share/logstash/pipeline/bd516-web/*.conf"
  pipeline.workers: 1
  pipeline.batch.size: 250
  queue.type: memory
BD516_EOF
```

| | file d'attente **memory** (défaut) | file d'attente **persisted** |
|---|---|---|
| Stockage | RAM (taille fixe, par lots) | disque (`path.data/queue/<pipeline>`) |
| Arrêt brutal / crash | événements en vol **perdus** | événements **conservés**, rejoués au redémarrage |
| Contre-pression (output lent) | bloque immédiatement les inputs | absorbe un pic jusqu'à `queue.max_bytes` |
| Coût | aucun | écritures disque (débit légèrement inférieur) |

### 3.3 Activer le service Logstash (profil Compose)

```bash
cd ~/BD516/docker
grep -q '^COMPOSE_PROFILES=' .env || echo 'COMPOSE_PROFILES=logstash' >> .env
source ~/BD516/scripts/bd516-env.sh
docker compose config --services | grep logstash
```

---

## Étape 4 – Mise au point du filtre (stdin → filtre → stdout)

On met au point le filtre sur l'**échantillon** avant toute indexation. Le dossier `docker/logstash/dev/` contient une entrée `stdin` et une sortie `stdout` ; vous y écrivez `50-filter.conf` étape par étape.

```bash
cat > ~/BD516/docker/logstash/dev/10-input-stdin.conf <<'BD516_EOF'
# BD516 - mise au point : entree standard (cat fichier | logstash ...)
input {
  stdin {
    codec => multiline {
      pattern => "^%{TIMESTAMP_ISO8601} "
      negate => true
      what => "previous"
      auto_flush_interval => 1
    }
  }
}
BD516_EOF
cat > ~/BD516/docker/logstash/dev/90-output-stdout.conf <<'BD516_EOF'
# BD516 - mise au point : affichage lisible de chaque evenement (avec @metadata)
output {
  stdout {
    codec => rubydebug { metadata => true }
  }
}
BD516_EOF
```

> `LS_CONFIG_RELOAD=false` désactive le rechargement automatique pour ces exécutions ponctuelles : sans cela, Logstash resterait actif à la fin de l'entrée standard au lieu de s'arrêter.

> **Outil interactif** : Kibana → ☰ → **Management → Dev Tools → Grok Debugger**. Collez une ligne de l'échantillon dans *Sample Data*, un motif dans *Grok Pattern*, puis **Simulate**. Construisez le motif de gauche à droite : `%{TIMESTAMP_ISO8601:log_ts} %{LOGLEVEL:level}` puis complétez.

### 4.1 Version 1 : extraction des champs avec grok

```bash
cat > ~/BD516/docker/logstash/dev/50-filter.conf <<'EOF'
# BD516 - mise au point, version 1 : grok uniquement
filter {
  grok {
    id => "f_app_grok"
    match => {
      "message" => [
        '(?m)^%{TIMESTAMP_ISO8601:[@metadata][log_ts]} %{LOGLEVEL:[log][level]}\s+\[%{DATA:[process][thread][name]}\] %{JAVACLASS:[log][logger]} - %{IP:[source][ip]} (?:-|%{NOTSPACE:[user][id]}) "%{WORD:[http][request][method]} %{URIPATH:[url][path]}(?:\?%{URIQUERY:[url][query]})?" %{INT:[http][response][status_code]:int} %{INT:[shop][duration_ms]}ms %{INT:[http][response][body][bytes]:int} - %{GREEDYDATA:message}',
        '(?m)^%{TIMESTAMP_ISO8601:[@metadata][log_ts]} %{LOGLEVEL:[log][level]}\s+\[%{DATA:[process][thread][name]}\] %{JAVACLASS:[log][logger]} - %{GREEDYDATA:message}'
      ]
    }
    overwrite => ["message"]
  }
}
EOF
cd ~/BD516/docker
docker compose run --rm --no-deps -T -e LS_CONFIG_RELOAD=false logstash \
  -f "/usr/share/logstash/dev/*.conf" -w 1 --path.data /tmp/lsdev --log.level warn \
  < ../data/logs/sample/shop-api-sample.log
```

Lecture du motif :

| Élément | Rôle |
|---|---|
| `(?m)` | le `.` accepte aussi les sauts de ligne : `message` récupère la stack trace entière |
| `%{TIMESTAMP_ISO8601:[@metadata][log_ts]}` | date brute stockée dans `@metadata` (jamais indexée) |
| `%{LOGLEVEL:[log][level]}\s+` | niveau aligné sur 5 caractères (`INFO `, `WARN `, `ERROR`) |
| `(?:-\|%{NOTSPACE:[user][id]})` | utilisateur anonyme `-` : champ simplement absent |
| `:int` | conversion de type directement dans grok |
| 2e motif | lignes techniques sans requête HTTP (cache, synchronisation, pool SQL) |

Attendu (après ~30 s de démarrage de la JVM ; l'avertissement `Ignoring the 'pipelines.yml' file` est normal avec `-f`) : un bloc `{ ... }` par événement avec `log.level`, `log.logger`, `source.ip`, `http.response.status_code` (entier), `shop.duration_ms` (**chaîne** pour l'instant)… Les **3 premières lignes** (démarrage) portent le tag `_grokparsefailure` ; l'événement `ERROR` contient la stack trace dans `message`. Le dernier événement peut ne pas s'afficher : il reste dans le tampon du codec `multiline` jusqu'à l'arrivée d'une nouvelle ligne.

### 4.2 Version 2 : date, types, stack trace

```bash
cat > ~/BD516/docker/logstash/dev/50-filter.conf <<'EOF'
# BD516 - mise au point, version 2 : + date, types et stack trace
filter {
  grok {
    id => "f_app_grok"
    match => {
      "message" => [
        '(?m)^%{TIMESTAMP_ISO8601:[@metadata][log_ts]} %{LOGLEVEL:[log][level]}\s+\[%{DATA:[process][thread][name]}\] %{JAVACLASS:[log][logger]} - %{IP:[source][ip]} (?:-|%{NOTSPACE:[user][id]}) "%{WORD:[http][request][method]} %{URIPATH:[url][path]}(?:\?%{URIQUERY:[url][query]})?" %{INT:[http][response][status_code]:int} %{INT:[shop][duration_ms]}ms %{INT:[http][response][body][bytes]:int} - %{GREEDYDATA:message}',
        '(?m)^%{TIMESTAMP_ISO8601:[@metadata][log_ts]} %{LOGLEVEL:[log][level]}\s+\[%{DATA:[process][thread][name]}\] %{JAVACLASS:[log][logger]} - %{GREEDYDATA:message}'
      ]
    }
    overwrite => ["message"]
  }

  if "_grokparsefailure" in [tags] {
    grok {
      id => "f_app_grok_ts_only"
      match => { "message" => '^%{TIMESTAMP_ISO8601:[@metadata][log_ts]}' }
      tag_on_failure => []
    }
  }

  if [message] =~ /\n/ {
    grok {
      id => "f_app_grok_stack"
      match => { "message" => '(?m)^%{DATA:[@metadata][first_line]}\n%{GREEDYDATA:[error][stack_trace]}' }
      tag_on_failure => ["_stacktrace_parse_failure"]
    }
    grok {
      id => "f_app_grok_error_type"
      match => { "[error][stack_trace]" => '^%{JAVACLASS:[error][type]}: %{DATA:[error][message]}$' }
      tag_on_failure => []
    }
    mutate {
      id => "f_app_first_line"
      replace => { "message" => "%{[@metadata][first_line]}" }
    }
  }

  date {
    id => "f_app_date"
    match => ["[@metadata][log_ts]", "yyyy-MM-dd HH:mm:ss.SSS"]
    timezone => "Europe/Paris"
    target => "@timestamp"
  }

  mutate {
    id => "f_app_mutate"
    convert => { "[shop][duration_ms]" => "integer" }
    add_field => {
      "[service][name]" => "shop-api"
      "[service][environment]" => "formation"
    }
  }
}
EOF
cd ~/BD516/docker
docker compose run --rm --no-deps -T -e LS_CONFIG_RELOAD=false logstash \
  -f "/usr/share/logstash/dev/*.conf" -w 1 --path.data /tmp/lsdev --log.level warn \
  < ../data/logs/sample/shop-api-sample.log
```

Vérifiez sur la sortie :

- `@timestamp` = heure du log **convertie en UTC** (`21:56:02` à Paris → `20:56:02Z` en hiver) ;
- `shop.duration_ms` est désormais un **entier** ;
- l'événement `ERROR` : `message` ne garde que la première ligne ; `error.type`, `error.message` et `error.stack_trace` sont renseignés ;
- les lignes de démarrage gardent `_grokparsefailure` mais ont la bonne date.

> **Exercice** : commentez la ligne `timezone` et relancez. Les dates sont alors interprétées dans le fuseau de la JVM du conteneur (UTC) : décalage d'une heure. Rétablissez la ligne.

### 4.3 Version 3 (finale) : géolocalisation, identifiant, indicateurs

```bash
cat > ~/BD516/docker/logstash/dev/50-filter.conf <<'BD516_EOF'
# BD516 - pipeline bd516-app : TRANSFORMATIONS (version finale)
filter {
  # 1. Empreinte deterministe de l'evenement brut -> _id du document
  fingerprint {
    id => "f_app_fingerprint"
    source => ["[log][file][path]", "message"]
    concatenate_sources => true
    method => "SHA256"
    target => "[@metadata][fingerprint]"
  }

  # 2. Parsing : requetes HTTP (motif 1) ou messages techniques (motif 2)
  grok {
    id => "f_app_grok"
    match => {
      "message" => [
        '(?m)^%{TIMESTAMP_ISO8601:[@metadata][log_ts]} %{LOGLEVEL:[log][level]}\s+\[%{DATA:[process][thread][name]}\] %{JAVACLASS:[log][logger]} - %{IP:[source][ip]} (?:-|%{NOTSPACE:[user][id]}) "%{WORD:[http][request][method]} %{URIPATH:[url][path]}(?:\?%{URIQUERY:[url][query]})?" %{INT:[http][response][status_code]:int} %{INT:[shop][duration_ms]}ms %{INT:[http][response][body][bytes]:int} - %{GREEDYDATA:message}',
        '(?m)^%{TIMESTAMP_ISO8601:[@metadata][log_ts]} %{LOGLEVEL:[log][level]}\s+\[%{DATA:[process][thread][name]}\] %{JAVACLASS:[log][logger]} - %{GREEDYDATA:message}'
      ]
    }
    overwrite => ["message"]
  }

  # Ligne non conforme : on recupere au moins la date pour bien la positionner
  if "_grokparsefailure" in [tags] {
    grok {
      id => "f_app_grok_ts_only"
      match => { "message" => '^%{TIMESTAMP_ISO8601:[@metadata][log_ts]}' }
      tag_on_failure => []
    }
  }

  # 3. Stack trace Java : la premiere ligne reste dans message, le reste va dans error.*
  if [message] =~ /\n/ {
    grok {
      id => "f_app_grok_stack"
      match => { "message" => '(?m)^%{DATA:[@metadata][first_line]}\n%{GREEDYDATA:[error][stack_trace]}' }
      tag_on_failure => ["_stacktrace_parse_failure"]
    }
    grok {
      id => "f_app_grok_error_type"
      match => { "[error][stack_trace]" => '^%{JAVACLASS:[error][type]}: %{DATA:[error][message]}$' }
      tag_on_failure => []
    }
    mutate {
      id => "f_app_first_line"
      replace => { "message" => "%{[@metadata][first_line]}" }
    }
  }

  # 4. Date de l'evenement (heure locale de Paris, sans decalage dans le log)
  date {
    id => "f_app_date"
    match => ["[@metadata][log_ts]", "yyyy-MM-dd HH:mm:ss.SSS"]
    timezone => "Europe/Paris"
    target => "@timestamp"
  }

  # 5. Types, champs ECS complementaires
  mutate {
    id => "f_app_mutate"
    convert => { "[shop][duration_ms]" => "integer" }
    add_field => {
      "[service][name]" => "shop-api"
      "[service][environment]" => "formation"
      "[event][dataset]" => "bd516.app"
      "[data_stream][type]" => "logs"
      "[data_stream][dataset]" => "bd516.app"
      "[data_stream][namespace]" => "default"
    }
  }

  if [http][response][status_code] {
    if [http][response][status_code] >= 500 {
      mutate { id => "f_app_outcome_failure" add_field => { "[event][outcome]" => "failure" } }
    } else {
      mutate { id => "f_app_outcome_success" add_field => { "[event][outcome]" => "success" } }
    }
  }

  if [shop][duration_ms] {
    if [shop][duration_ms] > 1000 {
      mutate { id => "f_app_tag_slow" add_tag => ["slow_request"] }
    }
  }

  # 6. Geolocalisation des IP publiques (les IP privees sont seulement marquees)
  if [source][ip] {
    cidr {
      id => "f_app_cidr"
      address => ["%{[source][ip]}"]
      network => ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]
      add_tag => ["internal_ip"]
    }
    if "internal_ip" not in [tags] {
      geoip {
        id => "f_app_geoip"
        source => "[source][ip]"
        target => "source"
      }
    }
  }
}
BD516_EOF
cd ~/BD516/docker
docker compose run --rm --no-deps -T -e LS_CONFIG_RELOAD=false logstash \
  -f "/usr/share/logstash/dev/*.conf" -w 1 --path.data /tmp/lsdev --log.level warn \
  < ../data/logs/sample/shop-api-sample.log
```

Nouveautés à observer :

- `source.geo.*` : pays, ville, `location` (lat/lon) – la base GeoLite2 est téléchargée automatiquement au démarrage (accès Internet requis) ;
- tag `internal_ip` sur les *health checks* (IP 10.x) : pas de géolocalisation des IP privées ;
- `[@metadata][fingerprint]` : empreinte SHA-256 qui servira d'identifiant de document ;
- `event.outcome` (`success` / `failure`), tag `slow_request` au-delà de 1 000 ms ;
- champs `data_stream.*`, `event.dataset` et `service.*` ajoutés par `mutate`.

Installez ce filtre dans le pipeline définitif (même fichier, aucune ressaisie) :

```bash
cp ~/BD516/docker/logstash/dev/50-filter.conf ~/BD516/docker/logstash/pipeline/bd516-app/50-filter.conf
```

---

## Étape 5 – Pipelines définitifs

### 5.1 Pipeline `bd516-app` : entrée et sortie

```bash
cat > ~/BD516/docker/logstash/pipeline/bd516-app/10-input.conf <<'BD516_EOF'
# BD516 - pipeline bd516-app : ENTREE
# Lecture "en continu" (tail) des logs applicatifs ; une stack trace Java
# (lignes ne commencant pas par une date) est rattachee a l'evenement precedent.
input {
  file {
    id => "in_app_file"
    path => ["/data/logs/app/*.log"]
    mode => "tail"
    start_position => "beginning"
    sincedb_path => "/usr/share/logstash/data/sincedb-bd516-app"
    codec => multiline {
      pattern => "^%{TIMESTAMP_ISO8601} "
      negate => true
      what => "previous"
      auto_flush_interval => 2
    }
  }
}
BD516_EOF
cat > ~/BD516/docker/logstash/pipeline/bd516-app/90-output.conf <<'BD516_EOF'
# BD516 - pipeline bd516-app : SORTIE
# Ecriture dans le data stream logs-bd516.app-default (modele d'index bd516-logs).
# document_id = empreinte de l'evenement : une re-ingestion ne cree pas de doublon.
output {
  elasticsearch {
    id => "out_es_app"
    hosts => ["https://es01:9200", "https://es02:9200", "https://es03:9200"]
    ssl_enabled => true
    ssl_certificate_authorities => ["/usr/share/logstash/config/certs/ca/ca.crt"]
    ssl_verification_mode => "full"
    user => "logstash_writer"
    password => "${LOGSTASH_WRITER_PASSWORD}"
    data_stream => "false"
    index => "logs-bd516.app-default"
    action => "create"
    document_id => "%{[@metadata][fingerprint]}"
    manage_template => false
    ilm_enabled => false
    silence_errors_in_log => ["version_conflict_engine_exception"]
  }
}
BD516_EOF
```

Points clés de la sortie :

| Option | Pourquoi |
|---|---|
| `hosts` (3 nœuds) | répartition de charge et bascule automatique |
| `ssl_enabled`, `ssl_certificate_authorities`, `ssl_verification_mode => "full"` | TLS vérifié (nom d'hôte inclus) |
| `user` / `password => "${LOGSTASH_WRITER_PASSWORD}"` | compte dédié ; variable d'environnement (en production : *keystore* Logstash) |
| `index` + `action => "create"` | écriture dans le data stream (les data streams n'acceptent que `create`) |
| `document_id => "%{[@metadata][fingerprint]}"` | **idempotence** : relire un fichier ne crée pas de doublon |
| `manage_template => false`, `ilm_enabled => false` | modèle d'index et ILM gérés côté Elasticsearch (étape 2) |
| `silence_errors_in_log` | les doublons refusés (409) ne polluent pas les logs |

### 5.2 Pipeline `bd516-web` (format nginx, motif grok fourni)

```bash
cat > ~/BD516/docker/logstash/pipeline/bd516-web/10-input.conf <<'BD516_EOF'
# BD516 - pipeline bd516-web : ENTREE (logs d'acces nginx, une ligne = un evenement)
input {
  file {
    id => "in_web_file"
    path => ["/data/logs/web/access*.log"]
    mode => "tail"
    start_position => "beginning"
    sincedb_path => "/usr/share/logstash/data/sincedb-bd516-web"
  }
}
BD516_EOF
cat > ~/BD516/docker/logstash/pipeline/bd516-web/50-filter.conf <<'BD516_EOF'
# BD516 - pipeline bd516-web : TRANSFORMATIONS
filter {
  fingerprint {
    id => "f_web_fingerprint"
    source => ["[log][file][path]", "message"]
    concatenate_sources => true
    method => "SHA256"
    target => "[@metadata][fingerprint]"
  }

  # Motif fourni par Logstash pour le format "combined" (champs ECS)
  grok {
    id => "f_web_grok"
    match => { "message" => "%{HTTPD_COMBINEDLOG}" }
  }

  # 25/Nov/2026:14:03:12 +0100 -> le decalage horaire est dans la date
  date {
    id => "f_web_date"
    match => ["timestamp", "dd/MMM/yyyy:HH:mm:ss Z"]
    locale => "en"
    target => "@timestamp"
    remove_field => ["timestamp"]
  }

  if [source][address] {
    cidr {
      id => "f_web_cidr"
      address => ["%{[source][address]}"]
      network => ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]
      add_tag => ["internal_ip"]
    }
    if "internal_ip" not in [tags] {
      geoip {
        id => "f_web_geoip"
        source => "[source][address]"
        target => "source"
      }
    }
  }

  if [user_agent][original] {
    useragent {
      id => "f_web_useragent"
      source => "[user_agent][original]"
      target => "[user_agent]"
    }
  }

  mutate {
    id => "f_web_mutate"
    add_field => {
      "[service][name]" => "shop-web"
      "[service][environment]" => "formation"
      "[event][dataset]" => "bd516.web"
      "[data_stream][type]" => "logs"
      "[data_stream][dataset]" => "bd516.web"
      "[data_stream][namespace]" => "default"
    }
  }

  if [http][response][status_code] {
    if [http][response][status_code] >= 500 {
      mutate { id => "f_web_outcome_failure" add_field => { "[event][outcome]" => "failure" } }
    } else {
      mutate { id => "f_web_outcome_success" add_field => { "[event][outcome]" => "success" } }
    }
  }
}
BD516_EOF
cat > ~/BD516/docker/logstash/pipeline/bd516-web/90-output.conf <<'BD516_EOF'
# BD516 - pipeline bd516-web : SORTIE -> data stream logs-bd516.web-default
output {
  elasticsearch {
    id => "out_es_web"
    hosts => ["https://es01:9200", "https://es02:9200", "https://es03:9200"]
    ssl_enabled => true
    ssl_certificate_authorities => ["/usr/share/logstash/config/certs/ca/ca.crt"]
    ssl_verification_mode => "full"
    user => "logstash_writer"
    password => "${LOGSTASH_WRITER_PASSWORD}"
    data_stream => "false"
    index => "logs-bd516.web-default"
    action => "create"
    document_id => "%{[@metadata][fingerprint]}"
    manage_template => false
    ilm_enabled => false
    silence_errors_in_log => ["version_conflict_engine_exception"]
  }
}
BD516_EOF
```

### 5.3 Tester la configuration sans démarrer les pipelines

```bash
cd ~/BD516/docker
for p in bd516-app bd516-web; do
  docker compose run --rm --no-deps -e LS_CONFIG_RELOAD=false logstash --config.test_and_exit \
    -f "/usr/share/logstash/pipeline/${p}/*.conf" --path.data /tmp/lsdev --log.level warn
done
```

Attendu, pour chaque pipeline : `Configuration OK` (message `Config Validation Result: OK`).

---

## Étape 6 – Démarrer Logstash

```bash
cd ~/BD516/docker
docker compose up -d logstash
docker compose logs -f logstash | grep -E "Pipeline started|Pipelines running|ERROR"
```

Attendu (Ctrl+C ensuite) :

```text
... Pipeline started {"pipeline.id"=>"bd516-app"}
... Pipeline started {"pipeline.id"=>"bd516-web"}
... Pipelines running {:count=>2, :running_pipelines=>[:"bd516-app", :"bd516-web"], :non_running_pipelines=>[]}
```

Suivez l'ingestion (environ 1 à 2 minutes) :

```bash
for i in $(seq 1 12); do
  printf 'app=%s  web=%s\n' \
    "$(es GET '/logs-bd516.app-default/_count' | jq .count)" \
    "$(es GET '/logs-bd516.web-default/_count' | jq .count)"
  sleep 10
done
```

Attendu en fin d'ingestion : **app = 50 004** et **web = 40 000**.

---

## Étape 7 – Vérifier le résultat dans Elasticsearch

```console
GET _data_stream/logs-bd516*?filter_path=data_streams.name,data_streams.template,data_streams.ilm_policy,data_streams.indices.index_name

GET logs-bd516.app-default/_mapping/field/source.ip,source.geo.location,shop.duration_ms,http.response.status_code

# Un événement d'erreur complet
GET logs-bd516.app-default/_search
{
  "size": 1,
  "query": { "term": { "log.level": "ERROR" } },
  "_source": ["@timestamp", "message", "log.*", "error.type", "error.message", "http.*", "url.path", "shop", "source.geo.country_name", "tags"]
}

# Contrôle qualité : tags d'enrichissement et d'échec
GET logs-bd516.app-default/_search
{
  "size": 0,
  "aggs": {
    "tags":    { "terms": { "field": "tags", "size": 10 } },
    "niveaux": { "terms": { "field": "log.level" } },
    "pays":    { "terms": { "field": "source.geo.country_iso_code", "size": 5 } },
    "erreurs": { "terms": { "field": "error.type" } }
  }
}

GET logs-bd516.web-default/_search
{
  "size": 0,
  "aggs": {
    "statuts":     { "terms": { "field": "http.response.status_code" } },
    "navigateurs": { "terms": { "field": "user_agent.name", "size": 5 } },
    "pays":        { "terms": { "field": "source.geo.country_iso_code", "size": 5 } }
  }
}
```

Attendu : types `ip`, `geo_point`, `long` ; niveaux `INFO` (≈ 47 500), `WARN` (≈ 2 000), `ERROR` (≈ 470) ; tag `_grokparsefailure` sur **3** événements ; `internal_ip` sur les *health checks* ; `FR` largement majoritaire ; trois types d'erreur Java (`GatewayTimeoutException`, `SQLTransientConnectionException`, `NullPointerException`).

---

## Étape 8 – Vérifier l'ingestion dans Kibana Discover

1. Kibana → ☰ → **Analytics → Discover**.
2. Sélecteur de *data view* (en haut à gauche) → **Create a data view** :
   - *Name* : `BD516 - logs shop-api` ; *Index pattern* : `logs-bd516.app-*` ; *Timestamp field* : `@timestamp` ;
   - cliquez **Use without saving** (vue temporaire ; les data views définitives sont créées au Lab 05).
3. Sélecteur de temps → **Last 7 days**.
4. Repérez le pic d'erreurs **d'hier entre 14:00 et 15:30** sur l'histogramme.
5. Dans la liste des champs, cliquez `log.level` puis `source.geo.country_name` (valeurs les plus fréquentes) ; ajoutez les colonnes `log.level`, `url.path`, `http.response.status_code`, `shop.duration_ms`.
6. Ouvrez un document (icône ↗ / *Toggle dialog with details*) : onglets *Table* et *JSON*.

---

## Étape 9 – Superviser Logstash et éprouver la file persistante

### 9.1 API de supervision (port 9600, accessible uniquement depuis l'hôte)

```bash
curl -s localhost:9600/?pretty | jq '{host, version, status, pipeline}'
curl -s localhost:9600/_node/pipelines?pretty | jq '.pipelines | map_values({workers, batch_size})'
curl -s localhost:9600/_node/stats/pipelines/bd516-app | jq '.pipelines["bd516-app"] | {events, queue: {type: .queue.type, events_count: .queue.events_count}}'
curl -s localhost:9600/_node/stats/pipelines/bd516-app | jq '.pipelines["bd516-app"].plugins.filters[] | select(.name=="grok") | {id, matches, failures}'
curl -s localhost:9600/_node/stats/jvm | jq '.jvm.mem.heap_used_percent'
curl -s 'localhost:9600/_node/hot_threads?human=true&threads=3' | head -20
curl -s localhost:9600/_health_report?pretty | head -25
```

Les identifiants `id => "..."` donnés à chaque plugin rendent ces statistiques lisibles (temps passé, événements entrés/sortis par plugin).

### 9.2 La file d'attente persistante en action

Simulons une panne d'écriture : on désactive le compte `logstash_writer` pendant que des logs arrivent.

Terminal 1 – désactivez le compte :

```bash
cd ~/BD516/docker
es PUT /_security/user/logstash_writer/_disable
```

Terminal 2 – générez du trafic « en direct » pendant 90 secondes :

```bash
python3 ~/BD516/data/generators/gen_logs.py follow --rate 5 --duration 90
```

Terminal 1 – observez les événements qui s'accumulent dans la file sur disque :

```bash
cd ~/BD516/docker
for i in $(seq 1 6); do
  curl -s localhost:9600/_node/stats/pipelines/bd516-app | jq -c '.pipelines["bd516-app"].queue | {events_count, queue_size_in_bytes}'
  sleep 10
done
docker compose logs --since 2m logstash | grep -m3 -i "retry"
```

Attendu : `events_count` augmente ; les logs signalent des erreurs **401** réessayées avec un délai croissant. Réactivez le compte : la file se vide et aucun événement n'est perdu.

```bash
cd ~/BD516/docker
es PUT /_security/user/logstash_writer/_enable
sleep 60
curl -s localhost:9600/_node/stats/pipelines/bd516-app | jq -c '.pipelines["bd516-app"].queue | {events_count}'
docker compose exec logstash ls -l /usr/share/logstash/data/queue/bd516-app
```

### 9.3 Idempotence : relire tous les fichiers sans créer de doublons

```bash
cd ~/BD516/docker
BEFORE=$(es GET '/logs-bd516.app-default/_count' | jq .count)
docker compose stop logstash
docker compose run --rm --no-deps --entrypoint bash logstash -c 'rm -f /usr/share/logstash/data/sincedb-bd516-app && echo "sincedb supprime"'
docker compose start logstash
sleep 120
AFTER=$(es GET '/logs-bd516.app-default/_count' | jq .count)
echo "avant=${BEFORE} apres=${AFTER}"
```

Attendu : sans *sincedb*, Logstash relit tout le fichier depuis le début, mais `avant` = `après` : chaque événement relu a la même empreinte, donc le même `_id`, et Elasticsearch refuse le doublon (`version_conflict`, volontairement silencieux).

---

## Étape 10 – Alternatives et compléments

| Outil | Où s'exécute le traitement | Quand le préférer |
|---|---|---|
| **Logstash** | instance(s) dédiée(s) | transformations riches, sources multiples (JDBC, Kafka…), routage, files persistantes |
| **Filebeat / Metricbeat** | agent léger sur chaque serveur | collecte de fichiers et métriques ; envoi vers Logstash (`beats`) ou Elasticsearch |
| **Elastic Agent + Fleet Server** | agent unique, géré centralement | parc important : intégrations prêtes à l'emploi, politiques centralisées |
| **Ingest pipelines** | nœuds Elasticsearch `ingest` | transformations simples sans composant supplémentaire |

Le même parsing nginx, réalisé **par Elasticsearch** avec un ingest pipeline (simulation) :

```console
POST _ingest/pipeline/_simulate
{
  "pipeline": {
    "description": "BD516 - equivalent cote Elasticsearch du pipeline bd516-web",
    "processors": [
      { "grok": { "field": "message", "patterns": ["%{COMBINEDAPACHELOG}"], "ecs_compatibility": "v1" } },
      { "date": { "field": "timestamp", "formats": ["dd/MMM/yyyy:HH:mm:ss Z"], "locale": "en" } },
      { "user_agent": { "field": "user_agent.original" } },
      { "remove": { "field": "timestamp" } }
    ]
  },
  "docs": [
    { "_source": { "message": "90.84.12.3 - - [25/Nov/2026:14:03:12 +0100] \"GET /produits/ski HTTP/1.1\" 200 5123 \"https://www.google.com/\" \"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0\"" } }
  ]
}
```

Attendu : `source.address`, `http.request.method`, `url.original`, `http.response.status_code`, `user_agent.name: Firefox`, `@timestamp: 2026-11-25T13:03:12.000Z`.

---

## Étape 11 (bonus) – Supervision de Logstash avec Metricbeat

En production, Logstash est supervisé par **Metricbeat** (module `logstash`, mode `xpack.enabled`) ou Elastic Agent, qui collectent `_node/stats` et alimentent l'application **Stack Monitoring** de Kibana.

```bash
cat > ~/BD516/docker/metricbeat/metricbeat.yml <<'BD516_EOF'
# =============================================================================
# BD516 - Metricbeat : supervision de Logstash (bonus Lab 04)
# Collecte l'API _node/stats de Logstash et envoie les donnees au format
# "Stack Monitoring" (index .monitoring-logstash-*) du cluster BD516.
# =============================================================================
metricbeat.modules:
  - module: logstash
    xpack.enabled: true
    period: 10s
    hosts: ["http://logstash:9600"]

output.elasticsearch:
  hosts: ["https://es01:9200", "https://es02:9200", "https://es03:9200"]
  username: "remote_monitoring_user"
  password: "${MONITORING_PASSWORD}"
  ssl.certificate_authorities: ["/usr/share/metricbeat/certs/ca/ca.crt"]

# Les donnees de supervision ont leurs propres modeles : pas de template ni d'ILM Metricbeat
setup.template.enabled: false
setup.ilm.enabled: false
monitoring.enabled: false
logging.level: info
BD516_EOF

cd ~/BD516/docker
sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=logstash,monitoring/' .env
source ~/BD516/scripts/bd516-env.sh
es POST /_security/user/remote_monitoring_user/_password -d "{\"password\":\"${MONITORING_PASSWORD}\"}"
docker compose up -d metricbeat
sleep 60
docker compose logs --tail 20 metricbeat | grep -iE "error|connection" | head -5
es GET '/_cat/indices/.monitoring-logstash-*?v&h=index,docs.count&expand_wildcards=all'
```

Attendu : un index `.ds-.monitoring-logstash-8-mb-...` dont le nombre de documents augmente. Dans Kibana : ☰ → **Management → Stack Monitoring** → carte *Logstash* (rubrique **Standalone cluster** : on n'a pas associé Logstash à l'UUID du cluster) → débit, latence et file d'attente par pipeline.

---

## Commandes utiles

```bash
cd ~/BD516/docker
docker compose logs -f --tail 50 logstash          # logs de Logstash
docker compose restart logstash                    # après modification de logstash.yml ou pipelines.yml
# (les fichiers *.conf des pipelines sont rechargés automatiquement toutes les 5 s)
curl -s localhost:9600/_node/stats/pipelines?pretty | less
```

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| `Cannot evaluate ${LOGSTASH_WRITER_PASSWORD}` | variable absente du `.env` : `./scripts/init-env.sh` (ou ajoutez-la) puis `docker compose up -d logstash` |
| Erreurs **401** en continu | utilisateur `logstash_writer` absent, désactivé ou mot de passe différent du `.env` (étape 2.3) |
| Erreurs **403** `action [indices:data/write/bulk[s]] is unauthorized` | rôle incomplet : privilèges `create_doc` et `auto_configure` sur `logs-bd516.*` |
| `Expected one of ... after` au démarrage d'un pipeline | erreur de syntaxe : relancez l'étape 5.3 pour obtenir la ligne et la colonne |
| Tag `_geoip_expired_database` | base GeoLite2 non téléchargée : accès sortant à `geoip.elastic.co` et `storage.googleapis.com` requis |
| Aucun document après 2 minutes | fichiers non visibles dans le conteneur : `docker compose exec logstash ls -l /data/logs/app` |
| Fichier ajouté dans `pipeline/` ignoré | seuls les fichiers `*.conf` des dossiers déclarés dans `pipelines.yml` sont chargés (un fichier `.txt` parasite est sans effet) |
| `mapper_parsing_exception` sur `constant_keyword` | un même data stream reçoit deux valeurs différentes de `data_stream.dataset` |

## Récapitulatif

- [x] Input `file` (mode tail, sincedb) avec codec `multiline` pour les stack traces Java
- [x] Parsing grok (2 motifs, `@metadata`, conversions), date avec fuseau, mutate, cidr, geoip, useragent
- [x] Output `elasticsearch` sécurisé (TLS vérifié, compte dédié), data streams + ILM, idempotence par empreinte
- [x] Deux pipelines dans `pipelines.yml`, file persistante éprouvée, supervision par l'API `_node/stats`
- [x] Ingestion vérifiée dans Elasticsearch et dans Kibana Discover
