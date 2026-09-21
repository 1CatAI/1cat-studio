// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useMemo, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Play, Leaf } from "lucide-react";
import { Button } from "@/onecat/ui";
import {
  useQuery,
  mutation,
  type Profile,
  type Engine,
  type Hardware,
} from "./api";
import {
  Page,
  Field,
  NumberField,
  Action,
  ErrorNotice,
  Modal,
  format,
  useText,
  jobLabel,
  saveFile,
} from "./common";

import { modelLabel } from "./model-label";
import { PowerModes } from "./power-modes";
import { LiveGPUs } from "./live-gpus";

type Point = {
  setting: Hardware | string;
  valid_repeats: number;
  prefill_tokens_s: number;
  decode_tokens_s: number;
  average_total_gpu_w: number;
  total_gpu_j: number;
  total_gpu_j_std: number;
  elapsed_s: number;
  ttft_s: number;
  request_energy_wh: number;
};
type Run = {
  id: string;
  state: string;
  created_at: number;
  profile: Profile;
  workload: { prompt_tokens: number; output_tokens: number };
  summary: Point[];
  matches_current_profile: boolean;
  hardware_restored?: boolean;
  error?: string;
};
type Results = {
  items: Run[];
  reference?: {
    contract: {
      model: string;
      prompt_tokens: number;
      completion_token_budget: number;
    };
    measurements: (Point & { n: number; setting: string })[];
  };
};
export function PerformancePage() {
  const t = useText();
  const { data, error } = useQuery<Results>("/api/efficiency", 3000);
  const { data: profiles } = useQuery<{ items: Profile[] }>("/api/profiles");
  const { data: engine } = useQuery<Engine>("/api/inference/status", 3000);
  const { data: cap } = useQuery<{ gpu_control: boolean }>(
    "/api/studio/capabilities",
    2000,
  );
  const [selected, setSelected] = useState("reference"),
    [testOpen, setTestOpen] = useState(false),
    [profileId, setProfileId] = useState(""),
    [prompt, setPrompt] = useState(8192),
    [output, setOutput] = useState(1024),
    [repeats, setRepeats] = useState(3);
  const [lowPrefill, setLowPrefill] = useState(0),
    [lowDecode, setLowDecode] = useState(0),
    [maxTtft, setMaxTtft] = useState(0),
    [recommendation, setRecommendation] = useState<Point | null>(null),
    [reason, setReason] = useState("");
  const run = data?.items.find((r) => r.id === selected);
  const history = selected === "reference";
  const points = useMemo(
    () =>
      [
        ...(history ? data?.reference?.measurements || [] : run?.summary || []),
      ].sort((a, b) => a.average_total_gpu_w - b.average_total_gpu_w),
    [history, data, run],
  );
  const pickedProfile = profiles?.items.find((p) => p.id === profileId);
  return (
    <Page
      title={t("性能与能效", "Performance & efficiency")}
      description={t(
        "查看真实功率与速率，用相同工作负载寻找适合本机的档位。",
        "Compare measured power and throughput under an identical workload.",
      )}
      action={
        <Button
          onClick={() => {
            setProfileId(engine?.profile_id || profiles?.items[0]?.id || "");
            setTestOpen(true);
          }}
        >
          <Play />
          {t("一键校准", "Run calibration")}
        </Button>
      }
    >
      <ErrorNotice error={error} />
      <PowerModes engine={engine} calibration={data?.items
        .filter(run => run.state === "completed" && run.matches_current_profile && run.hardware_restored)
        .sort((a, b) => b.created_at - a.created_at)[0]} />
      <LiveGPUs />
      <div className="oc-row oc-spaced">
        <div>
          <h2>{t("能效测量", "Efficiency measurements")}</h2>
          <p className="oc-muted">{modelLabel(engine?.profile)}</p>
        </div>
      </div>
      <div className="oc-row oc-spaced">
        <Field label={t("测量记录", "Measurement record")}>
          <select
            value={selected}
            onChange={(e) => {
              setSelected(e.target.value);
              setRecommendation(null);
              setReason("");
            }}
          >
            <option value="reference">
              {t("V100 历史实测 · 8K / 1K", "V100 reference · 8K / 1K")}
            </option>
            {data?.items.map((r) => (
              <option key={r.id} value={r.id}>
                {modelLabel(r.profile)} ·{" "}
                {new Date(r.created_at * 1000).toLocaleString(t("zh-CN", "en-US"))} · {jobLabel(r.state, t)}
              </option>
            ))}
          </select>
        </Field>
        <span className="oc-status-warn">
          {history
            ? t(
                "历史参考，不能直接视为当前配置的验证结果",
                "Historical reference; not a validation of the current configuration",
              )
            : run?.matches_current_profile
              ? t(
                  "运行配置匹配；适用范围以本次测试条件为准",
                  "Runtime profile matches; workload conditions still apply",
                )
              : t(
                  "与当前运行配置不匹配",
                  "Does not match the current runtime profile",
                )}
        </span>
      </div>
      <Button
        variant="ghost"
        onClick={() =>
          saveFile("onecat-efficiency.json", history ? data?.reference : run)
        }
      >
        {t("导出测量记录", "Export measurements")}
      </Button>
      <p className="oc-muted oc-spaced">
        {history
          ? "Qwen3.8-27B NVFP4 · V100 × 4 · 8192 → 1024 tokens · DFlash2/MTP OFF"
          : run
            ? modelLabel(run.profile) +
              " · " +
              run.workload.prompt_tokens +
              " → " +
              run.workload.output_tokens +
              " tokens"
            : ""}
      </p>
      <div className="oc-chart-grid">
        <div className="oc-chart">
          <h2>Prefill</h2>
          <Curve
            points={points}
            metric="prefill_tokens_s"
            label="Prefill tok/s"
          />
        </div>
        <div className="oc-chart">
          <h2>Decode</h2>
          <Curve
            points={points}
            metric="decode_tokens_s"
            label="Decode tok/s"
          />
        </div>
      </div>
      <p className="oc-muted oc-chart-caption">
        {t(
          "横轴为完整请求的 GPU 板卡平均功率，未计 CPU、内存及电源损耗。",
          "The x-axis is average GPU board power over the full request; CPU, RAM, and PSU losses are excluded.",
        )}
      </p>
      <div className="oc-table-wrap">
        <table className="oc-table">
          <thead>
            <tr>
              <th>{t("档位", "Setting")}</th>
              <th>{t("平均功率", "Average power")}</th>
              <th>Prefill</th>
              <th>Decode</th>
              <th>TTFT</th>
              <th>{t("每次请求", "Per request")}</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p, index) => (
              <tr key={index}>
                <td>{settingName(p.setting, t)}</td>
                <td>{format(p.average_total_gpu_w, 0)} W</td>
                <td>{format(p.prefill_tokens_s, 0)} tok/s</td>
                <td>{format(p.decode_tokens_s, 2)} tok/s</td>
                <td>{format(p.ttft_s, 3)} s</td>
                <td>
                  {format(p.request_energy_wh ?? p.total_gpu_j / 3600, 3)} Wh
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!history && (
        <div className="oc-panel">
          <h2>
            {t("按体验要求选档", "Choose a setting for your requirements")}
          </h2>
          <div className="oc-form-grid">
            <NumberField
              label={t("最低 Prefill tok/s", "Minimum prefill tok/s")}
              value={lowPrefill}
              onChange={setLowPrefill}
              min={0}
            />
            <NumberField
              label={t("最低 Decode tok/s", "Minimum decode tok/s")}
              value={lowDecode}
              onChange={setLowDecode}
              min={0}
            />
            <NumberField
              label={t(
                "最大 TTFT 秒（0 为不限）",
                "Maximum TTFT seconds (0 = no limit)",
              )}
              value={maxTtft}
              onChange={setMaxTtft}
              min={0}
              step={0.1}
            />
          </div>
          <Action
            disabled={
              !run?.matches_current_profile ||
              run?.state !== "completed" ||
              !run?.hardware_restored
            }
            run={async () => {
              const r = await mutation<{
                recommendation: Point | null;
                reason?: string;
              }>("/api/efficiency/recommend", {
                run_id: selected,
                min_prefill_tokens_s: lowPrefill,
                min_decode_tokens_s: lowDecode,
                max_ttft_s: maxTtft || null,
              });
              setRecommendation(r.recommendation);
              setReason(r.reason || "");
            }}
          >
            <Leaf />
            {t("推荐已测档位", "Recommend measured setting")}
          </Action>
          {reason && <p className="oc-muted oc-spaced">{reason}</p>}
          {recommendation && (
            <div className="oc-recommendation">
              <div>
                <strong>{settingName(recommendation.setting, t)}</strong>
                <p>
                  {format(recommendation.average_total_gpu_w, 0)} W ·{" "}
                  {format(recommendation.decode_tokens_s, 2)} tok/s ·{" "}
                  {format(recommendation.total_gpu_j / 3600, 3)} Wh
                </p>
              </div>
              <Action
                disabled={!cap?.gpu_control}
                run={() =>
                  mutation("/api/inference/hardware", recommendation.setting)
                }
                success={t("已提交档位应用任务", "Hardware task created")}
              >
                {t("应用到当前模型", "Apply to active model")}
              </Action>
              <Action
                variant="outline"
                disabled={!engine?.profile}
                run={() =>
                  mutation("/api/profiles", {
                    ...engine!.profile,
                    hardware_profile: recommendation.setting,
                  })
                }
                success={t(
                  "已保存，下次启动此预设时应用",
                  "Saved for the next launch of this profile",
                )}
              >
                {t("保存为启动档位", "Save for launch")}
              </Action>
            </div>
          )}
        </div>
      )}
      <ErrorNotice error={run?.error} />
      <Modal
        open={testOpen}
        onOpenChange={setTestOpen}
        title={t("固定工作负载校准", "Fixed-workload calibration")}
        description={t(
          "测试时暂停普通请求，完成或取消后恢复原硬件设置。",
          "Normal requests pause during the test. Previous hardware settings are restored on completion or cancellation.",
        )}
      >
        <Field label={t("测试预设", "Test profile")}>
          <select
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
          >
            <option value="">{t("选择启动预设", "Choose a profile")}</option>
            {profiles?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {modelLabel(p)}
              </option>
            ))}
          </select>
        </Field>
        <div className="oc-form-grid">
          <NumberField
            label={t("固定输入 tokens", "Fixed input tokens")}
            value={prompt}
            onChange={setPrompt}
            min={128}
            max={131072}
          />
          <NumberField
            label={t("固定输出 tokens", "Fixed output tokens")}
            value={output}
            onChange={setOutput}
            min={16}
            max={8192}
          />
          <NumberField
            label={t("每档重复次数", "Repeats per setting")}
            value={repeats}
            onChange={setRepeats}
            min={2}
            max={10}
          />
        </div>
        <p className="oc-muted">
          {t(
            "默认使用确定性采样、关闭思考、避免前缀缓存命中。V100 会复测默认档及 900/930/975/1020 MHz 中支持的档位。",
            "Uses deterministic sampling, thinking disabled, and unique prefix-cache salts. V100 tests the default and supported 900/930/975/1020 MHz settings.",
          )}
        </p>
        {pickedProfile?.speculative_config && (
          <ErrorNotice
            error={t(
              "此预设开启了 DFlash2/MTP。请复制预设并关闭推测解码后测试。",
              "This profile enables speculation. Duplicate it and disable DFlash2/MTP before calibration.",
            )}
          />
        )}{" "}
        {!cap?.gpu_control && (
          <ErrorNotice
            error={t(
              "GPU 控制助手尚未配置。请在设置中查看安装步骤。",
              "The GPU helper is not configured. See Settings for installation instructions.",
            )}
          />
        )}
        <Action
          disabled={
            !profileId ||
            !!pickedProfile?.speculative_config ||
            !cap?.gpu_control
          }
          run={async () => {
            await mutation("/api/efficiency/run", {
              profile_id: profileId,
              prompt_tokens: prompt,
              output_tokens: output,
              repeats,
            });
            setTestOpen(false);
          }}
          success={t(
            "校准任务已创建，可在服务页查看进度",
            "Calibration created. Follow progress on the Service page.",
          )}
        >
          <Play />
          {t("开始校准", "Start calibration")}
        </Action>
      </Modal>
    </Page>
  );
}
function settingName(
  setting: Hardware | string,
  t: ReturnType<typeof useText>,
) {
  if (typeof setting === "string") {
    if (setting.startsWith("clock_")) return setting.slice(6) + " MHz";
    return t("默认动态频率", "Default dynamic clocks");
  }
  return setting.graphics_clock_mhz
    ? setting.graphics_clock_mhz + " MHz"
    : (setting.power_limit_w || "—") + " W / GPU";
}
function Curve({
  points,
  metric,
  label,
}: {
  points: Point[];
  metric: "prefill_tokens_s" | "decode_tokens_s";
  label: string;
}) {
  return (
    <div
      className="oc-plot"
      role="img"
      aria-label={label + " versus average GPU board power"}
    >
      <ResponsiveContainer width="100%" height={260}>
        <LineChart
          data={points}
          margin={{ top: 15, right: 18, bottom: 18, left: 3 }}
        >
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            type="number"
            dataKey="average_total_gpu_w"
            domain={["dataMin - 20", "dataMax + 20"]}
            tickFormatter={(v) => Math.round(v) + ""}
            label={{
              value: "GPU W",
              position: "insideBottom",
              offset: -12,
              fill: "var(--muted-foreground)",
            }}
            stroke="var(--muted-foreground)"
          />
          <YAxis
            width={58}
            stroke="var(--muted-foreground)"
            tickFormatter={(v) =>
              v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(v)
            }
          />
          <Tooltip
            contentStyle={{
              background: "var(--popover)",
              borderColor: "var(--border)",
              color: "var(--foreground)",
              borderRadius: 10,
            }}
            labelFormatter={(v) => format(Number(v), 0) + " W"}
            formatter={(v) => [format(Number(v), 2) + " tok/s", label]}
          />
          <Line
            type="linear"
            dataKey={metric}
            stroke="var(--primary)"
            strokeWidth={2}
            dot={{ r: 4, fill: "var(--background)", strokeWidth: 2 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
