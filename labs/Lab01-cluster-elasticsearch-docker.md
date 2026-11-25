## BD516– Lab 01 : démarrer un cluster Elasticsearch sécurisé avec Docker Compose

> **Chapitre 1 – Introduction à Elastic Stack et architecture d'Elasticsearch**
> Prérequis : Lab 00 terminé (`./scripts/check-prereqs.sh` sans erreur) – Durée : 1 h 30

## Objectifs

| Travaux pratiques du programme | Étapes |
|---|---|
| Démarrer un cluster Elasticsearch local via Docker Compose (Elasticsearch + Kibana) | 1 à 3 |
| Consulter les logs du conteneur Elasticsearch (`docker logs`) et identifier les messages de démarrage clés | 4 |
| Vérifier l'état du cluster via les API `_cluster/health` et `_cat` | 5 |
| Interroger l'API REST avec curl et Kibana Dev Tools (Console) | 5 à 7 |
| Configurer l'authentification et créer un premier utilisateur | 8 |
| *Compléments : rôles des nœuds, TLS, tolérance aux pannes, ECS* | 6, 9, 10 |

## Architecture déployée

```text
                       Navigateur (Windows ou VM)  https://localhost:5601
                                         |
  +--------------------------------------+-------------------------------------------+
  |  réseau Docker "elastic"             |                                           |
  |                                +-----------+                                     |
  |  setup (one-shot) : CA + certs |  kibana   |  HTTPS, 3 hôtes ES                  |
  |                                +-----------+                                     |
  |         TLS (HTTP 9200 + transport 9300)  |                                      |
  |   +----------------+   +----------------+   +--------------------------------+   |
  |   | es01           |   | es02           |   | es03                           |   |
  |   | master, ingest |   | master, ingest |   | master *voting_only*, ingest   |   |
  |   | data_content   |   | data_content   |   | data_warm, data_cold           |   |
  |   | data_hot, ml   |   | data_hot, ml   |   |                                |   |
  |   +-------+--------+   +-------+--------+   +---------------+----------------+   |
  +-----------|--------------------|----------------------------|--------------------+
          :9200                 :9201                        :9202     (ports de l'hôte)
```

- **3 nœuds éligibles master** : le quorum (2 sur 3) tolère la perte d'un nœud.
- **es03** est *voting_only* : il vote mais ne peut pas être élu ; il porte les **tiers warm/cold** utilisés par ILM (Lab 02).
- **Sécurité native activée** : authentification obligatoire, **TLS** sur l'API HTTP *et* entre les nœuds (transport).
- Réglages de production portés par le fichier Compose : `bootstrap.memory_lock`, `ulimits memlock/nofile`, `net.ipv4.tcp_retries2=5`, limites mémoire (le *heap* JVM est dimensionné automatiquement à partir de la limite mémoire du conteneur), version d'image figée (9.5.7), volumes de données dédiés.

---

## Étape 1 – Créer les fichiers de déploiement

### 1.1 Script du conteneur `setup` (certificats TLS, mot de passe `kibana_system`)

