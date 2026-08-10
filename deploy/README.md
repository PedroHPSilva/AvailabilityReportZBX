# Deploy em Debian 12 (execução em segundo plano)

Esta pasta contém tudo que é necessário para rodar o Zabbix Automation como
serviço em background em um servidor Debian 12, substituindo os scripts
PowerShell (`scripts/*.ps1`, uso local Windows) por uma configuração
baseada em **systemd** (backend) + **Nginx ou Apache2** (frontend estático +
proxy reverso para o backend — escolha o que já roda no seu servidor).

## Arquitetura

Neste servidor já rodam o Zabbix (frontend web em 80/443) e o Grafana (porta
3000). Por isso, esta aplicação usa a **porta 8080**, liberada para acesso
de outras máquinas da rede pelo IP do servidor.

```
Outras máquinas da rede ──▶ http://<IP-do-servidor>:8080/
                                │
                                ▼
                Nginx/Apache2 :8080 ──▶ /            → arquivos estáticos (frontend/dist)
                                    └──▶ /api/*      → proxy_pass → 127.0.0.1:8000 (backend)

systemd ──▶ zabbix-automation-backend.service
              └─ .venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 8000

Zabbix frontend  → 80 / 443   (não é tocado)
Grafana          → 3000       (não é tocado)
```

### Nginx ou Apache2? O instalador decide por você

O `install_debian.sh` detecta automaticamente: se o **Apache2 já estiver
ativo** no servidor (comum, já que as instalações oficiais do pacote Zabbix
usam Apache por padrão para o frontend em 80/443), a aplicação é publicada
também via Apache2 — assim você não precisa manter dois servidores web
rodando. Caso contrário, usa Nginx.

Para forçar uma opção manualmente:

```bash
sudo WEB_SERVER=apache ./deploy/install_debian.sh
# ou
sudo WEB_SERVER=nginx ./deploy/install_debian.sh
```

As duas configurações (`deploy/nginx/zabbix-automation.conf` e
`deploy/apache/zabbix-automation.conf`) fazem exatamente a mesma coisa:
servem `frontend/dist` como arquivos estáticos na porta 8080 e repassam
`/api/*` para o backend em `127.0.0.1:8000`.

- O backend **nunca fica exposto diretamente** na rede; só escuta em
  `127.0.0.1:8000` e é acessado através do Nginx/Apache2.
- O frontend é **compilado** (`npm run build`) em vez de rodar `vite dev`
  em produção — isso é mais leve, mais rápido e mais seguro.
- Como frontend e backend passam a responder na mesma origem (`/` e
  `/api/`), CORS deixa de ser um problema em produção.

## Passo a passo

1. Copie o projeto inteiro (a pasta `zabbix_automation/`) para o servidor
   Debian 12, por exemplo via `scp` ou `rsync`.
2. Preencha `backend/.env` com a `ZABBIX_URL` real (baseado em
   `backend/.env.example`). **Não** copie `ZABBIX_USERNAME`/`ZABBIX_PASSWORD`
   para lá — o login web é feito via sessão interativa, como já está
   documentado no `README.md` do projeto.
3. Rode o instalador como root, a partir da raiz do projeto:

   ```bash
   sudo ./deploy/install_debian.sh
   ```

   O script detecta se deve usar Nginx ou Apache2 (ver seção acima),
   instala os pacotes necessários (`python3-venv`, `nodejs` e o servidor web
   escolhido), cria o usuário de sistema `zabbixauto`, copia o projeto para
   `/opt/zabbix_automation`, monta o virtualenv do backend, builda o
   frontend e sobe os serviços.

4. Acesse `http://<ip-do-servidor>:8080/` a partir de qualquer máquina da
   mesma rede. Se o servidor tiver `ufw` ativo, o instalador já libera a
   porta `8080/tcp` automaticamente; se usar outro firewall (`firewalld`,
   `nftables` manual, regras de nuvem/segurança), libere a porta 8080 por
   lá também.

## Operação do dia a dia

