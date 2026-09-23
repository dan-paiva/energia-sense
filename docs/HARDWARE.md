# Hardware e instalação

> **Segurança.** A ligação no quadro de distribuição envolve tensão de rede e deve ser feita por eletricista habilitado, com o circuito desenergizado, seguindo a NR-10 e a NBR 5410. O grupo monta e testa o nó em bancada; a intervenção no quadro fica com o profissional.

## Lista de materiais (um nó)

| Item | Qtde | Observação |
|---|---|---|
| ESP32 DevKit V1 (ESP-WROOM-32) | 1 | Wi-Fi 2,4 GHz |
| PZEM-004T v3.0 (100 A) com TC de núcleo dividido | 1 por circuito | mede V, I, P, FP, f e energia |
| SCT-013-030 (saída 1 V) | opcional | circuitos em que basta a corrente |
| Resistores 10 kΩ | 2 por SCT | divisor que centraliza o sinal em 1,65 V |
| Capacitor eletrolítico 10 µF | 1 por SCT | estabiliza o ponto médio |
| Conector P2 fêmea | 1 por SCT | para o plugue do sensor |
| Fonte 5 V / 2 A para trilho DIN ou do tipo carregador | 1 | alimenta ESP32 e PZEM |
| Caixa plástica com trilho DIN | 1 | IP compatível com o local |
| Placa perfurada, bornes e jumpers | — | |

## Ligações

### PZEM-004T v3 (barramento Modbus compartilhado)

Todos os PZEM ficam em paralelo na mesma UART2 do ESP32, cada um com um endereço Modbus próprio.

| PZEM (lado TTL) | ESP32 |
|---|---|
| 5V | 3V3 ¹ |
| GND | GND |
| TX | GPIO16 (RX2) |
| RX | GPIO17 (TX2) |

¹ A interface TTL do PZEM é isolada por optoacopladores e costuma funcionar em 3,3 V, o que evita levar 5 V ao ESP32. Se a comunicação falhar, alimente em 5 V e use um conversor de nível lógico.

Lado de potência (feito pelo eletricista): borne de tensão em fase e neutro do circuito; o TC abraça **somente o condutor fase** do circuito medido.

**Endereçar os módulos:** ligue um PZEM por vez e rode

```bash
pio run -e enderecar -t upload && pio device monitor
```

Digite o endereço (1, 2, 3…) e confira a resposta. Os endereços precisam bater com `CANAL_PZEM` em `config.h`.

### SCT-013-030 (opcional)

```
3V3 ──[10 kΩ]──┬──[10 kΩ]── GND
               │
              (+) 10 µF (−) ── GND
               │
               ├──── ponta do SCT
                     outra ponta do SCT ──── GPIO34
```

Use pinos do ADC1 (GPIO32 a 39): o ADC2 não funciona com o Wi-Fi ligado. O firmware amostra cerca de 5 ciclos de rede, remove o nível DC e calcula a corrente RMS. Sem medir tensão, a potência enviada é aparente (I × tensão nominal).

## Firmware

```bash
cd firmware
cp include/config.exemplo.h include/config.h   # preencha Wi-Fi, broker e canais
pio run -t upload
pio device monitor
```

No monitor, o nó mostra os canais, a conexão MQTT e os PZEM que não responderem. As leituras começam a aparecer no painel em até 5 s. Canais novos surgem como "configurar": dê um nome e os limites em **Configuração**.

### Comportamento em falha

| Situação | O que o nó faz |
|---|---|
| Wi-Fi ou broker fora | grava as leituras na flash (até ~1,2 MB, girando a mais antiga) e reenvia depois |
| Nó reinicia sem relógio | só volta a guardar leituras offline depois de sincronizar o NTP |
| PZEM não responde | omite aquele canal na mensagem e registra no serial |
| Travamento | o watchdog reinicia o ESP32 após 60 s sem progresso |
| Queda sem aviso | o broker publica `offline` (*last will*) e o servidor abre alerta `sem_dados` |

## Validação da medição

Para validar a acurácia, compare a energia acumulada pelo sistema no período (`/api/analise/ranking?inicio=…&fim=…`) com a diferença entre duas leituras do medidor da concessionária, anotadas com data e hora. A comparação só vale para o total se todos os circuitos daquele medidor estiverem monitorados; caso contrário, compare um circuito isolado com um alicate wattímetro de referência.
