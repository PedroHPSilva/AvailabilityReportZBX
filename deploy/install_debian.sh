#!/usr/bin/env bash
#
# Instala e configura o Zabbix Automation para rodar em segundo plano
# (systemd) em um servidor Debian 12, com o frontend servido via Nginx ou
# Apache2 (o backend roda sempre em systemd/uvicorn, independente da
# escolha do servidor web).
#
# Uso (como root, a partir da pasta raiz do projeto já copiada para o servidor):
#   sudo ./deploy/install_debian.sh
#
# Por padrão o script detecta automaticamente qual servidor web usar:
#   - se o Apache2 já estiver ativo no servidor (comum em instalações
#     oficiais do Zabbix, que já usam Apache para o frontend em 80/443),
#     a aplicação também é publicada via Apache;
#   - caso contrário, usa Nginx.
# Para forçar uma opção, defina WEB_SERVER=apache ou WEB_SERVER=nginx:
#   sudo WEB_SERVER=apache ./deploy/install_debian.sh
#
# O que este script faz:
#   1. Instala pacotes de sistema necessários (python3-venv, node/npm, e
#      nginx OU apache2, conforme WEB_SERVER)
#   2. Cria o usuário de sistema "zabbixauto" (sem login, sem home real)
#   3. Copia o projeto para /opt/zabbix_automation
#   4. Cria o virtualenv do backend e instala as dependências Python
#   5. Builda o frontend (Vite) com VITE_API_BASE_URL=/api
#   6. Instala e habilita o serviço systemd do backend + a configuração do
#      servidor web escolhido, servindo tudo na porta 8080
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Este script precisa ser executado como root (sudo)." >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/zabbix_automation"
SERVICE_USER="zabbixauto"
NODE_MAJOR="20"

# --- Escolha do servidor web -------------------------------------------------
WEB_SERVER="${WEB_SERVER:-}"
if [[ -z "${WEB_SERVER}" ]]; then
  if systemctl is-active --quiet apache2 2>/dev/null; then
    WEB_SERVER="apache"
    echo "==> Apache2 já está ativo neste servidor (comum em instalações do Zabbix); usando Apache2."
  else
    WEB_SERVER="nginx"
  fi
fi
if [[ "${WEB_SERVER}" != "nginx" && "${WEB_SERVER}" != "apache" ]]; then
  echo "WEB_SERVER inválido: '${WEB_SERVER}'. Use 'nginx' ou 'apache'." >&2
  exit 1
fi
echo "==> Servidor web escolhido: ${WEB_SERVER}"

echo "==> [1/6] Instalando pacotes de sistema..."
apt-get update -y
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  rsync ca-certificates curl gnupg

if [[ "${WEB_SERVER}" == "nginx" ]]; then
  apt-get install -y --no-install-recommends nginx
else
  apt-get install -y --no-install-recommends apache2
  a2enmod proxy proxy_http rewrite headers >/dev/null
fi

if ! command -v node >/dev/null 2>&1; then
  echo "==> Node.js não encontrado. Instalando Node.js ${NODE_MAJOR}.x via NodeSource..."
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
  apt-get install -y nodejs
fi

echo "==> [2/6] Criando usuário de sistema '${SERVICE_USER}'..."
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

echo "==> [3/6] Copiando projeto para ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
  --exclude ".git" \
  --exclude "backend/.venv" \
  --exclude "backend/__pycache__" \
  --exclude "backend/src/**/__pycache__" \
  --exclude "backend/output" \
  --exclude "frontend/node_modules" \
  --exclude "frontend/dist" \
  --exclude "logs" \
  "${SOURCE_DIR}/" "${INSTALL_DIR}/"

# logs/ e backend/output/ são dados de runtime (não fazem parte do código-
# fonte) e por isso ficam de fora do rsync acima -- se fossem sincronizados,
# o "--delete" apagaria a cada deploy os logs e os arquivos exportados pelos
# usuários. Criamos as pastas aqui, já com o dono certo.
mkdir -p "${INSTALL_DIR}/logs" "${INSTALL_DIR}/backend/output"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}/logs" "${INSTALL_DIR}/backend/output"

echo "==> [4/6] Configurando ambiente virtual e dependências do backend..."
python3 -m venv "${INSTALL_DIR}/backend/.venv"
"${INSTALL_DIR}/backend/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/backend/.venv/bin/pip" install -r "${INSTALL_DIR}/backend/requirements.txt"

if [[ ! -f "${INSTALL_DIR}/backend/.env" ]]; then
  echo "!! backend/.env não encontrado. Copiando a partir de .env.example."
  echo "   Edite ${INSTALL_DIR}/backend/.env com a URL real do Zabbix antes de iniciar o serviço."
  cp "${INSTALL_DIR}/backend/.env.example" "${INSTALL_DIR}/backend/.env"
fi

echo "==> [5/6] Build do frontend (Vite)..."
# Em produção, o frontend é servido pelo Nginx/Apache e fala com o backend
# em /api, que é repassado para 127.0.0.1:8000 (ver deploy/nginx/ ou
# deploy/apache/zabbix-automation.conf).
echo "VITE_API_BASE_URL=/api" > "${INSTALL_DIR}/frontend/.env"
(
  cd "${INSTALL_DIR}/frontend"
  npm ci
  npm run build
)

echo "==> [6/6] Instalando systemd + ${WEB_SERVER}..."
cp "${INSTALL_DIR}/deploy/systemd/zabbix-automation-backend.service" \
   /etc/systemd/system/zabbix-automation-backend.service

if [[ "${WEB_SERVER}" == "nginx" ]]; then
  cp "${INSTALL_DIR}/deploy/nginx/zabbix-automation.conf" \
     /etc/nginx/sites-available/zabbix-automation.conf
  ln -sf /etc/nginx/sites-available/zabbix-automation.conf \
     /etc/nginx/sites-enabled/zabbix-automation.conf
  # Não mexemos no site "default" nem em outros sites do Nginx: este
  # servidor pode já hospedar outras coisas em 80/443; esta aplicação usa
  # a porta 8080, então não há conflito.
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
  nginx -t
  systemctl daemon-reload
  systemctl enable --now zabbix-automation-backend.service
  systemctl reload nginx
else
  cp "${INSTALL_DIR}/deploy/apache/zabbix-automation.conf" \
     /etc/apache2/sites-available/zabbix-automation.conf
  a2ensite zabbix-automation.conf >/dev/null
  # Não desabilitamos outros sites (ex.: o do próprio Zabbix em 80/443):
  # esta aplicação usa a porta 8080 e convive com eles.
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
  apache2ctl configtest
  systemctl daemon-reload
  systemctl enable --now zabbix-automation-backend.service
  systemctl reload apache2
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  echo "==> ufw ativo detectado: liberando a porta 8080/tcp..."
  ufw allow 8080/tcp
fi

echo ""
echo "==> Instalação concluída (servidor web: ${WEB_SERVER})."
echo "    Backend (systemd): systemctl status zabbix-automation-backend"
echo "    Logs backend:      ${INSTALL_DIR}/logs/app.log e app-error.log"
echo "    Frontend:          http://<ip-do-servidor>:8080/  (acessível pela rede)"
echo "    Confira/edite:     ${INSTALL_DIR}/backend/.env"
echo "    Obs: 80/443 seguem reservados para o frontend do Zabbix neste servidor."