```bash
# status / logs do backend
sudo systemctl status zabbix-automation-backend
sudo journalctl -u zabbix-automation-backend -f
tail -f /opt/zabbix_automation/logs/app.log          # log geral (INFO+)
tail -f /opt/zabbix_automation/logs/app-error.log    # só erros, com stack trace

# reiniciar / parar / iniciar
sudo systemctl restart zabbix-automation-backend
sudo systemctl stop zabbix-automation-backend
sudo systemctl start zabbix-automation-backend

# Servidor web (use o que foi instalado — veja qual está ativo com
# `systemctl status nginx` ou `systemctl status apache2`)
sudo nginx -t && sudo systemctl reload nginx
# ou, se estiver usando Apache2:
sudo apache2ctl configtest && sudo systemctl reload apache2
```

O serviço do backend está habilitado (`systemctl enable`), então ele volta
a subir sozinho após reinício do servidor. O servidor web escolhido também
já vem habilitado pelo pacote do Debian.

## Atualizando o código

Depois de copiar uma versão nova do projeto para o mesmo servidor (fora de
`/opt/zabbix_automation`), rode:

```bash
sudo ./deploy/update_debian.sh
```

Isso sincroniza o código (preservando os `.env` já configurados em
produção), reinstala dependências Python, rebuilda o frontend e reinicia o
backend.

## Diferenças em relação aos scripts PowerShell originais

| Item | Windows (`scripts/*.ps1`) | Debian 12 (`deploy/`) |
|---|---|---|
| Backend | `Start-Process` + PID file | `systemd` (`zabbix-automation-backend.service`) |
| Frontend | `vite dev` (modo desenvolvimento) | Build estático (`npm run build`) servido pelo Nginx ou Apache2 |
| Reinício automático | Não | Sim (`Restart=on-failure` no systemd) |
| Início no boot | Não | Sim (`systemctl enable`) |
| Porta exposta externamente | 3000 e 8000 | Só a 8080 (Nginx/Apache2); backend fica só em localhost |
| Logs | `logs/*.log` manuais | `logs/*.log` + `journalctl -u zabbix-automation-backend` |

Os scripts PowerShell continuam existindo em `scripts/` para quem ainda
usa a aplicação localmente no Windows — não foram removidos.

## Observações de segurança

- O serviço systemd roda como usuário dedicado sem privilégios
  (`zabbixauto`), sem shell de login.
- `ProtectSystem=strict` e `ProtectHome=true` limitam o que o processo pode
  escrever em disco (só `backend/output` e `logs/` ficam liberados).
- Se o servidor tiver `ufw`/`firewalld`, libere apenas a porta 8080 (e 443
  se configurar HTTPS depois) — a porta 8000 (backend) não precisa ficar
  acessível de fora, pois o Nginx/Apache2 já faz o proxy internamente. As portas
  80/443 (Zabbix) e 3000 (Grafana) não são alteradas por este deploy.
- Para HTTPS, o caminho mais simples é `certbot --nginx` (ou `certbot
  --apache`, conforme o servidor web em uso) via Let's Encrypt, depois de
  configurar um domínio real (`server_name` no Nginx / `ServerName` no
  Apache) — nesse caso, normalmente se usa a porta 443 com um nome
  específico (ex.: subdomínio), para não colidir com o certificado/config
  já usado pelo Zabbix na mesma porta.

## Problemas de permissão em logs/

O backend roda como o usuário de sistema `zabbixauto` (ver
`deploy/systemd/zabbix-automation-backend.service`). Se `logs/app.log` ou
`logs/app-error.log` forem criados por **outro** usuário — o caso mais
comum é rodar o `uvicorn` manualmente com `sudo` (como root) para debugar
algo — o serviço systemd deixa de conseguir escrever nesses arquivos e o
backend não sobe.

- **A partir desta versão**, isso não derruba mais a aplicação: se a
  aplicação não conseguir abrir `app.log`/`app-error.log` por permissão,
  ela continua rodando só com log no console (visível via
  `journalctl -u zabbix-automation-backend`), e escreve um aviso avisando
  exatamente disso.
- **Para corrigir de vez** (arquivos já existentes com dono errado):
  ```bash
  sudo chown -R zabbixauto:zabbixauto /opt/zabbix_automation/logs
  sudo systemctl restart zabbix-automation-backend
  ```
