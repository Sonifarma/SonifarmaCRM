# Sonifarma CRM

Painel Python para atendimento, pós-venda e consultas ao ERP PostgreSQL.
Importado do pacote Sonifarma.zip fornecido pela equipe.

## Versão principal

O painel mais recente está em `integracao/painel/` (a documentação incluída identifica a interface como V20.5.13). As pastas `integracao - Copia/` e `sonifarma_integracao/` foram preservadas como versões anteriores e ferramentas de diagnóstico.

## Desenvolvimento local — Python 3.12

Na raiz do repositório:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r integracao/requirements.txt
cd integracao/painel
../../.venv/bin/python iniciar_interface.py
```

No Windows, use o executável `.venv\Scripts\python.exe` e os caminhos equivalentes.
O modo padrão inicia o painel em loopback, porta 8765, sem conexão ao ERP. Os bancos SQLite locais são criados automaticamente. O primeiro acesso permite cadastrar o administrador; dados e usuários existentes não estão incluídos neste repositório.

Verificação local em outro terminal:

```sh
curl --fail http://127.0.0.1:8765/api/auth/status
```

## Integração com o ERP

Copie `integracao/.env.example` para `integracao/.env` e ajuste a conexão. Inicie o painel com `--erp`; a senha é solicitada interativamente ou lida de `SONIFARMA_PG_PASSWORD`. É necessário acesso à rede do PostgreSQL e usuário com permissões somente de leitura. O modo conectado ao ERP não foi validado no ambiente em nuvem.

O modo `--lan` e os controles PowerShell originais dependem de Windows/configuração da rede local e IP fixo definidos no código. Não são o fluxo de desenvolvimento na nuvem. HTTPS também exige `cryptography`, não declarado no requirements original.

## Arquivos locais

Credenciais, arquivos `.env`, chave/certificado TLS, bancos SQLite, logs, relatórios gerados, backups e ambientes virtuais do ZIP não foram versionados. O ZIP original permanece como fonte desses arquivos; não use o Git para armazená-los. Os exemplos `.env.example` não incluem senha.

## Verificação da importação

- Todos os 14 arquivos Python passaram pela análise de sintaxe.
- Dependência `psycopg2-binary==2.9.11` instalada em Python 3.12.
- Painel iniciou sem ERP; página HTML e endpoint de estado de autenticação responderam HTTP 200.

Essas verificações não validam as regras comerciais, o acesso ao ERP nem uma implantação em produção.