```bash
cat > ~/BD516/docker/setup/setup.sh <<'BD516_EOF'
#!/usr/bin/env bash
# =============================================================================
# BD516 - Conteneur "setup" (execute une fois a chaque "docker compose up")
#  1. prepare le repertoire partage des snapshots (proprietaire elasticsearch)
#  2. genere l'autorite de certification (CA) et les certificats TLS
#     des noeuds es01/es02/es03 et de Kibana (si absents)
#  3. attend que le cluster reponde puis fixe le mot de passe de kibana_system
# Le script est idempotent : il peut etre relance sans risque.
# =============================================================================
set -euo pipefail

CERTS=/usr/share/elasticsearch/config/certs
SNAP=/usr/share/elasticsearch/snapshots
cd /usr/share/elasticsearch

log() { echo "[setup] $(date '+%H:%M:%S') $*"; }

[[ -n "${ELASTIC_PASSWORD:-}" ]] || { log "ERREUR : ELASTIC_PASSWORD absent du fichier .env"; exit 1; }
[[ -n "${KIBANA_PASSWORD:-}"  ]] || { log "ERREUR : KIBANA_PASSWORD absent du fichier .env";  exit 1; }

# --- 1. Repertoire des snapshots (volume partage par les 3 noeuds) -----------
log "Droits du depot de snapshots ${SNAP}"
mkdir -p "${SNAP}"
chown -R 1000:0 "${SNAP}"
chmod 2770 "${SNAP}"

# --- 2. Certificats ---------------------------------------------------------
if [[ ! -f "${CERTS}/ca/ca.crt" ]]; then
  log "Creation de l'autorite de certification (CA)"
  bin/elasticsearch-certutil ca --silent --pem --days 1825 -out "${CERTS}/ca.zip"
  unzip -o -q "${CERTS}/ca.zip" -d "${CERTS}"
  rm -f "${CERTS}/ca.zip"
fi

if [[ ! -f "${CERTS}/kibana/kibana.crt" ]]; then
  log "Creation des certificats es01, es02, es03, kibana"
  # SAN supplementaires (nom et IP de la machine hote) passes par le fichier .env
  extra_dns=""; extra_ip=""
  IFS=',' read -r -a dns_list <<< "${CERT_SAN_DNS:-}"
  for d in "${dns_list[@]}"; do
    if [[ -n "$d" ]]; then extra_dns+="      - ${d}"$'\n'; fi
  done
  IFS=',' read -r -a ip_list <<< "${CERT_SAN_IPS:-}"
  for i in "${ip_list[@]}"; do
    if [[ -n "$i" ]]; then extra_ip+="      - ${i}"$'\n'; fi
  done

  {
    echo "instances:"
    for name in es01 es02 es03 kibana; do
      echo "  - name: ${name}"
      echo "    dns:"
      echo "      - ${name}"
      echo "      - localhost"
      printf '%s' "${extra_dns}"
      echo "    ip:"
      echo "      - 127.0.0.1"
      printf '%s' "${extra_ip}"
    done
  } > "${CERTS}/instances.yml"
  cat "${CERTS}/instances.yml"

  bin/elasticsearch-certutil cert --silent --pem --days 1825 \
    --in "${CERTS}/instances.yml" -out "${CERTS}/certs.zip" \
    --ca-cert "${CERTS}/ca/ca.crt" --ca-key "${CERTS}/ca/ca.key"
  unzip -o -q "${CERTS}/certs.zip" -d "${CERTS}"
  rm -f "${CERTS}/certs.zip"
fi

log "Application des permissions sur les certificats"
chown -R root:root "${CERTS}"
find "${CERTS}" -type d -exec chmod 750 {} \;
find "${CERTS}" -type f ! -name .gitkeep -exec chmod 640 {} \;
# Le certificat PUBLIC de la CA doit etre lisible par tous (Logstash, Metricbeat, curl sur l'hote)
chmod 755 "${CERTS}" "${CERTS}/ca"
chmod 644 "${CERTS}/ca/ca.crt"
chmod 600 "${CERTS}/ca/ca.key"
touch /tmp/certs.ready   # marqueur utilise par le healthcheck du service setup

# --- 3. Mot de passe de l'utilisateur interne kibana_system ------------------
log "Attente de la disponibilite d'Elasticsearch (https://es01:9200)"
for attempt in $(seq 1 120); do
  if curl -s --cacert "${CERTS}/ca/ca.crt" https://es01:9200 | grep -q "missing authentication credentials"; then
    break
  fi
  if [[ "${attempt}" -eq 120 ]]; then
    log "ERREUR : es01 ne repond pas apres 10 minutes -> docker compose logs es01"
    exit 1
  fi
  sleep 5
done

log "Definition du mot de passe de kibana_system"
for attempt in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST --cacert "${CERTS}/ca/ca.crt" \
         -u "elastic:${ELASTIC_PASSWORD}" -H 'Content-Type: application/json' \
         https://es01:9200/_security/user/kibana_system/_password \
         -d "{\"password\":\"${KIBANA_PASSWORD}\"}" || true)
  if [[ "${code}" == "200" ]]; then
    break
  fi
  if [[ "${attempt}" -eq 60 ]]; then
    log "ERREUR : impossible de definir le mot de passe kibana_system (dernier code HTTP : ${code})"
    log "Verifiez ELASTIC_PASSWORD dans docker/.env (il n'est pris en compte qu'au tout premier demarrage)"
    exit 1
  fi
  sleep 5
done

log "Termine : certificats en place et kibana_system configure."
# Reste actif quelques secondes pour que Docker Compose constate l'etat "healthy"
# (redemarrage d'un seul noeud alors que le cluster repond deja)
sleep 10
BD516_EOF
chmod +x ~/BD516/docker/setup/setup.sh
```

### 1.2 Configuration de Kibana

```bash
cat > ~/BD516/docker/kibana/kibana.yml <<'BD516_EOF'
# =============================================================================
# BD516 - Configuration Kibana (montee en lecture seule dans le conteneur)
# Les valeurs ${VAR} sont substituees par Kibana a partir des variables
# d'environnement du conteneur (elles-memes issues du fichier .env).
# =============================================================================
server.name: kibana
server.host: "0.0.0.0"
server.shutdownTimeout: "5s"
# URL publique : utilisee dans les liens de partage et les rapports
server.publicBaseUrl: "${KIBANA_PUBLIC_URL}"

# --- HTTPS cote navigateur ---------------------------------------------------
server.ssl.enabled: true
server.ssl.certificate: /usr/share/kibana/config/certs/kibana/kibana.crt
server.ssl.key: /usr/share/kibana/config/certs/kibana/kibana.key

# --- Connexion au cluster (les 3 noeuds, TLS verifie par la CA) --------------
elasticsearch.hosts: ["https://es01:9200", "https://es02:9200", "https://es03:9200"]
elasticsearch.username: "kibana_system"
elasticsearch.password: "${KIBANA_PASSWORD}"
elasticsearch.ssl.certificateAuthorities: ["/usr/share/kibana/config/certs/ca/ca.crt"]
elasticsearch.ssl.verificationMode: full

# --- Cles de chiffrement (obligatoires en production, >= 32 caracteres) -------
xpack.encryptedSavedObjects.encryptionKey: "${KIBANA_ENC_KEY}"
xpack.reporting.encryptionKey: "${KIBANA_REPORTING_KEY}"
xpack.security.encryptionKey: "${KIBANA_SECURITY_KEY}"

# --- Divers -------------------------------------------------------------------
monitoring.ui.container.elasticsearch.enabled: true
telemetry.optIn: false
# Interface en francais : decommentez la ligne suivante (les labs citent les libelles anglais)
#i18n.locale: "fr-FR"
BD516_EOF
```