- **`install_debian.sh`/`update_debian.sh`** também não sincronizam mais
  `logs/` nem `backend/output/` via `rsync` — antes, como esses diretórios
  não faziam parte do código-fonte, o `--delete` do rsync apagava a cada
  deploy os logs e os arquivos exportados pelos usuários.
- **Se precisar testar o backend manualmente**, rode como o usuário de
  serviço em vez de root, para não repetir o problema:
  ```bash
  cd /opt/zabbix_automation/backend
  sudo -u zabbixauto ./.venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
  ```

## HTTPS na porta 8080 (necessário se o Zabbix usa HTTPS)

Se você acessa o Zabbix via `https://`, e principalmente se planeja usar o
**módulo do Zabbix** (`zabbix-module/`, que embute a aplicação num iframe
dentro do Zabbix), a aplicação também precisa responder em HTTPS na porta
8080 — caso contrário o navegador bloqueia por "Mixed Content" (página
HTTPS não pode carregar um iframe HTTP).

Como normalmente é o **mesmo domínio** já usado pelo Zabbix, não é preciso
gerar um certificado novo — dá para reaproveitar os mesmos arquivos de
certificado do vhost do Zabbix:

1. Descubra os caminhos reais do certificado já usado pelo Zabbix:
   ```bash
   sudo grep -rE "SSLCertificateFile|SSLCertificateKeyFile|ssl_certificate " \
     /etc/apache2/sites-enabled/*.conf /etc/nginx/sites-enabled/* 2>/dev/null
   ```
2. Use a variante HTTPS da configuração em vez da padrão:
   - Nginx: copie `deploy/nginx/zabbix-automation-ssl.conf` para
     `/etc/nginx/sites-available/zabbix-automation.conf` (mesmo nome do
     site, só troca o conteúdo).
   - Apache2: copie `deploy/apache/zabbix-automation-ssl.conf` para
     `/etc/apache2/sites-available/zabbix-automation.conf` e rode
     `sudo a2enmod ssl`.
   Edite o arquivo copiado com os caminhos reais do passo 1 e o domínio
   correto em `server_name`/`ServerName`.
3. `sudo nginx -t && sudo systemctl reload nginx` (ou
   `sudo apache2ctl configtest && sudo systemctl reload apache2`).
4. Se estiver usando o módulo do Zabbix, atualize `APP_URL` em
   `zabbix-module/Availability/Module.php` para `https://...:8080/`.
5. Opcional, mas recomendado agora que há HTTPS: no `backend/.env`, defina
   `SESSION_COOKIE_SECURE=true` para o cookie de sessão só trafegar
   criptografado.

Se o certificado for renovado automaticamente (ex.: `certbot` com Let's
Encrypt), nada muda aqui — como o arquivo referenciado é o mesmo usado pelo
Zabbix, a renovação já cobre os dois.

## Removendo a aplicação do servidor

```bash
sudo ./deploy/uninstall_debian.sh
```

Isso para e desabilita o serviço systemd, remove a configuração do Nginx ou
Apache2 (o que estiver em uso) e libera a porta 8080 no `ufw` se estava
liberada. Em seguida pergunta se você quer apagar `/opt/zabbix_automation`
(que pode conter CSVs/PDFs exportados em `backend/output/` e os logs) e o
usuário de sistema `zabbixauto`. Para apagar tudo sem perguntar:

```bash
sudo ./deploy/uninstall_debian.sh --purge
```

O script **não** desinstala Node.js, Nginx ou Apache2 (podem estar sendo
usados por outras coisas no servidor) e **não** mexe no módulo do Zabbix —
isso é removido pela própria interface do Zabbix:

1. **Administração geral → Módulos** → desabilite "Disponibilidade por
   Trigger" → clique em **Escanear diretório**.
2. Apague os arquivos do módulo, por exemplo:
   ```bash
   sudo rm -rf /usr/share/zabbix/modules/Availability
   ```
   (ajuste o caminho para onde você copiou a pasta na instalação).
