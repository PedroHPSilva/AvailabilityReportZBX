<?php declare(strict_types = 1);
/** @var CView $this */
/** @var array $data */
?>
<style>
	.availability-module-toolbar {
		margin-bottom: 10px;
		font-size: 12px;
		color: #767676;
	}
	.availability-module-toolbar a {
		color: #0275b8;
		text-decoration: none;
	}
	.availability-module-frame {
		width: 100%;
		height: calc(100vh - 150px);
		min-height: 480px;
		border: 1px solid #d8d8d8;
		background: #fff;
	}
</style>

<?php
	// Anexa o sessionid do Zabbix na URL (?sso=...) para a aplicacao logar
	// automaticamente como o usuario atual (ver App.tsx / api.ts, funcao
	// loginWithZabbixSession). O separador muda se app_url ja tiver uma
	// query string (nao deveria ter, mas fica seguro de qualquer forma).
	$separator = (strpos($data['app_url'], '?') === false) ? '?' : '&';
	$app_url_with_sso = $data['zbx_sessionid'] !== ''
		? $data['app_url'] . $separator . 'sso=' . urlencode($data['zbx_sessionid'])
		: $data['app_url'];

	$toolbar = (new CDiv())
		->addClass('availability-module-toolbar')
		->addItem(
			_('Se a pagina abaixo nao carregar (tela em branco), o navegador pode estar bloqueando o iframe por politica de seguranca (CSP). ')
		)
		->addItem(
			(new CLink(_('Abrir em uma nova aba'), $app_url_with_sso))->setTarget('_blank')
		);

	$iframe = (new CTag('iframe', true))
		->addClass('availability-module-frame')
		->setAttribute('src', $app_url_with_sso)
		->setAttribute('title', _('Disponibilidade por Host/Trigger'))
		->setAttribute('referrerpolicy', 'no-referrer');

	(new CHtmlPage())
		->setTitle(_('Disponibilidade por Host/Trigger'))
		->addItem($toolbar)
		->addItem($iframe)
		->show();
?>
