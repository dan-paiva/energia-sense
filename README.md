# EnergIA Sense

Sistema IoT de medição, processamento e alerta do consumo de energia elétrica nas áreas comuns de um condomínio residencial.

Nós sensores com ESP32 medem cada circuito do quadro de distribuição a cada 5 segundos e publicam as leituras via MQTT. O servidor grava tudo em um banco de séries temporais, avisa o síndico quando algo sai do normal e mostra em um painel web o que consome mais, quando consome e quanto disso acontece fora de hora.

Projeto Integrador em Computação VI, UNIVESP, 2º semestre de 2026.

## O que o sistema faz

- **Mede por circuito:** tensão, corrente, potência ativa, fator de potência, frequência e energia acumulada (PZEM-004T), ou só corrente (SCT-013).
- **Não perde leitura em queda de rede:** o nó guarda as leituras na flash e reenvia depois; o servidor descarta duplicatas.
- **Avisa na hora:** sobrecorrente, fator de potência baixo, consumo fora do horário de operação, bomba ligada sem parar (falha de boia) e nó sem comunicação. Os alertas aparecem no painel e podem ir para Telegram ou webhook.
- **Analisa:** ranking de consumo por circuito, perfil de carga por dia da semana e hora, comparação com o período anterior, curva acumulada do mês contra a média histórica, consumo fora de hora e projeção da conta do mês.
- **Tudo cadastrável:** circuitos, janelas de operação, limites e tarifa são configurados pelo painel.

## Estrutura

```
firmware/   nó sensor (ESP32, PlatformIO)
backend/    ingestão MQTT, motor de alertas, análises, API e painel web (Python)
infra/      configuração do broker Mosquitto
docs/       arquitetura, hardware e contrato MQTT
```

## Como rodar

Requisitos: Docker com Compose.

```bash
cp .env.example .env        # troque as senhas
docker compose up -d        # banco, broker, ingestão e API
```

O painel fica em <http://localhost:8000> e a documentação da API em <http://localhost:8000/docs>.

### Demonstração sem hardware

O simulador faz o papel de um nó com os seis circuitos típicos das áreas comuns. Ele grava 45 dias de histórico e depois publica leituras ao vivo:

```bash
docker compose --profile demo up -d
```

Os dados simulados ficam no nó `sim-01`, com a etiqueta **simulado** no painel. Para provocar um alerta:

```bash
docker compose stop simulador
docker compose --profile demo run --rm simulador \
  energia-simulador --tempo-real --anomalia sobrecorrente
```

Anomalias disponíveis: `bomba_travada`, `fp_baixo`, `sobrecorrente`, `salao_esquecido`.

### Nó sensor real

Veja [docs/HARDWARE.md](docs/HARDWARE.md): lista de materiais, ligações, endereçamento dos PZEM e gravação do firmware.

## Desenvolvimento

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
ruff check .
pytest                                      # testes unitários
TEST_DATABASE_URL=postgresql://postgres:senha@localhost:5432/energia_teste pytest   # + integração
```

Os testes de integração apagam e recriam o banco indicado em `TEST_DATABASE_URL`. A CI roda lint, testes com TimescaleDB, build da imagem e compilação do firmware a cada push.

## Documentação

- [Arquitetura e decisões](docs/ARQUITETURA.md)
- [Hardware e instalação](docs/HARDWARE.md)
- [Contrato MQTT](docs/MQTT.md)

## Licença

MIT.
