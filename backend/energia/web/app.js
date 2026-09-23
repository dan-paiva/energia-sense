// Painel do EnergIA Sense: SPA sem build, com rotas por hash e dados de /api.

const main = document.getElementById("conteudo");
const aviso = document.getElementById("aviso");
const graficos = [];
let temporizador = null;

const DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
const TIPOS = {
  sobrecorrente: "Sobrecorrente",
  fp_baixo: "Fator de potência baixo",
  fora_de_janela: "Consumo fora de hora",
  operacao_continua: "Operação contínua",
  sem_dados: "Nó sem comunicação",
};
const SEVERIDADE = { alta: "critico", media: "atencao", baixa: "neutra" };

// ---- utilitários ------------------------------------------------------------------------

const num = (v, casas = 0) =>
  v == null || Number.isNaN(v) ? "—" :
  Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
const reais = (v) =>
  v == null ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const pct = (v, casas = 0) =>
  v == null ? "—" : Number(v).toLocaleString("pt-BR", { style: "percent", maximumFractionDigits: casas });
const potencia = (w) => (w >= 1000 ? `${num(w / 1000, 2)} kW` : `${num(w)} W`);
const dataHora = (s) =>
  s ? new Date(s).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(caminho, opcoes = {}) {
  const r = await fetch(`/api${caminho}`, {
    headers: { "Content-Type": "application/json" },
    ...opcoes,
    body: opcoes.body ? JSON.stringify(opcoes.body) : undefined,
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const corpo = await r.json();
      msg = typeof corpo.detail === "string" ? corpo.detail : corpo.detail?.map((d) => d.msg).join("; ") ?? msg;
    } catch { /* corpo não é JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

function avisar(texto) {
  aviso.textContent = texto;
  aviso.hidden = false;
  clearTimeout(avisar.t);
  avisar.t = setTimeout(() => { aviso.hidden = true; }, 4000);
}

function cor(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

function grafico(canvas, config) {
  const g = new Chart(canvas, config);
  graficos.push(g);
  return g;
}

function limpar() {
  clearInterval(temporizador);
  temporizador = null;
  while (graficos.length) graficos.pop().destroy();
}

function opcoesEixos(unidade) {
  const grade = cor("--borda");
  const texto = cor("--texto-2");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: texto } } },
    scales: {
      x: { ticks: { color: texto, maxRotation: 0, autoSkipPadding: 16 }, grid: { color: grade } },
      y: {
        beginAtZero: true,
        ticks: { color: texto, callback: (v) => `${num(v, Number.isInteger(v) ? 0 : 1)} ${unidade}` },
        grid: { color: grade },
      },
    },
  };
}

function tabelaDados(cabecalhos, linhas) {
  return `<details><summary>Ver dados em tabela</summary><div class="rolagem"><table>
    <thead><tr>${cabecalhos.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${linhas.map((l) => `<tr>${l.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div></details>`;
}

// ---- Painel -----------------------------------------------------------------------------

async function painel() {
  main.innerHTML = `
    <div class="cabecalho-secao">
      <div><h1>Painel</h1><p>Leituras atualizadas a cada 5 segundos.</p></div>
    </div>
    <div class="grade indicadores" id="indicadores" aria-live="polite"></div>
    <h2 style="margin-top:1.5rem">Circuitos</h2>
    <div class="grade" id="circuitos"></div>
    <section class="cartao" style="margin-top:1.5rem">
      <h2>Consumo total nas últimas 24 horas</h2>
      <div class="grafico"><canvas id="g24h" role="img" aria-label="Potência total por hora nas últimas 24 horas"></canvas></div>
      <div id="t24h"></div>
    </section>`;

  const [proj, agora] = await Promise.all([api("/analise/projecao"), api("/agora")]);
  desenharAgora(agora, proj);
  temporizador = setInterval(async () => {
    try { desenharAgora(await api("/agora"), proj); } catch (e) { console.warn(e); }
  }, 5000);
  await grafico24h(agora.circuitos);
}

function desenharAgora(agora, proj) {
  const abertos = agora.circuitos.reduce((s, c) => s + c.alertas_abertos, 0);
  document.getElementById("indicadores").innerHTML = `
    <div class="cartao indicador"><div class="rotulo">Potência agora</div>
      <div class="valor">${potencia(agora.potencia_total_w)}</div>
      <div class="detalhe">soma dos circuitos monitorados</div></div>
    <div class="cartao indicador"><div class="rotulo">Consumo no mês</div>
      <div class="valor">${num(proj.consumido_kwh, 1)} kWh</div>
      <div class="detalhe">${reais(proj.consumido_custo)} a ${reais(proj.tarifa_kwh)}/kWh</div></div>
    <div class="cartao indicador"><div class="rotulo">Projeção para o mês</div>
      <div class="valor">${reais(proj.projetado_custo)}</div>
      <div class="detalhe">${proj.projetado_kwh == null ? "histórico insuficiente" : `${num(proj.projetado_kwh)} kWh, pela média dos últimos 7 dias`}</div></div>
    <div class="cartao indicador"><div class="rotulo">Alertas abertos</div>
      <div class="valor">${abertos}</div>
      <div class="detalhe"><a href="#/alertas">ver alertas</a></div></div>`;

  document.getElementById("circuitos").innerHTML = agora.circuitos.map((c) => {
    const semLeitura = c.ts == null;
    const carga = c.disjuntor_a && c.corrente_a != null ? c.corrente_a / c.disjuntor_a : null;
    const etiquetas = [
      c.simulado ? `<span class="etiqueta sim" title="Dados gerados pelo simulador">simulado</span>` : "",
      semLeitura ? `<span class="etiqueta neutra">sem leitura</span>` : "",
      !c.configurado ? `<span class="etiqueta atencao">configurar</span>` : "",
      c.alertas_abertos ? `<span class="etiqueta critico">${c.alertas_abertos} alerta${c.alertas_abertos > 1 ? "s" : ""}</span>` : "",
    ].join(" ");
    return `<article class="cartao circuito ${c.alertas_abertos ? "com-alerta" : ""}">
      <header><h3>${esc(c.nome)}</h3><div>${etiquetas}</div></header>
      <div class="potencia">${semLeitura ? "—" : potencia(c.potencia_w)}</div>
      <dl>
        <div><dt>Corrente</dt><dd>${num(c.corrente_a, 2)} A</dd></div>
        <div><dt>Tensão</dt><dd>${num(c.tensao_v, 1)} V</dd></div>
        <div><dt>FP</dt><dd>${num(c.fp, 2)}</dd></div>
      </dl>
      ${carga == null ? "" : `<div class="barra ${carga >= 0.8 ? "alta" : ""}" role="meter" aria-valuemin="0" aria-valuemax="100"
        aria-valuenow="${Math.round(carga * 100)}" aria-label="Carga em relação ao disjuntor de ${num(c.disjuntor_a)} A">
        <span style="width:${Math.min(100, carga * 100)}%"></span></div>
        <p class="dica">${pct(carga)} do disjuntor de ${num(c.disjuntor_a)} A</p>`}
    </article>`;
  }).join("") || `<p class="vazio">Nenhum circuito cadastrado. Ligue um nó sensor ou o simulador.</p>`;
}

async function grafico24h(circuitos) {
  const fim = new Date();
  const inicio = new Date(fim - 24 * 3600e3);
  const series = await Promise.all(circuitos.map((c) =>
    api(`/series?circuito=${encodeURIComponent(c.id)}&inicio=${inicio.toISOString()}&fim=${fim.toISOString()}&resolucao=1h`)));
  const total = new Map();
  for (const s of series) for (const p of s.pontos) total.set(p.ts, (total.get(p.ts) ?? 0) + p.potencia_w);
  const pontos = [...total.entries()].sort(([a], [b]) => a.localeCompare(b));
  const rotulos = pontos.map(([t]) => new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }));
  grafico(document.getElementById("g24h"), {
    type: "bar",
    data: { labels: rotulos, datasets: [{ label: "Potência média (kW)", data: pontos.map(([, w]) => w / 1000), backgroundColor: cor("--serie-1") }] },
    options: opcoesEixos("kW"),
  });
  document.getElementById("t24h").innerHTML = tabelaDados(["Hora", "Potência média (kW)"],
    pontos.map(([t, w], i) => [rotulos[i], num(w / 1000, 2)]));
}

// ---- Histórico --------------------------------------------------------------------------

async function historico() {
  const circuitos = await api("/circuitos");
  const params = new URLSearchParams(location.hash.split("?")[1]);
  const sel = params.get("c") ?? circuitos[0]?.id;
  main.innerHTML = `
    <div class="cabecalho-secao"><div><h1>Histórico</h1><p>Potência e fator de potência por circuito.</p></div></div>
    <div class="cartao">
      <div class="controles">
        <label>Circuito <select id="h-circuito">${circuitos.map((c) =>
          `<option value="${esc(c.id)}" ${c.id === sel ? "selected" : ""}>${esc(c.nome)}</option>`).join("")}</select></label>
        <div class="segmentado" role="group" aria-label="Período">
          ${[["6h", "6 h"], ["24h", "24 h"], ["7d", "7 dias"], ["30d", "30 dias"]].map(([v, r]) =>
            `<button type="button" data-periodo="${v}" aria-pressed="${v === "24h"}">${r}</button>`).join("")}
        </div>
      </div>
    </div>
    <section class="cartao">
      <h2 id="h-titulo">Potência</h2>
      <div class="grafico"><canvas id="h-grafico" role="img" aria-labelledby="h-titulo"></canvas></div>
      <div id="h-tabela"></div>
    </section>`;
  if (!circuitos.length) return;
  let periodo = "24h";
  const horas = { "6h": 6, "24h": 24, "7d": 168, "30d": 720 };
  const carregar = async () => {
    while (graficos.length) graficos.pop().destroy();
    const cid = document.getElementById("h-circuito").value;
    const fim = new Date();
    const inicio = new Date(fim - horas[periodo] * 3600e3);
    const s = await api(`/series?circuito=${encodeURIComponent(cid)}&inicio=${inicio.toISOString()}&fim=${fim.toISOString()}`);
    const curto = horas[periodo] <= 24;
    const rot = s.pontos.map((p) => new Date(p.ts).toLocaleString("pt-BR",
      curto ? { hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "2-digit", hour: "2-digit" }));
    const opcoes = opcoesEixos("W");
    opcoes.scales.fp = { position: "right", min: 0, max: 1, grid: { drawOnChartArea: false }, ticks: { color: cor("--texto-2") } };
    grafico(document.getElementById("h-grafico"), {
      type: "line",
      data: {
        labels: rot,
        datasets: [
          { label: "Potência (W)", data: s.pontos.map((p) => p.potencia_w), borderColor: cor("--serie-1"), pointRadius: 0, borderWidth: 1.5, fill: false },
          { label: "Fator de potência", data: s.pontos.map((p) => p.fp), borderColor: cor("--serie-2"), pointRadius: 0, borderWidth: 1, yAxisID: "fp" },
        ],
      },
      options: opcoes,
    });
    document.getElementById("h-titulo").textContent = `Potência — resolução ${s.resolucao === "bruta" ? "de cada leitura" : s.resolucao}`;
    const amostra = s.pontos.length > 200 ? s.pontos.filter((_, i) => i % Math.ceil(s.pontos.length / 200) === 0) : s.pontos;
    document.getElementById("h-tabela").innerHTML = tabelaDados(["Momento", "Potência (W)", "Energia (Wh)", "FP"],
      amostra.map((p) => [dataHora(p.ts), num(p.potencia_w, 1), num(p.energia_wh, 2), num(p.fp, 2)]));
  };
  document.getElementById("h-circuito").addEventListener("change", carregar);
  main.querySelectorAll("[data-periodo]").forEach((b) => b.addEventListener("click", () => {
    periodo = b.dataset.periodo;
    main.querySelectorAll("[data-periodo]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    carregar();
  }));
  await carregar();
}

// ---- Análises ---------------------------------------------------------------------------

async function analises() {
  main.innerHTML = `
    <div class="cabecalho-secao"><div><h1>Análises</h1><p>O que consome mais, quando consome e quanto disso acontece fora de hora.</p></div>
      <a href="/api/exportar.csv" download>Exportar consumo por hora (CSV)</a></div>
    <div class="grade-2">
      <section class="cartao"><h2 id="a-rank-t">Participação no consumo (30 dias)</h2>
        <div class="grafico"><canvas id="a-rank" role="img" aria-labelledby="a-rank-t"></canvas></div>
        <div id="a-rank-tab"></div></section>
      <section class="cartao"><h2 id="a-acum-t">Energia acumulada no mês</h2>
        <div class="grafico"><canvas id="a-acum" role="img" aria-labelledby="a-acum-t"></canvas></div>
        <p class="dica" id="a-acum-dica"></p></section>
    </div>
    <section class="cartao"><div class="cabecalho-secao"><h2>Comparação com o período anterior</h2>
      <div class="segmentado" role="group" aria-label="Período de comparação">
        ${[["dia", "Hoje"], ["semana", "Semana"], ["mes", "Mês"]].map(([v, r]) =>
          `<button type="button" data-comp="${v}" aria-pressed="${v === "semana"}">${r}</button>`).join("")}
      </div></div><div id="a-comp"></div></section>
    <section class="cartao"><h2>Consumo fora da janela de operação (7 dias)</h2><div id="a-fora"></div></section>
    <section class="cartao"><h2>Perfil de carga (média das últimas 4 semanas, kW)</h2>
      <p class="dica">Cada célula é a potência média naquele dia da semana e hora. Cores mais fortes indicam mais consumo.</p>
      <div class="rolagem" id="a-perfil"></div></section>`;

  const [rank, acum, fora, perfil] = await Promise.all([
    api("/analise/ranking"), api("/analise/acumulado"), api("/analise/fora-de-janela"), api("/analise/perfil"),
  ]);

  const paleta = ["--serie-1", "--serie-2", "--sim", "--atencao", "--ok", "--critico"].map(cor);
  grafico(document.getElementById("a-rank"), {
    type: "doughnut",
    data: { labels: rank.circuitos.map((c) => c.nome), datasets: [{ data: rank.circuitos.map((c) => c.kwh), backgroundColor: paleta, borderColor: cor("--superficie") }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { position: "right", labels: { color: cor("--texto-2") } } } },
  });
  document.getElementById("a-rank-tab").innerHTML = `<div class="rolagem"><table>
    <thead><tr><th>Circuito</th><th class="num">kWh</th><th class="num">Parcela</th><th class="num">Custo</th></tr></thead>
    <tbody>${rank.circuitos.map((c) => `<tr><td>${esc(c.nome)}</td><td class="num">${num(c.kwh, 1)}</td>
      <td class="num">${pct(c.participacao, 1)}</td><td class="num">${reais(c.custo)}</td></tr>`).join("")}</tbody>
    <tfoot><tr><th>Total</th><th class="num">${num(rank.total_kwh, 1)}</th><th></th><th class="num">${reais(rank.total_custo)}</th></tr></tfoot>
  </table></div>`;

  grafico(document.getElementById("a-acum"), {
    type: "line",
    data: {
      labels: acum.pontos.map((p) => new Date(`${p.dia}T12:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })),
      datasets: [
        { label: "Este mês", data: acum.pontos.map((p) => p.acumulado_kwh), borderColor: cor("--serie-1"), backgroundColor: cor("--serie-1"), pointRadius: 2 },
        { label: "Média histórica", data: acum.pontos.map((p) => p.referencia_kwh), borderColor: cor("--texto-2"), borderDash: [6, 4], pointRadius: 0 },
      ],
    },
    options: opcoesEixos("kWh"),
  });
  document.getElementById("a-acum-dica").textContent = acum.media_diaria_referencia_kwh == null
    ? "Ainda não há histórico dos 90 dias anteriores para comparar."
    : `Referência: média de ${num(acum.media_diaria_referencia_kwh, 1)} kWh por dia nos 90 dias anteriores ao mês.`;

  document.getElementById("a-fora").innerHTML = fora.circuitos.length ? `<div class="rolagem"><table>
    <thead><tr><th>Circuito</th><th>Janela</th><th class="num">Total (kWh)</th><th class="num">Fora da janela (kWh)</th><th class="num">Parcela</th><th class="num">Custo fora</th></tr></thead>
    <tbody>${fora.circuitos.map((c) => `<tr><td>${esc(c.nome)}</td><td>${c.janela_inicio.slice(0, 5)}–${c.janela_fim.slice(0, 5)}</td>
      <td class="num">${num(c.total_kwh, 1)}</td><td class="num">${num(c.fora_kwh, 2)}</td><td class="num">${pct(c.fracao, 1)}</td>
      <td class="num">${reais(c.custo_fora)}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="vazio">Nenhum circuito tem janela de operação configurada.</p>`;

  desenharPerfil(perfil);

  const comparar = async (periodo) => {
    const c = await api(`/analise/comparacao?periodo=${periodo}`);
    const seta = (v) => (v == null ? "—" : `${v > 0 ? "▲" : v < 0 ? "▼" : "="} ${pct(Math.abs(v), 1)}`);
    document.getElementById("a-comp").innerHTML = `
      <p>Até agora: <strong>${num(c.atual_kwh, 1)} kWh</strong>; no mesmo trecho do período anterior:
        <strong>${num(c.anterior_kwh, 1)} kWh</strong> (${seta(c.variacao)}).</p>
      <div class="rolagem"><table><thead><tr><th>Circuito</th><th class="num">Atual (kWh)</th><th class="num">Anterior (kWh)</th><th class="num">Variação</th></tr></thead>
      <tbody>${c.circuitos.map((x) => `<tr><td>${esc(x.nome)}</td><td class="num">${num(x.atual_kwh, 1)}</td>
        <td class="num">${num(x.anterior_kwh, 1)}</td><td class="num">${seta(x.variacao)}</td></tr>`).join("")}</tbody></table></div>`;
  };
  main.querySelectorAll("[data-comp]").forEach((b) => b.addEventListener("click", () => {
    main.querySelectorAll("[data-comp]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    comparar(b.dataset.comp);
  }));
  await comparar("semana");
}

function desenharPerfil(perfil) {
  const mapa = new Map(perfil.celulas.map((c) => [`${c.dia_semana}-${c.hora}`, c.kw_medio]));
  const max = Math.max(0.001, ...perfil.celulas.map((c) => c.kw_medio));
  const base = cor("--marca");
  const horas = [...Array(24).keys()];
  document.getElementById("a-perfil").innerHTML = perfil.celulas.length ? `<table class="mapa-calor">
    <caption class="pular">Potência média por dia da semana e hora</caption>
    <thead><tr><th scope="col">Dia</th>${horas.map((h) => `<th scope="col">${h}h</th>`).join("")}</tr></thead>
    <tbody>${DIAS.map((d, i) => `<tr><th scope="row">${d}</th>${horas.map((h) => {
      const v = mapa.get(`${i + 1}-${h}`);
      const a = v == null ? 0 : v / max;
      return `<td style="background:color-mix(in srgb, ${base} ${Math.round(a * 85)}%, transparent);${a > 0.55 ? "color:var(--marca-texto)" : ""}"
        title="${d} ${h}h: ${num(v, 2)} kW">${num(v, 1)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table>` : `<p class="vazio">Sem dados suficientes.</p>`;
}

// ---- Alertas ----------------------------------------------------------------------------

async function alertas() {
  main.innerHTML = `
    <div class="cabecalho-secao"><div><h1>Alertas</h1><p>Eventos detectados automaticamente pelo motor de regras.</p></div>
      <div class="segmentado" role="group" aria-label="Filtro">
        <button type="button" data-estado="abertos" aria-pressed="true">Abertos</button>
        <button type="button" data-estado="todos" aria-pressed="false">Todos</button>
      </div></div>
    <div id="lista-alertas"></div>`;
  let estado = "abertos";
  const carregar = async () => {
    const lista = await api(`/alertas?estado=${estado}`);
    document.getElementById("lista-alertas").innerHTML = lista.length ? lista.map((a) => `
      <article class="cartao alerta ${a.resolvido_em ? "resolvido" : ""}">
        <span class="etiqueta ${a.resolvido_em ? "ok" : SEVERIDADE[a.severidade]}">${a.resolvido_em ? "resolvido" : `severidade ${a.severidade === "media" ? "média" : a.severidade}`}</span>
        <div>
          <h2>${esc(TIPOS[a.tipo] ?? a.tipo)}${a.circuito_nome ? ` — ${esc(a.circuito_nome)}` : ""}</h2>
          <p>${esc(a.mensagem)}</p>
          <p class="quando">Aberto em ${dataHora(a.aberto_em)}${a.resolvido_em ? ` · resolvido em ${dataHora(a.resolvido_em)}` : ""}
            ${a.reconhecido_em ? ` · ciente: ${esc(a.reconhecido_por)} em ${dataHora(a.reconhecido_em)}` : ""}</p>
        </div>
        ${a.reconhecido_em ? "" : `<button type="button" data-reconhecer="${a.id}">Marcar como ciente</button>`}
      </article>`).join("") : `<p class="vazio">Nenhum alerta ${estado === "abertos" ? "aberto" : "registrado"}.</p>`;
    main.querySelectorAll("[data-reconhecer]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api(`/alertas/${b.dataset.reconhecer}/reconhecer`, { method: "POST", body: { por: "síndico" } });
        avisar("Alerta marcado como ciente.");
        await carregar();
        atualizarContador();
      } catch (e) { avisar(`Não foi possível: ${e.message}`); b.disabled = false; }
    }));
  };
  main.querySelectorAll("[data-estado]").forEach((b) => b.addEventListener("click", () => {
    estado = b.dataset.estado;
    main.querySelectorAll("[data-estado]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    carregar();
  }));
  await carregar();
  temporizador = setInterval(carregar, 15000);
}

// ---- Configuração -----------------------------------------------------------------------

async function configuracao() {
  const [circuitos, nos, cfg] = await Promise.all([api("/circuitos?incluir_inativos=true"), api("/nos"), api("/configuracao")]);
  main.innerHTML = `
    <div class="cabecalho-secao"><div><h1>Configuração</h1><p>Cadastro de circuitos, limites dos alertas e tarifa.</p></div></div>
    <section class="cartao"><h2>Circuitos</h2>
      <p class="dica">Canais novos aparecem aqui sozinhos quando um nó começa a enviar leituras. Dê um nome e defina os limites.</p>
      <div class="rolagem"><table><thead><tr><th>Nome</th><th>Nó / canal</th><th class="num">Disjuntor</th><th>Janela</th><th class="num">Máx. contínuo</th><th>Situação</th><th></th></tr></thead>
      <tbody>${circuitos.map((c) => `<tr><td>${esc(c.nome)}</td><td>${esc(c.no_id)} / ${esc(c.canal)}</td>
        <td class="num">${c.disjuntor_a ? `${num(c.disjuntor_a)} A` : "—"}</td>
        <td>${c.janela_inicio ? `${c.janela_inicio.slice(0, 5)}–${c.janela_fim.slice(0, 5)}` : "livre"}</td>
        <td class="num">${c.max_continuo_min ? `${c.max_continuo_min} min` : "—"}</td>
        <td>${c.ativo ? (c.configurado ? `<span class="etiqueta ok">ativo</span>` : `<span class="etiqueta atencao">configurar</span>`) : `<span class="etiqueta neutra">inativo</span>`}</td>
        <td><button type="button" data-editar="${esc(c.id)}">Editar</button></td></tr>`).join("")}</tbody></table></div>
    </section>
    <div class="grade-2">
      <section class="cartao"><h2>Parâmetros gerais</h2>
        <form id="f-cfg" class="formulario">
          <label class="largo">Nome do local <input name="nome_local" value="${esc(cfg.nome_local)}" required maxlength="80"></label>
          <label>Tarifa (R$/kWh) <input name="tarifa_kwh" type="number" step="0.0001" min="0" value="${cfg.tarifa_kwh}" required></label>
          <label>Alerta de corrente (% do disjuntor) <input name="pct_disjuntor" type="number" min="1" max="100" value="${Math.round(cfg.pct_disjuntor * 100)}" required></label>
          <label>Fora de hora após (min) <input name="min_fora_janela" type="number" min="1" max="240" value="${cfg.min_fora_janela}" required></label>
          <label>Janela do FP (min) <input name="janela_fp_min" type="number" min="1" max="60" value="${cfg.janela_fp_min}" required></label>
          <label>Nó sem comunicação após (s) <input name="no_offline_s" type="number" min="30" value="${cfg.no_offline_s}" required></label>
          <div class="largo acoes"><button class="primario">Salvar parâmetros</button></div>
        </form></section>
      <section class="cartao"><h2>Nós sensores</h2>
        <div class="rolagem"><table><thead><tr><th>Nó</th><th>Situação</th><th>Último contato</th><th class="num">Circuitos</th></tr></thead>
        <tbody>${nos.map((n) => `<tr><td>${esc(n.id)} ${n.simulado ? `<span class="etiqueta sim">simulado</span>` : ""}<br><span class="dica">${esc(n.descricao)}</span></td>
          <td><span class="etiqueta ${n.status === "online" ? "ok" : "critico"}">${esc(n.status)}</span></td>
          <td>${dataHora(n.ultimo_contato)}</td><td class="num">${n.circuitos}</td></tr>`).join("")}</tbody></table></div>
      </section>
    </div>
    <dialog id="d-circuito" aria-labelledby="d-titulo"></dialog>`;

  document.getElementById("f-cfg").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      const salvo = await api("/configuracao", { method: "PUT", body: {
        nome_local: f.get("nome_local"), tarifa_kwh: Number(f.get("tarifa_kwh")),
        pct_disjuntor: Number(f.get("pct_disjuntor")) / 100, min_fora_janela: Number(f.get("min_fora_janela")),
        janela_fp_min: Number(f.get("janela_fp_min")), no_offline_s: Number(f.get("no_offline_s")),
      } });
      document.getElementById("nome-local").textContent = salvo.nome_local;
      avisar("Parâmetros salvos.");
    } catch (e) { avisar(`Erro: ${e.message}`); }
  });
  main.querySelectorAll("[data-editar]").forEach((b) => b.addEventListener("click", () =>
    editarCircuito(circuitos.find((c) => c.id === b.dataset.editar))));
}

function editarCircuito(c) {
  const d = document.getElementById("d-circuito");
  const hora = (t) => (t ? t.slice(0, 5) : "");
  d.innerHTML = `<form method="dialog" id="f-circuito">
    <h2 id="d-titulo">Circuito ${esc(c.no_id)} / ${esc(c.canal)}</h2>
    <div class="formulario">
      <label class="largo">Nome <input name="nome" value="${esc(c.nome)}" required maxlength="80"></label>
      <label>Sensor <select name="sensor"><option value="pzem" ${c.sensor === "pzem" ? "selected" : ""}>PZEM-004T</option>
        <option value="sct" ${c.sensor === "sct" ? "selected" : ""}>SCT-013 (só corrente)</option></select></label>
      <label>Disjuntor (A) <input name="disjuntor_a" type="number" min="1" step="1" value="${c.disjuntor_a ?? ""}"></label>
      <label>FP mínimo <input name="fp_min" type="number" min="0" max="1" step="0.01" value="${c.fp_min}" required></label>
      <label>Standby (W) <input name="standby_w" type="number" min="0" step="1" value="${c.standby_w}" required>
        <span class="dica">Abaixo disso o circuito conta como desligado.</span></label>
      <label>Início da janela <input name="janela_inicio" type="time" value="${hora(c.janela_inicio)}"></label>
      <label>Fim da janela <input name="janela_fim" type="time" value="${hora(c.janela_fim)}">
        <span class="dica">Deixe os dois vazios se o circuito pode funcionar a qualquer hora.</span></label>
      <label>Máx. ligado sem parar (min) <input name="max_continuo_min" type="number" min="1" value="${c.max_continuo_min ?? ""}">
        <span class="dica">Útil para bombas: detecta falha de boia.</span></label>
      <fieldset class="largo"><legend>Dias da janela</legend><div class="dias">
        ${DIAS.map((n, i) => `<label><input type="checkbox" name="dias" value="${i + 1}" ${c.dias_semana.includes(i + 1) ? "checked" : ""}> ${n}</label>`).join("")}
      </div></fieldset>
      <label class="largo" style="display:flex;gap:.5rem;align-items:center"><input type="checkbox" name="ativo" ${c.ativo ? "checked" : ""}> Circuito ativo</label>
    </div>
    <div class="acoes"><button value="cancelar" formnovalidate>Cancelar</button><button class="primario" value="salvar">Salvar</button></div>
  </form>`;
  d.showModal();
  d.querySelector("form").addEventListener("submit", async (ev) => {
    if (ev.submitter?.value !== "salvar") return;
    ev.preventDefault();
    const f = new FormData(ev.target);
    const vazio = (k) => (f.get(k) === "" ? null : f.get(k));
    try {
      await api(`/circuitos/${encodeURIComponent(c.id)}`, { method: "PUT", body: {
        nome: f.get("nome"), sensor: f.get("sensor"),
        disjuntor_a: vazio("disjuntor_a") && Number(f.get("disjuntor_a")),
        fp_min: Number(f.get("fp_min")), standby_w: Number(f.get("standby_w")),
        janela_inicio: vazio("janela_inicio"), janela_fim: vazio("janela_fim"),
        dias_semana: f.getAll("dias").map(Number),
        max_continuo_min: vazio("max_continuo_min") && Number(f.get("max_continuo_min")),
        ativo: f.get("ativo") === "on",
      } });
      d.close();
      avisar("Circuito salvo.");
      configuracao();
    } catch (e) { avisar(`Erro: ${e.message}`); }
  });
}

// ---- roteamento -------------------------------------------------------------------------

const ROTAS = { painel, historico, analises, alertas, configuracao };

async function atualizarContador() {
  try {
    const abertos = await api("/alertas?estado=abertos&limite=1000");
    const el = document.getElementById("contador-alertas");
    el.hidden = abertos.length === 0;
    el.textContent = abertos.length;
    el.setAttribute("aria-label", `${abertos.length} abertos`);
  } catch { /* ignora */ }
}

async function navegar() {
  const nome = location.hash.replace(/^#\//, "").split("?")[0] || "painel";
  const rota = ROTAS[nome] ?? painel;
  limpar();
  document.querySelectorAll("nav a").forEach((a) => a.removeAttribute("aria-current"));
  document.querySelector(`nav a[href="#/${nome}"]`)?.setAttribute("aria-current", "page");
  main.innerHTML = `<p class="carregando">Carregando…</p>`;
  try {
    await rota();
    document.title = `${main.querySelector("h1")?.textContent ?? "Painel"} · EnergIA Sense`;
  } catch (e) {
    main.innerHTML = `<div class="cartao"><h1>Não foi possível carregar</h1><p>${esc(e.message)}</p>
      <button type="button" onclick="location.reload()">Tentar de novo</button></div>`;
  }
  main.focus({ preventScroll: true });
}

window.addEventListener("hashchange", navegar);
api("/configuracao").then((c) => { document.getElementById("nome-local").textContent = c.nome_local; }).catch(() => {});
atualizarContador();
setInterval(atualizarContador, 30000);
navegar();
