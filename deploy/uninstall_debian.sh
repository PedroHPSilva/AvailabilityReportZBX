#!/usr/bin/env bash
#
# Remove o Zabbix Automation instalado via deploy/install_debian.sh:
# para/desabilita o serviço systemd, remove a configuração do servidor web
# (Nginx ou Apache2, o que estiver em uso), libera a porta no firewall (se
# aplicável) e, opcionalmente, apaga os arquivos da aplicação e o usuário
# de sistema.
#
# Uso (como root):
#   sudo ./deploy/uninstall_debian.sh            # remove serviço/config, mas
#                                                 # PRESERVA /opt/zabbix_automation
#                                                 # (pede confirmação para apagar)
#   sudo ./deploy/uninstall_debian.sh --purge     # remove tudo sem perguntar,
#                                                 # incluindo /opt/zabbix_automation
#                                                 # e o usuário "zabbixauto"
#
# Este script NÃO desinstala Node.js, Nginx ou Apache2 em si (eles podem
# estar sendo usados por outras coisas no servidor) e NÃO mexe no módulo do
# Zabbix (zabbix-module/) — isso é removido separadamente pela interface do
# Zabbix (ver instruções no final da execução).
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Este script precisa ser executado como root (sudo)." >&2
  exit 1
fi

INSTALL_DIR="/opt/zabbix_automation"
SERVICE_USER="zabbixauto"
PURGE=false

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=true ;;
    *) echo "Argumento desconhecido: $arg" >&2; exit 1 ;;
  esac
done

echo "==> Parando e desabilitando o serviço do backend..."
systemctl stop zabbix-automation-backend.service 2>/dev/null || true
systemctl disable zabbix-automation-backend.service 2>/dev/null || true
rm -f /etc/systemd/system/zabbix-automation-backend.service
systemctl daemon-reload

echo "==> Removendo a configuração do servidor web..."
if [[ -f /etc/apache2/sites-enabled/zabbix-automation.conf || -f /etc/apache2/sites-available/zabbix-automation.conf ]]; then
  a2dissite zabbix-automation.conf >/dev/null 2>&1 || true
  rm -f /etc/apache2/sites-available/zabbix-automation.conf
  systemctl reload apache2 2>/dev/null || true
  echo "    Apache2: site removido."
fi
if [[ -f /etc/nginx/sites-enabled/zabbix-automation.conf || -f /etc/nginx/sites-available/zabbix-automation.conf ]]; then
  rm -f /etc/nginx/sites-enabled/zabbix-automation.conf
  rm -f /etc/nginx/sites-available/zabbix-automation.conf
  systemctl reload nginx 2>/dev/null || true
  echo "    Nginx: site removido."
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  echo "==> Removendo regra de firewall (porta 8080/tcp) do ufw..."
  ufw delete allow 8080/tcp 2>/dev/null || true
fi

remove_data() {
  echo "==> Removendo ${INSTALL_DIR}..."
  rm -rf "${INSTALL_DIR}"
  if id "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "==> Removendo usuário de sistema '${SERVICE_USER}'..."
    userdel "${SERVICE_USER}" 2>/dev/null || true
  fi
}

if [[ "${PURGE}" == true ]]; then
  remove_data
elif [[ -d "${INSTALL_DIR}" ]]; then
  echo ""
  echo "O diretório ${INSTALL_DIR} ainda existe (pode conter arquivos gerados"
  echo "em backend/output/ e logs em logs/)."
  read -r -p "Deseja apagar ${INSTALL_DIR} e o usuário '${SERVICE_USER}' agora? [s/N] " resp
  if [[ "${resp}" =~ ^[sS]$ ]]; then
    remove_data
  else
    echo "Mantido. Para remover depois: sudo rm -rf ${INSTALL_DIR} && sudo userdel ${SERVICE_USER}"
  fi
fi

echo ""
echo "==> Desinstalação do serviço/config concluída."
echo ""
echo "Passos adicionais, se aplicável:"
echo "  1. Módulo do Zabbix (se instalado): na interface web, vá em"
echo "     Administração geral -> Módulos, desabilite 'Disponibilidade por Trigger'"
echo "     e clique em 'Escanear diretório'. Depois apague os arquivos, ex.:"
echo "       sudo rm -rf /usr/share/zabbix/modules/Availability"
echo "     (ajuste o caminho conforme onde o módulo foi copiado)."
echo "  2. Node.js, Nginx e Apache2 NÃO foram removidos por este script, pois"
echo "     podem ser usados por outras aplicações no servidor. Remova-os"
echo "     manualmente (apt-get purge ...) somente se tiver certeza de que"
echo "     nada mais depende deles."