### 1.3 Fichier Docker Compose

Le fichier contient déjà les services **logstash** et **metricbeat** : ils appartiennent à des *profils* Compose et ne démarreront qu'au Lab 04.

```bash
cat > ~/BD516/docker/docker-compose.yml <<'BD516_EOF'
# =============================================================================
# BD516 - Elastic Stack : Elasticsearch, Logstash et Kibana
# Cluster de formation "proche production" :
#   - 3 noeuds Elasticsearch, securite activee, TLS sur HTTP et transport
#   - roles differencies (es03 = voting_only + tiers warm/cold) pour ILM
#   - Kibana en HTTPS
#   - Logstash (profil "logstash", Lab 04) et Metricbeat (profil "monitoring")
# Toutes les valeurs variables sont dans le fichier .env (scripts/init-env.sh)
# =============================================================================
name: bd516

x-es-common: &es-common
  image: docker.elastic.co/elasticsearch/elasticsearch:${STACK_VERSION}
  restart: unless-stopped
  mem_limit: ${ES_MEM_LIMIT}
  ulimits:
    memlock:
      soft: -1
      hard: -1
    nofile:
      soft: 65535
      hard: 65535
  sysctls:
    net.ipv4.tcp_retries2: 5
  networks:
    - elastic
  healthcheck:
    test: ["CMD-SHELL", "curl -s --cacert config/certs/ca/ca.crt https://localhost:9200 | grep -q 'missing authentication credentials'"]
    interval: 10s
    timeout: 10s
    retries: 120
    start_period: 30s

x-es-env: &es-env
  cluster.name: ${CLUSTER_NAME}
  cluster.initial_master_nodes: es01,es02,es03
  ELASTIC_PASSWORD: ${ELASTIC_PASSWORD}
  bootstrap.memory_lock: "true"
  path.repo: /usr/share/elasticsearch/snapshots
  xpack.security.enabled: "true"
  xpack.security.http.ssl.enabled: "true"
  xpack.security.http.ssl.certificate_authorities: certs/ca/ca.crt
  xpack.security.transport.ssl.enabled: "true"
  xpack.security.transport.ssl.certificate_authorities: certs/ca/ca.crt
  xpack.security.transport.ssl.verification_mode: certificate
  xpack.license.self_generated.type: ${LICENSE}
  xpack.ml.use_auto_machine_memory_percent: "true"

services:
  # ---------------------------------------------------------------------------
  # Initialisation : certificats TLS + mot de passe kibana_system
  # ---------------------------------------------------------------------------
  setup:
    image: docker.elastic.co/elasticsearch/elasticsearch:${STACK_VERSION}
    user: "0"
    command: ["bash", "/setup/setup.sh"]
    environment:
      ELASTIC_PASSWORD: ${ELASTIC_PASSWORD}
      KIBANA_PASSWORD: ${KIBANA_PASSWORD}
      CERT_SAN_DNS: ${CERT_SAN_DNS:-}
      CERT_SAN_IPS: ${CERT_SAN_IPS:-}
    volumes:
      - ./setup/setup.sh:/setup/setup.sh:ro
      - ./certs:/usr/share/elasticsearch/config/certs
      - snapshots:/usr/share/elasticsearch/snapshots
    networks:
      - elastic
    healthcheck:
      test: ["CMD-SHELL", "test -f /tmp/certs.ready"]
      interval: 2s
      timeout: 5s
      retries: 150

  # ---------------------------------------------------------------------------
  # Noeuds Elasticsearch
  # ---------------------------------------------------------------------------
  es01:
    <<: *es-common
    depends_on:
      setup:
        condition: service_healthy
    hostname: es01
    ports:
      - "${BIND_IP}:9200:9200"
    volumes:
      - ./certs:/usr/share/elasticsearch/config/certs:ro
      - esdata01:/usr/share/elasticsearch/data
      - snapshots:/usr/share/elasticsearch/snapshots
    environment:
      <<: *es-env
      node.name: es01
      node.roles: master,data_content,data_hot,ingest,ml,remote_cluster_client,transform
      discovery.seed_hosts: es02,es03
      xpack.security.http.ssl.key: certs/es01/es01.key
      xpack.security.http.ssl.certificate: certs/es01/es01.crt
      xpack.security.transport.ssl.key: certs/es01/es01.key
      xpack.security.transport.ssl.certificate: certs/es01/es01.crt

  es02:
    <<: *es-common
    depends_on:
      setup:
        condition: service_healthy
    hostname: es02
    ports:
      - "${BIND_IP}:9201:9200"
    volumes:
      - ./certs:/usr/share/elasticsearch/config/certs:ro
      - esdata02:/usr/share/elasticsearch/data
      - snapshots:/usr/share/elasticsearch/snapshots
    environment:
      <<: *es-env
      node.name: es02
      node.roles: master,data_content,data_hot,ingest,ml,remote_cluster_client,transform
      discovery.seed_hosts: es01,es03
      xpack.security.http.ssl.key: certs/es02/es02.key
      xpack.security.http.ssl.certificate: certs/es02/es02.crt
      xpack.security.transport.ssl.key: certs/es02/es02.key
      xpack.security.transport.ssl.certificate: certs/es02/es02.crt

  es03:
    <<: *es-common
    depends_on:
      setup:
        condition: service_healthy
    hostname: es03
    ports:
      - "${BIND_IP}:9202:9200"
    volumes:
      - ./certs:/usr/share/elasticsearch/config/certs:ro
      - esdata03:/usr/share/elasticsearch/data
      - snapshots:/usr/share/elasticsearch/snapshots
    environment:
      <<: *es-env
      node.name: es03
      # noeud "voting_only" : vote pour l'election du master mais ne peut pas etre elu
      node.roles: master,voting_only,data_warm,data_cold,ingest
      discovery.seed_hosts: es01,es02
      xpack.security.http.ssl.key: certs/es03/es03.key
      xpack.security.http.ssl.certificate: certs/es03/es03.crt
      xpack.security.transport.ssl.key: certs/es03/es03.key
      xpack.security.transport.ssl.certificate: certs/es03/es03.crt

  # ---------------------------------------------------------------------------
  # Kibana (HTTPS)
  # ---------------------------------------------------------------------------
  kibana:
    image: docker.elastic.co/kibana/kibana:${STACK_VERSION}
    restart: unless-stopped
    depends_on:
      setup:
        condition: service_completed_successfully
      es01:
        condition: service_healthy
      es02:
        condition: service_healthy
      es03:
        condition: service_healthy
    hostname: kibana
    mem_limit: ${KB_MEM_LIMIT}
    ports:
      - "${BIND_IP}:5601:5601"
    environment:
      KIBANA_PASSWORD: ${KIBANA_PASSWORD}
      KIBANA_PUBLIC_URL: ${KIBANA_PUBLIC_URL}
      KIBANA_ENC_KEY: ${KIBANA_ENC_KEY}
      KIBANA_REPORTING_KEY: ${KIBANA_REPORTING_KEY}
      KIBANA_SECURITY_KEY: ${KIBANA_SECURITY_KEY}
    volumes:
      - ./kibana/kibana.yml:/usr/share/kibana/config/kibana.yml:ro
      - ./certs:/usr/share/kibana/config/certs:ro
      - kibanadata:/usr/share/kibana/data
    networks:
      - elastic
    healthcheck:
      test: ["CMD-SHELL", "curl -s -o /dev/null -w '%{http_code}' --cacert /usr/share/kibana/config/certs/ca/ca.crt https://localhost:5601/ | grep -q 302"]
      interval: 10s
      timeout: 10s
      retries: 120
      start_period: 60s

  # ---------------------------------------------------------------------------
  # Logstash (Lab 04) - active avec COMPOSE_PROFILES=logstash dans .env
  # ---------------------------------------------------------------------------
  logstash:
    profiles: ["logstash"]
    image: docker.elastic.co/logstash/logstash:${STACK_VERSION}
    restart: unless-stopped
    depends_on:
      setup:
        condition: service_completed_successfully
      es01:
        condition: service_healthy
    hostname: logstash
    mem_limit: ${LS_MEM_LIMIT}
    ulimits:
      nofile:
        soft: 65535
        hard: 65535
    environment:
      LS_JAVA_OPTS: "-Xms${LS_HEAP} -Xmx${LS_HEAP}"
      LOGSTASH_WRITER_PASSWORD: ${LOGSTASH_WRITER_PASSWORD}
    ports:
      - "127.0.0.1:9600:9600"
    volumes:
      - ./logstash/config/logstash.yml:/usr/share/logstash/config/logstash.yml:ro
      - ./logstash/config/pipelines.yml:/usr/share/logstash/config/pipelines.yml:ro
      - ./logstash/pipeline:/usr/share/logstash/pipeline:ro
      - ./logstash/dev:/usr/share/logstash/dev:ro
      - ./certs:/usr/share/logstash/config/certs:ro
      - ../data/logs:/data/logs:ro
      - logstashdata:/usr/share/logstash/data
    networks:
      - elastic
    healthcheck:
      test: ["CMD-SHELL", "curl -s -f http://localhost:9600/ > /dev/null"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 60s

  # ---------------------------------------------------------------------------
  # Metricbeat (bonus Lab 04) - supervision de Logstash, profil "monitoring"
  # ---------------------------------------------------------------------------
  metricbeat:
    profiles: ["monitoring"]
    image: docker.elastic.co/beats/metricbeat:${STACK_VERSION}
    restart: unless-stopped
    depends_on:
      es01:
        condition: service_healthy
    hostname: metricbeat
    command: ["-environment", "container", "-e", "--strict.perms=false"]
    environment:
      MONITORING_PASSWORD: ${MONITORING_PASSWORD}
    volumes:
      - ./metricbeat/metricbeat.yml:/usr/share/metricbeat/metricbeat.yml:ro
      - ./certs:/usr/share/metricbeat/certs:ro
    networks:
      - elastic

networks:
  elastic:
    driver: bridge

volumes:
  esdata01:
  esdata02:
  esdata03:
  snapshots:
  kibanadata:
  logstashdata:
BD516_EOF
```

