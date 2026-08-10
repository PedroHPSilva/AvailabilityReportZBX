# Módulo Zabbix: Disponibilidade por Host/Trigger

Este módulo segue a estrutura oficial de módulos do frontend do Zabbix 6.4
(https://www.zabbix.com/documentation/6.4/pt/devel/modules/tutorials/module)
e adiciona um item **Relatórios → Disponibilidade** que abre, dentro do
próprio Zabbix (via `iframe`), a aplicação implantada em
`deploy/` (backend FastAPI + frontend React servidos pelo Nginx na porta
8080 do mesmo servidor).

Ele também faz **SSO** (login automático): o usuário já autenticado no
Zabbix abre o menu e cai direto na aplicação, sem digitar usuário/senha de
novo — ver seção "Login automático (SSO)" abaixo.

## Estrutura

```
Availability/
├── manifest.json              # metadados do módulo e mapa de actions
├── Module.php                 # registra o item de menu "Disponibilidade"
├── actions/
│   └── AvailabilityView.php   # controller da action "availability.view"
└── views/
    └── availability.view.php  # HTML/PHP que renderiza o iframe
```

## Se o módulo não aparecer na lista após "Escanear diretório"

O Zabbix **ignora silenciosamente** manifestos inválidos ou incompatíveis —
não mostra erro na tela, só não lista o módulo. Confira, nesta ordem:

1. **Estrutura de pastas**: o `manifest.json` precisa estar **diretamente**
   dentro da pasta do módulo, que por sua vez fica **diretamente** dentro do
   diretório `modules/` do frontend. Ou seja:
   `<diretório de módulos>/Availability/manifest.json` — não
   `.../modules/zabbix-module/Availability/manifest.json` (um nível a mais
   por engano é o erro mais comum ao copiar a pasta).

2. **`manifest_version` precisa ser `2.0`** (numérico, sem aspas) — não
   `"1.0"`. Esse valor mudou entre versões do Zabbix; o Zabbix 6.4 exige
   `2.0`. Se o arquivo tiver `"manifest_version": "1.0"`, o módulo **não é
   listado**, sem nenhum aviso. (Os arquivos deste repositório já usam o
   valor certo — se você copiou uma versão antiga, atualize.)

3. **JSON válido**: rode `python3 -m json.tool manifest.json` (ou
   `php -r "var_dump(json_decode(file_get_contents('manifest.json')));"`) no
   servidor para garantir que não há vírgula sobrando, aspas erradas, etc.

4. **Dono/permissão de leitura**: o usuário do PHP-FPM/Apache (geralmente
   `www-data`) precisa conseguir ler os arquivos:

   ```bash
   sudo chown -R www-data:www-data /usr/share/zabbix/modules/Availability
   sudo find /usr/share/zabbix/modules/Availability -type d -exec chmod 755 {} \;
   sudo find /usr/share/zabbix/modules/Availability -type f -exec chmod 644 {} \;
   ```

5. **Caminho certo do diretório de módulos**: confirme onde o frontend do
   Zabbix realmente está servindo os arquivos (pode não ser
   `/usr/share/zabbix`, dependendo de como o Apache/Nginx foi configurado).
   Rode `sudo find / -maxdepth 6 -type d -name modules 2>/dev/null | grep -i zabbix`
   para localizar o diretório correto, ou veja o `DocumentRoot`/`root` no
   vhost do Zabbix.

6. Depois de corrigir, clique em **Escanear diretório** de novo em
   **Administração geral → Módulos**.

## Instalação

1. **Edite a URL da aplicação** em `Module.php`:

   ```php
   public const APP_URL = 'http://SEU_SERVIDOR:8080/';
   ```

   Troque `SEU_SERVIDOR` pelo IP ou hostname real do servidor (o mesmo
   configurado no deploy da aplicação — ver `deploy/README.md`).

2. Copie a pasta `Availability/` para o diretório de módulos do frontend do
   Zabbix. O caminho varia conforme a instalação:

   ```bash
   # Pacotes oficiais Zabbix (Debian/Ubuntu, via repositório Zabbix):
   sudo cp -r zabbix-module/Availability /usr/share/zabbix/modules/

   # Instalações manuais (frontend copiado para o Apache/Nginx):
   sudo cp -r zabbix-module/Availability /var/www/html/zabbix/modules/
   ```

   Ajuste o dono do diretório para o mesmo usuário do restante do frontend
   (geralmente `www-data` no Debian):

   ```bash
   sudo chown -R www-data:www-data /usr/share/zabbix/modules/Availability
   ```

3. No Zabbix, acesse **Administração geral → Módulos** (Administration →
   General → Modules), clique em **Escanear diretório** (Scan directory) e
   habilite o módulo **"Disponibilidade por Trigger"** na lista.

4. Um novo item **Disponibilidade** vai aparecer no menu **Relatórios**.

## Se a tela do iframe aparecer em branco

Abra o console do navegador (F12) — a mensagem de erro ali identifica a
causa exata. As duas mais comuns:

### 1. "Mixed Content" (Zabbix em HTTPS, aplicação em HTTP) — causa mais comum

Se o Zabbix é acessado via `https://`, o navegador **bloqueia
silenciosamente** um iframe carregado via `http://` — é o erro mais comum
nesse tipo de setup. O console mostra algo como:

```
Mixed Content: The page at 'https://seu-zabbix/...' was loaded over HTTPS,
but requested an insecure frame 'http://seu-zabbix:8080/'. This request
has been blocked; the content must be served over HTTPS.
```

**Solução**: publicar a aplicação também em HTTPS, na mesma porta 8080.
Como normalmente é o **mesmo domínio** já usado pelo Zabbix, não é preciso
gerar um certificado novo — dá para reaproveitar os mesmos arquivos de
certificado do vhost do Zabbix. Use:

- `deploy/nginx/zabbix-automation-ssl.conf` (em vez de
  `deploy/nginx/zabbix-automation.conf`), ou
- `deploy/apache/zabbix-automation-ssl.conf` (em vez de
  `deploy/apache/zabbix-automation.conf`)

Ambos os arquivos têm instruções de como localizar os caminhos reais do
certificado (`SSLCertificateFile`/`ssl_certificate` do vhost do próprio
Zabbix) e onde colocá-los. Depois, atualize `APP_URL` em `Module.php` para
`https://` também.

### 2. CSP (Content-Security-Policy) do Zabbix

Mais raro, mas possível: o Zabbix pode enviar um cabeçalho
`Content-Security-Policy` que restringe de onde iframes podem carregar
(diretiva `frame-src`), bloqueando mesmo com HTTPS configurado
corretamente. O console mostra uma mensagem citando
`Content-Security-Policy` ou `frame-src`.

- A view já inclui um link **"Abrir em uma nova aba"** como alternativa que
  sempre funciona, independente do CSP.
- Se quiser resolver o bloqueio definitivamente, o caminho mais simples é
  servir a aplicação **na mesma origem** do Zabbix — por exemplo,
  configurando no Nginx/Apache do Zabbix um `location /disponibilidade/`
  (ou equivalente) que faça proxy para `127.0.0.1:8080/`. Isso está fora do
  escopo deste módulo (que assume a aplicação já publicada em `deploy/`),
  mas é uma extensão natural se o CSP for um problema no seu ambiente.

## Login automático (SSO)

Quando a aplicação é aberta pelo menu do Zabbix (`AvailabilityView.php`),
o módulo pega o `sessionid` da sessão atual do usuário
(`CWebUser::$data['sessionid']`) e anexa na URL do iframe como
`?sso=<sessionid>`. Esse `sessionid` **é o mesmo token que o Zabbix usa
para autenticar chamadas na própria API JSON-RPC** (é assim que o frontend
PHP do Zabbix se autentica internamente) — então não é uma senha nem
precisa ser uma; é só o token de uma sessão que já existe.

O frontend da aplicação (`App.tsx`) detecta esse parâmetro ao carregar,
chama `POST /api/auth/sso` no backend, que por sua vez valida o token
direto com o Zabbix (`user.checkAuthentication`) antes de criar a sessão da
aplicação. Se o token for inválido/expirado, cai de volta para a tela de
login normal (usuário/senha) — nada quebra.

Detalhes de segurança:

- O token só é aceito se ainda for uma sessão válida no Zabbix — não há
  como forjar um token arbitrário, e ele expira junto com a sessão do
  Zabbix (`user.checkAuthentication` inclusive prolonga essa mesma sessão).
- Ele aparece brevemente na URL do iframe (visível a quem tiver acesso ao
  DOM/devtools da página do Zabbix), mas a aplicação remove esse parâmetro
  da URL assim que o login termina (`history.replaceState`), então não fica
  salvo no histórico do navegador nem sobrevive a um F5.
- Continua exigindo HTTPS (ver seção de Mixed Content acima) — o token não
  deve trafegar em texto claro por HTTP.
- **Acessar a aplicação diretamente (fora do Zabbix)** continua pedindo
  login normal com usuário/senha — o SSO só funciona vindo do menu do
  Zabbix, que é quem fornece o `sessionid`.

## Limitações conhecidas

- O módulo apenas embute a aplicação existente; não há comunicação de dados
  entre o Zabbix (PHP) e a aplicação (Python/React) além do que a própria
  aplicação já faz diretamente com a API do Zabbix (e agora também o
  `sessionid` usado no SSO, descrito acima).
