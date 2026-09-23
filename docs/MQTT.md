# Contrato MQTT

| Tópico | Quem publica | QoS | Retido | Conteúdo |
|---|---|---|---|---|
| `energia/<nó>/leituras` | nó sensor | 0 ¹ | não | JSON abaixo |
| `energia/<nó>/status` | nó sensor e broker (*last will*) | 1 | sim | `online` ou `offline` |

¹ A biblioteca PubSubClient publica só em QoS 0; a garantia de entrega vem da fila offline do nó e da deduplicação no servidor.

A ingestão assina os dois tópicos com QoS 1 e sessão persistente (`clean_session=false`), então o broker guarda o que chegar enquanto ela reinicia.

## Payload de leituras

```json
{
  "no": "no-01",
  "ts": 1758650000000,
  "seq": 42,
  "intervalo_s": 5,
  "fw": "0.1.0",
  "circuitos": [
    {"id": "c1", "v": 127.2, "i": 3.412, "p": 380.5, "fp": 0.88, "f": 60.0, "e_kwh": 12.345},
    {"id": "c4", "i": 1.250, "p": 158.8}
  ]
}
```

| Campo | Tipo | Regra |
|---|---|---|
| `no` | texto | `[a-z0-9][a-z0-9_-]{0,31}`; precisa ser igual ao do tópico |
| `ts` | inteiro | epoch em ms; `0` = relógio não sincronizado (usa a hora de recebimento). Fora de 7 dias no passado a 5 min no futuro também usa a hora de recebimento |
| `seq` | inteiro | contador do nó, para diagnóstico |
| `intervalo_s` | número | intervalo nominal entre leituras |
| `circuitos[].id` | texto | canal no nó; no servidor o circuito vira `<nó>.<id>` |
| `v` | V, 0–300 ou `null` | |
| `i` | A, 0–200 | obrigatório |
| `p` | W, 0–60 000 | obrigatório (ativa no PZEM, aparente no SCT) |
| `fp` | 0–1 ou `null` | |
| `f` | Hz, 40–70 ou `null` | |
| `e_kwh` | kWh ou `null` | contador acumulado do PZEM |

Payload fora do contrato é descartado e registrado no log da ingestão.