---

## Étape 2 – Générer le fichier `.env`

```bash
cd ~/BD516
./scripts/init-env.sh
source ~/BD516/scripts/bd516-env.sh      # recharge les variables (ELASTIC_PASSWORD, ...)
grep -vE 'PASSWORD|KEY' docker/.env       # paramètres non secrets
```

> Le mot de passe de l'utilisateur `elastic` s'affiche ; retrouvez-le à tout moment avec `bd516_passwords`.

Contrôlez la configuration finale interprétée par Compose (variables substituées) :

```bash
cd ~/BD516/docker
docker compose config --quiet && echo "docker-compose.yml valide"
docker compose config --services
```

Résultat attendu : `docker-compose.yml valide` puis `setup`, `es01`, `es02`, `es03`, `kibana` (les services des profils `logstash` et `monitoring` n'apparaissent pas).

---

## Étape 3 – Démarrer le cluster

```bash
cd ~/BD516/docker
docker compose up -d              # rend la main quand Kibana peut démarrer (1 à 3 min au premier lancement)
docker compose logs setup         # doit se terminer par "Termine : certificats en place et kibana_system configure."
```

Suivez la montée en charge (1 à 3 minutes selon le poste) :

```bash
cd ~/BD516/docker
watch -n 5 docker compose ps -a
```

Résultat attendu (Ctrl+C pour quitter `watch`) :

```text
NAME             SERVICE   STATUS                     PORTS
bd516-es01-1     es01      Up 2 minutes (healthy)     0.0.0.0:9200->9200/tcp, 9300/tcp
bd516-es02-1     es02      Up 2 minutes (healthy)     0.0.0.0:9201->9200/tcp, 9300/tcp
bd516-es03-1     es03      Up 2 minutes (healthy)     0.0.0.0:9202->9200/tcp, 9300/tcp
bd516-kibana-1   kibana    Up 1 minute (healthy)      0.0.0.0:5601->5601/tcp
bd516-setup-1    setup     Exited (0) 1 minute ago
```

> `setup` en **Exited (0)** est normal : c'est un conteneur d'initialisation. Les certificats générés sont visibles dans `~/BD516/docker/certs/` (la clé privée de la CA n'est lisible que par root).

```bash
ls -l ~/BD516/docker/certs ~/BD516/docker/certs/ca
```

---

## Étape 4 – Lire les logs de démarrage

Les logs Elasticsearch sont au format JSON (ECS). Repérez les messages clés du nœud **es01** :

```bash
cd ~/BD516/docker
docker compose logs es01 2>&1 | grep -E "non-loopback|master node changed|\"started|health status changed" | cut -c1-260
```

| Message | Signification |
|---|---|
| `bound or publishing to a non-loopback address, enforcing bootstrap checks` | mode production : les *bootstrap checks* (mémoire virtuelle, memory lock, descripteurs…) sont **bloquants** |
| `master node changed {previous [], current [{es0X}...]}` | le nœud a rejoint le cluster ; le nom placé après `current` est le **master élu** (message présent sur les 3 nœuds) |
| `started {es01}{...}` | le nœud est démarré et accepte les requêtes |
| `Cluster health status changed from [YELLOW] to [GREEN]` | toutes les copies de shards sont allouées (affiché par le master) |

Trouvez le nœud élu :

```bash
cd ~/BD516/docker
for n in es01 es02 es03; do
  printf '%s -> master : ' "$n"
  docker compose logs "$n" 2>&1 | grep -o 'current \[{es0[1-3]}' | tail -1 | sed -E 's/.*\{(es0[1-3])\}/\1/'
done
```

Résultat attendu : les trois nœuds désignent **le même master**, es01 ou es02 selon l'ordre de démarrage (es03, *voting_only*, n'est jamais élu). Par exemple :

```text
es01 -> master : es02
es02 -> master : es02
es03 -> master : es02
```

`tail -1` retient le dernier changement de master connu de chaque nœud (utile après la bascule de l'étape 9). L'API `_cat/master` de l'étape 5 donne la même information, et la commande Docker équivalente `docker logs bd516-es01-1` fonctionne aussi.

> Ne cherchez pas le master avec le marqueur interne `_FINISH_ELECTION_` : il peut figurer dans les logs de plusieurs nœuds et ne désigne donc pas le nœud élu de façon fiable.

Vérifiez que les réglages de production sont actifs **dans** le conteneur :

```bash
cd ~/BD516/docker
docker compose exec es01 bash -c 'ulimit -n; ulimit -l; cat /proc/sys/net/ipv4/tcp_retries2; cat /proc/sys/vm/max_map_count'
```

Résultat attendu : `65535`, `unlimited`, `5`, `1048576`.

---

## Étape 5 – Interroger l'API REST avec curl

### 5.1 Sans authentification, puis avec

```bash
curl -s --cacert ~/BD516/docker/certs/ca/ca.crt https://localhost:9200 ; echo
curl -s --cacert ~/BD516/docker/certs/ca/ca.crt -u "elastic:${ELASTIC_PASSWORD}" https://localhost:9200 | jq
```

La première requête renvoie une erreur **401** (`missing authentication credentials`), la seconde la fiche du cluster (`cluster_name: bd516-cluster`, `version.number: 9.5.7`, `cluster_uuid`, `tagline`).

À partir de maintenant, on utilise la fonction `es` (définie dans `scripts/bd516-env.sh`), qui ajoute la CA et les identifiants : `es MÉTHODE CHEMIN [options curl]`. **Mettez toujours le chemin entre apostrophes** (caractères `?` et `&`).

### 5.2 Santé du cluster

```bash
es GET '/_cluster/health?pretty'
es GET '/_cat/health?v'
```

Résultat attendu : `"status" : "green"`, `"number_of_nodes" : 3`, `"number_of_data_nodes" : 3`, `"unassigned_shards" : 0`.

| Couleur | Signification |
|---|---|
| green | tous les shards primaires et réplicas sont alloués |
| yellow | tous les primaires sont alloués, au moins un réplica ne l'est pas |
| red | au moins un shard primaire n'est pas alloué (données indisponibles) |

### 5.3 Les nœuds et leurs rôles

```bash
es GET '/_cat/nodes?v&h=name,ip,node.role,master,heap.max,ram.max,cpu,load_1m&s=name'
es GET '/_cat/master?v'
es GET '/_nodes/es03?filter_path=nodes.*.name,nodes.*.roles&pretty'
```

Résultat attendu :

```text
name ip         node.role master heap.max ram.max ...
es01 172.x.x.x  hilmrst   *      1gb      2gb
es02 172.x.x.x  hilmrst   -      1gb      2gb
es03 172.x.x.x  cimvw     -      1gb      2gb
```

(le `*` désigne le master ; les tailles dépendent du profil mémoire choisi par `init-env.sh`)

| Lettre | Rôle | Lettre | Rôle |
|---|---|---|---|
| m | master | v | voting_only |
| s | data_content | h | data_hot |
| w | data_warm | c | data_cold |
| i | ingest | l | ml (machine learning) |
| r | remote_cluster_client | t | transform |

> **Nœud « coordinating »** : tout nœud coordonne les requêtes qu'il reçoit (répartition sur les shards, fusion des résultats). Un nœud *coordinating-only* a simplement `node.roles: [ ]`.

### 5.4 Allocation, index et shards

```bash
es GET '/_cat/allocation?v'
es GET '/_cat/indices?v&expand_wildcards=all&s=index'
es GET '/_cat/shards?v&s=index' | head -20
```

Les index système (préfixe `.`, cachés) existent déjà : sécurité, Kibana, etc. Observez que leurs shards sont uniquement sur **es01/es02** : es03 n'a pas le rôle `data_content`.

---

## Étape 6 – Vérifier le chiffrement TLS

```bash
# Le certificat présenté par es01 et ses noms alternatifs (SAN)
openssl s_client -connect localhost:9200 -CAfile ~/BD516/docker/certs/ca/ca.crt </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName

# HTTP en clair : refusé
curl -s -m 5 http://localhost:9200 || echo "=> connexion HTTP en clair refusee (TLS obligatoire)"
```

Résultat attendu : `subject=CN=es01`, `issuer=CN=Elastic Certificate Tool Autogenerated CA`, SAN `DNS:es01, DNS:localhost, IP Address:127.0.0.1, ...`, puis le message de refus.

---

## Étape 7 – Kibana et la console Dev Tools

1. Ouvrez **https://localhost:5601** (sous WSL, depuis le navigateur Windows ; sur une VM Hyper-V consultée depuis Windows, utilisez `https://<IP-de-la-VM>:5601`, l'IP étant donnée par `hostname -I`).
2. Le navigateur signale un certificat non reconnu (CA de formation) : *Paramètres avancés → Continuer vers le site*.
   *Facultatif* – faire confiance à la CA sous Windows : copiez-la (`cp ~/BD516/docker/certs/ca/ca.crt /mnt/c/Users/<vous>/Downloads/bd516-ca.crt` sous WSL), puis en **PowerShell** :
   `Import-Certificate -FilePath "$env:USERPROFILE\Downloads\bd516-ca.crt" -CertStoreLocation Cert:\CurrentUser\Root`
3. Connectez-vous avec **elastic** / le mot de passe affiché par `bd516_passwords`.
4. Sur la page d'accueil, choisissez **Explore on my own**.
5. Ouvrez le menu ☰ → **Management → Dev Tools** (onglet **Console**).

Saisissez les requêtes suivantes ; exécutez chacune avec **Ctrl+Entrée** (ou ▶) :

```console
GET /

GET _cluster/health

GET _cat/nodes?v&h=name,node.role,master,heap.percent,disk.used_percent

GET _cat/indices?v&expand_wildcards=all&s=index
```

Astuces Console : auto-complétion (Ctrl+Espace), indentation (Ctrl+I), historique, et l'icône 🔧 → **Copy as curl** pour obtenir la commande curl équivalente.

---

## Étape 8 – Authentification : rôle, utilisateur et clé d'API

Le super-utilisateur `elastic` ne doit servir qu'à l'administration initiale. Créons un profil **analyste** en lecture seule.

### 8.1 Qui suis-je ?

```console
GET _security/_authenticate
```

Repérez `username`, `roles` (`superuser`) et `authentication_realm` (`reserved`). Les utilisateurs internes (`kibana_system`, `logstash_system`, `remote_monitoring_user`…) sont listés par :

```console
GET _security/user?filter_path=*.username,*.roles
```

### 8.2 Créer le rôle `bd516_analyst`

Ce rôle donne la lecture sur les futurs index de la formation (`bd516-*`, `logs-bd516.*`) et un accès **en lecture** à Kibana. On utilise l'API Kibana, qui gère à la fois la partie Elasticsearch et la partie Kibana du rôle :

```bash
kb PUT /api/security/role/bd516_analyst -d @- <<'EOF'
{
  "elasticsearch": {
    "cluster": ["monitor"],
    "indices": [
      { "names": ["bd516-*", "logs-bd516.*"], "privileges": ["read", "view_index_metadata"] }
    ]
  },
  "kibana": [
    { "base": ["read"], "spaces": ["*"] }
  ]
}
EOF
es GET '/_security/role/bd516_analyst?pretty'
```

Résultat attendu : réponse vide (HTTP 204) puis la définition du rôle (la partie Kibana apparaît dans `applications`). Le même rôle se crée dans l'interface : ☰ → **Stack Management → Security → Roles → Create role**.

### 8.3 Créer l'utilisateur `analyste`

```console
POST _security/user/analyste
{
  "password": "Analyste-BD516",
  "roles": ["bd516_analyst"],
  "full_name": "Analyste BD516",
  "email": "analyste@bd516.example"
}
```

Résultat attendu : `{"created": true}`.

Testez ses droits avec curl :

```bash
curl -s --cacert "$ES_CA" -u analyste:Analyste-BD516 "$ES_URL/_security/_authenticate" | jq '{username, roles}'
curl -s --cacert "$ES_CA" -u analyste:Analyste-BD516 "$ES_URL/_cluster/health" | jq .status
curl -s --cacert "$ES_CA" -u analyste:Analyste-BD516 -X PUT "$ES_URL/bd516-interdit" | jq .error.type
```

Résultat attendu : `"analyste"`, `"green"` puis `"security_exception"` (création d'index refusée : **403**).

Dans une **fenêtre de navigation privée**, connectez-vous à Kibana avec `analyste` : les menus de création et de gestion sont absents (lecture seule).

### 8.4 Clé d'API (authentification des applications)

En production, une application s'authentifie avec une **clé d'API** révocable et à durée de vie limitée plutôt qu'avec un mot de passe :

```bash
API_KEY=$(es POST /_security/api_key -d '{
  "name": "bd516-app-lecture",
  "expiration": "7d",
  "role_descriptors": {
    "lecture_bd516": { "indices": [ { "names": ["bd516-*"], "privileges": ["read"] } ] }
  }
}' | jq -r .encoded)
echo "$API_KEY"
curl -s --cacert "$ES_CA" -H "Authorization: ApiKey ${API_KEY}" "$ES_URL/_security/_authenticate" | jq '{username, authentication_type}'
```

Résultat attendu : `"authentication_type": "api_key"`. Les clés sont visibles dans ☰ → **Stack Management → Security → API keys**.

---

## Étape 9 – Tolérance aux pannes : perte du master

Les index système (`.security`, `.kibana`…) sont créés avec `auto_expand_replicas: 0-1` : leur nombre de réplicas s'adapte au nombre de nœuds éligibles, si bien qu'ils restent **green** même lorsqu'il ne reste qu'un nœud `data_content`. Pour observer l'effet d'une panne sur des données, créez d'abord un index métier de 2 shards avec 1 réplica :

```bash
cd ~/BD516/docker
es PUT /bd516-ha-demo -d '{"settings":{"number_of_shards":2,"number_of_replicas":1}}'
for i in 1 2 3 4; do es POST /bd516-ha-demo/_doc -d "{\"msg\":\"document ${i}\"}" > /dev/null; done
es POST /bd516-ha-demo/_refresh > /dev/null
es GET '/_cat/shards/bd516-ha-demo?v&h=index,shard,prirep,state,node&s=shard,prirep'
```

Résultat attendu : 4 copies `STARTED` (2 primaires `p`, 2 réplicas `r`) réparties sur es01 et es02.

Arrêtez maintenant le master et interrogez le cluster par un nœud restant :

```bash
cd ~/BD516/docker
MASTER=$(es GET '/_cat/master?h=node' | tr -d '[:space:]'); echo "Master actuel : ${MASTER}"
if [ "${MASTER}" = "es01" ]; then SURVIVANT=https://localhost:9201; else SURVIVANT=https://localhost:9200; fi

docker compose stop "${MASTER}"
sleep 30
ES_URL=${SURVIVANT} es GET '/_cat/nodes?v&h=name,node.role,master'
ES_URL=${SURVIVANT} es GET '/_cluster/health?filter_path=status,number_of_nodes,unassigned_shards'
ES_URL=${SURVIVANT} es GET '/_cat/shards/bd516-ha-demo?v&h=index,shard,prirep,state,node&s=shard,prirep'
ES_URL=${SURVIVANT} es GET '/bd516-ha-demo/_count'
```

Résultat attendu :

- 2 nœuds et **un nouveau master** (es01 ou es02, jamais es03 qui est *voting_only*) ;
- statut **yellow**, `unassigned_shards: 2` : les deux réplicas de `bd516-ha-demo` ne peuvent pas être réalloués, car seuls es01 et es02 portent le rôle `data_content` et deux copies d'un même shard ne cohabitent jamais sur un même nœud ;
- les deux primaires sont sur le nœud restant (les réplicas ont été **promus primaires**) et `count: 4` : aucune donnée n'est perdue.

Redémarrez le nœud, constatez le retour au vert, puis supprimez l'index de démonstration :

```bash
cd ~/BD516/docker
docker compose start "${MASTER}"
# Attente du retour au vert (3 minutes au plus), en interrogeant le noeud reste actif
ES_URL=${SURVIVANT} es GET '/_cluster/health?wait_for_nodes=3&wait_for_status=green&timeout=180s&filter_path=status,number_of_nodes,unassigned_shards,timed_out'
es GET '/_cat/shards/bd516-ha-demo?v&h=index,shard,prirep,state,node&s=shard,prirep'
es GET '/_cat/master?v'
es DELETE /bd516-ha-demo
```

Résultat attendu : statut **green**, 3 nœuds, `unassigned_shards: 0`, `timed_out: false`, puis 4 copies `STARTED`. La réponse arrive dès le retour au vert, en général après 1 à 2 minutes : le nœud redémarre, puis les réplicas sont reconstruits, y compris ceux des index système, qui repassent de 0 à 1 réplica (2 reconstructions simultanées par nœud par défaut). Le master reste le nouveau nœud élu : il n'y a pas de retour automatique à l'ancien master.

> Un statut **yellow** juste après le redémarrage est donc normal ; s'il persiste au-delà de 3 minutes (`timed_out: true`), consultez `es GET '/_cat/recovery?v&active_only=true'` et `es GET '/_cluster/allocation/explain?filter_path=index,shard,allocate_explanation'`.

> Si `docker compose start` affiche `dependency failed to start: container bd516-setup-1 exited (0)`, votre fichier `setup/setup.sh` ne contient pas encore les trois dernières lignes (`sleep 10`) de l'étape 1.1 : ajoutez-les, ou démarrez le conteneur sans passer par les dépendances Compose avec `docker start bd516-${MASTER}-1`.

---

## Étape 10 – Repères : ECS et modes de déploiement

**Elastic Common Schema (ECS)** : la pile embarque des modèles de mapping ECS réutilisables (`ecs@mappings`) que Logstash, Beats et Elastic Agent respectent. Nous utiliserons ces noms de champs au Lab 04 (`source.ip`, `http.response.status_code`, `log.level`…).

```console
GET _component_template/ecs@mappings?filter_path=component_templates.name,component_templates.component_template._meta
```

| Mode de déploiement | Usage typique | Dans cette formation |
|---|---|---|
| Self-managed (paquets deb/rpm, archives) | serveurs dédiés, contrôle total | – |
| **Docker / Docker Compose** | postes de dev, labs, petits environnements | **ce lab** |
| Elastic Cloud (hébergé / serverless) | SaaS géré par Elastic | – |
| ECK (Elastic Cloud on Kubernetes) | opérateur Kubernetes, production conteneurisée | – |

---

## Commandes utiles

```bash
cd ~/BD516/docker
docker compose ps -a                 # état des services
docker compose logs -f --tail 50 es01
docker compose stop                  # arrêt (données conservées)
docker compose start                 # redémarrage
docker compose down                  # suppression des conteneurs (volumes conservés)
~/BD516/scripts/reset-lab.sh         # remise à zéro TOTALE (conteneurs, volumes, certificats)
```

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| es0X redémarre en boucle, log `max virtual memory areas vm.max_map_count [65530] is too low` | réglage noyau absent : Lab 00 étape 3 (WSL : `kernelCommandLine` puis `wsl --shutdown`) |
| `memory locking requested for elasticsearch process but memory is not locked` | `ulimits memlock` absent du Compose ou démon Docker restreint ; vérifier l'étape 4 |
| Code de sortie 137 (`docker compose ps -a`) | conteneur tué faute de mémoire : RAM de la VM/WSL insuffisante, profil plus petit (`./scripts/init-env.sh --force` après `reset-lab.sh`) |
| Kibana `unhealthy` / « Kibana server is not ready yet » | patientez 2 min ; sinon `docker compose logs kibana \| grep -i error` |
| `dependency failed to start: container bd516-setup-1 exited (0)` au redémarrage d'un nœud | `setup.sh` sans le `sleep 10` final (étape 1.1) ; contournement : `docker start bd516-es0X-1` |
| `setup` en erreur `impossible de definir le mot de passe kibana_system` | `ELASTIC_PASSWORD` modifié après le premier démarrage : il n'est lu qu'une fois → `reset-lab.sh` |
| `curl: (60) SSL certificate problem` | option `--cacert ~/BD516/docker/certs/ca/ca.crt` oubliée |
| Statut yellow durable sur un poste dont le disque est plein à plus de 85 % | *disk watermark* : libérez de l'espace ou agrandissez le disque |

## Récapitulatif

- [x] Cluster de 3 nœuds 9.5.7, TLS HTTP + transport, authentification active, statut **green**
- [x] Messages de démarrage clés identifiés (bootstrap checks, élection, started)
- [x] API `_cluster/health`, `_cat/*` maîtrisées avec curl et la console Dev Tools
- [x] Rôle `bd516_analyst`, utilisateur `analyste` et clé d'API créés et testés
- [x] Bascule du master observée (yellow sans perte de données) et retour au vert
