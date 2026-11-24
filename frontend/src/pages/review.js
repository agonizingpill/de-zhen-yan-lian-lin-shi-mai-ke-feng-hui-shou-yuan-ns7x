import {el} from '../dom.js';
import {mixer} from '../audio-api.js';
import {desk,outcomes,seats,verdicts} from '../audio-state.js';
import {choices,field,run} from '../audio-form.js';
import {status,titleBlock} from '../widgets.js';
import {badge} from '../components/badge.js';

const LISTENERS=new Set(['reviewer','reviewer2']);

function verdictTag(value){
  return value?el('span',{class:'signal-tag','data-state':value},verdicts[value]):el('span',{},'—');
}

function metricLine(metrics){
  const rate=metrics.miss_rate===null?'暂无（无有定论的真啸叫窗）':(metrics.miss_rate*100).toFixed(1)+'%';
  return el('div',{class:'note'},
    el('p',{},'有定论窗 '+metrics.decided_windows+' / 共 '+metrics.total_windows+
      '，剔除 '+metrics.excluded_windows+'（待裁定 '+metrics.unresolved_windows+
      '，未交齐 '+metrics.pending_windows+'）'),
    el('p',{},'真啸叫 '+metrics.true_howl_windows+' 窗，机器漏报 '+metrics.missed_windows+
      ' 窗，虚警 '+metrics.false_alarm_windows+' 窗'),
    el('p',{class:'mono'},'漏报率 = '+rate));
}

