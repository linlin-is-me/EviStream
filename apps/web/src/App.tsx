import { FormEvent, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes, useParams } from 'react-router-dom'
import { api, terminalJobs } from './api'

type Page<T> = { items: T[]; next_cursor: number | null }
type Profile = { name: string; gateway: string; configured: boolean; models: Record<string, string | null> }
type Video = { video_id: string; original_name: string; status: string; triage_status: string; model_profile: string; duration_ms: number }
type Job = { job_id: string; status: string; attempt: number; max_attempts: number; retryable: boolean; error_code?: string; error_message?: string }
type Case = { case_id: string; video_id: string; policy_id: string; policy_version: number; status: string; model_profile: string }
type Triage = { policy_id: string; policy_version: number; status: string; action?: string; reason_code?: string; case_id?: string }
type VideoDetail = { video: Video; job: Job | null; triage_checks: Triage[]; cases: Case[]; search_document_count: number }
type CaseDetail = { case: Case; requirements: Array<Record<string, unknown>>; current_decision: Record<string, unknown> | null; appeals: Array<Record<string, unknown>>; video_content_url: string }
type TimelineEvent = { event_type?: string; start_ms?: number; content?: string; id?: string }

function Layout() {
  return <div className="app-shell">
    <header className="topbar">
      <Link className="brand" to="/">EviStream</Link>
      <nav aria-label="Main navigation">
        <NavLink to="/">任务中心</NavLink>
        <NavLink to="/policies">规则与重放</NavLink>
      </nav>
    </header>
    <Routes>
      <Route path="/" element={<TaskCenter />} />
      <Route path="/cases/:caseId" element={<CaseWorkspace />} />
      <Route path="/policies" element={<PolicyWorkspace />} />
    </Routes>
  </div>
}

function usePolling<T>(path: string, active = true) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!active) return
    let cancelled = false
    const refresh = () => {
      if (document.hidden) return
      api<T>(path).then(value => { if (!cancelled) { setData(value); setError('') } }).catch(reason => { if (!cancelled) setError(String(reason)) })
    }
    refresh()
    const timer = window.setInterval(refresh, 2000)
    document.addEventListener('visibilitychange', refresh)
    return () => { cancelled = true; window.clearInterval(timer); document.removeEventListener('visibilitychange', refresh) }
  }, [active, path])
  return { data, error, setData }
}

function TaskCenter() {
  const profiles = usePolling<Page<Profile>>('/api/v1/model-profiles')
  const videos = usePolling<Page<Video>>('/api/v1/videos')
  const [profile, setProfile] = useState('mock')
  const [profileHealth, setProfileHealth] = useState<Record<string, unknown> | null>(null)
  const [message, setMessage] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const detail = usePolling<VideoDetail>(selected ? `/api/v1/videos/${selected}` : '', Boolean(selected))

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const input = form.elements.namedItem('video') as HTMLInputElement
    if (!input.files?.[0]) return
    const body = new FormData()
    body.append('file', input.files[0])
    body.append('model_profile', profile)
    try {
      const result = await api<{ video: Video }>('/api/v1/videos', { method: 'POST', body })
      setSelected(result.video.video_id)
      setMessage('上传已接受，后台任务已创建。')
      videos.setData(null)
    } catch (error) { setMessage(String(error)) }
  }

  async function retry(jobId: string) {
    try { await api(`/api/v1/jobs/${jobId}/retry`, { method: 'POST' }); setMessage('任务已重新投递。') }
    catch (error) { setMessage(String(error)) }
  }

  async function checkProfile() {
    try { setProfileHealth(await api(`/api/v1/model-profiles/${profile}/health`)); setMessage('模型档案健康检查通过。') }
    catch (error) { setProfileHealth(null); setMessage(String(error)) }
  }

  return <main className="workspace">
    <section className="hero"><p className="eyebrow">Stage 6 · Operations</p><h1>任务中心</h1><p>上传视频，观察媒体处理、索引和自动初筛。</p></section>
    <section className="grid two">
      <article className="card">
        <h2>新任务</h2>
        <form onSubmit={upload} className="stack">
          <label>模型档案<select value={profile} onChange={event => setProfile(event.target.value)}>{profiles.data?.items.map(item => <option key={item.name} value={item.name} disabled={!item.configured}>{item.name} · {item.gateway}</option>)}</select></label>
          <button type="button" className="secondary" onClick={checkProfile}>检查模型档案</button>
          {profileHealth && <pre>{JSON.stringify(profileHealth, null, 2)}</pre>}
          <label>视频文件<input name="video" type="file" accept="video/*" required /></label>
          <button type="submit">上传并处理</button>
        </form>
        {message && <p className="notice" role="status">{message}</p>}
      </article>
      <article className="card">
        <h2>视频</h2>
        {videos.error && <ErrorState message={videos.error} />}
        <div className="list">{videos.data?.items.map(item => <button className="list-row" key={item.video_id} onClick={() => setSelected(item.video_id)}><span>{item.original_name}</span><Status value={item.status} /><small>{item.triage_status}</small></button>)}</div>
      </article>
    </section>
    {detail.data && <section className="card detail">
      <div><h2>{detail.data.video.original_name}</h2><p>{detail.data.search_document_count} 条检索文档 · {detail.data.video.model_profile}</p></div>
      {detail.data.job && <div className="job"><Status value={detail.data.job.status} /><span>尝试 {detail.data.job.attempt}/{detail.data.job.max_attempts}</span>{detail.data.job.error_code && <code>{detail.data.job.error_code}</code>}{detail.data.job.retryable && !terminalJobs.has(detail.data.job.status) && <button onClick={() => retry(detail.data!.job!.job_id)}>立即重试</button>}</div>}
      <div className="triage-list">{detail.data.triage_checks.map(item => <div key={`${item.policy_id}-${item.policy_version}`}><strong>{item.policy_id} v{item.policy_version}</strong><span>{item.action ?? item.status}</span><small>{item.reason_code}</small></div>)}</div>
      <div className="case-links">{detail.data.cases.map(item => <Link key={item.case_id} to={`/cases/${item.case_id}`}>打开案件 {item.policy_id}</Link>)}</div>
    </section>}
  </main>
}

