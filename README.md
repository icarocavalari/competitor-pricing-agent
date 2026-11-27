# Competitor Pricing Agent

Lê as páginas de preços do LinkedIn Sales Navigator e de quatro concorrentes (Apollo.io, Lusha, ZoomInfo e Cognism), transforma tudo em uma tabela de planos, preços, licenças e créditos, e compara cada execução com a anterior. Cada número é conferido na página, e o que não estiver claro vai para uma pessoa, em vez de ser adivinhado.

## Como funciona

1. Um navegador Chrome abre cada página de preços e lê o texto.
2. O Claude (Haiku 5.5) extrai planos, preços, licenças e créditos, e copia o trecho de onde saiu cada número.
3. O Python confere cada número na página e marca como ok, REVIEW (uma pessoa deve olhar) ou FAIL (não confiar), com o motivo.
4. Cada execução é comparada com a anterior. Se algo mudou, o Claude lê a página mais duas vezes, e só vale a resposta que se repete.
5. Tudo é salvo em um banco SQLite (`results/prices.db`) e em um Excel por data, com abas de preços, mudanças e descontos anuais.

## Resultados

- **Precisão.** Em cópias salvas das páginas, as execuções corretas foram de 31 de 50 para 49 de 50, principalmente deixando as regras mais claras. Com as regras finais, 99 de 100 estavam corretas. Na rodada mais recente, que também confere licenças e créditos, foram 50 de 50. Nenhum preço errado passou como ok. Detalhes em `tests/`.
- **Última execução (27/11/2026).** Os 16 preços publicados passaram, e nada mudou desde 26/11. O ZoomInfo não pôde ser lido e vai para uma pessoa.
- **Histórico.** Comparando 22/11 com 26/11, a ferramenta encontrou sozinha que o Apollo tirou o preço do plano Organization (antes US$ 119 e US$ 149, agora preço sob consulta).
- **Custo.** Cerca de US$ 0,01 por execução. As releituras só acontecem quando algo muda.

## O que a ferramenta mostrou

- O Sales Navigator é o único cobrado só por licença. Os outros combinam licenças com créditos.
- Ele é a opção premium no plano de entrada, US$ 89,99 por mês no plano anual, contra US$ 49 do Apollo Basic e US$ 37,45 do Lusha Starter.
- Só 3 de 5 publicam seus preços.

## Limitações

- Compara preços de tabela, e os produtos funcionam de formas diferentes. Cada um cobra um número diferente de créditos por ação.
- Lê os preços apenas em dólar.
- Só compara números e planos entre execuções. Textos não são comparados, porque a IA os escreve de um jeito diferente a cada vez.

## Próximos passos

- Rodar todas as manhãs com o n8n e mandar as mudanças por e-mail para uma pessoa.
- Comparar o custo do mesmo trabalho, como contatar 100 pessoas por mês, e não só os preços de entrada.
- Acompanhar outros produtos do LinkedIn e seus concorrentes.

## Como rodar

Precisa de Python 3.9+, Google Chrome e uma chave da API da Anthropic no `.env`, como `ANTHROPIC_API_KEY`.

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python pricing_agent.py
```
