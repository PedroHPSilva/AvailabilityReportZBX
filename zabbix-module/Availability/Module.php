<?php declare(strict_types = 1);

namespace Modules\Availability;

use Zabbix\Core\CModule;
use APP;
use CMenuItem;

/**
 * Modulo do frontend do Zabbix que adiciona, no menu "Relatorios", um item
 * que abre a aplicacao de Disponibilidade por Host/Trigger (o mesmo projeto
 * "zabbix_automation" implantado separadamente com Nginx/Apache2 + FastAPI).
 *
 * IMPORTANTE: edite a constante APP_URL abaixo para o endereco real onde a
 * aplicacao foi implantada (ver deploy/README.md do projeto principal).
 */
class Module extends CModule {

	// Endereco publico da aplicacao React/FastAPI, incluindo a porta usada
	// pelo Nginx/Apache2 (por padrao 8080, pois 80/443 ja sao usados pelo
	// proprio Zabbix e a 3000 pelo Grafana neste servidor).
	//
	// IMPORTANTE: se o Zabbix e acessado via https://, este URL TAMBEM
	// precisa ser https:// (com certificado valido) -- caso contrario o
	// navegador bloqueia o iframe por "Mixed Content" (pagina https
	// carregando conteudo http). Ver deploy/nginx/zabbix-automation-ssl.conf
	// ou deploy/apache/zabbix-automation-ssl.conf no projeto principal.
	public const APP_URL = 'https://SEU_SERVIDOR:8080/';

	public function init(): void {
		APP::Component()->get('menu.main')
			->findOrAdd(_('Reports'))
			->getSubmenu()
			->add(
				(new CMenuItem(_('Disponibilidade')))
					->setAction('availability.view')
			);
	}
}
