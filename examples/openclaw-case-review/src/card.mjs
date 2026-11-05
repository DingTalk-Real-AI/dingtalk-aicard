export const CATALOG='https://dingtalk.com/card/a2ui/catalogs/public/catalog.json';
const bind=path=>({path});
const text=(id,value,extra={})=>({id,component:'Text',text:value,...extra});
const md=(id,content)=>({id,component:'Markdown',content});
const event=(name,context)=>({event:{name,context}});
const displayText=(value,fallback='未核实',limit=2000)=>typeof value==='string'&&value.trim()?value.slice(0,limit):fallback;
const listText=(items,fallback='未核实')=>Array.isArray(items)&&items.length
  ? items.filter(x=>typeof x==='string'&&x.trim()).slice(0,12).map(x=>`- ${x.slice(0,500)}`).join('\n')||fallback
  : fallback;

export function buildCard(card,{actionsEnabled=false}={}) {
  if(card.mode!=='sandbox') throw new Error('PRODUCTION_NOT_IMPLEMENTED');
  const report=card.report??{};
  const surfaceId=`case-highlight-${card.id}`;
  const components=[
    {id:'root',component:'Column',gap:12,children:['title','notice','progress','meetings','warning','decision-title','decision','start-title','start','end-title','end','submit','submit-label-note','document']},
    text('title','案例亮点｜交互测试', {bold:true}),
    md('notice','**独立测试数据，非正式汇报。** 本卡不修改真实文档、日程或参会名单。'),
    md('progress',`### 项目状态\n${displayText(report.stage,'未核实：尚未读取主文档中的当前状态')}\n\n会议月份：${displayText(report.meetingMonth,card.cycle)}\n目标发布月份：${displayText(report.targetReleaseMonth)}\n候选案例：\n${listText(report.candidates,'未核实：候选案例尚未读取')}\n待办与风险：\n${listText(report.risks,'未核实：风险和待办尚未读取')}`),
    md('meetings',`**会议周期：${card.cycle}**\n\n初筛会：${displayText(report.initialMeeting,'未核实：尚未读取对应日程')}\n\n兜底会当前日程：${card.meeting.start.slice(0,10)} ${card.meeting.start.slice(11,16)}—${card.meeting.end.slice(11,16)}（沙盒副本，未修改真实日程）\n\n参会人核对：${displayText(report.attendeeStatus,'未核实：尚未交叉核对文档与日历')}\n\n人工裁决：${displayText(report.manualDecision,'未核实：尚未读取文档裁决字段')}`),
    md('warning','**操作范围：仅此测试兜底会。** 先选择并检查时间，再提交变更摘要，经二次确认后才执行。'),
    text('decision-title','是否召开兜底会'),
    {id:'decision',component:'ChoicePicker',variant:'mutuallyExclusive',displayStyle:'chips',label:'请选择',options:[{label:'确认召开',value:'open'},{label:'本月不开',value:'closed'}],value:bind('/form/decision')},
    text('start-title','开始日期与时间（选择召开时填写）'),
    {id:'start',component:'DateTimeInput',enableDate:true,enableTime:true,label:'开始日期与时间',value:bind('/form/start')},
    text('end-title','结束日期与时间（选择召开时填写）'),
    {id:'end',component:'DateTimeInput',enableDate:true,enableTime:true,label:'结束日期与时间',value:bind('/form/end')},
    {id:'submit',component:'Button',child:'submit-label',variant:'primary',disabled:!actionsEnabled,checks:[{condition:{call:'required',args:{value:bind('/form/decision')}},message:'请选择是否召开'}],action:event('project_review.propose',{cardId:card.id,nonce:card.nonce,revision:card.revision,form:bind('/form')})},
    text('submit-label','检查变更'),
    text('submit-label-note',actionsEnabled?'仅提交变更提案；真实写入还需查看摘要并再次确认。':'交互通道尚未验证：按钮停用，不能提交。',{variant:'caption',maxLine:5}),
    md('document',displayText(report.documentLink,'正式主文档链接未核实'))
  ];
  return [
    {version:'v1.0',createSurface:{surfaceId,catalogId:CATALOG}},
    {version:'v1.0',updateDataModel:{surfaceId,path:'/',value:{form:{decision:[],start:card.meeting.start,end:card.meeting.end},ui:{status:'transport_not_connected'}}}},
    {version:'v1.0',updateComponents:{surfaceId,components}}
  ];
}
export function statusDelta(messages,statusText) {
  return [{version:'v1.0',updateComponents:{surfaceId:messages[0].createSurface.surfaceId,components:[text('submit-label-note',statusText,{variant:'caption',maxLine:5})]}}];
}

/** A state-preserving confirmation view. Disabled until an authenticated transport exists. */
export function confirmationDelta(messages,card,proposal,{actionsEnabled=false}={}) {
  const surfaceId=messages[0].createSurface.surfaceId;
  return [{version:'v1.0',updateComponents:{surfaceId,components:[
    {id:'root',component:'Column',gap:12,children:['title','confirm-summary','confirm-notice','confirm-button','back-button']},
    text('title','兜底会｜确认测试变更',{bold:true}),
    md('confirm-summary',proposal.summary),
    md('confirm-notice','只修改独立沙盒，不影响真实会议。确认令牌五分钟内有效；返回不写入裁决。'),
    {id:'confirm-button',component:'Button',child:'confirm-label',variant:'primary',disabled:!actionsEnabled,action:event('project_review.confirm',{cardId:card.id,nonce:card.nonce,revision:card.revision,confirmationId:proposal.confirmationId})},
    text('confirm-label','确认测试变更'),
    {id:'back-button',component:'Button',child:'back-label',disabled:!actionsEnabled,action:event('project_review.back',{cardId:card.id,nonce:card.nonce,revision:card.revision})},
    text('back-label','返回修改')
  ]}}];
}

export function backDelta(messages) {
  const original=messages.find(m=>m.updateComponents).updateComponents;
  return [{version:'v1.0',updateComponents:{surfaceId:original.surfaceId,components:original.components}}];
}

export function resultDelta(messages,result) {
  if(!['sandbox','dws'].includes(result.mode))throw new Error('UNKNOWN_RESULT_MODE');
  return [{version:'v1.0',updateComponents:{surfaceId:messages[0].createSurface.surfaceId,components:[
    {id:'root',component:'Column',gap:12,children:['title','result','result-scope']},
    text('title',result.mode==='dws'
      ? (result.state==='completed'?'兜底会｜DWS 处理完成':'兜底会｜DWS 处理未完成')
      : (result.state==='completed'?'兜底会｜沙盒处理完成':'兜底会｜沙盒处理未完成'),{bold:true}),
    md('result',result.message),
    text('result-scope',result.mode==='dws'?'已按部署配置执行并完成写后回读。':'测试结果，不代表真实文档或日程已改变。',{variant:'caption',maxLine:5})
  ]}}];
}
