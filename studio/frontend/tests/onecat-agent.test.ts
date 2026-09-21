import assert from "node:assert/strict";
import test from "node:test";
import { reduceAgent, activeAgent, latestAgentAnswer, agentItemText, type AgentTask } from "../src/onecat/agent-state.ts";
import { fileArtifact } from "../src/onecat/artifacts.ts";
const snapshot: AgentTask = {
  id:"task",project_id:"project",title:"Test",state:"running",model:"local",created_at:0,updated_at:0,elapsed_s:0,
  items:[{id:"old",type:"agentMessage",text:"stable",finished:true},{id:"new",type:"agentMessage",text:"中"}],
  approvals:[],model_calls:1,usage:{input_tokens:42,output_tokens:9,cached_tokens:0,missing_calls:0,cache_missing_calls:1},
};
test("incremental text preserves previous item identity and exact Unicode",()=>{
 const result=reduceAgent(snapshot,{type:"batch",events:[{type:"delta",id:"new",field:"text",delta:"文👨‍👩‍👧"},{type:"delta",id:"new",field:"text",delta:" code"}]})!;
 assert.equal(result.items[0],snapshot.items[0]); assert.equal(result.items[1].text,"中文👨‍👩‍👧 code");
 assert.equal(snapshot.items[1].text,"中"); assert.equal(result.usage,snapshot.usage);
});
test("a reconnect snapshot replaces history rather than appending duplicates",()=>{
 const modified=reduceAgent(snapshot,{type:"delta",id:"new",field:"text",delta:"tail"});
 assert.equal(reduceAgent(modified,{type:"snapshot",task:snapshot}),snapshot);
});
test("completed items update in place and stats stay task scoped",()=>{
 const result=reduceAgent(snapshot,{type:"item",item:{id:"new",type:"agentMessage",text:"done",finished:true}})!;
 assert.equal(result.items.length,2);assert.equal(result.items[0],snapshot.items[0]);assert.equal(result.items[1].text,"done");
 assert.equal(activeAgent("waiting"),true);assert.equal(activeAgent("interrupted"),false);
 assert.equal(reduceAgent(undefined,{type:"delta",id:"none",field:"text",delta:"x"}),undefined);
});

test("project JS/CSS previews retain their executable wrapper on every update",()=>{
 for (const text of ["document.body.textContent='first'", "document.body.textContent='updated'"]) {
  const result=fileArtifact("app.JS",text)!;
  assert.equal(result.language,"html"); assert.equal(result.source,`<script>${text}</script>`);
 }
 assert.equal(fileArtifact("app.css","body{color:red}")!.source,"<style>body{color:red}</style>");
 const jsx='export default ()=> <pre>{"```html"}</pre>';
 assert.equal(fileArtifact("App.tsx",jsx)!.source,jsx);
 assert.equal(fileArtifact("main.py","print(1)"),undefined);
});

test("live stats preserve transcript identity and end without moving the transcript", () => {
 const live = reduceAgent(snapshot, {type:"live",live_metrics:{output_tokens:128,elapsed_s:2,decode_tokens_s:74.5}})!;
 assert.equal(live.items,snapshot.items);
 const ended = reduceAgent(live,{type:"usage",live_metrics:null,usage:{...snapshot.usage,output_tokens:128}})!;
 assert.equal(ended.items,snapshot.items);assert.equal(ended.live_metrics,null);
 assert.equal(snapshot.usage.output_tokens,9);
});

import { parseAgentCommand, matchingAgentCommands } from "../src/onecat/agent-commands.ts";
test("slash commands match complete names and preserve multiline arguments", () => {
 assert.deepEqual(parseAgentCommand(" /review inspect\nall files "),{name:"review",argument:"inspect\nall files"});
 assert.deepEqual(parseAgentCommand("/permissions read-only"),{name:"permissions",argument:"read-only"});
 assert.equal(parseAgentCommand("Describe /review"),null);
 assert.deepEqual(matchingAgentCommands("/re").map(c=>c[0]),["resume","review"]);
 assert.deepEqual(matchingAgentCommands("/review argument"),[]);
 assert.equal(matchingAgentCommands("/does-not-exist").length,0);
});

test("copy selects the newest plan/review and export includes command and output", () => {
 const items = [...snapshot.items, {id:"plan", type:"plan", text:"New proposed plan"}];
 assert.equal(latestAgentAnswer(items), "New proposed plan");
 assert.equal(latestAgentAnswer([...items, {id:"review",type:"exitedReviewMode",review:"Latest review"}]), "Latest review");
 assert.equal(agentItemText({id:"tool",type:"commandExecution",command:"pytest",aggregatedOutput:"passed"}), "pytest\n\npassed");
});
