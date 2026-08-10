<?php declare(strict_types = 1);

namespace Modules\Availability\Actions;

use CController;
use CControllerResponseData;
use CWebUser;
use Modules\Availability\Module;

/**
 * Renderiza a pagina que embute a aplicacao de Disponibilidade dentro do
 * Zabbix (via iframe), ja autenticada com o mesmo usuario logado no Zabbix
 * (SSO -- ver views/availability.view.php e o comentario abaixo).
 */
class AvailabilityView extends CController {

	protected function init(): void {
		// A aplicacao embutida faz suas proprias chamadas via fetch(), fora
		// do fluxo de formularios do Zabbix, entao nao precisa de validacao
		// de token CSRF do Zabbix nesta action.
		$this->disableCsrfValidation();
	}

	protected function checkInput(): bool {
		return true;
	}

	protected function checkPermissions(): bool {
		// Visivel para qualquer usuario autenticado no Zabbix. Se quiser
		// restringir por perfil, valide aqui com $this->getUserType().
		return true;
	}

	protected function doAction(): void {
		$data = [
			'app_url' => Module::APP_URL,
			// CWebUser::$data['sessionid'] e' o id da sessao atual do
			// frontend do Zabbix -- e' o MESMO token aceito pela API JSON-RPC
			// (user.checkAuthentication/demais metodos). A view abaixo anexa
			// esse valor na URL do iframe (?sso=...) para a aplicacao logar
			// automaticamente como esse usuario, sem pedir senha de novo.
			'zbx_sessionid' => CWebUser::$data['sessionid'] ?? '',
		];

		$response = new CControllerResponseData($data);
		$response->setTitle(_('Disponibilidade'));
		$this->setResponse($response);
	}
}
