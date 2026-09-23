# Arquitetura

O EnergIA Sense transforma o consumo das áreas comuns de um condomínio em um fluxo contínuo de dados: mede por circuito, transmite, grava, analisa e avisa.

```
 Quadro de distribuição                    Servidor (Docker Compose)
┌─────────────────────────┐              ┌──────────────────────────────────────────────┐
│ ESP32                   │    MQTT      │  Mosquitto ──► ingestão ──► TimescaleDB       │
│  ├─ PZEM-004T (Modbus)  │ ───────────► │               │   ▲           │               │
│  ├─ PZEM-004T           │  QoS/LWT     │               ▼   │           ▼               │
│  └─ SCT-013 (ADC)       │              │         motor de alertas   API + painel web   │
│  fila offline (LittleFS)│              │               │                  ▲            │
└─────────────────────────┘              │               ▼                  │            │
                                         │     webhook / Telegram        navegador      │
                                         └──────────────────────────────────────────────┘
```

## Camadas

| Camada | Onde | Responsabilidade |
|---|---|---|
| Captura | `firmware/` | Lê tensão, corrente, potência, FP, frequência e energia de cada canal a cada 5 s. |
| Transporte | Mosquitto | Recebe as publicações dos nós; guarda mensagens da sessão persistente da ingestão. |
| Processamento | `energia/ingestao.py` | Valida o payload, calcula a energia do intervalo e grava em lote. |
| Persistência | TimescaleDB | Hypertable de leituras + agregações contínuas por minuto, hora e dia. |
| Análise | `energia/regras.py`, `energia/motor.py`, `energia/analise.py` | Alertas a cada 30 s e análises descritivas sob demanda. |
| Apresentação | `energia/api.py`, `energia/web/` | API REST e painel web responsivo. |

## Decisões

**MQTT em vez de HTTP.** O nó publica e esquece; a conexão fica aberta, o cabeçalho é de poucos bytes e o *last will* avisa o servidor quando o nó cai sem se despedir.

**Fila offline no nó.** Sem Wi-Fi ou sem broker, cada leitura vai para um arquivo na flash com o horário em que foi medida. Quando a conexão volta, o nó reenvia a fila. A chave primária `(circuito_id, ts)` torna o reenvio idempotente: a mesma leitura duas vezes vira uma linha só.

**Energia pelo contador, com rede de segurança.** O PZEM mantém um contador acumulado em kWh. A ingestão usa a diferença entre leituras consecutivas, que também cobre períodos sem comunicação. Quando a diferença não é confiável (primeira leitura, contador zerado, leitura fora de ordem ou salto implausível) ou o sensor não tem contador (SCT-013), integra a potência no intervalo. Ver `energia_intervalo_wh` em `payload.py`.

**TimescaleDB.** É PostgreSQL, então SQL comum, `JOIN` com os cadastros e as ferramentas de sempre, mais três recursos que pesam aqui:
- *hypertable* particionada por dia, com compressão depois de 14 dias;
- agregações contínuas (`leituras_1min`, `leituras_1h`, `leituras_1d`) mantidas pelo próprio banco, que respondem às análises sem varrer as leituras brutas;
- agregação em tempo real: a consulta junta o que já foi materializado com os minutos mais recentes.

**Regras como funções puras.** `regras.py` não conhece o banco: recebe amostras e devolve condições ativas. O motor sincroniza essas condições com a tabela `alertas`. Uma condição nova abre um alerta, uma que continua só atualiza o valor e uma que sumiu resolve o alerta. Um índice único parcial garante no máximo um alerta aberto por alvo e tipo.

**Tudo cadastrável.** Um canal novo aparece sozinho, sem configuração, na primeira leitura. Nome, disjuntor, janela de operação, FP mínimo, standby e tempo máximo ligado são definidos pelo painel. Tarifa e limites gerais também.

**Dado simulado separado do real.** O simulador publica como o nó `sim-01`, marcado `simulado` no banco e com etiqueta no painel.

## Regras de alerta

| Tipo | Dispara quando | Severidade |
|---|---|---|
| `sobrecorrente` | pico de corrente no último minuto ≥ 80% do disjuntor | alta |
| `fp_baixo` | FP agregado (ΣP/ΣS) dos últimos 5 min abaixo do mínimo do circuito, com carga acima do standby | média |
| `fora_de_janela` | ≥ 90% das leituras dos últimos 10 min fora da janela de operação e acima do standby | média |
| `operacao_continua` | circuito ligado sem parar por mais que o máximo configurado (ex.: bomba com falha de boia) | alta |
| `sem_dados` | nó sem enviar leituras há mais de 3 min | alta |

Os limites são configuráveis em **Configuração** no painel.

## Análises

| Endpoint | O que responde |
|---|---|
| `/api/analise/ranking` | Quanto cada circuito consumiu no período, com participação e custo. |
| `/api/analise/perfil` | Potência média por dia da semana e hora (perfil de carga). |
| `/api/analise/comparacao` | Consumo do dia, da semana ou do mês até agora contra o mesmo trecho do período anterior. |
| `/api/analise/acumulado` | Curva de energia acumulada no mês contra a média diária dos 90 dias anteriores. |
| `/api/analise/fora-de-janela` | Energia consumida fora da janela de operação, por circuito. |
| `/api/analise/projecao` | Consumo e custo do mês até agora e a projeção para o fechamento. |
| `/api/exportar.csv` | Consumo por hora e circuito, para planilhas. |

A documentação interativa da API fica em `/docs` quando o sistema está rodando.
