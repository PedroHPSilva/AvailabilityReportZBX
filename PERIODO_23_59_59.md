# Ajuste: fim do período padrão (23:59:00 → 23:59:59)

## Onde estava o problema

O período padrão do relatório (00:00 até 23:59 do dia atual) é montado no
frontend, em `frontend/src/App.tsx`, pela função `localDateTime()`. O
`<input type="datetime-local">` do HTML, por padrão, só armazena precisão de
**minuto** — sem o atributo `step`, qualquer valor com segundos que a
aplicação tentasse colocar ali era truncado para `:00`.

Isso significa que, mesmo a função já calculando internamente
`23:59:59` no objeto `Date`, o valor que efetivamente chegava ao campo do
formulário (e depois ao backend, em `period_end`) virava `...T23:59:00`.

O backend usa `period_end - period_start` para calcular o total de segundos
do período (`backend/src/domain/availability_calculator.py`,
`total_seconds = int((period_end - period_start).total_seconds())`), que é
o **denominador** do percentual de disponibilidade. Uma diferença de 59
segundos nesse denominador é pequena, mas é exatamente o tipo de diferença
que aparece na 3ª/4ª casa decimal do percentual — e o Zabbix, ao definir o
fim do dia como `23:59:59` (não `23:59:00`), usa um denominador diferente do
que a aplicação estava usando por padrão.

## O que foi alterado

1. **`frontend/src/App.tsx` — `localDateTime()`**: a string retornada agora
   inclui os segundos (`...T23:59:59` em vez de `...T23:59`).
2. **`frontend/src/App.tsx` — `PeriodInputs`**: os dois campos de período
   (`Inicio` e `Fim`) ganharam o atributo `step={1}`, que faz o navegador
   exibir e **preservar** os segundos no `datetime-local` — sem isso, o
   valor com `:59` seria truncado de volta para `:00` assim que o campo
   fosse renderizado, mesmo vindo certo do `localDateTime()`.

Nenhuma mudança foi necessária no backend: `period_start`/`period_end` já
são campos `datetime` do Pydantic (`backend/src/api/schemas.py`), que
aceitam segundos normalmente — o problema era só o valor que chegava até
eles, truncado no frontend.

## Efeito

- O período padrão passa a ser `00:00:00` até `23:59:59` (um dia completo
  de 86.400 segundos), igual à convenção usada pelo próprio Zabbix.
- Se você digitar um horário manualmente (não usar o padrão), agora também
  é possível especificar os segundos exatos, se precisar — antes isso nem
  era possível pela interface.
