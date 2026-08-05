import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type Step={target_force:number;sample_count:number;quality_label:string;quality_score:number;heart_rate_bpm:number|null;pulse_amplitude:number|null};
type Result={manifest:{session_id:string;statistics:{frame_count:number;missing_frame_count:number;crc_error_count:number}};report:{analysis_allowed:boolean;best_target_force:number|null;steps:Step[];disclaimer:string}};
type D2Step={step_index:number;target_force_au:number;quality_label:string;stable_sample_count:number;score:number|null};
type D2Report={analysis_allowed:boolean;best_target_force_au:number|null;calibration_id:string;report_sha256:string;steps:D2Step[];disclaimer:string};

function App(){
 const [health,setHealth]=useState('检查中'); const [device,setDevice]=useState('握手中'); const [result,setResult]=useState<Result|null>(null); const [d2,setD2]=useState<D2Report|null>(null); const [busy,setBusy]=useState(false); const [d2Busy,setD2Busy]=useState(false); const [error,setError]=useState(''); const [wave,setWave]=useState<number[]>([]); const [force,setForce]=useState<number[]>([]);
 useEffect(()=>{
  fetch('/api/health').then(r=>r.json()).then(x=>setHealth(`${x.stage}服务正常`)).catch(()=>setHealth('服务未连接'));
  fetch('/api/device/d1-demo?fragment_size=5').then(r=>r.json()).then(x=>{
   const ready=x.connected&&x.exchanges.every((e:{status:string})=>e.status==='ACK');
   setDevice(ready?'虚拟设备已握手':'设备协议异常');
  }).catch(()=>setDevice('虚拟设备未连接'));
 },[]);
 function run(){setBusy(true);setError('');setResult(null);setWave([]);setForce([]);const scheme=location.protocol==='https:'?'wss':'ws';const ws=new WebSocket(`${scheme}://${location.host}/ws/simulate`);ws.onopen=()=>ws.send(JSON.stringify({sample_rate_hz:250,heart_rate_bpm:72,target_forces:[40,80,120],stabilize_s:.8,acquire_s:5}));ws.onmessage=(event)=>{const message=JSON.parse(event.data);if(message.type==='samples'){setWave(v=>[...v,...message.data.map((x:{pulse:number})=>x.pulse)].slice(-300));setForce(v=>[...v,...message.data.map((x:{force:number})=>x.force)].slice(-300));}if(message.type==='complete'){setResult({manifest:message.manifest,report:message.report});setBusy(false);}if(message.type==='error'){setError(message.message);setBusy(false);}};ws.onerror=()=>{setError('实时连接失败');setBusy(false);};}
 function runD2(){setD2Busy(true);setError('');fetch('/api/experiments/d2/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_forces_au:[40,80,120],sample_rate_hz:250,seed:20260805})}).then(r=>{if(!r.ok)throw new Error(`D2请求失败：${r.status}`);return r.json()}).then(x=>setD2(x)).catch(e=>setError(e.message)).finally(()=>setD2Busy(false));}
 function points(values:number[],height:number){if(values.length<2)return '';const min=Math.min(...values),max=Math.max(...values),span=max-min||1;return values.map((v,i)=>`${i/(values.length-1)*100},${height-(v-min)/span*height}`).join(' ')}
 return <main><header><div><p className="eyebrow">ADAPTIVE RADIAL PULSE · D2</p><h1>标定与多压力数字实验</h1><p>设备握手 → 合成标定 → 稳态门控 → 压力响应</p></div><div className="statusGroup"><span className="status">{health}</span><span className="status">{device}</span></div></header>
 <section className="controls"><div><h2>D2合成工程实验</h2><p>force_au · 仿射标定 · 40/80/120相对平台</p></div><button onClick={runD2} disabled={d2Busy}>{d2Busy?'实验中…':'运行D2实验'}</button></section>
 {d2&&<><section className="metrics"><article><small>标定</small><strong>{d2.calibration_id}</strong></article><article><small>分析门控</small><strong>{d2.analysis_allowed?'通过':'阻断'}</strong></article><article><small>候选平台</small><strong>{d2.best_target_force_au??'—'} au</strong></article><article><small>报告摘要</small><strong>{d2.report_sha256.slice(0,8)}</strong></article></section><section><h2>D2压力步骤</h2><div className="steps">{d2.steps.map(s=><article key={`${s.step_index}-${s.target_force_au}`}><h3>{s.target_force_au} au</h3><p>质量：{s.quality_label}</p><p>稳态样本：{s.stable_sample_count}</p><p>评分：{s.score?.toFixed(3)??'已阻断'}</p></article>)}</div><footer>{d2.disclaimer}</footer></section></>}
 <section className="controls"><div><h2>模拟会话</h2><p>250 Hz · 72 bpm · 三个研究用载荷平台</p></div><button onClick={run} disabled={busy}>{busy?'采集中…':'开始模拟采集'}</button></section>
 {wave.length>1&&<section><h2>实时数据</h2><p>脉搏通道</p><svg className="chart" viewBox="0 0 100 50" preserveAspectRatio="none"><polyline points={points(wave,50)}/></svg><p>载荷通道</p><svg className="chart force" viewBox="0 0 100 50" preserveAspectRatio="none"><polyline points={points(force,50)}/></svg></section>}
 {error&&<p className="error">{error}</p>}{result&&<><section className="metrics"><article><small>有效分析</small><strong>{result.report.analysis_allowed?'通过':'阻断'}</strong></article><article><small>完整帧</small><strong>{result.manifest.statistics.frame_count}</strong></article><article><small>丢帧</small><strong>{result.manifest.statistics.missing_frame_count}</strong></article><article><small>最佳模拟载荷（相对值）</small><strong>{result.report.best_target_force===null?'—':result.report.best_target_force/1000}</strong></article></section>
 <section><h2>各载荷平台结果</h2><div className="steps">{result.report.steps.map(s=><article key={s.target_force}><div className="bar" style={{height:`${Math.max(10,(s.pulse_amplitude??0)/2000)}px`}}/><h3>{s.target_force/1000}</h3><p>质量：{s.quality_label}</p><p>心率：{s.heart_rate_bpm?.toFixed(1)??'已阻断'} bpm</p></article>)}</div></section><footer>{result.report.disclaimer}</footer></>}
 {!result&&<section className="empty"><div className="pulse">∿</div><h2>尚未生成会话</h2><p>启动模拟后将显示数据完整性、质量门控和压力响应。</p></section>}</main>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
