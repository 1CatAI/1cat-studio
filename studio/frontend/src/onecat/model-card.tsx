// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useState, type RefObject } from "react";
import { BookOpen, Copy, Download, ExternalLink, LoaderCircle, RefreshCw, Star } from "lucide-react";
import { Button } from "./ui";
import { api, copyText, useQuery } from "./api";
import { Action, bytes, ErrorNotice, Modal, useText } from "./common";
import { Markdown } from "./markdown";
import { safeMarkdownUrl } from "./markdown-url";
import "./styles/model-card.css";

type Card = {
  repo_id: string;
  publisher: string;
  endpoint: string;
  source_url: string;
  description: string;
  readme: string;
  base_models: string[];
  tags: string[];
  license: string | null;
  downloads: number | null;
  likes: number | null;
  updated_at: number | null;
  status: "ready" | "stale" | "unavailable";
};
const cardPath = (repo: string) => `/api/models/card?catalog_id=${encodeURIComponent(repo)}`;

export function ModelIdentity({ repo, onOpen }: { repo: string; onOpen: (button: HTMLButtonElement) => void }) {
  const { data } = useQuery<Card>(cardPath(repo));
  const t = useText();
  const publisher = repo.split("/")[0];
  return <div className="oc-model-identity">
    <button className="oc-model-publisher" onClick={event => onOpen(event.currentTarget)} aria-label={`${t("查看模型介绍", "About this model")} · ${repo}`}>
      <span className="oc-publisher-mark" aria-hidden="true">{publisher.slice(0, 2).toUpperCase()}</span>
      <span><strong>{publisher}</strong><small title={repo}>{repo}</small></span>
      <BookOpen size={16} />
    </button>
    {data?.description && <p className="oc-model-description" title={data.description}>{data.description}</p>}
  </div>;
}

export function ModelCard({ repo, name, quantization, size, onClose, returnFocusRef }: {
  repo: string; name: string; quantization: string; size: number | null; onClose: () => void;
  returnFocusRef: RefObject<HTMLElement | null>;
}) {
  const t = useText();
  const { data, error, refresh } = useQuery<Card>(cardPath(repo));
  const [reloading, setReloading] = useState(false);
  const [reloadError, setReloadError] = useState("");
  const publisher = repo.split("/")[0];
  const endpoint = data?.endpoint || "https://modelscope.cn";
  const source = data?.source_url || `${endpoint}/models/${repo}`;
  function modelUrl(value: string) {
    if (!safeMarkdownUrl(value)) return undefined;
    if (/^[a-z][a-z0-9+.-]*:|^\/\/|^#/i.test(value)) return value;
    // README-relative images and files belong to the publisher, never Studio's origin.
    try {
      const path = new URL(value, "https://model-card.invalid/").pathname.replace(/^\//, "");
      return `${endpoint}/api/v1/models/${repo}/repo?Revision=master&FilePath=${encodeURIComponent(decodeURIComponent(path))}`;
    } catch { return undefined; }
  }
  return <Modal open onOpenChange={open => { if (!open) onClose(); }} title={name} returnFocusRef={returnFocusRef}
    footer={<><Button variant="ghost" disabled={reloading} onClick={async () => {
      setReloading(true); setReloadError("");
      try { await api(cardPath(repo) + "&refresh=true"); refresh(); }
      catch (error) { setReloadError((error as Error).message); }
      finally { setReloading(false); }
    }}><RefreshCw size={14} className={reloading ? "animate-spin" : ""} />{t("刷新介绍", "Refresh details")}</Button>
      <Button variant="outline" asChild><a href={source} target="_blank" rel="noopener noreferrer">ModelScope <ExternalLink size={14} /></a></Button></>}>
    <div className="oc-model-card-detail">
      <ErrorNotice error={error || reloadError} />
      <div className="oc-model-source-heading">
        <span className="oc-publisher-mark" aria-hidden="true">{publisher.slice(0, 2).toUpperCase()}</span>
        <div><strong><a href={source} target="_blank" rel="noopener noreferrer">{publisher}</a></strong><div className="oc-model-repo"><span>{repo}</span><Action variant="ghost" run={() => copyText(repo)} success={t("已复制", "Copied")}><Copy size={14} /><span className="sr-only">{t("复制仓库名", "Copy repository ID")}</span></Action></div></div>
      </div>
      <dl className="oc-model-facts">
        <div><dt>{t("版本发布者", "Published by")}</dt><dd>{publisher}</dd></div>
        <div><dt>{t("量化", "Quantization")}</dt><dd>{quantization}{size != null && ` · ${bytes(size)}`}</dd></div>
        {!!data?.base_models.length && <div className="oc-model-base"><dt>{t("基础模型 / 原作者", "Base model / original author")}</dt><dd>{data.base_models.map(base => <a key={base} href={`${endpoint}/models/${base}`} target="_blank" rel="noopener noreferrer">{base} <ExternalLink size={12} /></a>)}</dd></div>}
        {data?.license && <div><dt>{t("模型许可", "Model license")}</dt><dd>{data.license}</dd></div>}
        {!!data?.updated_at && <div><dt>{t("仓库更新", "Repository updated")}</dt><dd>{new Date(data.updated_at * 1000).toLocaleDateString()}</dd></div>}
      </dl>
      {data && <div className="oc-model-stats">
        {data.downloads != null && <span title={t("ModelScope 下载次数", "ModelScope downloads")}><Download size={14} />{data.downloads.toLocaleString()}</span>}
        {data.likes != null && <span title={t("ModelScope 收藏", "ModelScope stars")}><Star size={14} />{data.likes.toLocaleString()}</span>}
        {data.status === "stale" && <span>{t("离线缓存", "Cached copy")}</span>}
      </div>}
      {!!data?.tags.length && <div className="oc-model-tags">{data.tags.slice(0, 8).map(tag => <span key={tag}>{tag}</span>)}</div>}
      <div className="oc-model-readme">
        <h3>{t("发布者介绍", "Publisher's model card")} <span>ModelScope</span></h3>
        {!data ? !error && <LoaderCircle className="animate-spin" aria-label={t("读取模型介绍", "Loading model card")} />
          : data.readme ? <Markdown text={data.readme} urlTransform={modelUrl} />
          : <p className="oc-muted">{data.description || (data.status === "unavailable" ? t("暂时无法读取模型介绍，可重试或查看原页。", "Model card is unavailable. Retry or open the source page.") : t("发布者尚未提供模型介绍。", "The publisher has not provided a model card."))}</p>}
      </div>
    </div>
  </Modal>;
}
