// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Activity, ChevronDown, Cpu, Layers, Thermometer } from "lucide-react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useQuery, type GPU, type DecodeAggregate } from "./api";
import { ErrorNotice, format, useText } from "./common";

type Point = {timestamp: number; board_w: Record<string, number | null>};
type Live = { available: boolean; stale: boolean; timestamp?: number; error?: string; gpus: GPU[];
  model_gpu_uuids: string[]; history: Point[] };
export type ModeChange = { at: number; mode: string | null; uuids: string[] };
type Props = { changes: ModeChange[]; devices: {uuid: string; confirmed_mode?: string | null}[];
  pending?: {mode: string | null; uuids: string[]} };

export function LiveGPUs({changes, devices, pending}: Props) {
  const t = useText();
  const {data, error} = useQuery<Live>("/api/gpu/live", 1000);
  const {data: decode, error: decodeError} = useQuery<DecodeAggregate>("/api/requests/aggregate?window_s=2", 1000);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [selected, setSelected] = useState<string>();
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  const gpus = data?.gpus || [];
  const assigned = data?.model_gpu_uuids || [];
  const main = assigned.length ? gpus.filter(g => assigned.includes(g.uuid)) : gpus;
  const other = assigned.length ? gpus.filter(g => !assigned.includes(g.uuid)) : [];
  const focus = gpus.find(g => g.uuid === selected);
  const ids = focus ? [focus.uuid] : assigned.length ? assigned : main.map(g => g.uuid);
  const fresh = !!data?.available && !data.stale && !error && now - (data.timestamp || 0) < 3;
  const rateFresh = decode && !decodeError && now - decode.at < 5;
  function sum(values: (number | undefined | null)[]) {
    return values.length && values.every((v): v is number => v != null) ? values.reduce((a,b) => a+b, 0) : null;
  }
  const allBoards = !assigned.length && !focus;
  const readings = ids.map(id => gpus.find(g => g.uuid === id)?.power_w);
  const reporting = readings.filter((w): w is number => w != null);
  const power = fresh ? sum(allBoards ? reporting : readings) : null;
  const limit = sum(ids.map(id => gpus.find(g => g.uuid === id)?.power_limit_w));
  const points = (data?.history || []).map(p => {
    const samples = ids.map(id => p.board_w?.[id]);
    return {time: p.timestamp, watts: sum(allBoards ? samples.filter(w => w != null) : samples)};
  });
  const end = data?.timestamp || now;
  const plot = useRef<HTMLDivElement>(null);
  const previous = useRef(end);
  useLayoutEffect(() => {
    const elapsed = end - previous.current;
    previous.current = end;
    const node = plot.current;
    if (!node || !fresh || elapsed <= 0 || elapsed > 3 || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const width = node.querySelector(".recharts-cartesian-grid")?.getBoundingClientRect().width || 0;
    const offset = width * elapsed / 120;
    const animations = Array.from(node.querySelectorAll(".recharts-area, .recharts-reference-line")).map(el =>
      el.animate([{transform: `translateX(${offset}px)`}, {transform: "translateX(0)"}],
        {duration: Math.min(1000, elapsed * 1000), easing: "linear"}));
    return () => animations.forEach(a => a.cancel());
  }, [end, fresh]);
  function modeName(id?: string | null) {
    if (id === "eco") return t("省电", "Eco");
    if (id === "balanced") return t("均衡", "Balanced");
    if (id === "performance") return t("性能", "Performance");
    return t("手动", "Manual");
  }
  const markers = changes.filter(c => c.at >= end - 120 && c.at <= end && c.uuids.some(id => ids.includes(id)));
  const scopeLabel = focus ? "GPU " + focus.index : assigned.length ? t("推理卡总功率", "Inference board power") : reporting.length < ids.length ? t("已测 GPU 功率", "Measured GPU power") : t("全部 GPU 总功率", "All GPU power");
  function board(g: GPU) {
    const open = focus?.uuid === g.uuid;
    const confirmed = devices.find(d => d.uuid === g.uuid)?.confirmed_mode;
    const verifying = pending?.uuids.includes(g.uuid);
    const memory = fresh && g.memory_used_mib != null && g.memory_total_mib ? g.memory_used_mib / g.memory_total_mib : null;
    return <article key={g.uuid} className={'perf-board' + (open ? ' selected' : '')}>
      <button type="button" className="perf-board-toggle" aria-expanded={open} onClick={() => setSelected(open ? undefined : g.uuid)}>
        <span className="perf-slot"><Cpu size={16}/>GPU {g.index}</span>
        <span className="perf-board-name"><b>{g.name.replace(/^Tesla /,"")}</b>
          <small>{verifying ? (pending?.mode && confirmed === pending.mode ? t("读数已确认","Readback confirmed") : t("等待确认","Awaiting confirmation")) : confirmed ? modeName(confirmed) : t("设备读数","Device readings")}</small>
        </span>
        <span className="perf-row-w">{format(fresh ? g.power_w : null, 0)} <small>W</small></span>
        <span className="perf-temp"><Thermometer size={14}/>{format(fresh ? g.temperature_c : null, 0)} °C</span>
        <span className="perf-memory"><small>{t("显存","VRAM")} {format(memory == null ? null : memory * 100,0)}%</small>
          <span className="perf-track"><i style={{width: (memory == null ? 0 : Math.min(100, memory * 100)) + "%"}}/></span>
        </span>
        <ChevronDown size={16} className="perf-disclose"/>
      </button>
      {open && <div className="perf-board-detail">
        <span>{t("显存","VRAM")} <b>{format(fresh && g.memory_used_mib != null ? g.memory_used_mib/1024 : null, 1)} / {format(g.memory_total_mib == null ? null : g.memory_total_mib/1024, 0)} GiB</b></span>
        <span>{t("利用率","Utilization")} <b>{format(fresh ? g.utilization : null,0)}%</b></span>
        <span>{t("核心频率","Clock")} <b>{format(fresh ? g.graphics_clock_mhz : null,0)} MHz</b></span>
        <span>{t("功率上限","Power limit")} <b>{format(fresh ? g.power_limit_w : null,0)} W</b></span>
      </div>}
    </article>;
  }
  return <>
    <section className="perf-signal" aria-label={t("实时 GPU 监测","Live GPU telemetry")}>
      <header><h2><Activity size={18}/>{t("实时功率","Live power")}</h2>
        <span className={'perf-fresh'+(fresh?' online':'')}><i/>{fresh?t("实时采样","Live"):data?t("采样已暂停","Paused"):t("等待读数","Awaiting data")}</span>
      </header>
      <div className="perf-signal-body">
        <div className="perf-metrics">
          <span>{scopeLabel}</span>
          <strong data-testid="live-power">{format(power,0)}<small> W</small></strong>
          <div className="perf-limit">{t("功率上限","Power limit")} {format(fresh ? limit : null,0)} W</div>
          {fresh && reporting.length < ids.length && <div className="perf-limit" data-testid="power-coverage">{t("功率读数","Power readings")} {reporting.length}/{ids.length} GPU</div>}
          <div className="perf-decode"><span>{t("整机生成速度","Total decode rate")}</span>
            {rateFresh && decode.decode_tokens_s != null && decode.active_requests > 0 ? <b>{format(decode.decode_tokens_s,1)} <small>tok/s</small></b>
             : <b>{rateFresh && decode.active_requests === 0 ? t("等待请求","Idle") : t("等待读数","Awaiting data")}</b>}
          </div>
        </div>
        <div className="perf-plot" ref={plot}>
          <ResponsiveContainer width="100%" height={194} minWidth={0}>
            <AreaChart data={points} margin={{top:22,right:12,bottom:0,left:0}}>
              <defs><linearGradient id="perf-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#a3e2bd" stopOpacity={0.34}/><stop offset="100%" stopColor="#a3e2bd" stopOpacity={0.02}/></linearGradient></defs>
              <CartesianGrid stroke="#ffffff20" strokeDasharray="3 6" vertical={false}/>
              <XAxis dataKey="time" type="number" domain={[end-120,end]} allowDataOverflow tickLine={false} axisLine={false} tick={{fontSize:10,fill:"#b7c8be"}} tickFormatter={v=>new Date(v*1000).toLocaleTimeString([], {minute:"2-digit",second:"2-digit"})} minTickGap={32}/>
              <YAxis domain={[0,(max:number)=>Math.max(50,Math.ceil(Math.max(max,limit || 0)/50)*50)]} tick={{fontSize:10,fill:"#b7c8be"}} tickLine={false} axisLine={false} width={38}/>
              <Tooltip contentStyle={{background:"#182c23", border:"1px solid #6d8f7a", borderRadius:8,color:"#fff"}} labelFormatter={v=>new Date(Number(v)*1000).toLocaleTimeString()} formatter={v=>[format(Number(v),1)+" W",scopeLabel]}/>
              <Area dataKey="watts" stroke="#b0f3c1" fill="url(#perf-fill)" strokeWidth={2.5} type="linear" connectNulls={false} isAnimationActive={false}/>
              {markers.map((m,i)=><ReferenceLine key={m.at} x={m.at} stroke="#ffab81" strokeDasharray="4 4" label={i===0?{value:modeName(m.mode),position:"insideTop",fill:"#ffd6bd",fontSize:11}:undefined}/>)}
            </AreaChart>
          </ResponsiveContainer>
          <footer><span><i/>{scopeLabel}</span><span>{t("最近 2 分钟","Last 2 minutes")}</span></footer>
        </div>
      </div>
      {focus && <button className="perf-reset" onClick={()=>setSelected(undefined)}>{t("返回总览","Back to overview")}</button>}
    </section>
    <ErrorNotice error={error || data?.error}/>
    <section className="perf-rack">
      <header><h2><Layers size={18}/>{assigned.length ? t("推理显卡","Inference GPUs") : t("显卡","GPUs")}</h2><small>{main.length} GPU</small></header>
      {main.map(board)}
      {!main.length && <p>{t("等待 GPU 设备数据","Waiting for GPU inventory")}</p>}
      {other.length > 0 && <details onToggle={e=>{if(!e.currentTarget.open && other.some(g=>g.uuid===selected)) setSelected(undefined);}}>
        <summary>{t("其他设备","Other devices")} ({other.length})</summary>{other.map(board)}
      </details>}
    </section>
  </>;
}
