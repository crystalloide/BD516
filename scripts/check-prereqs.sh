#!/usr/bin/env bash
# =============================================================================
# BD516 - Verification des prerequis du poste stagiaire
# (VM Ubuntu 24.04 sous Hyper-V ou Ubuntu 24.04 sous WSL2)
# Usage : ./scripts/check-prereqs.sh      -> code retour 0 si tout est OK
# =============================================================================
set -uo pipefail

BD516_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ok=0; warn=0; ko=0
pass() { printf '  [ OK ] %s\n' "$*"; ok=$((ok + 1)); }
attn() { printf '  [WARN] %s\n' "$*"; warn=$((warn + 1)); }
fail() { printf '  [ KO ] %s\n' "$*"; ko=$((ko + 1)); }

echo "=== BD516 - verification des prerequis ($(date '+%Y-%m-%d %H:%M')) ==="

# --- Systeme ----------------------------------------------------------------
if grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null; then
  pass "Ubuntu 24.04 ($(grep PRETTY_NAME /etc/os-release | cut -d'"' -f2))"
else
  attn "Systeme non teste : $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2)"
fi
if grep -qi microsoft /proc/version 2>/dev/null; then
  ENV_KIND="WSL2"
  if [[ "$(ps -p 1 -o comm= 2>/dev/null)" == "systemd" ]]; then
    pass "WSL2 avec systemd actif"
  else
    fail "WSL2 sans systemd : ajoutez [boot] systemd=true dans /etc/wsl.conf puis 'wsl --shutdown'"
  fi
else
  ENV_KIND="VM"
  pass "Machine virtuelle / Linux natif"
fi

cpus=$(nproc)
if (( cpus >= 4 )); then pass "${cpus} vCPU"; else attn "${cpus} vCPU (4 recommandes)"; fi

mem_gib=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
if (( mem_gib >= 15 )); then pass "${mem_gib} Gio de RAM"
elif (( mem_gib >= 11 )); then attn "${mem_gib} Gio de RAM (16 recommandes, profil memoire reduit)"
elif (( mem_gib >= 7 )); then attn "${mem_gib} Gio de RAM : profil minimal, Kibana sera lent (16 recommandes)"
else fail "${mem_gib} Gio de RAM : insuffisant (minimum 8, 16 recommandes)"; fi

avail_gb=$(df -BG --output=avail "${HOME}" | tail -1 | tr -dc '0-9')
used_pct=$(df --output=pcent "${HOME}" | tail -1 | tr -dc '0-9')
if (( avail_gb >= 30 && used_pct < 70 )); then pass "Disque : ${avail_gb} Go libres (${used_pct}% utilise)"
elif (( avail_gb >= 15 && used_pct < 80 )); then attn "Disque : ${avail_gb} Go libres, ${used_pct}% utilise (30 Go libres recommandes)"
else fail "Disque : ${avail_gb} Go libres, ${used_pct}% utilise -> au-dela de 85% Elasticsearch n'alloue plus les replicas"; fi

case "${BD516_HOME}" in
  /mnt/*) fail "Le projet est sur un disque Windows (${BD516_HOME}) : placez-le dans ~/BD516 (systeme de fichiers Linux)";;
  *) pass "Projet dans ${BD516_HOME}";;
esac

# --- Reglages noyau ------------------------------------------------------------
mmc=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
if (( mmc >= 1048576 )); then pass "vm.max_map_count = ${mmc}"
else fail "vm.max_map_count = ${mmc} (1048576 requis) -> voir Lab 00, etape 3"; fi

swp=$(sysctl -n vm.swappiness 2>/dev/null || echo 60)
if (( swp <= 1 )); then pass "vm.swappiness = ${swp}"; else attn "vm.swappiness = ${swp} (1 recommande)"; fi

swap_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
if (( swap_kb == 0 )); then pass "Swap desactive"; else attn "Swap actif ($((swap_kb / 1024)) Mio) : memory_lock protege la heap, mais desactiver le swap est recommande"; fi

tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo "?")
if [[ "${tz}" == "Europe/Paris" ]]; then pass "Fuseau horaire Europe/Paris"; else attn "Fuseau horaire ${tz} (Europe/Paris attendu par les labs)"; fi

# --- Outils -------------------------------------------------------------------
for tool in curl jq git openssl python3 unzip; do
  if command -v "${tool}" >/dev/null 2>&1; then pass "${tool} present"; else fail "${tool} absent (sudo apt install -y ${tool})"; fi
done
if python3 -c 'from zoneinfo import ZoneInfo; ZoneInfo("Europe/Paris")' 2>/dev/null; then
  pass "Python $(python3 -c 'import platform; print(platform.python_version())') avec zoneinfo"
else fail "Python : module zoneinfo / tzdata indisponible (sudo apt install -y tzdata)"; fi

# --- Docker -------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  pass "Docker CLI $(docker version --format '{{.Client.Version}}' 2>/dev/null)"
  if docker info >/dev/null 2>&1; then
    pass "Moteur Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null) accessible sans sudo"
    drv=$(docker info --format '{{.Driver}}' 2>/dev/null)
    pass "Stockage Docker : ${drv}"
    logdrv=$(docker info --format '{{.LoggingDriver}}' 2>/dev/null)
    if [[ "${logdrv}" == "local" ]]; then pass "Pilote de logs Docker : local (rotation)"; else attn "Pilote de logs Docker : ${logdrv} ('local' recommande, Lab 00 etape 5)"; fi
  else
    fail "Moteur Docker inaccessible : 'sudo usermod -aG docker \$USER' puis reconnectez-vous"
  fi
  if docker compose version >/dev/null 2>&1; then pass "$(docker compose version)"; else fail "Plugin docker compose absent"; fi
else
  fail "Docker absent -> Lab 00, etape 4"
fi

# --- Ports --------------------------------------------------------------------
for port in 9200 9201 9202 5601 9600; do
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${port}\$"; then
    if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":${port}->"; then pass "Port ${port} utilise par BD516"
    else fail "Port ${port} deja utilise par un autre programme"; fi
  else pass "Port ${port} libre"; fi
done

echo "=== Environnement ${ENV_KIND} : ${ok} OK, ${warn} avertissement(s), ${ko} erreur(s) ==="
(( ko == 0 ))
