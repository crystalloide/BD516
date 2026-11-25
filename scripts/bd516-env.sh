# shellcheck shell=bash
# =============================================================================
# BD516 - Variables et fonctions d'aide pour le shell (a "sourcer")
#   source ~/BD516/scripts/bd516-env.sh
# Fonctions :
#   es     METHODE CHEMIN [options curl]   -> API REST Elasticsearch (utilisateur elastic)
#   esbulk CHEMIN FICHIER.ndjson           -> API _bulk (Content-Type x-ndjson)
#   kb     METHODE CHEMIN [options curl]   -> API REST Kibana
#   bd516_passwords                        -> affiche les mots de passe du .env
# =============================================================================
export BD516_HOME="${BD516_HOME:-${HOME}/BD516}"
export ES_URL="https://localhost:9200"
export KB_URL="https://localhost:5601"
export ES_CA="${BD516_HOME}/docker/certs/ca/ca.crt"

if [[ -f "${BD516_HOME}/docker/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${BD516_HOME}/docker/.env"
  set +a
fi

es() {
  local method="$1" path="$2"
  shift 2
  curl -sS --cacert "${ES_CA}" -u "elastic:${ELASTIC_PASSWORD}" -X "${method}" \
       -H 'Content-Type: application/json' "${ES_URL}${path}" "$@"
  echo
}

esbulk() {
  local path="$1" file="$2"
  curl -sS --cacert "${ES_CA}" -u "elastic:${ELASTIC_PASSWORD}" -X POST \
       -H 'Content-Type: application/x-ndjson' "${ES_URL}${path}" --data-binary "@${file}"
  echo
}

kb() {
  local method="$1" path="$2"
  shift 2
  curl -sS --cacert "${ES_CA}" -u "elastic:${ELASTIC_PASSWORD}" -X "${method}" \
       -H 'kbn-xsrf: bd516' -H 'Content-Type: application/json' "${KB_URL}${path}" "$@"
  echo
}

bd516_passwords() {
  grep -E '^(ELASTIC|KIBANA|LOGSTASH_WRITER|MONITORING)_PASSWORD=' "${BD516_HOME}/docker/.env"
}

# Raccourci : se placer dans le dossier docker pour les commandes "docker compose"
alias bd516='cd "${BD516_HOME}/docker"'