function CaseWorkspace() {
  const { caseId = '' } = useParams()
  const detail = usePolling<CaseDetail>(`/api/v1/cases/${caseId}`)
  const timeline = usePolling<TimelineEvent[]>(`/api/v1/cases/${caseId}/timeline`)
  const trace = usePolling<Record<string, unknown>>(`/api/v1/cases/${caseId}/trace`)
  const player = useRef<HTMLVideoElement>(null)
  const [message, setMessage] = useState('')

  async function investigate() {
    try { await api(`/api/v1/cases/${caseId}/investigate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); setMessage('调查任务已提交。') }
    catch (error) { setMessage(String(error)) }
  }

  async function governance(event: FormEvent<HTMLFormElement>, action: 'reviews' | 'appeals') {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const body = action === 'reviews'
      ? { reviewer: form.get('actor'), verdict: form.get('verdict'), note: form.get('text'), evidence_ids: [] }
      : { submitter: form.get('actor'), statement: form.get('text') }
    try { await api(`/api/v1/cases/${caseId}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); setMessage(action === 'reviews' ? '复核已保存。' : '申诉已提交。') }
    catch (error) { setMessage(String(error)) }
  }

  async function resolveAppeal(event: FormEvent<HTMLFormElement>, appealId: string) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    try {
      await api(`/api/v1/cases/${caseId}/appeals/${appealId}/resolve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewer: form.get('reviewer'), verdict: form.get('verdict'), note: form.get('note'), evidence_ids: [] }),
      })
      setMessage('申诉已解决。')
    } catch (error) { setMessage(String(error)) }
  }

  if (detail.error) return <main className="workspace"><ErrorState message={detail.error} /></main>
  if (!detail.data) return <main className="workspace"><p>加载案件…</p></main>
  return <main className="workspace">
    <section className="hero"><p className="eyebrow">Case workspace</p><h1>{detail.data.case.policy_id}</h1><p>{detail.data.case.case_id} · {detail.data.case.status}</p></section>
    <section className="grid case-grid">
      <article className="card media-panel"><video ref={player} controls src={detail.data.video_content_url} /><button onClick={investigate}>开始或恢复调查</button>{message && <p className="notice">{message}</p>}</article>
      <article className="card"><h2>正式结论</h2><pre>{JSON.stringify(detail.data.current_decision, null, 2)}</pre><h3>Requirements</h3>{detail.data.requirements.map((item, index) => <pre key={index}>{JSON.stringify(item, null, 2)}</pre>)}</article>
    </section>
    <section className="grid two">
      <article className="card"><h2>证据时间线</h2>{timeline.data?.map((event, index) => <button className="timeline-event" key={event.id ?? index} onClick={() => { if (player.current && typeof event.start_ms === 'number') player.current.currentTime = event.start_ms / 1000 }}><strong>{event.event_type ?? 'event'}</strong><span>{event.content ?? JSON.stringify(event)}</span></button>)}</article>
      <article className="card"><h2>Agent 轨迹</h2><pre>{JSON.stringify(trace.data, null, 2)}</pre></article>
    </section>
    <section className="grid two">
      <GovernanceForm title="人工复核" action="reviews" onSubmit={governance} />
      <GovernanceForm title="提交申诉" action="appeals" onSubmit={governance} />
    </section>
    {detail.data.appeals.length > 0 && <section className="card"><h2>开放申诉</h2>{detail.data.appeals.map((appeal, index) => <form className="inline-form" key={String(appeal.id ?? index)} onSubmit={event => resolveAppeal(event, String(appeal.id))}><code>{String(appeal.id)}</code><label>审核员<input name="reviewer" required /></label><label>结论<select name="verdict"><option>NEEDS_HUMAN_REVIEW</option><option>APPROVE</option><option>REJECT</option></select></label><label>说明<input name="note" required /></label><button type="submit">解决申诉</button></form>)}</section>}
  </main>
}

function GovernanceForm({ title, action, onSubmit }: { title: string; action: 'reviews' | 'appeals'; onSubmit: (event: FormEvent<HTMLFormElement>, action: 'reviews' | 'appeals') => void }) {
  return <form className="card stack" onSubmit={event => onSubmit(event, action)}><h2>{title}</h2><label>操作者<input name="actor" required /></label>{action === 'reviews' && <label>结论<select name="verdict"><option>APPROVE</option><option>REJECT</option><option>NEEDS_HUMAN_REVIEW</option></select></label>}<label>说明<textarea name="text" required /></label><button type="submit">提交</button></form>
}

function PolicyWorkspace() {
  const [policyId, setPolicyId] = useState('restricted.violence_weapon.display')
  const [yaml, setYaml] = useState('')
  const [versions, setVersions] = useState<Page<Record<string, unknown>> | null>(null)
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [replayVersions, setReplayVersions] = useState({ from: 1, to: 2 })
  const [replayJobId, setReplayJobId] = useState('')
  const replayStatus = usePolling<Record<string, unknown>>(replayJobId ? `/api/v1/replay-jobs/${replayJobId}` : '', Boolean(replayJobId))
  const replayDiff = usePolling<Array<Record<string, unknown>>>(replayJobId ? `/api/v1/replay-jobs/${replayJobId}/diff` : '', Boolean(replayJobId))
  const [message, setMessage] = useState('')
  async function publish(lifecycle: 'draft' | 'published') { try { await api('/api/v1/policies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_yaml: yaml, lifecycle }) }); setMessage('规则已保存。'); setVersions(await api(`/api/v1/policies/${policyId}/versions`)) } catch (error) { setMessage(String(error)) } }
  async function loadVersions() { try { setVersions(await api(`/api/v1/policies/${policyId}/versions`)) } catch (error) { setMessage(String(error)) } }
  async function previewReplay(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); const values = { from: Number(form.get('from')), to: Number(form.get('to')) }; setReplayVersions(values); try { setPreview(await api(`/api/v1/policies/${policyId}/replay/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ from_version: values.from, to_version: values.to, model_change_policy: 'keep' }) })) } catch (error) { setMessage(String(error)) } }
  async function runReplay() { if (!preview?.preview_sha256) return; try { const result = await api<{ job: { job_id: string } }>(`/api/v1/policies/${policyId}/replay`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ from_version: replayVersions.from, to_version: replayVersions.to, preview_sha256: preview.preview_sha256, model_change_policy: 'keep' }) }); setReplayJobId(result.job.job_id); setMessage('重放任务已提交。') } catch (error) { setMessage(String(error)) } }
  return <main className="workspace"><section className="hero"><p className="eyebrow">Policy governance</p><h1>规则与重放</h1><p>发布版本，预览影响范围，再确认执行。</p></section><section className="grid two"><article className="card stack"><label>Policy ID<input value={policyId} onChange={event => setPolicyId(event.target.value)} /></label><label>规则 YAML<textarea className="yaml" value={yaml} onChange={event => setYaml(event.target.value)} /></label><div className="actions"><button onClick={() => publish('draft')}>保存草稿</button><button onClick={() => publish('published')}>发布</button><button onClick={loadVersions}>刷新版本</button></div>{message && <p className="notice">{message}</p>}</article><article className="card"><h2>版本</h2><pre>{JSON.stringify(versions, null, 2)}</pre></article></section><section className="card"><h2>选择性重放</h2><form className="inline-form" onSubmit={previewReplay}><label>源版本<input name="from" type="number" min="1" defaultValue="1" /></label><label>目标版本<input name="to" type="number" min="1" defaultValue="2" /></label><button type="submit">计算预览</button></form><pre>{JSON.stringify(preview, null, 2)}</pre>{Boolean(preview?.preview_sha256) && <button onClick={runReplay}>确认执行</button>}<h3>任务状态</h3><pre>{JSON.stringify(replayStatus.data, null, 2)}</pre><h3>结论差异</h3><pre>{JSON.stringify(replayDiff.data, null, 2)}</pre></section></main>
}

function Status({ value }: { value: string }) { return <span className={`status status-${value.toLowerCase()}`}>{value}</span> }
function ErrorState({ message }: { message: string }) { return <div className="error" role="alert"><strong>请求失败</strong><span>{message}</span></div> }

export default function App() { return <BrowserRouter><Layout /></BrowserRouter> }
