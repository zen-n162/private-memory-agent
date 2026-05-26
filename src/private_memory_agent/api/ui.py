"""Self-contained HTML for the local evidence-first agent console."""

from __future__ import annotations


def agent_console_html() -> str:
    """Return the Phase 9-A local chat console HTML."""

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Private Memory Agent Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f1;
      --panel: #ffffff;
      --ink: #172019;
      --muted: #5d675f;
      --line: #d9ded5;
      --accent: #1f6f5b;
      --accent-2: #8a5b16;
      --danger: #9f2f24;
      --ok: #1f6f38;
      --warn-bg: #fff4cf;
      --warn-line: #d1a23b;
      --code: #f7f8f5;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }
    main {
      width: min(1360px, calc(100vw - 28px));
      margin: 0 auto;
      padding: 18px 0 34px;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: flex-start;
      margin-bottom: 14px;
    }
    h1 { margin: 0; font-size: 1.35rem; letter-spacing: 0; }
    h2 { margin: 0 0 10px; font-size: 1rem; letter-spacing: 0; }
    h3 { margin: 12px 0 7px; font-size: 0.92rem; letter-spacing: 0; }
    .notice {
      border: 1px solid var(--warn-line);
      background: var(--warn-bg);
      border-radius: 8px;
      padding: 9px 11px;
      color: #4d3c0a;
      max-width: 560px;
      font-size: 0.9rem;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(320px, 0.72fr) minmax(0, 1.4fr);
      gap: 14px;
      align-items: start;
    }
    form, section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }
    .stack { display: grid; gap: 12px; }
    label { display: grid; gap: 5px; font-weight: 650; }
    textarea, select, input[type="number"], input[type="text"] {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      padding: 10px 11px;
      font: inherit;
    }
    textarea { min-height: 112px; resize: vertical; }
    fieldset {
      margin: 0;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 13px;
    }
    legend {
      padding: 0 5px;
      color: var(--muted);
      font-size: 0.85rem;
      font-weight: 700;
    }
    .inline {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      font-weight: 520;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .grid-3 {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    button {
      border: 0;
      border-radius: 8px;
      min-height: 40px;
      padding: 0 14px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 750;
      cursor: pointer;
    }
    button:disabled { opacity: 0.65; cursor: wait; }
    .status-line { color: var(--muted); font-size: 0.9rem; }
    .error { color: var(--danger); }
    .ok { color: var(--ok); }
    .panels {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 0.72fr);
      gap: 14px;
      align-items: start;
    }
    .full { grid-column: 1 / -1; }
    .metric-row, .tag-row {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      align-items: center;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fbfcf8;
      padding: 3px 8px;
      color: var(--muted);
      font-size: 0.84rem;
      max-width: 100%;
      overflow-wrap: anywhere;
    }
    .pill.strong { color: var(--ink); border-color: #c4ccc0; }
    .pill.warn { color: var(--accent-2); border-color: #e4c06e; background: #fff9e7; }
    .pill.bad { color: var(--danger); border-color: #dfa59e; background: #fff0ee; }
    .kv {
      display: grid;
      grid-template-columns: minmax(120px, 0.42fr) minmax(0, 1fr);
      gap: 6px 10px;
      font-size: 0.92rem;
    }
    .kv dt { color: var(--muted); }
    .kv dd { margin: 0; overflow-wrap: anywhere; }
    .evidence-list { display: grid; gap: 9px; }
    .evidence-item {
      border-top: 1px solid var(--line);
      padding-top: 9px;
    }
    .evidence-item:first-child { border-top: 0; padding-top: 0; }
    .snippet {
      margin-top: 7px;
      padding: 8px;
      background: var(--code);
      border: 1px solid var(--line);
      border-radius: 8px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: #303832;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 0.85rem ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: var(--code);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
    }
    @media (max-width: 920px) {
      header, .layout, .panels, .grid-2, .grid-3 {
        grid-template-columns: 1fr;
        display: grid;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Private Memory Agent Console</h1>
        <div id="system-summary" class="status-line">Checking local system status...</div>
      </div>
      <div class="notice">Local-only developer console. Answer text is shown by default for usability. Raw evidence snippets, file paths, OCR, GPS, and raw model output stay hidden unless explicitly enabled.</div>
    </header>

    <div class="layout">
      <form id="chat-form" class="stack">
        <label>
          Question
          <textarea id="question" required autocomplete="off"></textarea>
        </label>

        <div class="grid-2">
          <label>
            Mode
            <select id="mode">
              <option value="retrieval-only" selected>retrieval-only</option>
              <option value="fake-model">fake-model</option>
              <option value="real-model">real-model</option>
            </select>
          </label>
          <label>
            Limit
            <input id="limit" type="number" min="1" max="20" value="5">
          </label>
        </div>

        <fieldset>
          <legend>Sources</legend>
          <label class="inline"><input type="checkbox" name="source" value="photos"> photos</label>
          <label class="inline"><input type="checkbox" name="source" value="line"> line</label>
          <label class="inline"><input type="checkbox" name="source" value="notes"> notes</label>
        </fieldset>

        <fieldset>
          <legend>Agent</legend>
          <label class="inline"><input id="leader-plan" type="checkbox" checked> leader_plan</label>
          <label class="inline"><input id="leader-rerank" type="checkbox" checked> leader_rerank</label>
          <label class="inline"><input id="semantic" type="checkbox"> semantic</label>
          <label class="inline"><input id="reranker-enabled" type="checkbox"> reranker</label>
          <label class="inline"><input id="strict" type="checkbox"> strict relevance</label>
          <label class="inline"><input id="show-answer" type="checkbox" checked> show_answer</label>
          <label class="inline"><input id="show-snippets" type="checkbox"> show_snippets</label>
        </fieldset>

        <div class="grid-2">
          <label>
            Retrieval repair
            <input id="retrieval-repair" type="number" min="0" max="3" value="1">
          </label>
          <label>
            Semantic model
            <select id="semantic-model">
              <option value="hash" selected>hash</option>
              <option value="fake">fake</option>
              <option value="ruri-v3-310m">ruri-v3-310m</option>
              <option value="bge-m3">bge-m3</option>
              <option value="qwen3-embedding-0.6b">qwen3-embedding-0.6b</option>
            </select>
          </label>
        </div>

        <div class="grid-3">
          <label>
            Reranker
            <select id="reranker">
              <option value="none" selected>none</option>
              <option value="fake">fake</option>
              <option value="ruri-v3-reranker-310m">ruri-v3-reranker-310m</option>
              <option value="qwen3-reranker-0.6b">qwen3-reranker-0.6b</option>
            </select>
          </label>
          <label>
            Timeout
            <input id="timeout" type="number" min="1" value="300">
          </label>
          <label>
            Max tokens
            <input id="max-tokens" type="number" min="1" max="4096" value="256">
          </label>
        </div>

        <button id="run" type="submit">Run</button>
        <div id="request-status" class="status-line" role="status" aria-live="polite"></div>
      </form>

      <div class="panels">
        <section>
          <h2>Answer</h2>
          <div id="answer-panel" class="stack"><div class="status-line">No query yet.</div></div>
        </section>

        <section>
          <h2>Privacy</h2>
          <div id="privacy-panel" class="stack"><div class="status-line">Answers are shown by default locally. Evidence snippets remain hidden.</div></div>
        </section>

        <section class="full">
          <h2>Evidence</h2>
          <div id="evidence-panel" class="evidence-list"><div class="status-line">No evidence yet.</div></div>
        </section>

        <section>
          <h2>Agent Trace</h2>
          <div id="trace-panel" class="stack"><div class="status-line">No trace yet.</div></div>
        </section>

        <section>
          <h2>System Status</h2>
          <pre id="system-panel">{}</pre>
        </section>
      </div>
    </div>
  </main>

  <script>
    const form = document.querySelector("#chat-form");
    const statusNode = document.querySelector("#request-status");
    const runButton = document.querySelector("#run");
    const answerPanel = document.querySelector("#answer-panel");
    const evidencePanel = document.querySelector("#evidence-panel");
    const tracePanel = document.querySelector("#trace-panel");
    const privacyPanel = document.querySelector("#privacy-panel");
    const systemPanel = document.querySelector("#system-panel");
    const systemSummary = document.querySelector("#system-summary");

    function value(id) { return document.querySelector(id).value; }
    function checked(id) { return document.querySelector(id).checked; }
    function selectedSources() {
      return Array.from(document.querySelectorAll("input[name='source']:checked")).map((node) => node.value);
    }
    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
    function el(tag, text, className) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      node.textContent = text;
      return node;
    }
    function pill(text, state) {
      const node = el("span", text, `pill ${state || ""}`.trim());
      return node;
    }
    function renderList(items) {
      const list = document.createElement("ul");
      items.forEach((item) => list.appendChild(el("li", String(item))));
      return list;
    }
    function renderKv(target, values) {
      const dl = document.createElement("dl");
      dl.className = "kv";
      Object.entries(values).forEach(([key, val]) => {
        dl.appendChild(el("dt", key));
        dl.appendChild(el("dd", val === null || val === undefined ? "n/a" : String(val)));
      });
      target.appendChild(dl);
    }
    function renderAnswer(payload) {
      clear(answerPanel);
      const answer = payload.answer || {};
      const row = document.createElement("div");
      row.className = "metric-row";
      row.appendChild(pill(`answer_succeeded=${Boolean(answer.answer_succeeded)}`, answer.answer_succeeded ? "strong" : "warn"));
      row.appendChild(pill(`answer_state=${answer.answer_state || "n/a"}`, answer.answer_state === "unknown" ? "warn" : "strong"));
      row.appendChild(pill(`confidence=${answer.confidence ?? "n/a"}`));
      row.appendChild(pill(`mode=${payload.mode}`));
      answerPanel.appendChild(row);
      if (!answer.answer_succeeded) {
        answerPanel.appendChild(el("div", "Answer generation did not succeed. Check retrieval status, model endpoint status, and warnings.", "status-line error"));
      } else if (answer.answer_hidden) {
        answerPanel.appendChild(el("div", "Answer was generated but hidden because Show answer is off.", "status-line"));
        answerPanel.appendChild(el("div", "Enable Show answer and run again to display the answer.", "status-line"));
      } else if (answer.conclusion) {
        const heading = answer.answer_state === "unknown" ? "Conclusion (unknown / insufficient evidence)" : "Conclusion";
        answerPanel.appendChild(el("h3", heading));
        answerPanel.appendChild(el("div", answer.conclusion));
      } else {
        answerPanel.appendChild(el("div", "No answer text was returned.", "status-line"));
      }
      renderKv(answerPanel, {
        used_sources: (answer.used_sources || []).join(", ") || "none",
        evidence_references: (answer.evidence_references || []).join(", ") || "none",
        unknowns_count: (answer.unknowns || []).length,
        error_class: answer.error_class || "none"
      });
      if ((answer.unknowns || []).length) {
        answerPanel.appendChild(el("h3", "Unknowns"));
        answerPanel.appendChild(renderList(answer.unknowns));
      }
      renderTemporalDates(payload);
    }
    function renderTemporalDates(payload) {
      const temporal = payload.temporal_event || {};
      const dates = temporal.candidate_dates || [];
      if (!dates.length) return;
      answerPanel.appendChild(el("h3", "Candidate Dates"));
      dates.forEach((item) => {
        const row = document.createElement("article");
        row.className = "evidence-item";
        const tags = document.createElement("div");
        tags.className = "tag-row";
        tags.appendChild(pill(item.date || "unknown", "strong"));
        tags.appendChild(pill(`confidence=${item.confidence ?? "n/a"}`));
        tags.appendChild(pill(`photos=${item.photo_count ?? 0}`));
        tags.appendChild(pill(`annotated=${item.annotated_photo_count ?? 0}`));
        tags.appendChild(pill(`line_support=${item.line_support_count ?? 0}`));
        tags.appendChild(pill(`notes_support=${item.notes_support_count ?? 0}`));
        row.appendChild(tags);
        row.appendChild(el("div", `reason=${item.reason || "n/a"}`, "status-line"));
        row.appendChild(el("div", `evidence=${(item.top_evidence_ids || []).join(", ") || "none"}`, "status-line"));
        answerPanel.appendChild(row);
      });
    }
    function renderEvidence(payload) {
      clear(evidencePanel);
      const evidence = payload.evidence || [];
      if (!evidence.length) {
        evidencePanel.appendChild(el("div", "No evidence returned.", "status-line"));
        return;
      }
      const groups = [
        ["used", "Used Evidence"],
        ["candidate", "Examined Candidate Evidence"],
        ["rejected", "Rejected / Weak Evidence"]
      ];
      groups.forEach(([role, label]) => {
        const items = evidence.filter((item) => (item.evidence_role || (item.used_by_answer ? "used" : "candidate")) === role);
        if (!items.length) return;
        evidencePanel.appendChild(el("h3", label));
        items.forEach((item) => renderEvidenceItem(item));
      });
      const ungrouped = evidence.filter((item) => !["used", "candidate", "rejected"].includes(item.evidence_role || ""));
      ungrouped.forEach((item) => renderEvidenceItem(item));
    }
    function renderEvidenceItem(item) {
        const row = document.createElement("article");
        row.className = "evidence-item";
        const tags = document.createElement("div");
        tags.className = "tag-row";
        tags.appendChild(pill(item.evidence_id || "unknown", "strong"));
        tags.appendChild(pill(`source=${item.source_type || "unknown"}`));
        tags.appendChild(pill(`role=${item.evidence_role || "candidate"}`));
        tags.appendChild(pill(`should_use=${item.should_use ?? "n/a"}`, item.should_use ? "strong" : "warn"));
        tags.appendChild(pill(`specificity=${item.specificity || "n/a"}`));
        tags.appendChild(pill(`relevance=${item.relevance_score ?? "n/a"}`));
        tags.appendChild(pill(`used_by_answer=${Boolean(item.used_by_answer)}`));
        row.appendChild(tags);
        row.appendChild(el("div", `reason=${item.reason_category || "n/a"}`, "status-line"));
        if (item.snippet) row.appendChild(el("div", item.snippet, "snippet"));
        evidencePanel.appendChild(row);
    }
    function renderTrace(payload) {
      clear(tracePanel);
      const trace = payload.trace || {};
      renderKv(tracePanel, {
        plan_created: trace.plan_created,
        main_entity_count: trace.plan?.main_entity_count,
        specific_concept_count: trace.plan?.specific_concept_count,
        generic_concept_count: trace.plan?.generic_concept_count,
        retrieval_query_count: trace.plan?.retrieval_query_count,
        semantic_candidate_count: trace.semantic_candidate_count,
        reranked_candidate_count: trace.reranked_candidate_count,
        repair_attempted: trace.repair_attempted,
        repair_improved: trace.repair_improved,
        usable_evidence_succeeded: trace.usable_evidence_succeeded,
        usable_evidence_count: trace.usable_evidence_count,
        final_relevance_score: trace.final_relevance_score,
        insufficient_evidence_reason: trace.insufficient_evidence_reason || "none",
        json_extraction_strategy: trace.json_extraction_strategy || "none"
      });
      renderTemporalDiagnostics(trace.temporal_diagnostics || null);
    }
    function renderTemporalDiagnostics(diagnostics) {
      if (!diagnostics) return;
      tracePanel.appendChild(el("h3", "Temporal Diagnostics"));
      renderKv(tracePanel, {
        parsed_date_range_start: diagnostics.parsed_date_range_start,
        parsed_date_range_end: diagnostics.parsed_date_range_end,
        date_range_source: diagnostics.date_range_source,
        parsed_temporal_expression: diagnostics.parsed_temporal_expression,
        timezone: diagnostics.timezone || "n/a",
        date_range_query_column: diagnostics.date_range_query_column,
        date_range_query_status: diagnostics.date_range_query_status,
        media_items_with_taken_at_count: diagnostics.media_items_with_taken_at_count,
        media_items_missing_taken_at_count: diagnostics.media_items_missing_taken_at_count,
        photo_candidates_count: diagnostics.photo_candidates_count,
        annotated_photo_candidates_count: diagnostics.annotated_photo_candidates_count,
        unannotated_photo_candidates_count: diagnostics.unannotated_photo_candidates_count,
        candidates_before_media_type_filter: diagnostics.candidates_before_media_type_filter,
        candidates_after_media_type_filter: diagnostics.candidates_after_media_type_filter,
        candidates_before_annotation_filter: diagnostics.candidates_before_annotation_filter,
        candidates_after_annotation_filter: diagnostics.candidates_after_annotation_filter,
        line_date_support_count: diagnostics.line_date_support_count,
        notes_date_support_count: diagnostics.notes_date_support_count,
        fallback_sources_used: (diagnostics.fallback_sources_used || []).join(", ") || "none"
      });
      if (diagnostics.nearby_month_counts) {
        tracePanel.appendChild(el("h3", "Nearby Month Counts"));
        renderKv(tracePanel, diagnostics.nearby_month_counts);
      }
    }
    function renderPrivacy(payload) {
      clear(privacyPanel);
      const privacy = payload.privacy || {};
      const row = document.createElement("div");
      row.className = "tag-row";
      row.appendChild(pill(`local_only=${Boolean(privacy.local_only)}`, "strong"));
      row.appendChild(pill(`snippets_hidden=${Boolean(privacy.snippets_hidden)}`));
      row.appendChild(pill(`answer_hidden=${Boolean(privacy.answer_hidden)}`));
      row.appendChild(pill(`raw_model_output_hidden=${Boolean(privacy.raw_model_output_hidden)}`));
      row.appendChild(pill(`external_network_disabled=${Boolean(privacy.external_network_disabled)}`));
      privacyPanel.appendChild(row);
      privacyPanel.appendChild(el("div", "Answer text is shown by default in this local-only console. It may still contain private evidence-derived information.", "status-line"));
      privacyPanel.appendChild(el("div", "Raw evidence snippets remain hidden unless Show snippets is enabled. Snippets may contain private LINE messages, note text, captions, OCR, filenames, or other sensitive data.", "status-line"));
      privacyPanel.appendChild(el("div", "Do not paste local answer or snippet output into public chats if it contains private information.", "status-line"));
      const warnings = payload.warnings || [];
      if (warnings.length) {
        privacyPanel.appendChild(el("h3", "Warnings"));
        privacyPanel.appendChild(renderList(warnings));
      }
    }
    async function loadSystemStatus() {
      try {
        const response = await fetch("/api/system/status");
        const payload = await response.json();
        systemPanel.textContent = JSON.stringify(payload, null, 2);
        systemSummary.textContent = `DB=${payload.db_exists ? "available" : "missing"}; endpoints=${(payload.models || []).length}; local_only=${payload.localhost_only}`;
      } catch (error) {
        systemSummary.textContent = "System status unavailable.";
      }
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      runButton.disabled = true;
      statusNode.className = "status-line";
      statusNode.textContent = "Running local query...";
      const rerankerEnabled = checked("#reranker-enabled");
      const timeoutValue = Number(value("#timeout"));
      const payload = {
        question: value("#question"),
        mode: value("#mode"),
        sources: selectedSources(),
        leader_plan: checked("#leader-plan"),
        leader_rerank: checked("#leader-rerank"),
        semantic: checked("#semantic"),
        semantic_model: value("#semantic-model"),
        reranker: rerankerEnabled ? value("#reranker") : "none",
        retrieval_repair: Number(value("#retrieval-repair")),
        strict_relevance: checked("#strict"),
        show_answer: checked("#show-answer"),
        show_snippets: checked("#show-snippets"),
        limit: Number(value("#limit")),
        timeout_seconds: timeoutValue > 0 ? timeoutValue : null,
        max_tokens: Number(value("#max-tokens"))
      };
      try {
        const response = await fetch("/api/chat/query", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "query failed");
        renderAnswer(result);
        renderEvidence(result);
        renderTrace(result);
        renderPrivacy(result);
        statusNode.textContent = result.ok ? "Done" : "Done; review warnings";
        statusNode.className = result.ok ? "status-line ok" : "status-line";
      } catch (error) {
        statusNode.className = "status-line error";
        statusNode.textContent = error instanceof Error ? error.message : "query failed";
      } finally {
        runButton.disabled = false;
      }
    });
    loadSystemStatus();
  </script>
</body>
</html>
"""
