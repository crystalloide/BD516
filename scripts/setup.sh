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