export async function review(root,role){
  const board=el('section',{class:'ledger'});
  const edit=el('section',{class:'action'});
  const isListener=LISTENERS.has(role);
  const isArbiter=role==='arbiter';

  async function load(note){
    const batches=await mixer.reviewBatches();
    board.replaceChildren(el('h2',{},'漏报复核批次'),
      ...batches.map(item=>el('p',{class:'note'},el('button',{type:'button',class:'quiet',
        onclick:()=>{desk.batch=item.code;select(item.code);}},item.code),
        ' · '+item.total_windows+' 窗 · 甲 '+item.submitted.a+' / 乙 '+item.submitted.b+
        (item.complete?' · 已交齐':' · 未交齐')+' · 封存 '+item.version_count+' 版')));
    if(!desk.batch&&batches.length) desk.batch=batches[0].code;
    if(desk.batch) await select(desk.batch,note);
  }

  async function select(code,note){
    const detail=await mixer.reviewBatch(code);
    edit.replaceChildren(el('h2',{},'批次 '+code),note?status(note,'ok'):null);

    if(isListener){
      const findings=await mixer.findings();
      const used=new Set(detail.windows.map(x=>x.finding));
      const available=findings.filter(x=>!used.has(x.code));
      if(available.length){
        edit.append(run('review-batch',[
          field('code','新批次编号',{placeholder:'RV-1124-01'}),
          choices('findings','纳入的已签诊断（按住多选）',
            available.map(x=>[x.code,x.code+' · '+x.focus_window]),undefined,true),
        ],'建复核批次',async form=>{
          const selected=form.getAll('findings');
          const item=await mixer.reviewCreate({code:form.get('code'),findings:selected});
          desk.batch=item.code;
          await load(item.code+' 已建立，等待两名复核员各自听音。');
        }));
      }
    }

    const rows=el('div',{});
    detail.windows.forEach(w=>{
      const locked=w.complete;
      const mySeat=role==='reviewer'?'a':role==='reviewer2'?'b':null;
      const mine=mySeat?w.listens[mySeat]:null;
      const block=el('div',{class:'note'},
        el('h3',{class:'mono'},w.window+' · '+w.point+' · 刻度 '+w.slot+' · '+w.frequency+' Hz'),
        el('p',{},'机器判定：',locked
          ?el('span',{},(w.machine_howl?'判为啸叫':'未告警')+'（'+w.machine_category+'）')
          :el('span',{class:'signal-tag'},'交齐前隐藏')));
      if(locked){
        block.append(el('p',{},'甲：',verdictTag(w.listens.a.verdict),' 乙：',verdictTag(w.listens.b.verdict),
          w.agreement===false?el('span',{class:'signal-tag','data-state':'unresolved'},'意见相反'):
            el('span',{class:'signal-tag','data-state':'normal'},'意见一致')));
        if(w.arbitration) block.append(el('p',{},'第三人裁定：',verdictTag(w.arbitration.verdict),
          w.arbitration.note?el('span',{},'（'+w.arbitration.note+'）'):null));
      }else if(mine){
        block.append(el('p',{},'我的记录（'+seats[mySeat]+'）：',verdictTag(mine.verdict),
          el('span',{},' 对方记录与机器判定交齐前不可见')));
      }else if(isListener){
        block.append(status('该窗你尚未提交听音记录。'));
      }
      if(isListener&&!locked){
        block.append(run('listen-'+w.window,[
          choices('verdict','现场听音结论',[['howl','有啸叫'],['clear','无啸叫']],
            mine?mine.verdict:undefined),
          field('note','听音备注（可选）',{placeholder:'现场环境、听感位置',...(mine?{value:mine.note||''}:{})}),
        ],mine?'更新我的听音记录':'提交听音记录',async form=>{
          await mixer.reviewListen({batch:code,window:w.window,verdict:form.get('verdict'),
            note:form.get('note')||null});
          await load(w.window+' 听音记录已保存。');
        }));
      }
      if(isArbiter&&locked&&w.agreement===false&&!w.arbitration){
        block.append(run('arb-'+w.window,[
          choices('verdict','第三人裁定',[['howl','有啸叫'],['clear','无啸叫']]),
          field('note','裁定依据（可选）',{placeholder:'复听位置与判断'}),
        ],'提交第三人裁定',async form=>{
          await mixer.reviewArbitrate({batch:code,window:w.window,verdict:form.get('verdict'),
            note:form.get('note')||null});
          await load(w.window+' 裁定已生效。');
        }));
      }
      rows.append(block);
    });
    edit.append(rows);

    if(detail.versions.length){
      edit.append(el('h2',{},'封存版本（漏报率与逐窗依据同时点快照）'),
        ...detail.versions.map(v=>el('details',{class:'clip'},
          el('summary',{},'v'+v.version+' · '+v.sealed_at.replace('T',' ').slice(0,19)+
            ' · '+v.sealed_by),metricLine(v.metrics),
          el('div',{class:'scroll'},el('table',{},
            el('thead',{},el('tr',{},...['声检窗','机器','最终听音','结论','依据'].map(x=>el('th',{},x)))),
            el('tbody',{},v.windows.map(x=>el('tr',{},
              el('td',{},x.window),
              el('td',{},x.machine_howl?'啸叫':'未告警'),
              el('td',{},x.final_verdict?verdicts[x.final_verdict]:'—'),
              el('td',{},badge(outcomes,x.outcome)),
              el('td',{},x.basis)))))))));
    }
    if(isListener||isArbiter){
      edit.append(run('seal-'+code,[],'封存当前时点',async()=>{
        const r=await mixer.reviewSeal({batch:code});
        await load(r.retried?'逐窗结论未变化：仍是 v'+r.version.version+'，未新增版本。':
          '已封存 v'+r.version.version+'，漏报率 '+(
            r.version.metrics.miss_rate===null?'不计':(r.version.metrics.miss_rate*100).toFixed(1)+'%'));
      }));
    }
  }

  root.replaceChildren(titleBlock('安澜 · 啸叫漏报复核','双盲听音与封存',
    '两名复核员各交现场听音记录，交齐前互不可见且看不到机器判定；意见相反交第三人裁定，无定论不计漏报率。封存取同一时点快照，重试不新增版本。'),
    el('div',{class:'mix'},board,edit));
  await load();
}
