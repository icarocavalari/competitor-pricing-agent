# Competitor Pricing Agent

Lê as páginas de preços do LinkedIn Sales Navigator e de quatro concorrentes (Apollo.io, Lusha, ZoomInfo e Cognism) e transforma tudo em uma tabela de planos, preços, licenças e créditos. Cada preço é conferido na página, e o que não estiver claro vai para uma pessoa, em vez de ser adivinhado.

## Como funciona

1. Um navegador Chrome abre cada página de preços e lê o texto.
2. O Claude (Haiku 5.5) extrai os preços e copia o trecho de onde saiu cada um como evidência.
3. O Python confere cada preço na página e marca como ok, REVIEW (uma pessoa deve olhar) ou FAIL (não confiar).
4. Os resultados são salvos por data em `results/`, incluindo uma tabela de Excel (`prices.xlsx`).

## Resultados

- **Precisão.** Em cópias salvas das páginas, as execuções corretas foram de 31 de 50 para 49 de 50, principalmente deixando as regras mais claras. Com as regras finais, 99 de 100 execuções estavam corretas, e nenhum preço errado passou como ok. Detalhes em `tests/`.
- **Última execução (26/11/2026).** Os 16 preços publicados passaram. O ZoomInfo foi para REVIEW porque a página bloqueia navegadores automatizados.
- **Histórico.** Entre 22/11 e 26/11, o Apollo tirou o preço do seu plano mais alto, o Organization (antes US$ 119 e US$ 149, agora preço sob consulta).
- **Custo.** Cerca de US$ 0,01 por execução.

## O que a ferramenta mostrou

- O Sales Navigator é o único cobrado só por licença. Os outros combinam licenças com créditos.
- Ele é a opção premium no plano de entrada, US$ 89,99 por mês no plano anual, contra US$ 49 do Apollo Basic e US$ 37,45 do Lusha Starter.
- Só 3 de 5 publicam seus preços.

## Limitações

- Compara só preços de tabela, e os planos são diferentes em recursos, licenças e créditos.
- Lê os preços apenas em dólar.

## Próximos passos

- Rodar todas as manhãs com o n8n e mandar qualquer mudança para uma pessoa.
- Quando um resultado mudar, rodar mais duas vezes e ficar com a maioria, para evitar alertas falsos.
- Comparar o custo do mesmo trabalho, como contatar 100 pessoas por mês, e não só os preços de entrada.

## Como rodar

Precisa de Python 3.9+, Google Chrome e uma chave da API da Anthropic no `.env`, como `ANTHROPIC_API_KEY`.

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python pricing_agent.py
```