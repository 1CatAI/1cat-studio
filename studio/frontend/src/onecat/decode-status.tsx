// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Popover } from "radix-ui";
import { useQuery, type DecodeAggregate } from "./api";
import { format, useText } from "./common";

export function DecodeStatus() {
  const t = useText();
  const { data: aggregate, error } = useQuery<DecodeAggregate>(
    "/api/requests/aggregate?window_s=2",
    1000,
  );
  const data = error ? undefined : aggregate;
  const label = t("实时聚合 Decode", "Live aggregate Decode");
  const buckets = [
    ["pi", "PI"],
    ["codex", "Codex"],
    ["chat", t("聊天", "Chat")],
    ["api", "API"],
  ] as const;
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" className="oc-header-decode" aria-label={label}>
          <span>Decode</span>
          <span>{format(data?.decode_tokens_s, 1)} tok/s</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="oc-decode-popover" side="bottom" align="end"
          sideOffset={10} collisionPadding={12} aria-label={label}>
          <strong>{label}</strong>
          <p className="oc-muted">{t("最近 2 秒 · 所有请求合计", "Last 2 seconds · All requests combined")}</p>
          <dl>
            <div><dt>{t("总速率", "Total")}</dt><dd>{format(data?.decode_tokens_s, 1)} tok/s</dd></div>
            {buckets.map(([key, name]) => <div key={key}>
              <dt>{name}</dt><dd>{format(data?.buckets[key], 1)} tok/s</dd>
            </div>)}
          </dl>
          <p className="oc-decode-requests">
            {data?.active_requests ?? "—"} {t("运行", "running")} · {data?.waiting_requests ?? "—"} {t("排队", "queued")}
          </p>
          {(error || data?.decode_tokens_s == null) && <small className="oc-muted">
            {error ? t("暂时无法获取实时数据", "Live data is temporarily unavailable")
              : t("等待可靠速率数据", "Waiting for reliable speed data")}
          </small>}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
