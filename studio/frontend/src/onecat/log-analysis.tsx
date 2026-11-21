// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Copy, Download, Save, ScanSearch, Square } from "lucide-react";
import { Button, Input } from "@/onecat/ui";
import { copyText, mutation, useQuery } from "./api";
import { Action, ErrorNotice, Field, saveFile, useText } from "./common";

type Provider = {
  base_url: string;
  model: string;
  api_key_set: boolean;
  configured: boolean;
};

type Analysis = {
  id: string;
  state: string;
  cancel_requested?: boolean;
  error?: string;
  result?: { report: string; model: string };
};

const settingsPath = "/api/settings/log-analysis";

export function LogAnalysisSettings() {
  const t = useText();
  const { data, error } = useQuery<Provider>(settingsPath);
  const [draft, setDraft] = useState<Partial<Pick<Provider, "base_url" | "model">>>({});
  const [key, setKey] = useState("");
  const base = draft.base_url ?? data?.base_url ?? "";
  const model = draft.model ?? data?.model ?? "";
  const dirty = base !== data?.base_url || model !== data?.model || !!key;

  async function save() {
    const submittedKey = key;
    const saved = await mutation<Provider>(settingsPath, { base_url: base.trim(), model: model.trim(), ...(submittedKey ? { api_key: submittedKey } : {}) }, "PUT");
    setDraft((current) => ({
      base_url: current.base_url === draft.base_url ? saved.base_url : current.base_url,
      model: current.model === draft.model ? saved.model : current.model,
    }));
    setKey((current) => current === submittedKey ? "" : current);
  }

  return (
    <section className="oc-panel" id="settings-log-analysis">
      <h2>{t("日志分析 Agent", "Log analysis agent")}</h2>
      <p className="oc-muted">{t(
        "配置兼容 OpenAI Chat Completions 的模型 API。点击「让 Agent 分析」时，Studio 会发送脱敏日志和只读诊断数据。",
        "Configure an OpenAI-compatible Chat Completions API. When you request analysis, Studio sends redacted logs and read-only diagnostics to that API.",
      )}</p>
      <ErrorNotice error={error} />
      <div className="oc-form-grid">
        <Field label={t("API 地址", "API base URL")}>
          <Input type="url" placeholder="https://api.deepseek.com" value={base} onChange={(event) => setDraft((current) => ({ ...current, base_url: event.target.value }))} />
        </Field>
        <Field label={t("模型名称", "Model name")}>
          <Input placeholder="deepseek-flash" value={model} onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))} />
        </Field>
      </div>
      <Field label="API Key" hint={t("更换 API 地址时，请填写新地址对应的密钥。", "When changing the API address, enter the key for the new address.")}>
        <Input
          type="password"
          autoComplete="new-password"
          placeholder={data?.api_key_set ? t("已保存密钥", "Key saved") : t("输入提供方密钥", "Enter provider key")}
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
      </Field>
      <div className="oc-actions">
        <Action run={save} disabled={!data || !base.trim() || !model.trim() || !dirty} success={t("日志分析配置已保存", "Log analysis settings saved")}>
          <Save />{t("保存分析配置", "Save analysis settings")}
        </Action>
        {data?.api_key_set && (
          <Action variant="outline" run={() => mutation(settingsPath, { base_url: data.base_url, model: data.model, api_key: "" }, "PUT")}>
            {t("清除已保存密钥", "Clear saved key")}
          </Action>
        )}
      </div>
    </section>
  );
}

export function RuntimeLogActions({ source, text }: { source: string; text: string }) {
  const t = useText();
  const endpoint = `/api/logs/${encodeURIComponent(source)}/analysis`;
  const { data, error } = useQuery<{ provider: Provider; job: Analysis | null }>(endpoint, 1500);
  const job = data?.job;
  const active = job && ["queued", "running"].includes(job.state);
  const report = job?.result?.report;

  return (
    <>
      <div className="oc-actions">
        <Button variant="outline" disabled={!text} onClick={() => saveFile(`${source}.log`, text)}>
          <Download />{t("导出日志", "Export log")}
        </Button>
        <Action disabled={!data?.provider.configured || !!active} run={() => mutation(endpoint, {})}>
          <ScanSearch />{report ? t("重新分析", "Analyze again") : t("让 Agent 分析", "Analyze with Agent")}
        </Action>
        {active && (
          <Action variant="outline" disabled={job.cancel_requested} run={() => mutation(`/api/jobs/${job.id}/cancel`, {})}>
            <Square />{t("取消分析", "Cancel analysis")}
          </Action>
        )}
        {data && !data.provider.configured && (
          <Button variant="outline" asChild><Link to="/settings" hash="settings-log-analysis">{t("配置分析 API", "Configure analysis API")}</Link></Button>
        )}
      </div>
      {data?.provider.configured && (
        <p className="oc-muted">{t("脱敏日志和诊断数据会发送到已配置的模型 API。", "Redacted logs and diagnostic data are sent to the configured model API.")}</p>
      )}
      {job && active && (
        <p className="oc-muted" role="status">{job.cancel_requested ? t("正在取消分析…", "Cancelling analysis…") : t("Agent 正在分析日志…", "The agent is analyzing the log…")}</p>
      )}
      {job?.state === "cancelled" && <p className="oc-muted" role="status">{t("分析已取消。", "Analysis cancelled.")}</p>}
      <ErrorNotice error={error || (job?.state === "failed" ? job.error : undefined)} />
      {report && (
        <section className="oc-spaced" aria-label={t("分析报告", "Analysis report")}>
          <div className="oc-row">
              <strong>{t("分析报告", "Analysis report")} · {job?.result?.model}</strong>
            <Action variant="outline" run={() => copyText(report)}><Copy />{t("复制", "Copy")}</Action>
          </div>
          <pre className="oc-log" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "35vh" }}>{report}</pre>
        </section>
      )}
    </>
  );
}
