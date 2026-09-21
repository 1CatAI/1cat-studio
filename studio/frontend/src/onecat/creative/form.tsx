// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import type { Ref } from "react";
import { useText } from "../common";
import { aspectRatio, sizeForRatio, sizesForRatio, type Size } from "./output-sizes";

export type ModelVariant = {
  name: string;
  name_en: string;
  short_name?: string;
  short_name_en?: string;
  download_bytes: number;
  denoise_steps: number | null;
  artifacts: { role: string; repository: string; filename: string; revision: string }[];
  sizes?: Size[];
  resolutions?: Record<string, ModelVariant>;
};

export type CreativeModel = {
  id: string;
  name: string;
  kind: "image" | "video";
  sizes: [number, number][];
  frames: number[];
  runtime_available: boolean;
  hardware_available: boolean;
  download_bytes: number;
  validation: string;
  partition?: "fl2va" | "ref2va";
  variants?: Record<string, ModelVariant>;
  text_only?: boolean;
  fast_variant?: ModelVariant;
  fast_available?: boolean;
};
export type OutputSpec = { width: number; height: number; num_frames: number };
const fallbackSizes: Record<"image" | "video", [number, number][]> = {
  image: [
    [1024, 1024],
    [1344, 768],
    [768, 1344],
    [1152, 864],
    [864, 1152],
  ],
  // Offline fallback only: the served model carries the full H3 ladder from
  // backend/onecat/creative/output_sizes.py. These are its official canvases.
  video: [
    [1536, 672],
    [1344, 768],
    [1152, 768],
    [1024, 768],
    [768, 768],
    [768, 1024],
    [768, 1152],
    [768, 1344],
  ],
};

/** Both surfaces submit the same dimensions and frame counts. */
export function OutputFields({
  kind,
  value,
  onChange,
  model,
  legacySizes,
  loading = false,
}: {
  kind: "image" | "video";
  value: OutputSpec;
  onChange: (value: Partial<OutputSpec>) => void;
  model?: CreativeModel;
  legacySizes?: [number, number][];
  loading?: boolean;
}) {
  const t = useText(),
    sizes: Size[] = loading ? [[value.width, value.height]] : legacySizes || model?.sizes || fallbackSizes[kind];
  const selected = `${value.width}x${value.height}`;
  const ratio = aspectRatio(value.width, value.height);
  const ratios = [...new Set(sizes.map(([w, h]) => aspectRatio(w, h)))];
  const resolutions = sizesForRatio(sizes, ratio);
  const supported = sizes.some(([w, h]) => `${w}x${h}` === selected);
  return (
    <>
      <label>
        <span>{t("画面比例", "Aspect ratio")}</span>
        <select
          aria-label={t("画面比例", "Aspect ratio")}
          disabled={loading}
          value={ratio}
          onChange={(event) => {
            const size = sizeForRatio(sizes, [value.width, value.height], event.target.value);
            if (size) onChange({ width: size[0], height: size[1] });
          }}
        >
          {!ratios.includes(ratio) && <option value={ratio} disabled>{ratio}</option>}
          {ratios.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("分辨率", "Resolution")}</span>
        <select
          aria-label={t("分辨率", "Resolution")}
          disabled={loading}
          value={selected}
          onChange={(event) => {
            const [width, height] = event.target.value.split("x").map(Number);
            onChange({ width, height });
          }}
        >
          {!supported && <option value={selected} disabled>{value.width} × {value.height} · {t("请重新选择", "Choose a supported size")}</option>}
          {resolutions.map(([w, h]) => (
            <option key={`${w}x${h}`} value={`${w}x${h}`}>{w} × {h}</option>
          ))}
        </select>
        {!loading && supported && resolutions.length === 1 && <small>{t("此工作流仅支持该分辨率", "Only this resolution for this workflow")}</small>}
      </label>
      {kind === "video" && (
        <label>
          <span>{t("时长", "Duration")}</span>
          <select
            aria-label={t("时长", "Duration")}
            disabled={loading}
            value={value.num_frames}
            onChange={(event) =>
              onChange({ num_frames: Number(event.target.value) })
            }
          >
            {model && !model.frames.includes(value.num_frames) && (
              <option value={value.num_frames} disabled>{t("请选择时长", "Choose a duration")}</option>
            )}
            {(loading ? [value.num_frames] : model?.frames || [107, 147, 243, 360]).map((frames) => (
              <option key={frames} value={frames}>
                {(frames / 24).toFixed(1)} s
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  );
}

export function CreativePrompt({
  value,
  onChange,
  inputRef,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  inputRef?: Ref<HTMLTextAreaElement>;
  placeholder?: string;
}) {
  const t = useText();
  return (
    <textarea
      ref={inputRef}
      aria-label={t("创作提示词", "Creative prompt")}
      value={value}
      maxLength={20000}
      onChange={(event) => onChange(event.target.value)}
      placeholder={
        placeholder ||
        t(
          "描述画面、光线、动作和你想保留的细节…",
          "Describe the scene, light, movement and details…",
        )
      }
    />
  );
}
